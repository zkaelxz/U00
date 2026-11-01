"""
bulk_poll.py -- the life of a submitted bulk job: polling the provider, applying
results by the job's kind, DeepSeek's scheduled off-peak run, resume after a
restart, and cancel.
"""
import datetime
import threading
import time

import background_jobs
from jobs import job_launch, job_table
from bulk_apply import (apply_bulk_results, apply_consistency_results, apply_emotion_results,
                        apply_flag_results, apply_notes_results, require_bulk_job)
from bulk_providers import (BulkAuthError, PollGuard, is_deepseek_offpeak,
                            make_provider, next_deepseek_offpeak_start, reschedule_for_next_window,
                            WindowGate, offpeak_cap)
from bulk_reflect import (_advance_pipeline, _fail_keeping_drafts, apply_reflect_stage_results,
                          sibling_stage_job)
from bulk_translate import _character_names, _utcnow
import db
import job_force_stop
from engine_backends import shared, translate_pipeline, pricing
from translation_finish import finish_translation_run, own_lines_callbacks

POLL_INTERVAL_SECONDS = 60


def poll_job_id(bulk_job_id: int) -> str:
    return f"bulkpoll_{bulk_job_id}"


def _polled_by_outer_job(job: dict) -> bool:
    """run_bulk_translate_job polls under its own id, not poll_job_id."""
    kind = job.get("kind") or "translate"
    kind = {"reflect": "translate", "translation_notes": "notes"}.get(kind, kind)
    outer_id = f"bulk_{kind}_{job['drama_id']}"
    with job_table._lock:
        # Force-stopped workers may still bill.
        return (background_jobs.is_running(outer_id)
                or job_force_stop.abandoned_alive_locked(outer_id))


_APPLIERS = {"translate": apply_bulk_results, "flag": apply_flag_results,
             "consistency": apply_consistency_results, "emotion": apply_emotion_results,
             "translation_notes": apply_notes_results, "reflect": apply_reflect_stage_results}


def _lines_per_request(bulk_job_id: int) -> dict:
    out = {}
    for r in db.list_bulk_job_lines(bulk_job_id):
        out[r["request_key"]] = out.get(r["request_key"], 0) + 1
    return out


def apply_results_for_job(bulk_job_id: int, results) -> dict:
    """Dispatches to the right apply_* function by the job's own kind --
    what check_once/_check_once_locked call instead of apply_bulk_results
    directly, now that a bulk job isn't always a translation. A request the
    provider never answered (a short result stream, an empty inlined list)
    counts as failed, with its lines missing, instead of vanishing."""
    results = list(results)
    kind = require_bulk_job(bulk_job_id).get("kind") or "translate"
    if kind not in _APPLIERS:
        raise ValueError(f"Unknown bulk job kind: {kind}")
    counts = _APPLIERS[kind](bulk_job_id, results)
    answered = {r[0] for r in results}
    for key, n_lines in _lines_per_request(bulk_job_id).items():
        if key not in answered:
            counts["failed_requests"] += 1
            if "missing" in counts:
                counts["missing"] += n_lines
    return counts


# ---------------------------------------------------------------------------
# Polling, scheduled runs, cancel, resume
# ---------------------------------------------------------------------------

_check_locks = {}
_check_locks_guard = threading.Lock()


def _check_lock(bulk_job_id: int) -> threading.Lock:
    with _check_locks_guard:
        return _check_locks.setdefault(bulk_job_id, threading.Lock())


def check_once(bulk_job_id: int, provider, engine=None, monthly_cap_usd: float | None = None,
               cancel_check=None) -> str:
    """One status check. Returns the job's new status. Applies results
    as soon as the provider reports the batch ended. Raises BulkAuthError
    (after recording it on the job) if the key was refused. Serialized per
    job, so the background poller and a "Check now" click can't both
    apply (and log the cost of) the same results.

    engine, monthly_cap_usd: only needed for a Reflect job (kind="reflect")
    -- once its results are in it submits the pipeline's next stage, which
    needs a live engine and is refused once the month's cap is used up.
    cancel_check: the polling job's Cancel, so a Cancel during the apply
    stops that next stage."""
    with _check_lock(bulk_job_id):
        return _check_once_locked(bulk_job_id, provider, engine, monthly_cap_usd, cancel_check)


