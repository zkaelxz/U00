"""Test double for gpu_process_job.run_in_child: runs the worker body in this
process so a test can fake the model call with a monkeypatch (a spawned child
imports everything fresh and would not see it). Cancel/timeout behaviour of
the real child is covered by tests/test_gpu_process_job.py and each moved
job's real-process test. An "item" put after Cancel raises JobCancelled, so a
test cancels mid-hearing by setting the flag from the body or a patched
call."""
import shutil
import tempfile

import background_jobs


class _Killed(BaseException):
    """Stands in for the kill: BaseException so a worker's `except Exception`
    can't swallow it, as it can't swallow a SIGKILL."""


class _InlineQueue:
    def __init__(self, job_id, on_item):
        self.job_id = job_id
        self.on_item = on_item
        self.result = None

    def put(self, item):
        if background_jobs._apply_progress_item(self.job_id, item):
            return
        if item[0] == "item":
            if self.on_item is not None:
                self.on_item(item[1])
            # The real parent kills the child at the next poll after Cancel.
            if background_jobs.is_cancel_requested(self.job_id):
                raise _Killed()
            return
        self.result = item


def run_in_child_inline(job_id, body, args, *, timeout_s, poll_s=0.25, on_item=None):
    channel = _InlineQueue(job_id, on_item)
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    scratch = tempfile.mkdtemp()
    try:
        body(*args, scratch, channel)
    except _Killed:
        raise background_jobs.JobCancelled(job_id) from None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    kind, *rest = channel.result
    if kind == "error":
        from services import gpu_process_job
        raise gpu_process_job.ChildFailed(rest[1])
    return rest[0]
