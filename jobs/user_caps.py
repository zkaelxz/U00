"""jobs/user_caps.py -- how many jobs one signed-in user may have at once.

So one member (or a stolen member account) can't keep the GPU busy for days
with runs on every title they can see while the PC owner's runs wait.
background_jobs calls these under its own lock and adds the job before
releasing it, so two starts at once can't both pass the count. Jobs are
counted by who started them, never by job id, so no job-id prefix escapes
the caps. Starter None is never capped: the PC owner, auth off, the CLI,
jobs started by jobs, and records from before owners were kept. Finished
and cancelled jobs don't count.
"""

import contextlib
import threading

from lib.errors import RateLimitedError

MAX_RUNNING_JOBS_PER_USER = 2
MAX_QUEUED_JOBS_PER_USER = 5


_refusing = threading.local()


def count(jobs: dict, owner_user_id, status: str) -> int:
    return sum(1 for job in jobs.values()
               if job.get("owner_user_id") == owner_user_id and job.get("status") == status)


def at_running_cap(jobs: dict, owner_user_id) -> bool:
    """A force-stopped job's worker that is still running counts too: its
    record says cancelled, but it still holds a thread (and maybe the GPU)."""
    if owner_user_id is None:
        return False
    import job_force_stop
    running = count(jobs, owner_user_id, "running")
    return running + job_force_stop.abandoned_owned_by_locked(owner_user_id) \
        >= MAX_RUNNING_JOBS_PER_USER


@contextlib.contextmanager
def refuse_at_cap():
    """For the job queue runner, which runs one item at a time: a start over
    the running cap is refused (429) rather than left waiting in the GPU
    queue, and a job it starts is never held back there for its starter's
    cap, so one member at the cap can't stall everyone's queue."""
    _refusing.on = True
    try:
        yield
    finally:
        _refusing.on = False


def refusing() -> bool:
    return getattr(_refusing, "on", False)


def wait_for_cap(jobs: dict, owner_user_id) -> bool:
    """Whether a GPU start should queue until the starter is under the
    running cap; inside refuse_at_cap it raises RateLimitedError instead."""
    if not at_running_cap(jobs, owner_user_id):
        return False
    if refusing():
        refuse_over_cap(jobs, owner_user_id, queueing=False)
    return True


def held(jobs: dict, entry: dict) -> bool:
    """Whether a GPU queue entry waits for its starter's running cap."""
    job = jobs.get(entry["job_id"]) or {}
    return not entry.get("cap_exempt") and at_running_cap(jobs, job.get("owner_user_id"))


def refuse_over_cap(jobs: dict, owner_user_id, queueing: bool) -> None:
    """Raises RateLimitedError (429) when one more job would pass a cap. The
    message gives only the caller's own limit, never anyone else's jobs."""
    if owner_user_id is None:
        return
    if queueing:
        if count(jobs, owner_user_id, "queued") >= MAX_QUEUED_JOBS_PER_USER:
            raise RateLimitedError(
                f"You already have {MAX_QUEUED_JOBS_PER_USER} jobs waiting to run. Wait for "
                "one to start, or cancel one, then try again.")
    elif at_running_cap(jobs, owner_user_id):
        raise RateLimitedError(
            f"You already have {MAX_RUNNING_JOBS_PER_USER} jobs running. Wait for one to "
            "finish, or cancel one, then try again.")