def _check_once_locked(bulk_job_id: int, provider, engine=None, monthly_cap_usd=None,
                       cancel_check=None) -> str:
    job = db.get_bulk_job(bulk_job_id)
    if job is not None and job["status"] == "needs_resubmit":
        if not db.set_bulk_job_status_if(bulk_job_id, ("needs_resubmit",), "applying"):
            return require_bulk_job(bulk_job_id)["status"]
        try:
            return _advance_pipeline(job, engine, monthly_cap_usd, job.get("result_summary") or {},
                                     cancel_check)
        except Exception:
            db.set_bulk_job_status_if(bulk_job_id, ("applying",), "needs_resubmit")
            raise
    if job is None or job["status"] not in ("submitted", "auth_error"):
        return job["status"] if job else "missing"
    try:
        state = provider.poll(job["provider_batch_id"])
        if state == "pending":
            if job["status"] != "submitted":
                if not db.set_bulk_job_status_if(bulk_job_id, ("auth_error",), "submitted"):
                    return require_bulk_job(bulk_job_id)["status"]
                db.update_bulk_job(bulk_job_id, last_error=None)
            return "submitted"
        failure = state.startswith("failed") and f"The provider reported the batch {state[7:] or 'failed'}."
        results = [] if failure else list(provider.results(job["provider_batch_id"]))
    except BulkAuthError as e:
        if db.set_bulk_job_status_if(bulk_job_id, ("submitted", "auth_error"), "auth_error"):
            db.update_bulk_job(bulk_job_id, last_error=str(e))
        raise
    # Claimed before anything is applied: applying logs paid usage and can
    # submit the next paid Reflect stage, so a Cancel clicked while results
    # were downloading wins, and an apply that fails part-way is never
    # retried (a retry would bill and submit everything again).
    if not db.set_bulk_job_status_if(bulk_job_id, ("submitted", "auth_error"), "applying"):
        return require_bulk_job(bulk_job_id)["status"]
    try:
        if failure:
            return _fail_keeping_drafts(job, failure)
        summary = apply_results_for_job(bulk_job_id, results)
        if not set(_lines_per_request(bulk_job_id)) & {r[0] for r in results}:
            return _fail_keeping_drafts(job, "The provider returned no results for this batch.",
                                        summary)
        if job.get("kind") == "reflect" and job.get("stage") in ("faithful", "reflect"):
            return _advance_pipeline(job, engine, monthly_cap_usd, summary, cancel_check)
        if not db.set_bulk_job_status_if(bulk_job_id, ("applying",), "applied"):
            # Cancelled during the apply: it stays cancelled, but what landed is recorded.
            db.update_bulk_job(bulk_job_id, result_summary=summary)
            return require_bulk_job(bulk_job_id)["status"]
        db.update_bulk_job(bulk_job_id, result_summary=summary, last_error=None)
        return "applied"
    except Exception as e:
        if not db.set_bulk_job_status_if(bulk_job_id, ("applying",), "failed"):
            return require_bulk_job(bulk_job_id)["status"]
        db.update_bulk_job(bulk_job_id, last_error=shared.redact_secrets(
            f"Applying the results failed part-way, so they won't be applied again: {e}"))
        return "failed"


