"""
services/jobs_service.py -- a read-only view of every
job this app knows about, cross-process, shared by the FastAPI
`/api/jobs` routes.

Reads `db.job_records` (the records-only mirror of
`background_jobs.py`'s own in-memory state) rather than
`background_jobs` itself -- the whole point of this service is answering
"what jobs exist" from a process (the API host) that never started any
of them, which `background_jobs`'s own in-memory `_jobs` dict can't do.
cancel_job flags the job_records row, which the
owning process's throttled check in background_jobs picks up.

No HTTP types: takes plain values, returns plain
dicts, so a script could call it too.
"""

import json
import time
from typing import Optional

import db
from services import job_stage_service, ownership_service
import diagnostics_report
import background_jobs
from jobs import gpu_guard, gpu_queue, job_results, job_table
import job_force_stop
from jobs import job_store
from lib.errors import ConflictError, InvalidInputError, NotFoundError


# A queued/running record whose owner has not heartbeated this long (see
# job_table.HEARTBEAT_INTERVAL, far shorter) is treated as owned by a
# dead process (records-only mirror, no resume).
STALE_JOB_SECONDS = job_table.STALE_JOB_SECONDS


def _with_live_progress(record: dict) -> dict:
    """job_records is written on status changes only, so while a job this
    process runs is running, its progress and message come from
    background_jobs' in-memory state (what update_progress set), so polls
    and the event stream both see the bar move. A job whose last write
    failed keeps its last written status, with a message saying so."""
    try:
        live = job_stage_service.annotate(background_jobs.get_status(record.get("job_id")))
    except Exception:
        return record
    if not live:
        # No can_force_stop: Force stop reaches only jobs live in this process.
        return record
    if record.get("status") == "running" and live.get("status") not in (None, "running", "queued"):
        # The job finished between the caller's read of its row and this read of
        # the live state; the row's message is still the one written at start.
        record = db.get_job_record(str(record.get("job_id") or "")) or record
    out = dict(record)
    if record.get("status") == "running" and live.get("status") == "running":
        out["can_force_stop"] = job_force_stop.can_force_stop(live)
        progress = live.get("progress")
        if isinstance(progress, (int, float)) and not isinstance(progress, bool):
            out["progress"] = progress
        if live.get("message"):
            out["message"] = live.get("message")
        if background_jobs.job_may_be_stalled(live):
            out["stalled"] = True
            out["message"] = ((out.get("message") or "").rstrip()
                              + " No update for a while: this job may be stalled.")
    if live.get("sync_error"):
        out["message"] = " ".join(filter(None, [(out.get("message") or "").rstrip(),
                                                job_store.sync_error_suffix(live["sync_error"])]))
    return out


def _redact(record: dict) -> dict:
    """Same redaction diagnostics_service._job_summary already applies --
    a stored error/message could echo an API error verbatim."""
    record = _with_live_progress(record)
    out = dict(record)
    raw = out.pop("result_json", None)
    for internal in ("owner_pid", "owner_instance", "detail_state", "sync_error",
                     "cancel_requested_at", "kind"):
        out.pop(internal, None)
    try:
        stored = json.loads(raw) if raw else None
    except ValueError:
        stored = None
    # Re-projected on read too, so an older row can never leak a dropped key.
    out["result"] = job_results.project_result(stored)
    out["message"] = job_results.scrub_text(record.get("message") or "")
    out["error"] = job_results.scrub_text(record.get("error") or "") or None
    out["gpu_touching"] = bool(record.get("gpu_touching"))
    if out.get("status") == "cancelled" and gpu_guard.starting_again(str(record.get("job_id") or "")):
        # The GPU guard ends a waiting run just before it starts the new one under
        # the same id; a page polling the job must not read that as Cancelled.
        out["status"] = "queued"
    outcome, message = job_results.derive_outcome(out.get("status"), out["error"], out["result"],
                                      record.get("detail_state"), job_id=record.get("job_id"))
    out["outcome"] = outcome
    out["outcome_message"] = (job_results.scrub_text(message)[:job_results.MAX_STR]
                              if message else None)
    out["stale"] = is_stale(record)
    out["stalled"] = bool(record.get("stalled"))
    out["can_force_stop"] = bool(record.get("can_force_stop"))
    return out


