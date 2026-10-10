"""
job_process_result.py -- the channel a process-job worker sends its result
through. Small items go straight onto the multiprocessing queue; a large
final result is written to a file and only a short marker is queued.

Why: a multiprocessing.Queue message bigger than the OS pipe buffer is written
in pieces, and the parent also holds the pipe's write end, so a child killed
partway through one never produces EOF and the parent's blocking get() waits
forever. A marker is small enough to be written whole, and a file the child
died writing is simply never announced.
"""

import os
import pickle
import uuid

# At most PIPE_BUF (4 KB on Linux): pipe writes up to that size are atomic, so
# a queued message this small is never left half-written by a child that is
# killed. Above ~16 KB CPython's Connection writes header and payload as two
# separate writes, which is the window a SIGKILL can split.
INLINE_LIMIT_BYTES = 4000
# An error message can embed a large provider response; the final status only
# needs the start of it. Bytes, not characters, and below the inline limit so
# an error item always travels as one atomic write.
MAX_ERROR_MESSAGE_BYTES = 3000
RESULT_FILE_ERROR = "The job's result file could not be written or read."

_FILE_MARKER = "ok_file"


class ResultChannel:
    """Wraps the job's multiprocessing queue. The worker calls put() and may
    close() its end of the queue; only the watcher calls get() and discard(),
    which removes the result file (so a cancel, where the file may exist but is
    never read, leaves none). close() must not delete it: a child closing after
    announcing a large result would remove the file the parent is about to
    read."""

    def __init__(self, mp_queue, result_dir: str, owner_token: str):
        self._queue = mp_queue
        self._dir = result_dir
        # The owner prefix is the library temp folder's naming (see
        # storage.new_workdir) so its startup sweep spares a live job's file.
        self._path = os.path.join(result_dir, f"{owner_token}~result-{uuid.uuid4().hex}.pkl")

    def put(self, item):
        if item and item[0] == "ok":
            data = pickle.dumps(item, pickle.HIGHEST_PROTOCOL)
            if len(data) > INLINE_LIMIT_BYTES:
                self._write_result_file(data)
                self._queue.put((_FILE_MARKER,))
                return
        elif item and item[0] == "error" and len(item) >= 3:
            # Redact before truncating: a key cut at the limit could fall
            # below the redaction pattern's minimum length and survive.
            from translate_engines import redact_secrets
            text = redact_secrets(str(item[2]))
            text = text.encode("utf-8")[:MAX_ERROR_MESSAGE_BYTES].decode("utf-8", "ignore")
            item = (item[0], item[1], text)
        self._queue.put(item)

    def _write_result_file(self, data: bytes):
        try:
            os.makedirs(self._dir, exist_ok=True)
            partial = self._path + ".part"
            with open(partial, "wb") as f:
                f.write(data)
            # Renamed only once complete: the marker never points at a
            # half-written file.
            os.replace(partial, self._path)
        except OSError:
            # The OSError text carries the library temp path, which must not
            # reach the stored job error or the log.
            raise OSError(RESULT_FILE_ERROR) from None

    def get(self, timeout=None):
        item = self._queue.get(timeout=timeout)
        if item and item[0] == _FILE_MARKER:
            try:
                with open(self._path, "rb") as f:
                    result = pickle.load(f)
            except OSError:
                raise OSError(RESULT_FILE_ERROR) from None
            # Removed before the watcher can publish the job as done: its
            # final discard() runs after that, so a client that saw "done"
            # could still find the file.
            self._remove_files()
            return result
        return item

    def close(self):
        try:
            self._queue.close()
        except Exception:
            pass

    def join_thread(self):
        self._queue.join_thread()

    def cancel_join_thread(self):
        self._queue.cancel_join_thread()

    def discard(self):
        """Watcher only: removes the result file and any partial one."""
        self._remove_files()

    def _remove_files(self):
        for path in (self._path, self._path + ".part"):
            try:
                os.remove(path)
            except OSError:
                pass


def wrap_queue(mp_queue, job_id) -> ResultChannel:
    """The channel for a job's queue, its file in the library temp folder."""
    import db
    import storage
    return ResultChannel(mp_queue, os.path.join(db.LIBRARY_DIR, storage.TEMP_DIRNAME),
                         storage._owner_token(job_id))