def run_scheduled_job(bulk_job_id: int, engine, cost_cap_usd: float | None = None,
                      cancel_check_cb=None, window_open=None,
                      monthly_cap_usd: float | None = None) -> dict:
    """DeepSeek off-peak: translates the scheduled lines that still exist
    and whose English hasn't changed since scheduling, as a normal run,
    and finishes it as one (finish_translation_run). Stops at the next
    batch once cancel_check_cb() is true or window_open() is false."""
    gate = WindowGate(window_open)
    job = require_bulk_job(bulk_job_id)
    args = job.get("translate_args") or {}
    did = job["drama_id"]
    rows = {r["line_id"]: r for r in db.list_bulk_job_lines(bulk_job_id)}
    lines = db.load_line_objects(did)
    eligible = {ln.id for ln in lines
                if ln.id in rows and (ln.en or "") == (rows[ln.id]["en_at_submit"] or "")}
    drama = db.get_drama(did) or {}
    terms = args.get("glossary_terms")
    options = {"batch_size": args.get("batch_size"),
               "context_window_ahead": args.get("context_window_ahead", 3),
               "scene_aware_batches": bool(args.get("scene_aware_batches"))}
    from services import line_provenance_service
    provenance = line_provenance_service.translate_run_tracker(
        did, lines, engine, job["engine"], terms, thinking=args.get("thinking"),
        locale=args.get("locale", "en-US"), style_preset=args.get("style_preset", ""), reflect=False,
        context_window=args.get("context_window", 6), style_note=args.get("style_note", ""),
        style_guidelines=args.get("style_guidelines", ""), **options)

    save, _notes, kept = own_lines_callbacks(did, lines, provenance)
    cap = {}
    _, errors = translate_pipeline.translate_lines_with_engine(
        lines, engine, drama_meta=drama, style_note=args.get("style_note", ""),
        novel_reference=args.get("novel_reference"), force_retranslate=True,
        target_ids=eligible, locale=args.get("locale", "en-US"),
        glossary_terms=terms, style_guidelines=args.get("style_guidelines", ""),
        context_window=args.get("context_window", 6), character_names=_character_names(did),
        save_cb=save, cancel_check_cb=cancel_check_cb, stop_before_batch=gate, **options,
        check_replies=args.get("check_replies"),
        usage_cb=lambda inp, out, cache_read=0, cache_write=0: db.log_usage(
            did, job["engine"], getattr(engine, "model", job["model"]), "translate_offpeak",
            inp, out, pricing.estimate_cost_for_engine(engine, inp, out, cache_read, cache_write),
            cache_read_tokens=cache_read),
        thinking=args.get("thinking"),
        cost_cap_usd=cost_cap_usd, cap_cb=lambda spent: cap.update(spent=spent),
        # An unattended run is the one nobody can stop, so the logged month
        # spend (other runs included) is re-read before each batch.
        month_cap_spent_cb=((lambda: db.get_month_spend() >= monthly_cap_usd)
                            if monthly_cap_usd else None))
    finish_translation_run(did, lines, engine, job["engine"], args.get("style_preset", ""), terms,
                           errors, cancelled=bool(cancel_check_cb and cancel_check_cb()) or gate.closed, enforce_ids={ln.id for ln in lines})
    return {"translated": len(eligible), "skipped_changed": len(rows) - len(eligible),
            "kept_your_edit": len(kept), "batch_errors": len(errors), "cap_reached": cap.get("spent"),
            "window_closed": gate.closed}


def run_bulk_poller(job_id: str, bulk_job_id: int, provider=None, engine=None,
                    interval: float = POLL_INTERVAL_SECONDS, sleep=time.sleep, now_fn=None,
                    monthly_cap_usd: float | None = None):
    """Background-thread target: polls a submitted batch until it's
    applied, or waits for a scheduled DeepSeek job's off-peak window and
    runs it. Stops on cancel, on a terminal status, or on an auth error
    (recorded on the job for the Bulk jobs panel)."""
    now_fn = now_fn or _utcnow
    guard = PollGuard()

    def wait(seconds):
        waited = 0.0
        while waited < seconds:
            if background_jobs.is_cancel_requested(job_id):
                return False
            step = min(1.0, seconds - waited)
            sleep(step)
            waited += step
        return True

    while not background_jobs.is_cancel_requested(job_id):
        job = db.get_bulk_job(bulk_job_id)
        if job is None or job["status"] not in db.BULK_PENDING_STATUSES:
            return
        now = now_fn()
        if job["status"] == "scheduled":
            start = datetime.datetime.fromisoformat(job["scheduled_for"])
            if now < start or not is_deepseek_offpeak(now):
                target = max(start, next_deepseek_offpeak_start(now))
                background_jobs.update_progress(job_id, 0.0, f"Waiting for the off-peak window "
                                                             f"({target:%Y-%m-%d %H:%M} UTC)")
                if not wait(max(1.0, min(interval, (target - now).total_seconds()))):
                    return
                continue
            if not db.set_bulk_job_status_if(bulk_job_id, ("scheduled",), "running"):
                return
            cap, refusal = offpeak_cap(job, monthly_cap_usd)
            if refusal:
                if db.set_bulk_job_status_if(bulk_job_id, ("running",), "failed"):
                    db.update_bulk_job(bulk_job_id, last_error=refusal)
                return
            try:
                summary = run_scheduled_job(bulk_job_id, engine, cost_cap_usd=cap,
                                            monthly_cap_usd=monthly_cap_usd,
                                            cancel_check_cb=lambda: background_jobs.is_cancel_requested(job_id),
                                            window_open=lambda: is_deepseek_offpeak(now_fn()))
            except Exception as e:
                if db.set_bulk_job_status_if(bulk_job_id, ("running",), "failed"):
                    db.update_bulk_job(bulk_job_id, last_error=shared.redact_secrets(str(e)))
                return
            if summary.pop("window_closed"):
                reschedule_for_next_window(bulk_job_id, now_fn())
                continue
            # A run stopped by Cancel stays cancelled, never "applied".
            db.set_bulk_job_status_if(bulk_job_id, ("running",), "cancelled"
                                      if background_jobs.is_cancel_requested(job_id) else "applied")
            db.update_bulk_job(bulk_job_id, result_summary=summary)
            background_jobs.set_result(job_id, summary)
            return
        try:
            status = check_once(bulk_job_id, provider, engine=engine, monthly_cap_usd=monthly_cap_usd,
                                cancel_check=lambda: background_jobs.is_cancel_requested(job_id))
            guard.reached()
            if status == "submitted" and PollGuard.expired(bulk_job_id, job, now):
                return
        except BulkAuthError:
            return
        except Exception as e:
            # Transient (network, 5xx): note it and keep polling.
            db.update_bulk_job(bulk_job_id, last_error=shared.redact_secrets(str(e)))
            status = "submitted"
            if why := guard.failed(bulk_job_id):
                background_jobs.set_result(job_id, {"stopped": why})
                return
        if status not in ("submitted", "needs_resubmit") or (status == "needs_resubmit" and not engine):
            return
        background_jobs.update_progress(job_id, 0.0, "Waiting for the provider to finish the batch")
        if not wait(interval):
            return


