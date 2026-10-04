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

import json
import os
import re
import time
from typing import Optional

import db
from services import ownership_service
import diagnostics
import background_jobs
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


# A queued/running record whose owner has not heartbeated this long (see
# background_jobs.HEARTBEAT_INTERVAL, far shorter) is treated as owned by a
# dead process (records-only mirror, no resume).
STALE_JOB_SECONDS = background_jobs.STALE_JOB_SECONDS


# Result projection: only these keys of a job's set_result dict are ever
# stored in job_records.result_json or returned over HTTP. Scalars and lists
# of scalars only; strings go through redact_for_support (secrets stripped,
# absolute paths collapsed to ".../name") and URLs are replaced.
RESULT_ALLOWED_KEYS = (
    "failed_reason", "detail", "errors", "lines_replaced", "cap_reached",
    "fixed_count", "total_flagged", "existing_line_count", "line_count",
    "gpu_fallback", "device_notice", "device", "word_align_error", "forced_align_error",
    "coverage_warning",
    "asr_backend", "alignment_method", "diarize_started", "flagged_count",
    "tagged", "note_count", "partial", "char_count", "image_count",
    "status", "stage", "last_error", "line_id", "candidate_count",
    # Sources chapter import (S-4): int counts only, never text.
    "imported_count", "skipped_count", "failed_count",
    # lightnovel-crawler import (Step 115b): the EPUB's reading-order count.
    "epub_chapters",
    # Own-lines re-translate: line ids whose review flag wasn't saved because
    # the line's text, timing or flag changed while the job ran.
    "flags_needing_recheck",
    # Re-split long lines: counts, the timing mode and a one-line note.
    "split_lines", "lines_before", "aligned_lines", "cleared_translations",
    "speakers_reassigned", "timing", "note",
)
_MAX_STR = 500
_MAX_LIST = 20
_MAX_JSON = 8000
_URL_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def _safe_scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return redact_text(_URL_PATTERN.sub("[URL]", value))[:_MAX_STR]
    return None


def redact_text(text: str) -> str:
    """redact_for_support, but never raising: getpass.getuser() inside it
    can fail in some containers, so fall back to secret + path redaction."""
    try:
        return diagnostics.redact_for_support(text)
    except Exception:
        import translate_engines
        text = translate_engines.redact_secrets(text or "")
        return diagnostics.PATH_PATTERN.sub(lambda m: ".../" + m.group(1), text)


def _error_item_text(item) -> str:
    """A translate batch error ({"batch_index", "lines": [idx...], "error"})
    as "lines 3-4: <message>" (1-based), instead of str(dict)."""
    if not isinstance(item, dict):
        return str(item)
    message = item.get("error") or item.get("message") or "failed"
    lines = [i for i in (item.get("lines") or []) if isinstance(i, int) and not isinstance(i, bool)]
    if not lines:
        return str(message)
    lo, hi = min(lines) + 1, max(lines) + 1
    where = f"line {lo}" if lo == hi else f"lines {lo}-{hi}"
    return f"{where}: {message}"


_BULK_SKIP_KEYS = ("running", "no_key", "no_lines", "cap", "engine_changed")
_MAX_BULK_ERROR = 120