def is_stale(record: dict, now: Optional[float] = None) -> bool:
    """A queued/running record no live owner has heartbeated for
    STALE_JOB_SECONDS: left behind by a process that died (the same test
    cancel_job applies before closing one). Judged on the server's clock,
    so a viewer's own clock can't make a live job look dead or the reverse."""
    if record.get("status") not in ("queued", "running"):
        return False
    if background_jobs.get_status(record.get("job_id")) is not None:
        return False
    if job_store.owner_gone(record):
        return True
    now = time.time() if now is None else now
    updated = record.get("updated_at") or 0
    if job_store.owner_alive_elsewhere(record.get("owner_pid"), updated, now):
        return False
    return now - updated > STALE_JOB_SECONDS


# Where the UI shows a job that has no title, by exact id or id prefix. Only
# a page name leaves the server (never the id's tail, a path or a URL). Ids
# with no entry, and drama-scoped ids (page "title"), are decided in job_page.
JOB_PAGE_BY_ID = {
    "sources_search": "sources", "sources_url_preview": "sources", "sources_save": "sources",
    "sources_url_preflight": "sources", "sources_url_identify": "sources",
    "sources_chapter_check": "sources",
    "discover_bulk_extract": "discover", "discover_navigation_help": "discover",
    "library_backup": "settings", "library_db_backup": "settings",
    "library_user_backup": "settings", "library_auto_backup": "settings",
    "deno_install": "diagnostics", "dependency_install": "diagnostics",
    "browser_install": "diagnostics", "upgrade_check": "diagnostics",
}
JOB_PAGE_BY_PREFIX = {
    "sources_series_": "sources", "sources_signin_": "sources", "sources_tiertest_": "sources",
    "live_": "live",
}


def job_page(job_id):
    """The page a job belongs to: "title" for a drama-scoped id (same rule as
    job_results.job_kind), else the page its id names, else None."""
    job_id = str(job_id or "")
    if background_jobs.drama_id_of_job(job_id) is not None:
        return "title"
    if job_id in JOB_PAGE_BY_ID:
        return JOB_PAGE_BY_ID[job_id]
    for prefix, page in JOB_PAGE_BY_PREFIX.items():
        if job_id.startswith(prefix):
            return page
    return None


def _visible(principal, record, drama_rows=None) -> bool:
    return ownership_service.can_see_job(principal, record.get("job_id"),
                                         record.get("owner_user_id"), drama_rows=drama_rows)


def _for_caller(principal, record, drama_rows=None) -> dict:
    """_redact plus `owned_by_me` (ownership_service.owns_job), the only
    word on who owns the job a response carries: never an owner id. Also
    `drama_id`, `kind` and `page`; callers reach this only for a job they may see,
    which for a drama job means they may see that drama."""
    out = _redact(record)
    out["drama_id"] = background_jobs.drama_id_of_job(record.get("job_id"))
    out["kind"] = job_results.job_kind(record.get("job_id"))
    out["page"] = job_page(record.get("job_id"))
    out["owned_by_me"] = ownership_service.owns_job(principal, record.get("job_id"),
                                                    record.get("owner_user_id"),
                                                    drama_rows=drama_rows)
    return out


def sweep_stale_job_records(records=None) -> int:
    """Closes every queued/running row left behind by a crashed, killed or
    restarted process (jobs/job_store.sweep_dead_owners). Returns how many."""
    return job_store.sweep_dead_owners(STALE_JOB_SECONDS, records=records)


def sweep_at_startup() -> list:
    """The startup sweep: closes what the last run left running, as
    sweep_stale_job_records does, and returns those rows for the restart
    notice."""
    closed = []
    job_store.sweep_dead_owners(STALE_JOB_SECONDS, closed_out=closed)
    return closed


def _still_active_here(job_id) -> bool:
    return (background_jobs.get_status(job_id) or {}).get("status") in job_store.ACTIVE


# GET /api/jobs is polled while the Jobs page is open. The sweep probes each
# active row's owner process; when the rows and this process's active jobs
# are unchanged since a sweep that closed nothing, the answer is the same, so
# the probes wait until something changes or this many seconds pass.
_SWEEP_REPEAT_SECONDS = 10.0
# The polled list carries every queued/running row but only this many finished ones, so it
# doesn't grow with the history; Clear finished still sees all of them.
_LISTED_FINISHED_JOBS = 300
_quiet_sweep = (None, 0.0)      # (what that sweep saw, time.monotonic() then)


