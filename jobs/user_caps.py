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
import shutil
import threading
import time

import db
import stored_settings
from lib import acting
from lib.errors import ConflictError, RateLimitedError

MAX_RUNNING_JOBS_PER_USER = 2
MAX_QUEUED_JOBS_PER_USER = 10


# A member's daily limit is the PC owner's brake per person, set in People and access. The limit and
# the day's count live in app_settings (no schema change), keyed by user id, so they survive a restart
# and a second server process sees them.
JOBS_PER_DAY_KEY = "member_jobs_per_day.{}"
JOBS_TODAY_KEY = "member_jobs_today.{}"


class DailyLimitError(RateLimitedError):
    """The member has started their daily number of jobs. A type of its own so the job queue can
    tell it from the running cap, which clears when a job ends, while this one lasts until tomorrow."""


class DiskLowError(ConflictError):
    """The library drive is nearly full. A type of its own so the job queue holds items while
    space is short instead of skipping them: it is a passing state, not something wrong with the item."""


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


def count_daily_start(owner_user_id, today: str | None = None) -> str | None:
    """Counts one job started by this member today, or raises DailyLimitError (429) when they are
    at their daily limit. The check and the count are one database transaction, so two starts at
    once can't both take the last one. Nobody without a limit (and never the PC owner) is counted.
    Returns the day the start was counted on (for refund_daily_start), None when nothing was."""
    limit = None if owner_user_id is None else jobs_per_day(owner_user_id)
    if limit is None:
        return None
    day = today or time.strftime("%Y-%m-%d")

    def take_one(row):
        n = row.get("n", 0) if isinstance(row, dict) and row.get("day") == day else 0
        if n >= limit:
            raise DailyLimitError(
                f"You've reached your limit of {limit} {'job' if limit == 1 else 'jobs'} for today. "
                "It starts again tomorrow, or the PC owner can raise it.")
        return {"day": day, "n": n + 1}
    db.update_app_setting(JOBS_TODAY_KEY.format(owner_user_id), take_one, default={})
    return day


def refund_daily_start(owner_user_id, day: str | None) -> None:
    """Gives back a start counted on `day` whose job never ran (its thread or process could not
    start). A count from an earlier day is already gone, so only that same day's count is lowered."""
    if owner_user_id is None or day is None:
        return

    def give_back(row):
        if isinstance(row, dict) and row.get("day") == day and row.get("n", 0) > 0:
            return {"day": day, "n": row["n"] - 1}
        return row
    db.update_app_setting(JOBS_TODAY_KEY.format(owner_user_id), give_back, default={})


# Matches the default of the "free disk space" PC alert. A member's download, import or dub can
# write gigabytes, and nobody is at the PC to notice the drive filling, so their jobs stop
# starting before the library drive is nearly full. The owner's and admins' own jobs are never
# held back.
MEMBER_MIN_FREE_GB = 10
# resource_monitor_service's rules setting and the range its disk limit is accepted in.
ALERT_RULES_SETTING = "resource_alert_rules"
_ALERT_LIMIT_RANGE = (1, 100000)


def member_min_free_gb() -> float:
    """MEMBER_MIN_FREE_GB, or the owner's own low-disk alert limit when they set it lower:
    members must not sit waiting for space at a level that never alerts the owner."""
    rules = stored_settings.read(ALERT_RULES_SETTING)
    disk = rules.get("disk_free") if isinstance(rules, dict) else None
    limit = disk.get("limit") if isinstance(disk, dict) else None
    if (isinstance(limit, bool) or not isinstance(limit, (int, float))
            or not _ALERT_LIMIT_RANGE[0] <= limit <= _ALERT_LIMIT_RANGE[1]):
        return MEMBER_MIN_FREE_GB
    return min(MEMBER_MIN_FREE_GB, limit)


def _is_admin_account(user_id) -> bool:
    """Read now, so a demotion or a deactivation applies at once."""
    user = db.auth_get_user(user_id)
    return bool(user and user["is_admin"] and user["is_active"])


def held_back_by_low_disk(owner_user_id) -> bool:
    """Whether this account's queue items wait for disk space: members only. With sign-in on,
    the PC owner's own account has a user id like everyone else's, so the admin flag is what
    tells them apart."""
    return owner_user_id is not None and not _is_admin_account(owner_user_id)


def refuse_if_disk_low(owner_user_id) -> None:
    """Raises DiskLowError (409) when a member would start a job while the library drive has
    less than member_min_free_gb() free. An unreadable drive is not a reason to refuse."""
    if owner_user_id is None:
        return
    try:
        free_gb = shutil.disk_usage(db.LIBRARY_DIR).free / 1024 ** 3
    except OSError:
        return
    if free_gb >= member_min_free_gb():
        return
    # The request's own principal must be an admin's too: a device token or scheduled work acts
    # for an admin account with member rights only, so it is held like a member's, and so is a
    # queue item it added (held_for_disk, set by the queue runner).
    principal = acting.acting_principal() or {}
    if not (principal.get("user_id") == owner_user_id and principal.get("is_admin")
            and not principal.get("held_for_disk") and _is_admin_account(owner_user_id)):
        raise DiskLowError("This PC is almost out of disk space, so new jobs can't start right now. "
                            "Try again later, or ask the PC owner to free some space.")


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
