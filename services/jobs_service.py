"""
services/jobs_service.py -- Migration Slice 8: a read-only view of every
job this app knows about, cross-process, shared by the FastAPI
`/api/jobs` routes.

Reads `db.job_records` (Migration Slice 7's records-only mirror of
`background_jobs.py`'s own in-memory state) rather than
`background_jobs` itself -- the whole point of this slice is answering
"what jobs exist" from a process (the API host) that never started any
of them, which `background_jobs`'s own in-memory `_jobs` dict can't do.
Cancelling a job from a different process than the one running it needs
its own mechanism beyond a records-only mirror (the owning process has
to notice the request) and is deliberately not built here -- see
docs/migration-review.md's Slice 8 note for why.

No Streamlit import, no HTTP types: takes plain values, returns plain
dicts, so `cli.py` or a script could call it too.
"""

import db
import diagnostics
from services.service_errors import NotFoundError


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
