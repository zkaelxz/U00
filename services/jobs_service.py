"""
services/jobs_service.py -- Migration Slice 8: a read-only view of every
job this app knows about, cross-process, shared by the FastAPI
`/api/jobs` routes.

Reads `db.job_records` (Migration Slice 7's records-only mirror of
`background_jobs.py`'s own in-memory state) rather than
`background_jobs` itself -- the whole point of this slice is answering
"what jobs exist" from a process (the API host) that never started any
of them, which `background_jobs`'s own in-memory `_jobs` dict can't do.
Slice 22 adds cancel_job: it flags the job_records row, which the
owning process's throttled check in background_jobs picks up.

No Streamlit import, no HTTP types: takes plain values, returns plain
dicts, so `cli.py` or a script could call it too.
"""

import time

import db
import diagnostics
import background_jobs
from services.service_errors import ConflictError, NotFoundError


# A queued/running record whose owner has not heartbeated this long (see
# background_jobs.HEARTBEAT_INTERVAL, far shorter) is treated as owned by a
# dead process (records-only mirror, no resume).
STALE_JOB_SECONDS = 15 * 60


def _redact(record: dict) -> dict:
    """Same redaction diagnostics_service._job_summary already applies --
    a stored error/message could echo an API error verbatim."""
    out = dict(record)
    out["message"] = diagnostics.redact_for_support(record.get("message") or "")
    out["error"] = diagnostics.redact_for_support(record.get("error") or "") or None
    out["gpu_touching"] = bool(record.get("gpu_touching"))
    return out


def list_jobs() -> list:
    """Every job_records row, newest-started first, redacted for HTTP."""
    return [_redact(r) for r in db.list_job_records()]


def get_job(job_id: str) -> dict:
    """One job's record. Raises NotFoundError if no such job has ever
    been mirrored -- a plain KeyError-shaped miss, not a soft None, same
    vocabulary every other service in this migration uses."""
    record = db.get_job_record(job_id)
    if record is None:
        raise NotFoundError(f"No job with id {job_id!r}.")
    return _redact(record)


def cancel_job(job_id: str) -> dict:
    """Requests cancellation of a queued/running job, possibly owned by
    another process. In-process jobs get the normal cancel flag at once;
    the job_records flag is set either way so the owning process notices
    it (throttled check in background_jobs). Unknown id -> NotFoundError;
    already finished -> ConflictError (409). Cancellation is
    asynchronous: the returned status is the record's current one."""
    record = db.get_job_record(job_id)
    if record is None:
        raise NotFoundError(f"No job with id {job_id!r}.")
    if record.get("status") not in ("queued", "running"):
        raise ConflictError(f"Job {job_id!r} already finished ({record.get('status')}).")
    background_jobs.request_cancel(job_id)
    db.request_job_record_cancel(job_id)
    if (background_jobs.get_status(job_id) is None
            and db.close_stale_job_record(job_id, time.time() - STALE_JOB_SECONDS)):
        # No heartbeat for STALE_JOB_SECONDS: the owner process is gone and
        # nobody will read the flag. The close is conditional on the row
        # still being stale, so a live owner's heartbeat or "done" wins.
        return {"job_id": job_id, "cancel_requested": True, "status": "cancelled"}
    return {"job_id": job_id, "cancel_requested": True, "status": record["status"]}
