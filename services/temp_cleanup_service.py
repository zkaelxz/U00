"""
services/temp_cleanup_service.py -- "Clean temp files now": removes everything
in Baihe's own temp folder (<library>/tmp) regardless of age, for the owner who
sees leftovers pile up after a crash or a killed job.

Refused with a ConflictError while anything could still be using that folder:
a job in this process, a maintenance run, a job another app process recorded
as running, or a live holder of the cross-process GPU lock (all a CLI run
leaves). Returns counts and megabytes only, never a path.
"""
import background_jobs
import db
import storage
from services import library_admin_service
from services.service_errors import ConflictError

BUSY = "Finish or cancel the running jobs first, then clean temp files."


def _busy() -> bool:
    if (background_jobs.active_job_ids() or background_jobs.maintenance_active()
            or library_admin_service.any_job_running()):
        return True
    try:
        return db.gpu_lock_status()[0] is not None
    except Exception:
        return True    # an unreadable lock table: assume something is running


def clean_now() -> dict:
    """{"removed": entries removed, "freed_mb": size freed}. 409 while busy."""
    # The exclusive hold keeps a job from starting between the check and the sweep.
    if not background_jobs.acquire_exclusive("cleaning temp files"):
        raise ConflictError(BUSY)
    try:
        if _busy():
            raise ConflictError(BUSY)
        result = storage.sweep_library_temp(max_age=0, measure=True)
    finally:
        background_jobs.release_exclusive()
    return {"removed": result["removed"],
            "freed_mb": round(result["freed_bytes"] / (1024 * 1024), 1)}
