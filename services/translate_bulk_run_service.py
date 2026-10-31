"""services/translate_bulk_run_service.py -- bulk translation job runner, listing, cancel and error dismissal.

Moved out of services/translate_run_service.py to keep it under the size ceiling. Import these names from
`services.translate_run_service`, which re-exports them at its end; nothing imports this module
first, because it needs `services.translate_run_service` fully defined.
"""

import background_jobs
import bulk_providers
import bulk_poll
import bulk_reflect
import db
import translate_engines
from services import bulk_job_view, ownership_service, translate_service
from services.service_errors import ConflictError, DependencyUnavailableError, NotFoundError
from services.translate_run_service import (ERRORS_LOCK)


def bulk_job_id(drama_id: int) -> str:
    return f"bulk_translate_{drama_id}"


_NEXT_REFLECT_STAGE = {"faithful": "reflect", "reflect": "expressive"}


def run_bulk_translate_job(job_id, engine, engine_name, submit, monthly_cap_usd=None):
    """Background job: submit, then poll inside this job until the batch is
    applied (bulk_poll.run_bulk_poller; results applied by line id).
    A bulk Reflect pipeline's later stages are submitted by bulk_translate
    as each prior stage applies; this follows each one in turn. Cancelling
    this job cancels the pending bulk job (at the provider when it can)."""
    if background_jobs.is_cancel_requested(job_id):
        return
    first_id = current = submit()
    provider = bulk_providers.make_provider(engine_name, engine)
    while True:
        bulk_poll.run_bulk_poller(job_id, current, provider=provider, engine=engine,
                                       interval=bulk_poll.POLL_INTERVAL_SECONDS,
                                       monthly_cap_usd=monthly_cap_usd)
        job = db.get_bulk_job(current) or {}
        if background_jobs.is_cancel_requested(job_id):
            if job.get("status") in db.BULK_PENDING_STATUSES + ("running",):
                bulk_poll.cancel_bulk_job(current, provider)
                job = db.get_bulk_job(current) or {}
            break
        nxt_stage = (_NEXT_REFLECT_STAGE.get(job.get("stage"))
                     if job.get("kind") == "reflect" and job.get("status") == "applied" else None)
        nxt = (bulk_reflect.sibling_stage_job(job["pipeline_id"], nxt_stage, skip_failed=True)
               if nxt_stage else None)
        if not nxt:
            break
        current = nxt["id"]
    background_jobs.set_result(job_id, {
        "bulk_job_id": first_id, "final_bulk_job_id": current, "status": job.get("status"),
        "stage": job.get("stage"), "summary": job.get("result_summary"),
        "last_error": job.get("last_error"), **bulk_poll.poller_outcome(job_id, job)})
    if job.get("status") in ("failed", "auth_error"):
        raise RuntimeError(job.get("last_error") or "The bulk job failed.")


def _bulk_engine_factory(engine_name, model):
    key = translate_service.resolve_api_key(engine_name)
    return translate_engines.get_engine(engine_name, key, model or None) if key else None


pending_bulk_engines = bulk_poll.resumable_engines


def list_bulk_translations(drama_id: int) -> dict:
    """Read-only: this drama's bulk jobs (newest first) with the status
    last recorded in the database. Never contacts a provider; polling stays
    with resume_bulk_translations."""
    ownership_service.require_drama(drama_id)
    return {"drama_id": drama_id,
            "jobs": [bulk_job_view.bulk_entry(j) for j in db.list_bulk_jobs(drama_id)]}


def cancel_bulk_translation(drama_id: int, bulk_job_id: int, principal) -> dict:
    """Cancel: stops polling, marks the job cancelled and
    asks the provider to cancel when a server-side key exists (best effort,
    same as bulk_poll.cancel_bulk_job). A batch records no starter, so
    only the title's owner and admins may cancel it
    (ownership_service.require_drama_work_cancellable)."""
    ownership_service.require_drama(drama_id)
    ownership_service.require_drama_work_cancellable(principal, drama_id)
    job = db.get_bulk_job(bulk_job_id)
    if not job or job["drama_id"] != drama_id:
        raise NotFoundError(f"Bulk job {bulk_job_id} not found for title {drama_id}.")
    if job["status"] not in bulk_job_view.CANCELLABLE:
        raise ConflictError(f"Bulk job {bulk_job_id} is {job['status']} and cannot be cancelled.")
    key = translate_service.resolve_api_key(job["engine"])
    package_missing = False
    try:
        engine = translate_engines.get_engine(
            job["engine"], key, job.get("model") or None) if key else None
    except DependencyUnavailableError:
        # Still cancel here: the batch keeps running at the provider, which the note says.
        engine, package_missing = None, True
    provider = bulk_providers.make_provider(job["engine"], engine) if engine else None
    note = bulk_poll.cancel_bulk_job(bulk_job_id, provider)
    if package_missing:
        note += (" The Python package for this engine is not installed, so the batch was not "
                 "cancelled at the provider; cancel it in the provider's console.")
    return {"drama_id": drama_id, "bulk_job": bulk_job_view.bulk_entry(db.get_bulk_job(bulk_job_id)),
            "message": note}


def dismiss_translate_errors(drama_id: int) -> dict:
    """Dismisses the failed-batches notice: clears only the drama's
    persisted record of the last run's failed batches
    (db.update_drama(last_translate_errors=None)); lines are untouched, so a
    later "Translate all lines" still retries the missing ones. NotFoundError
    for an unknown drama; `dismissed` is False when there was nothing to clear."""
    ownership_service.require_drama(drama_id)
    with ERRORS_LOCK:
        had = bool((db.get_drama(drama_id) or {}).get("last_translate_errors"))
        if had:
            db.update_drama(drama_id, last_translate_errors=None)
    return {"drama_id": drama_id, "dismissed": had}
