"""jobs/user_caps.py -- how many jobs one signed-in user may have at once.

So one member (or a stolen member account) can't keep the GPU busy for days
with runs on every title they can see while the PC owner's runs wait.
background_jobs calls these under its own lock and adds the job before
releasing it, so two starts at once can't both pass the count. Jobs are
counted by who started them, never by job id, so no job-id prefix escapes
the caps. Starter None is never capped: the PC owner, auth off,
jobs started by jobs, and records from before owners were kept. Finished
and cancelled jobs don't count.
"""

import contextlib
import threading
import time

import db
from lib.errors import RateLimitedError

MAX_RUNNING_JOBS_PER_USER = 2
MAX_QUEUED_JOBS_PER_USER = 10


# A member's daily limit is the PC owner's brake per person, set in People and access. The limit and
# the day's count live in app_settings (no schema change), keyed by user id, so they survive a restart
# and a second server process sees them.
JOBS_PER_DAY_KEY = "member_jobs_per_day.{}"
JOBS_TODAY_KEY = "member_jobs_today.{}"


def jobs_per_day(user_id) -> int | None:
    """The member's daily job limit, or None when they have none."""
    value = db.get_app_setting(JOBS_PER_DAY_KEY.format(user_id))
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def set_jobs_per_day(user_id, limit: int | None) -> None:
    db.set_app_setting(JOBS_PER_DAY_KEY.format(user_id), limit)


def jobs_today(user_id, today: str | None = None) -> int:
    """How many jobs this member has started today (the PC's local date, when the count resets)."""
    row = db.get_app_setting(JOBS_TODAY_KEY.format(user_id)) or {}
    return row.get("n", 0) if row.get("day") == (today or time.strftime("%Y-%m-%d")) else 0


def count_daily_start(owner_user_id, today: str | None = None) -> None:
    """Counts one job started by this member today, or raises RateLimitedError (429) when they are
    at their daily limit. The check and the count are one database transaction, so two starts at
    once can't both take the last one. Nobody without a limit (and never the PC owner) is counted."""
    limit = None if owner_user_id is None else jobs_per_day(owner_user_id)
    if limit is None:
        return
    day = today or time.strftime("%Y-%m-%d")

    def take_one(row):
        n = row.get("n", 0) if isinstance(row, dict) and row.get("day") == day else 0
        if n >= limit:
            raise RateLimitedError(
                f"You've reached your limit of {limit} {'job' if limit == 1 else 'jobs'} for today. "
                "It starts again tomorrow, or the PC owner can raise it.")
        return {"day": day, "n": n + 1}
    db.update_app_setting(JOBS_TODAY_KEY.format(owner_user_id), take_one, default={})


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