def list_jobs(principal=None) -> list:
    """Every job_records row `principal` may see (
    ownership_service.can_see_job; None = auth off, all), newest-started
    first, redacted for HTTP. Stale rows of dead owners are closed first
    (sweep_stale_job_records), so they don't list as running forever."""
    records = db.list_job_records(_LISTED_FINISHED_JOBS)
    # The list is polled: with nothing queued or running, here or in a row,
    # the sweep has nothing to close. The rows are read again unless every
    # queued/running row is a job still queued or running in this process:
    # any other may have been closed since the read, by this sweep or by a
    # concurrent poll's, and must not list as running.
    global _quiet_sweep
    active = [r for r in records if r.get("status") in job_store.ACTIVE]
    here = background_jobs.active_job_ids()
    seen = (tuple((r.get("job_id"), r.get("status"), r.get("updated_at"), r.get("owner_pid"))
                  for r in active), tuple(here), db.DB_PATH)
    if (active or here) and not (
            _quiet_sweep[0] == seen
            and time.monotonic() - _quiet_sweep[1] < _SWEEP_REPEAT_SECONDS):
        closed = sweep_stale_job_records(records)
        _quiet_sweep = (seen, time.monotonic()) if not closed else (None, 0.0)
        if not all(_still_active_here(r.get("job_id")) for r in active):
            records = db.list_job_records(_LISTED_FINISHED_JOBS)
    rows = ownership_service.job_drama_ownership(principal, [r.get("job_id") for r in records])
    with diagnostics_report.reusing_account_names():
        return [_for_caller(principal, r, rows) for r in records if _visible(principal, r, rows)]


def get_job(job_id: str, principal=None) -> dict:
    """One job's record. Raises NotFoundError if no such job has ever
    been mirrored -- a plain KeyError-shaped miss, not a soft None, same
    vocabulary every other service in this migration uses -- and, the
    same way, for a job `principal` may not see."""
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    return _for_caller(principal, record)


def get_job_stages(job_id: str, principal=None) -> dict:
    """The job's per-stage timing and spend for its latest
    runs (job_timing). Same visibility as get_job."""
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    import job_timing
    runs = job_timing.list_runs(job_id)
    if not ownership_service.sees_every_job(principal):
        # A shared job id (sources_search, bulk_series_translate...) keeps
        # earlier runs by other users: show only the run the caller can see.
        started = record.get("started_at") or 0
        runs = [r for r in runs if r["run_started_at"] >= started]
    for run in runs:
        for stage in run["stages"]:
            stage["stage"] = job_results.scrub_text(stage["stage"])
    return {"job_id": job_id, "runs": runs}


def cancel_job(job_id: str, principal=None) -> dict:
    """Requests cancellation of a queued/running job, possibly owned by
    another process. In-process jobs get the normal cancel flag at once;
    the job_records flag is set either way so the owning process notices
    it (throttled check in background_jobs). Unknown id -> NotFoundError;
    already finished -> ConflictError (409). Cancellation is
    asynchronous: the returned status is the record's current one. A job
    `principal` may not see is a 404; one they see but may not stop
    (ownership_service.may_cancel_job: another member's job on a shared
    title, say) is a 403."""
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    owner = record.get("owner_user_id")
    ownership_service.require_job_changeable(principal, record.get("job_id"), owner)
    if record.get("status") not in ("queued", "running"):
        raise ConflictError(f"Job {job_id!r} already finished ({record.get('status')}).")
    # Only the run just checked: a shared id (sources_search,
    # bulk_series_translate...) may have been restarted by someone else.
    # A finished run of this id here says nothing about another process's
    # run, which job_store's owner check covers.
    if not background_jobs.request_cancel(job_id, owner_user_id=owner) or (
            not job_store.request_cancel(job_id, owner_user_id=owner)
            and _restarted_by_someone_else(job_id, owner)):
        raise ConflictError(_RESTARTED)
    live = background_jobs.get_status(job_id)
    if live is None and (job_store.close_if_owner_gone(record) or job_store.close_stale(
            job_id, time.time() - STALE_JOB_SECONDS)):
        # The owner process has exited, or sent no heartbeat for
        # STALE_JOB_SECONDS: nobody will read the flag. Each close is
        # conditional, so a live owner's heartbeat, "done" or new run wins.
        # It lists as interrupted, not cancelled: the work had already
        # stopped when its process died; the Cancel only tidies the row.
        return {"job_id": job_id, "cancel_requested": True, "status": "cancelled"}
    # A queued job here ends at once (request_cancel); a running one says
    # "Cancelling..." until its worker stops.
    status = live["status"] if live else record["status"]
    return {"job_id": job_id, "cancel_requested": True, "status": status}


