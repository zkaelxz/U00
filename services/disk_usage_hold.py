"""
services/disk_usage_hold.py -- the library hold of the data-folder disk usage
feature: why clear, move and restore are refused right now, and the exclusive
hold (plus the one-change-at-a-time lock) around inspect -> re-resolve ->
change, so no job starts in between. UI-free; raises services/service_errors.
"""

import contextlib
import threading

import background_jobs
from jobs import job_launch
from jobs import gpu_slots
from services.service_errors import ConflictError, InvalidInputError

BUSY = ("A job, restore or other library task is running. Wait for it to finish, then try "
        "again.")

_op_lock = threading.Lock()


def busy_reason():
    """Why clear and move are refused right now, or None."""
    if (background_jobs.active_job_ids() or job_launch.maintenance_active()
            or job_launch.exclusive_active()):
        return BUSY
    return None


def _require_confirm(confirm):
    if confirm is not True:
        raise InvalidInputError("Confirm to continue (confirm must be true).")


def _busy_under_hold() -> bool:
    """Busy checks that stay valid while this module holds the library: jobs
    in this process, maintenance, fresh running job_records rows written by
    another app process, and a live holder of the cross-process GPU lock. The
    CLI writes no job_records, so a CLI run is seen only while it holds the
    GPU lock (its GPU steps); a CLI run of non-GPU steps is not detected.
    Fails closed: an unreadable lock table counts as busy."""
    from services import library_admin_service
    if (background_jobs.active_job_ids() or job_launch.maintenance_active()
            or library_admin_service.any_job_running()):
        return True
    try:
        return gpu_slots.status()[0] is not None
    except Exception:
        return True


@contextlib.contextmanager
def _exclusive(label: str):
    """The exclusive library hold around inspect -> re-resolve -> change, so no
    job starts in between. ConflictError when it can't be taken."""
    if not job_launch.acquire_exclusive(label):
        raise ConflictError(BUSY)
    try:
        if _busy_under_hold():
            raise ConflictError(BUSY)
        yield
    finally:
        job_launch.release_exclusive()


@contextlib.contextmanager
def _changing(label: str):
    """One change at a time, under the library's exclusive hold."""
    if not _op_lock.acquire(blocking=False):
        raise ConflictError("Another clear or move is in progress. Try again in a moment.")
    try:
        with _exclusive(label):
            yield
    finally:
        _op_lock.release()