def start_poller(bulk_job_id: int, provider=None, engine=None, monthly_cap_usd: float | None = None) -> bool:
    """Starts (or leaves running) the background poller for one job."""
    job_id = poll_job_id(bulk_job_id)
    return job_launch.start_job(job_id, run_bulk_poller, job_id, bulk_job_id,
                                     provider=provider, engine=engine,
                                     monthly_cap_usd=monthly_cap_usd,
                                     description=f"Bulk translation #{bulk_job_id}",
                                     line_fields=("en", "flag", "flag_note"))


# A resumed job can send new paid work, not only poll: an off-peak run
# submits its batch, and a Reflect run sends its next stage.
RESUMABLE_STATUSES = ("submitted", "scheduled", "running", "needs_resubmit", "applying")


def resumable_engines(drama_id: int) -> list:
    """(engine, model) of each bulk job `resume_pending` would restart, for
    the caller's paid-engine check."""
    return [(job["engine"], job["model"])
            for job in db.list_bulk_jobs(drama_id, statuses=RESUMABLE_STATUSES)]


def resume_pending(drama_id: int, engine_factory, monthly_cap_usd: float | None = None) -> dict:
    """After a restart the in-memory job tracker is empty, but pending
    bulk jobs are on disk: start a poller for each one that isn't already
    being polled. engine_factory(engine_choice, model) returns an engine
    built with the user's current key, or None if there's no key yet.
    Returns {bulk_job_id: "polling" | "needs_key" | "running"}."""
    out = {}
    for job in db.list_bulk_jobs(drama_id, statuses=RESUMABLE_STATUSES):
        if job["status"] == "applying":
            job["status"] = _recover_stranded_apply(job)
            if job["status"] != "needs_resubmit":
                continue
        if background_jobs.is_running(poll_job_id(job["id"])) or _polled_by_outer_job(job):
            out[job["id"]] = "running"
            continue
        if job["status"] == "running":
            # An off-peak run the app died in the middle of. Safe to run
            # again: lines it already translated no longer match their
            # English at scheduling time, so they're skipped.
            if not db.set_bulk_job_status_if(job["id"], ("running",), "scheduled"):
                continue
        engine = engine_factory(job["engine"], job["model"])
        if engine is None:
            out[job["id"]] = "needs_key"
            continue
        provider = make_provider(job["engine"], engine)
        start_poller(job["id"], provider=provider, engine=engine, monthly_cap_usd=monthly_cap_usd)
        out[job["id"]] = "polling"
    return out