_RESTARTED = "That job finished and a new one started. Refresh the list."


def _restarted_by_someone_else(job_id: str, owner) -> bool:
    fresh = db.get_job_record(job_id)
    return bool(fresh) and fresh.get("status") in ("queued", "running") \
        and fresh.get("owner_user_id") != owner


def force_stop_job(job_id: str, principal=None) -> dict:
    """Closes the record of a thread job that ignored Cancel for over a
    minute; same visibility and ownership rules as cancel_job. Unknown/invisible -> NotFoundError; not eligible ->
    ConflictError. The worker thread itself cannot be killed (see
    job_force_stop), so `worker_still_running` says whether it is still alive."""
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    ownership_service.require_job_changeable(principal, record.get("job_id"),
                                             record.get("owner_user_id"))
    result = job_force_stop.force_stop(job_id)
    return {"job_id": job_id, "force_stopped": True, **result}


def queue_summary(principal=None) -> dict:
    """Counts of running and waiting jobs across every process, and the
    place in the GPU line (1 = next) of each waiting job of the caller's
    own (ownership_service.owns_job): the GPU queue is the only one jobs
    wait in. Names, titles, ids and owners of anyone else's jobs never
    appear, only the counts. A stale row of a dead process counts until
    GET /api/jobs sweeps it, which the Jobs page does on every load."""
    counts = db.count_active_job_records()
    order = gpu_queue.gpu_queue_order()
    rows = ownership_service.job_drama_ownership(principal, [jid for jid, _o in order])
    mine = [{"job_id": jid, "position": n}
            for n, (jid, owner) in enumerate(order, start=1)
            if ownership_service.owns_job(principal, jid, owner, drama_rows=rows)]
    return {"lane": "gpu", "running": counts["running"], "waiting": counts["queued"],
            "mine": mine}


# job_records / background_jobs status names of a job that has ended.
_FINISHED_STATUSES = ("done", "error", "cancelled")


def _is_finished(record: Optional[dict]) -> bool:
    """True only when neither the mirror row nor this process's live state
    says queued/running: a row another process still marks active is never
    deletable, and a missing live entry says nothing about that process."""
    live = background_jobs.get_status((record or {}).get("job_id"))
    return ((record or {}).get("status") in _FINISHED_STATUSES
            and (live is None or live.get("status") in _FINISHED_STATUSES))


def delete_job(job_id: str, confirm: bool = False, principal=None) -> dict:
    """Permanently removes a finished job's record (in-memory and
    job_records). PC-owner action; a job `principal` may not see is a 404.
    Unknown id -> NotFoundError; queued/running -> ConflictError (409)."""
    if confirm is not True:
        raise InvalidInputError("Deleting a job needs confirm=true.")
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    if not _is_finished(record):
        raise ConflictError(f"Job {job_id!r} is still {record.get('status')}; cancel it first.")
    if not background_jobs.clear_finished_job(job_id, started_at=record.get("started_at")):
        raise ConflictError(_RESTARTED)
    return {"job_id": job_id, "deleted": True}


def clear_finished_jobs(confirm: bool = False) -> dict:
    """Permanently removes every finished job's record, leaving queued and
    running jobs (including ones another process owns) untouched."""
    if confirm is not True:
        raise InvalidInputError("Deleting job history needs confirm=true.")
    deleted = 0
    for rec in db.list_job_records():
        if _is_finished(rec) and background_jobs.clear_finished_job(
                rec["job_id"], started_at=rec.get("started_at")):
            deleted += 1
    return {"deleted_count": deleted}
