"""
services/diagnostics_service.py -- a read-only snapshot of Diagnostics for
GET /api/diagnostics.

This wraps `diagnostics.py`'s and `background_jobs.py`'s existing
read-only checks verbatim -- no new logic, no admin action (those are in
diagnostics_gaps_service and diagnostics_installs_service, behind PC-only
routes). Every check here is local (imports,
filesystem, GPU driver, in-process job state) -- never a network call,
matching this service's own "diagnostics overview" scope; version
freshness checks hit PyPI, so they live in diagnostics_gaps_service.
check_package_updates, behind an explicit click.

No HTTP types: takes no arguments, returns a plain
dict, so `cli.py` or a script could call it too.
"""

import os

import background_jobs
import db
import diagnostics
import applog

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _job_summary(job_id: str, job: dict) -> dict:
    """A redacted, HTTP-safe view of one job's tracked state -- never the
    raw `result` (arbitrary, possibly non-serializable job output) and
    never an unredacted `message`/`error` (could echo a stored API
    error verbatim, the exact thing `diagnostics.redact_for_support`
    exists to strip)."""
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "progress": job.get("progress"),
        "message": diagnostics.redact_for_support(job.get("message") or ""),
        "error": diagnostics.redact_for_support(job.get("error") or "") or None,
        "description": job.get("description"),
        "gpu_touching": bool(job.get("gpu_touching")),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
    }


def get_diagnostics_overview() -> dict:
    """{dependencies, file_completeness, library_writable, gpu,
    model_engine_versions, running_jobs, recent_log_lines} -- the same
    facts Diagnostics' own routine view already shows, reusing its
    existing functions rather than recomputing anything. Log lines are
    redacted with diagnostics.redact_for_support, as in the support
    report."""
    running = background_jobs.list_running_jobs()
    import core
    gpu = dict(diagnostics.get_gpu_status())
    # What faster-whisper's own runtime (ctranslate2) sees -- can differ
    # from torch (missing cuBLAS/cuDNN makes Whisper silently use CPU).
    gpu["whisper"] = core.gpu_status()
    return {
        "dependencies": diagnostics.check_all_dependencies(),
        "file_completeness": diagnostics.check_file_completeness(PROJECT_ROOT),
        "library_writable": diagnostics.check_library_writable(db.LIBRARY_DIR),
        "gpu": gpu,
        "model_engine_versions": diagnostics.get_model_engine_versions(),
        "running_jobs": [_job_summary(jid, j) for jid, j in running.items()],
        "recent_log_lines": [diagnostics.redact_for_support(ln) for ln in applog.tail(50)],
    }