def _recover_stranded_apply(job: dict) -> str:
    """A job left "applying" by a stopped app: some results may have landed
    and been billed, so they are never applied a second time. A Reflect
    faithful or reflection stage whose results were recorded but whose next
    stage was never sent holds its paid drafts only in bulk_job_lines, so it
    goes back to needs_resubmit: the next poll sends that stage once
    (_advance_pipeline checks for it), or writes the drafts once the attempts
    run out. Returns the job's status. Holds the job's check lock, so a
    poller can't re-enter applying in between."""
    lock = _check_lock(job["id"])
    if not lock.acquire(blocking=False):
        return require_bulk_job(job["id"])["status"]
    try:
        if _resumable_stage(job):
            if db.set_bulk_job_status_if(job["id"], ("applying",), "needs_resubmit"):
                db.update_bulk_job(job["id"], last_error=(
                    "The app stopped while applying these results; the next Reflect stage is "
                    "sent when the batch is resumed."))
        elif db.set_bulk_job_status_if(job["id"], ("applying",), "failed"):
            db.update_bulk_job(job["id"], last_error=(
                "The app stopped while applying these results; lines without a result can be "
                "run again."))
        return require_bulk_job(job["id"])["status"]
    finally:
        lock.release()


def _resumable_stage(job: dict) -> bool:
    if job.get("kind") != "reflect" or job.get("stage") not in ("faithful", "reflect"):
        return False
    nxt = "reflect" if job["stage"] == "faithful" else "expressive"
    if sibling_stage_job(job["pipeline_id"], nxt, skip_failed=True) is not None:
        return False
    # A faithful stage that stopped before its drafts were recorded has
    # nothing for the next stage; a reflection stage always has the drafts.
    return job["stage"] == "reflect" or any(
        r["result_text"] for r in db.list_bulk_job_lines(job["id"]))


STRANDED_APPLY_MIN_AGE_SECONDS = 600


def reconcile_stranded_applies(min_age_seconds: float = STRANDED_APPLY_MIN_AGE_SECONDS) -> None:
    """App start: recovers every job a stopped app left "applying" (see
    _recover_stranded_apply), whether or not pending batches are resumed.
    Sends nothing to a provider. The check lock is per process, so only rows
    unchanged for min_age_seconds are touched: a second app process starting
    must not take over an apply another process is still running."""
    cutoff = (_utcnow() - datetime.timedelta(seconds=min_age_seconds)).isoformat()
    for job in db.list_bulk_jobs(statuses=("applying",)):
        if (job.get("updated_at") or "") <= cutoff:
            _recover_stranded_apply(job)


# Every status a Cancel may end. "applying" is included so a Cancel that lands
# while results are being applied wins over the applier's own status write.
_CANCEL_FROM = ("submitting",) + db.BULK_PENDING_STATUSES + ("running", "applying")


def cancel_bulk_job(bulk_job_id: int, provider=None) -> str:
    """Stops polling and marks the job cancelled, so any result that
    still arrives is ignored. Cancels at the provider too when there's a
    batch id and a provider to ask; if that call fails the job is still
    cancelled locally. A job that already finished (applied, failed) keeps
    its status. Returns a message for the panel."""
    job = db.get_bulk_job(bulk_job_id)
    background_jobs.request_cancel(poll_job_id(bulk_job_id))
    if not db.set_bulk_job_status_if(bulk_job_id, _CANCEL_FROM, "cancelled"):
        return "This batch had already finished, so there was nothing to cancel."
    note = "Cancelled -- results that still arrive will be ignored."
    if job and job.get("provider_batch_id") and provider is not None:
        try:
            provider.cancel(job["provider_batch_id"])
            note = "Cancelled at the provider and here."
        except Exception as e:
            note = ("Stopped here, but the provider's own cancel call failed -- results that "
                    f"still arrive will be ignored. ({shared.redact_secrets(str(e))})")
    db.update_bulk_job(bulk_job_id, last_error=None)
    return note


def poller_outcome(job_id, bulk_job) -> dict:
    """What run_bulk_poller's result said about how the run ended (it gave
    up, the spending cap stopped it, batches failed), for the outer bulk
    job's final result that replaces it, so that one does not read as a
    plain success."""
    before = (background_jobs.get_status(job_id) or {}).get("result")
    before = before if isinstance(before, dict) else {}
    summary = bulk_job.get("result_summary")
    summary = summary if isinstance(summary, dict) else {}
    kept = {}
    if before.get("stopped"):
        kept["stopped"] = before["stopped"]
    # The final stage's own summary only: an earlier Reflect stage's flags
    # say nothing about how the last one ended.
    cap = summary.get("cap_reached")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        kept["cap_reached"] = cap
    # batch_errors: an off-peak run; failed/unknown_requests: a provider batch.
    if any(summary.get(k) for k in ("batch_errors", "failed_requests", "unknown_requests")):
        kept["partial"] = True
    return kept
