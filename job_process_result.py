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

# Under the pipe buffer (64 KB on Linux): a queued message this size is never
# left half-written by a child that is killed.
INLINE_LIMIT_BYTES = 32 * 1024
# An error message can embed a large provider response; the final status only
# needs the start of it.
MAX_ERROR_MESSAGE_CHARS = 8000

_FILE_MARKER = "ok_file"


class ResultChannel:
    """Wraps the job's multiprocessing queue. The worker only calls put();
    the watcher calls get() and close(), which also removes the result file
    (so a cancel, where the file may exist but is never read, leaves none)."""

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
                os.makedirs(self._dir, exist_ok=True)
                partial = self._path + ".part"
                with open(partial, "wb") as f:
                    f.write(data)
                # Renamed only once complete: the marker below never points
                # at a half-written file.
                os.replace(partial, self._path)
                self._queue.put((_FILE_MARKER,))
                return
        elif item and item[0] == "error" and len(item) >= 3:
            item = (item[0], item[1], str(item[2])[:MAX_ERROR_MESSAGE_CHARS])
        self._queue.put(item)

    def get(self, timeout=None):
        item = self._queue.get(timeout=timeout)
        if item and item[0] == _FILE_MARKER:
            with open(self._path, "rb") as f:
                return pickle.load(f)
        return item

    def close(self):
        try:
            self._queue.close()
        except Exception:
            pass
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