def _int_id(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _project_fallbacks(events):
    """A FallbackEngine's switch events ({"from", "to", "reason", "detail"})
    as [{"from", "to", "count"}]: engine names and how often each switch
    happened, never error text. Also accepts its own output (re-projection)."""
    counts = {}
    for ev in events if isinstance(events, (list, tuple)) else []:
        if not isinstance(ev, dict):
            continue
        pair = ((_safe_scalar(str(ev.get("from") or "")) or "")[:40],
                (_safe_scalar(str(ev.get("to") or "")) or "")[:40])
        n = _int_id(ev.get("count")) or 1
        counts[pair] = counts.get(pair, 0) + max(1, n)
    return [{"from": a, "to": b, "count": n} for (a, b), n in list(counts.items())[:_MAX_LIST]]


def _cap_bytes(text: str, limit: int) -> str:
    """text cut to at most `limit` UTF-8 bytes, never mid-character."""
    return text.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _json_len(value) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _trim_bulk(bulk) -> bool:
    """Halve bulk's longest id/error list; False when nothing is left to trim
    (counts and the cancelled flag always stay, so the outcome never changes)."""
    lists = [(bulk, k) for k in ("failed", "partial", "translated_ids") if len(bulk.get(k) or []) > 1]
    lists += [(v, "drama_ids") for v in (bulk.get("skipped") or {}).values()
              if len(v.get("drama_ids") or []) > 1]
    if not lists:
        for k in ("failed", "partial", "translated_ids"):
            if bulk.get(k):
                bulk[k] = []
                return True
        return False
    owner, key = max(lists, key=lambda ok: _json_len(ok[0][ok[1]]))
    owner[key] = owner[key][: len(owner[key]) // 2]
    return True


def _project_bulk(result):
    """Library bulk translate's per-drama outcome (translated / skipped_* /
    errors {drama_id: message}) as counts plus bounded id lists and short,
    redacted error text."""
    translated = [i for i in (_int_id(v) for v in result.get("translated") or []) if i is not None]
    skipped = {}
    for key in _BULK_SKIP_KEYS:
        ids = [i for i in (_int_id(v) for v in result.get(f"skipped_{key}") or []) if i is not None]
        if ids:
            skipped[key] = {"count": len(ids), "drama_ids": ids[:_MAX_LIST]}
    raw_errors = result.get("errors") if isinstance(result.get("errors"), dict) else {}
    failed = []
    for did, message in raw_errors.items():
        did = _int_id(did)
        if did is None:
            continue
        text = _safe_scalar(str(message or "failed")) or "failed"
        failed.append({"drama_id": did, "error": _cap_bytes(text, _MAX_BULK_ERROR)})
    raw_partial = result.get("partial") if isinstance(result.get("partial"), dict) else {}
    partial = []
    for did, reason in raw_partial.items():
        did = _int_id(did)
        if did is not None:
            partial.append({"drama_id": did,
                            "reason": _cap_bytes(_safe_scalar(str(reason or "")) or "", 40)})
    return {"cancelled": bool(result.get("cancelled")),
            "partial_count": len(partial), "partial": partial[:_MAX_LIST],
            "translated_count": len(translated), "translated_ids": translated[:_MAX_LIST],
            "skipped": skipped, "skipped_count": sum(v["count"] for v in skipped.values()),
            "failed_count": len(failed), "failed": failed[:_MAX_LIST]}


def _reproject_bulk(bulk):
    """A stored bulk projection, re-sanitised through _project_bulk (the
    counts may exceed the capped id lists, so they are carried over)."""
    raw = {"translated": bulk.get("translated_ids") or [],
           "cancelled": bulk.get("cancelled"),
           "partial": {f.get("drama_id"): f.get("reason") for f in bulk.get("partial") or []
                       if isinstance(f, dict)},
           "errors": {f.get("drama_id"): f.get("error") for f in bulk.get("failed") or []
                      if isinstance(f, dict)}}
    stored_skipped = bulk.get("skipped") if isinstance(bulk.get("skipped"), dict) else {}
    for key in _BULK_SKIP_KEYS:
        entry = stored_skipped.get(key)
        if isinstance(entry, dict):
            raw[f"skipped_{key}"] = entry.get("drama_ids") or []
    out = _project_bulk(raw)
    for key in ("translated_count", "failed_count", "partial_count"):
        n = _int_id(bulk.get(key))
        if n is not None and n > out[key]:
            out[key] = n
    for key, entry in out["skipped"].items():
        n = _int_id(stored_skipped[key].get("count"))
        if n is not None and n > entry["count"]:
            entry["count"] = n
    out["skipped_count"] = sum(v["count"] for v in out["skipped"].values())
    return out


def _is_bulk_result(result) -> bool:
    return isinstance(result, dict) and "translated" in result and "skipped_running" in result


def project_result(result):
    """A JSON-safe, redacted, size-capped view of a job's result dict, or
    None when there is nothing to show (no result, or a non-dict result
    such as live-translate's cue list)."""
    if not isinstance(result, dict):
        return None
    out = {}
    if _is_bulk_result(result):
        out["bulk"] = _project_bulk(result)
    elif isinstance(result.get("bulk"), dict):
        out["bulk"] = _reproject_bulk(result["bulk"])  # stored row, on read
    if result.get("fallbacks"):
        out["fallbacks"] = _project_fallbacks(result["fallbacks"])
    for key in RESULT_ALLOWED_KEYS:
        if key not in result or (key == "errors" and "bulk" in out):
            continue
        value = result[key]
        if isinstance(value, (list, tuple)):
            items = (_safe_scalar(v if isinstance(v, (str, int, float, bool)) else _error_item_text(v))
                     for v in list(value)[:_MAX_LIST])
            out[key] = [v for v in items if v is not None]
        else:
            safe = _safe_scalar(value)
            if safe is not None or value is None:
                out[key] = safe
    while out and _json_len(out) > _MAX_JSON:
        # bulk is trimmed, never dropped: without it the outcome reads "ok".
        if "bulk" in out and _json_len(out["bulk"]) * 2 > _MAX_JSON and _trim_bulk(out["bulk"]):
            continue
        others = [k for k in out if k != "bulk"]
        if not others:
            if not _trim_bulk(out["bulk"]):
                break
            continue
        biggest = max(others, key=lambda k: _json_len(out[k]))
        if isinstance(out[biggest], list) and len(out[biggest]) > 1:
            out[biggest] = out[biggest][: len(out[biggest]) // 2]
        else:
            del out[biggest]
    return out


def project_result_json(result):
    """project_result, JSON-encoded for db.job_records.result_json."""
    projected = project_result(result)
    return json.dumps(projected) if projected is not None else None


_FAILED_REASON_MESSAGES = {
    "model_download": "The speech model could not be downloaded.",
    "empty": "Nothing was produced: no speech or text was found.",
    "dependency_missing": "A required component is not installed.",
    "qwen3_asr": "Qwen3-ASR failed on this audio.",
    "moss_td": "MOSS-Transcribe-Diarize (experimental) failed on this audio.",
    "groq": "The Groq transcription request failed.",
    "vocal_separation": "Separating the vocals failed.",
}


def derive_outcome(status, error, result):
    """(outcome, outcome_message) for a finished job, normalised from its
    status and projected result so a client never has to know each job's
    own result shape; (None, None) while queued/running. outcome is one of
    ok, failed, cancelled, partial, kept_existing. Transcribe warnings
    (gpu_fallback, word_align_error, forced_align_error) map to partial."""
    if status == "error":
        return "failed", (error or "The job failed.")
    if status == "cancelled":
        # An error on a cancelled record says why (INTERRUPTED_MESSAGE).
        return "cancelled", (error or "The job was cancelled.")
    if status != "done":
        return None, None
    result = result if isinstance(result, dict) else {}
    reason = result.get("failed_reason")
    detail = result.get("detail")
    if reason == "cancelled":
        return "cancelled", "The job was cancelled before it finished."
    if reason == "empty_kept_existing":
        count = result.get("existing_line_count")
        return "kept_existing", (f"Nothing new was produced, so the existing {count} line(s) were kept."
                                 if count is not None else
                                 "Nothing new was produced, so the existing lines were kept.")
    if reason:
        msg = _FAILED_REASON_MESSAGES.get(reason, "The job did not finish.")
        return "failed", (f"{msg} {detail}" if detail else msg)
    if result.get("status") in ("failed", "auth_error"):
        return "failed", (result.get("last_error") or "The job failed.")
    if result.get("status") == "cancelled":
        return "cancelled", "The bulk translation was cancelled."
    bulk = result.get("bulk")
    if isinstance(bulk, dict):
        done = bulk.get("translated_count") or 0
        failed = bulk.get("failed_count") or 0
        skipped = bulk.get("skipped_count") or 0
        unfinished = bulk.get("partial_count") or 0
        parts = [f"Translated {done} drama(s)."]
        if unfinished:
            parts.append(f"{unfinished} only partly translated.")
        if failed:
            parts.append(f"{failed} failed.")
        if skipped:
            parts.append(f"{skipped} skipped.")
        if bulk.get("cancelled"):
            parts.insert(0, "The bulk translation was cancelled.")
            return ("partial" if done or unfinished else "cancelled"), " ".join(parts)
        if not failed and not skipped and not unfinished:
            return "ok", " ".join(parts)
        if unfinished and not done:
            return "partial", " ".join(parts)
        return ("partial" if done else "failed"), " ".join(parts)
    errors = result.get("errors") or []
    cap = result.get("cap_reached")
    parts = []
    if "fixed_count" in result:
        parts.append(f"Fixed {result.get('fixed_count') or 0} of "
                     f"{result.get('total_flagged') or 0} flagged line(s).")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        parts.append(f"Stopped at the spending cap after about ${cap:.2f}; finished work was kept.")
    if errors:
        parts.append(f"{len(errors)} problem(s), first: {errors[0]}")
    if result.get("partial"):
        parts.append("Only part of the work finished.")
    # Transcribe warnings (Streamlit warned on these): the job worked, but
    # not the way the user asked, so it is reported as partial, not ok.
    warned = False
    if result.get("device_notice"):
        parts.append(str(result["device_notice"]))
        warned = True
    elif result.get("gpu_fallback"):
        parts.append(f"Ran on CPU because the GPU wasn't available ({result['gpu_fallback']}).")
        warned = True
    if result.get("word_align_error"):
        parts.append("Splitting long lines by word timing failed; the original timings were kept "
                     f"({result['word_align_error']}).")
        warned = True
    if result.get("forced_align_error"):
        parts.append("Qwen3 forced alignment failed; timings use the fallback alignment "
                     f"({result['forced_align_error']}).")
        warned = True
    if result.get("coverage_warning"):
        parts.append(str(result["coverage_warning"]))
        warned = True
    if result.get("flags_needing_recheck"):
        parts.append("Some lines changed while the job ran, so their review flags "
                     "weren't saved; recheck them.")
        warned = True
    fallbacks = [f for f in result.get("fallbacks") or [] if isinstance(f, dict)]
    if fallbacks:
        parts.append("Switched engine: " + ", ".join(
            f"{f.get('from')} to {f.get('to')}" for f in fallbacks) + ".")
    if cap is not None or errors or result.get("partial") or warned:
        return "partial", " ".join(parts)
    return "ok", (" ".join(parts) or "Finished.")


def _with_live_progress(record: dict) -> dict:
    """job_records is written on status changes only, so while a job this
    process runs is running, its progress and message come from
    background_jobs' in-memory state (what update_progress set), so polls
    and the event stream both see the bar move."""
    if record.get("status") != "running":
        return record
    try:
        live = background_jobs.get_status(record.get("job_id"))
    except Exception:
        return record
    if not live or live.get("status") != "running":
        return record
    out = dict(record)
    progress = live.get("progress")
    if isinstance(progress, (int, float)) and not isinstance(progress, bool):
        out["progress"] = progress
    if live.get("message"):
        out["message"] = live.get("message")
    if background_jobs.job_may_be_stalled(live):
        out["stalled"] = True
        out["message"] = ((out.get("message") or "").rstrip()
                          + " No update for a while: this job may be stalled.")
    return out


def _redact(record: dict) -> dict:
    """Same redaction diagnostics_service._job_summary already applies --
    a stored error/message could echo an API error verbatim."""
    record = _with_live_progress(record)
    out = dict(record)
    raw = out.pop("result_json", None)
    out.pop("owner_pid", None)
    try:
        stored = json.loads(raw) if raw else None
    except ValueError:
        stored = None
    # Re-projected on read too, so an older row can never leak a dropped key.
    out["result"] = project_result(stored)
    out["message"] = redact_text(record.get("message") or "")
    out["error"] = redact_text(record.get("error") or "") or None
    out["gpu_touching"] = bool(record.get("gpu_touching"))
    outcome, message = derive_outcome(out.get("status"), out["error"], out["result"])
    out["outcome"] = outcome
    out["outcome_message"] = (redact_text(message)[:_MAX_STR]
                              if message else None)
    out["stale"] = is_stale(record)
    out["stalled"] = bool(record.get("stalled"))
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
    if _owner_gone(record):
        return True
    updated = record.get("updated_at") or 0
    return (time.time() if now is None else now) - updated > STALE_JOB_SECONDS


def _owner_gone(record: dict) -> bool:
    """True when the row names an owner process that no longer runs it: an
    exited pid, or this process's own pid with no such job live here (an
    earlier run of the server had the same pid). Rows written before
    owner_pid existed fall back to the heartbeat cutoff. Callers have
    already checked the job isn't live in this process."""
    pid = record.get("owner_pid")
    if pid is None:
        return False
    if pid == os.getpid():
        return True
    return not background_jobs.owner_process_alive(pid)


def _close_if_owner_gone(record: dict) -> bool:
    return _owner_gone(record) and db.close_orphaned_job_record(
        record.get("job_id"), record.get("owner_pid"), error=background_jobs.INTERRUPTED_MESSAGE)


def _visible(principal, record) -> bool:
    return ownership_service.can_see_job(principal, record.get("job_id"),
                                         record.get("owner_user_id"))


def _for_caller(principal, record) -> dict:
    """_redact plus `owned_by_me` (ownership_service.owns_job), the only
    word on who owns the job a response carries: never an owner id."""
    out = _redact(record)
    out["owned_by_me"] = ownership_service.owns_job(principal, record.get("job_id"),
                                                    record.get("owner_user_id"))
    return out


def sweep_stale_job_records() -> int:
    """Closes (as cancelled, with INTERRUPTED_MESSAGE) every queued/running
    job_records row that is not live in this process and whose owner
    process has exited (owner_pid) or has not heartbeated for
    STALE_JOB_SECONDS -- left behind by a crashed, killed or restarted
    process. Each close is one conditional UPDATE, so a live owner's
    heartbeat, "done" or new run always wins. Also marks this process's own
    running jobs whose worker thread is gone
    (background_jobs.reconcile_dead_workers). Returns how many it closed."""
    background_jobs.reconcile_dead_workers()
    cutoff = time.time() - STALE_JOB_SECONDS
    closed = 0
    for rec in db.list_job_records():
        job_id = rec.get("job_id")
        if rec.get("status") not in ("queued", "running") or background_jobs.get_status(job_id):
            continue
        if _close_if_owner_gone(rec) or (
                (rec.get("updated_at") or 0) < cutoff
                and db.close_stale_job_record(job_id, cutoff,
                                              error=background_jobs.INTERRUPTED_MESSAGE)):
            closed += 1
    return closed


def list_jobs(principal=None) -> list:
    """Every job_records row `principal` may see (auth B2:
    ownership_service.can_see_job; None = auth off, all), newest-started
    first, redacted for HTTP. Stale rows of dead owners are closed first
    (sweep_stale_job_records), so they don't list as running forever."""
    sweep_stale_job_records()
    return [_for_caller(principal, r) for r in db.list_job_records() if _visible(principal, r)]


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
    """Step 41 item 5: the job's per-stage timing and spend for its latest
    runs (services/job_timing_service). Same visibility as get_job."""
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    from services import job_timing_service
    runs = job_timing_service.list_runs(job_id)
    if not ownership_service.sees_every_job(principal):
        # A shared job id (sources_search, bulk_series_translate...) keeps
        # earlier runs by other users: show only the run the caller can see.
        started = record.get("started_at") or 0
        runs = [r for r in runs if r["run_started_at"] >= started]
    return {"job_id": job_id, "runs": runs}


def cancel_job(job_id: str, principal=None) -> dict:
    """Requests cancellation of a queued/running job, possibly owned by
    another process. In-process jobs get the normal cancel flag at once;
    the job_records flag is set either way so the owning process notices
    it (throttled check in background_jobs). Unknown id -> NotFoundError;
    already finished -> ConflictError (409). Cancellation is
    asynchronous: the returned status is the record's current one. A job
    `principal` may not see is a 404 (visibility, not starter-only: anyone
    who can see a drama may cancel its jobs, as before); one an admin sees
    only through the admin view (away from the PC) is a 403."""
    record = db.get_job_record(job_id)
    if record is None or not _visible(principal, record):
        raise NotFoundError(f"No job with id {job_id!r}.")
    ownership_service.require_job_changeable(principal, record.get("job_id"),
                                             record.get("owner_user_id"))
    if record.get("status") not in ("queued", "running"):
        raise ConflictError(f"Job {job_id!r} already finished ({record.get('status')}).")
    background_jobs.request_cancel(job_id)
    db.request_job_record_cancel(job_id)
    live = background_jobs.get_status(job_id)
    if live is None and (_close_if_owner_gone(record) or db.close_stale_job_record(
            job_id, time.time() - STALE_JOB_SECONDS, error=background_jobs.INTERRUPTED_MESSAGE)):
        # The owner process has exited, or sent no heartbeat for
        # STALE_JOB_SECONDS: nobody will read the flag. Each close is
        # conditional, so a live owner's heartbeat, "done" or new run wins.
        return {"job_id": job_id, "cancel_requested": True, "status": "cancelled"}
    # A queued job here ends at once (request_cancel); a running one says
    # "Cancelling..." until its worker stops.
    status = live["status"] if live else record["status"]
    return {"job_id": job_id, "cancel_requested": True, "status": status}


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
    background_jobs.clear_job(job_id)
    return {"job_id": job_id, "deleted": True}


def clear_finished_jobs(confirm: bool = False) -> dict:
    """Permanently removes every finished job's record, leaving queued and
    running jobs (including ones another process owns) untouched."""
    if confirm is not True:
        raise InvalidInputError("Deleting job history needs confirm=true.")
    deleted = 0
    for rec in db.list_job_records():
        if _is_finished(rec):
            background_jobs.clear_job(rec["job_id"])
            deleted += 1
    return {"deleted_count": deleted}
