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
import re
import time
from typing import Optional

import db
from services import job_stage_service, ownership_service, run_settings_service
import diagnostics_report
import background_jobs
from jobs import gpu_queue, job_table
import job_force_stop
from jobs import job_store
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


# A queued/running record whose owner has not heartbeated this long (see
# job_table.HEARTBEAT_INTERVAL, far shorter) is treated as owned by a
# dead process (records-only mirror, no resume).
STALE_JOB_SECONDS = job_table.STALE_JOB_SECONDS


# Result projection: only these keys of a job's set_result dict are ever
# stored in job_records.result_json or returned over HTTP. Scalars and lists
# of scalars only; strings go through redact_for_support (secrets stripped,
# absolute paths collapsed to ".../name") and URLs are replaced.
RESULT_ALLOWED_KEYS = (
    "failed_reason", "detail", "errors", "lines_replaced", "cap_reached",
    "fixed_count", "total_flagged", "timing_skipped_count", "existing_line_count", "line_count",
    "gpu_fallback", "device_notice", "device", "word_align_error", "forced_align_error",
    "coverage_warning", "ollama_notice", "speaker_count_hint", "stage_summary", "diarize_skipped_reason",
    "asr_backend", "alignment_method", "diarize_started", "flagged_count",
    "tagged", "note_count", "partial", "char_count", "image_count",
    "status", "stage", "last_error", "line_id", "candidate_count",
    # Review timing check: counts and the no-speech notice.
    "checked", "flagged", "cleared", "skipped_flagged", "notice",
    # Sources chapter import (S-4): int counts only, never text.
    "imported_count", "skipped_count", "failed_count",
    # lightnovel-crawler import: the EPUB's reading-order count.
    "epub_chapters",
    # Own-lines re-translate: line ids whose review flag wasn't saved because
    # the line's text, timing or flag changed while the job ran.
    "flags_needing_recheck",
    # Fix flagged: line ids it left alone because the user edited them meanwhile.
    "kept_your_edit",
    # Re-split long lines: counts, the timing mode and a one-line note.
    "split_lines", "lines_before", "aligned_lines", "cleared_translations",
    "speakers_reassigned", "timing", "note",
    # A paid job a money rule ended early: the plain reason ("Stopped: ...").
    "stopped",
)
_MAX_STR = 500
_MAX_LIST = 20
_MAX_JSON = 8000
URL_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def scrub_text(text: str) -> str:
    # URLs first: a fetched URL can carry a token or a private host that the
    # path and key redaction wouldn't recognise.
    return redact_text(URL_PATTERN.sub("[URL]", text or ""))


def _safe_scalar(value):
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return scrub_text(value)[:_MAX_STR]
    return None


def redact_text(text: str) -> str:
    """redact_for_support, but never raising: getpass.getuser() inside it
    can fail in some containers, so fall back to secret + path redaction."""
    try:
        return diagnostics_report.redact_for_support(text)
    except Exception:
        import translate_engines
        text = translate_engines.redact_secrets(text or "")
        return diagnostics_report.PATH_PATTERN.sub(lambda m: ".../" + m.group(1), text)


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
    out = {"cancelled": bool(result.get("cancelled")),
           "partial_count": len(partial), "partial": partial[:_MAX_LIST],
           "translated_count": len(translated), "translated_ids": translated[:_MAX_LIST],
           "skipped": skipped, "skipped_count": sum(v["count"] for v in skipped.values()),
           "failed_count": len(failed), "failed": failed[:_MAX_LIST]}
    # Why the run gave up before its last title (FailureStreak), so the
    # titles it never tried are not passed over in silence.
    stopped = _safe_scalar(str(result.get("stopped") or ""))
    if stopped:
        out["stopped"] = _cap_bytes(stopped, _MAX_BULK_ERROR)
    return out


def _reproject_bulk(bulk):
    """A stored bulk projection, re-sanitised through _project_bulk (the
    counts may exceed the capped id lists, so they are carried over)."""
    raw = {"translated": bulk.get("translated_ids") or [],
           "cancelled": bulk.get("cancelled"), "stopped": bulk.get("stopped"),
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


# The stages a transcription run records (transcribe_pipeline); anything else is dropped.
_TRANSCRIBE_STAGE_KEYS = ("separate", "load", "decode_vad", "transcribe", "align")


def _project_stage_seconds(raw) -> dict:
    """The recorded seconds of a transcription run's known stages, nothing else."""
    if not isinstance(raw, dict):
        return {}
    return {k: round(float(raw[k]), 2) for k in _TRANSCRIBE_STAGE_KEYS
            if isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool)
            and raw[k] >= 0 and raw[k] == raw[k]}


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
    run_settings = run_settings_service.sanitise(result.get("run_settings"))
    if run_settings:
        out["run_settings"] = run_settings
    stage_seconds = _project_stage_seconds(result.get("stage_seconds"))
    if stage_seconds:
        out["stage_seconds"] = stage_seconds
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


def project_result_json(result, run_settings=None):
    """project_result, JSON-encoded for db.job_records.result_json. The
    job's run settings ride along even when the result isn't a dict
    (live-translate's cue list)."""
    settings = run_settings_service.sanitise(run_settings)
    if settings:
        result = {**(result if isinstance(result, dict) else {}), "run_settings": settings}
    projected = project_result(result)
    return json.dumps(projected) if projected is not None else None


FAILED_REASON_MESSAGES = {
    "model_download": "The speech model could not be downloaded.",
    "empty": "Nothing was produced: no speech or text was found.",
    "dependency_missing": "A required component is not installed.",
    "qwen3_asr": "Qwen3-ASR failed on this audio.",
    "anime_whisper": "Anime-whisper failed on this audio.",
    "groq": "The Groq transcription request failed.",
    "vocal_separation": "Separating the vocals failed.",
}


_MODEL_DOWNLOAD_BY_KIND = (
    (("transcribe_",), "The speech model could not be downloaded."),
    (("retime_", "resegment_", "resplit_", "resegpreview_"), "The aligner model could not be downloaded."),
)


def model_download_message(job_id: str = "") -> str:
    """The failed-download sentence for a job kind; the detail that follows names the model."""
    for prefixes, text in _MODEL_DOWNLOAD_BY_KIND:
        if (job_id or "").startswith(prefixes):
            return text
    return "A model could not be downloaded."


NO_LINES_MESSAGE = (
    "Finished but found no lines. Possible reasons: voice detection removed everything "
    "(try Sensitivity: More sensitive); the model returned nothing (try Whisper large-v3-turbo); "
    "or the audio is silent.")


NO_TEXT_MESSAGE = (
    "Finished but found no on-screen text. Possible reasons: wrong OCR language or interval, "
    "no burned-in subtitles in this video.")


def speaker_skip_text(result):
    """"Speaker detection did not run: <reason>." when the switch was on and nothing started."""
    why = result.get("diarize_skipped_reason") if isinstance(result, dict) else None
    return f"Speaker detection did not run: {why}." if why else None


def derive_outcome(status, error, result, detail_state=None, job_id=None):
    """(outcome, outcome_message) for a finished job, normalised from its
    status and projected result so a client never has to know each job's
    own result shape; (None, None) while queued/running. outcome is one of
    ok, failed, cancelled, interrupted, partial, kept_existing. Transcribe
    warnings (gpu_fallback, word_align_error, forced_align_error) map to
    partial. detail_state: the job_records column; "interrupted" marks a
    row closed because its server stopped (jobs/job_store._close), which
    is stored as cancelled but nobody cancelled it. job_id: tells a bulk
    translation from the bulk review passes that share its result shape."""
    if status == "error":
        return "failed", (error or "The job failed.")
    if status == "cancelled" and detail_state == "interrupted":
        return "interrupted", (error or background_jobs.INTERRUPTED_MESSAGE)
    if status == "cancelled":
        return "cancelled", (error or "The job was cancelled.")
    if status != "done":
        return None, None
    result = result if isinstance(result, dict) else {}
    if result.get("failed_reason"):
        return _failed_reason_outcome(result)
    if result.get("status") in ("failed", "auth_error"):
        return "failed", (result.get("last_error") or "The job failed.")
    if result.get("status") == "cancelled":
        # bulk_flag_/bulk_consistency_ run the same batch job and result.
        return "cancelled", ("The bulk translation was cancelled."
                             if str(job_id or "").startswith("bulk_translate_") or job_id is None
                             else "The job was cancelled.")
    if isinstance(result.get("bulk"), dict):
        return _bulk_outcome(result["bulk"])
    return _summary_outcome(result)


def _failed_reason_outcome(result: dict) -> tuple:
    reason = result["failed_reason"]
    if reason == "cancelled":
        return "cancelled", "The job was cancelled before it finished."
    if reason == "empty_kept_existing":
        count = result.get("existing_line_count")
        return "kept_existing", (f"Nothing new was produced, so the existing {count} line(s) were kept."
                                 if count is not None else
                                 "Nothing new was produced, so the existing lines were kept.")
    if reason == "no_text":
        return "partial", NO_TEXT_MESSAGE
    if reason == "no_lines":
        skipped = speaker_skip_text(result)
        return "partial", f"{NO_LINES_MESSAGE} {skipped}" if skipped else NO_LINES_MESSAGE
    detail = result.get("detail")
    msg = FAILED_REASON_MESSAGES.get(reason, "The job did not finish.")
    msg = f"{msg} {detail}" if detail else msg
    if result.get("gpu_fallback"):
        # A CPU rerun after a GPU crash that then failed: the crash is the first cause.
        msg = f"{msg} Ran on CPU because the GPU wasn't available ({result['gpu_fallback']})."
    return "failed", msg


def _bulk_outcome(bulk: dict) -> tuple:
    done = bulk.get("translated_count") or 0
    failed = bulk.get("failed_count") or 0
    skipped = bulk.get("skipped_count") or 0
    unfinished = bulk.get("partial_count") or 0
    parts = [f"Translated {done} title(s)."]
    if unfinished:
        parts.append(f"{unfinished} only partly translated.")
    if failed:
        parts.append(f"{failed} failed.")
    if skipped:
        parts.append(f"{skipped} skipped.")
    if bulk.get("stopped"):
        parts.append(f"{str(bulk['stopped']).rstrip('.')}.")
    if bulk.get("cancelled"):
        parts.insert(0, "The bulk translation was cancelled.")
        return ("partial" if done or unfinished else "cancelled"), " ".join(parts)
    if not failed and not skipped and not unfinished:
        return "ok", " ".join(parts)
    if unfinished and not done:
        return "partial", " ".join(parts)
    return ("partial" if done else "failed"), " ".join(parts)


def _summary_outcome(result: dict) -> tuple:
    errors = result.get("errors") or []
    cap = result.get("cap_reached")
    parts = []
    if "fixed_count" in result:
        parts.append(f"Fixed {result.get('fixed_count') or 0} of "
                     f"{result.get('total_flagged') or 0} flagged line(s).")
    if result.get("timing_skipped_count"):
        n = result['timing_skipped_count']
        parts.append(f"{n} timing {'flag' if n == 1 else 'flags'} skipped "
                     "(fix them in the timing tools).")
    kept = result.get("kept_your_edit")
    if kept:
        # A count from the re-segment and off-peak batch jobs, a list of line ids from the others.
        count = kept if isinstance(kept, int) and not isinstance(kept, bool) else len(kept)
        parts.append(f"{count} line(s) edited while it ran kept your edit.")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool):
        parts.append(f"Stopped at the spending cap after about ${cap:.2f}; finished work was kept.")
    if errors:
        parts.append(f"{len(errors)} problem(s), first: {errors[0]}")
    stopped = result.get("stopped")
    if stopped:
        parts.append(f"{str(stopped).rstrip('.')}. Finished work was kept.")
    if result.get("partial"):
        parts.append("Only part of the work finished.")
    warnings = _warning_parts(result)
    parts.extend(warnings)
    if result.get("speaker_count_hint"):
        # A suggestion, not a failure, so it doesn't make the outcome partial.
        parts.append(str(result["speaker_count_hint"]))
    fallbacks = [f for f in result.get("fallbacks") or [] if isinstance(f, dict)]
    if fallbacks:
        parts.append("Switched engine: " + ", ".join(
            f"{f.get('from')} to {f.get('to')}" for f in fallbacks) + ".")
    if cap is not None or errors or stopped or result.get("partial") or warnings:
        return "partial", " ".join(parts)
    return "ok", (" ".join(parts) or "Finished.")


def _warning_parts(result: dict) -> list:
    """Transcribe warnings: the job worked, but not the way the user asked,
    so any of these makes the outcome partial, not ok."""
    parts = []
    if result.get("device_notice"):
        parts.append(str(result["device_notice"]))
    elif result.get("gpu_fallback"):
        parts.append(f"Ran on CPU because the GPU wasn't available ({result['gpu_fallback']}).")
    if result.get("word_align_error"):
        parts.append("Splitting long lines by word timing failed; the original timings were kept "
                     f"({result['word_align_error']}).")
    if result.get("forced_align_error"):
        parts.append("Qwen3 forced alignment failed; timings use the fallback alignment "
                     f"({result['forced_align_error']}).")
    if result.get("coverage_warning"):
        parts.append(str(result["coverage_warning"]))
    if result.get("ollama_notice"):
        parts.append(str(result["ollama_notice"]))
    skipped_speakers = speaker_skip_text(result)
    if skipped_speakers:
        parts.append(skipped_speakers)
    if result.get("flags_needing_recheck"):
        parts.append("Some lines changed while the job ran, so their review flags "
                     "weren't saved; recheck them.")
    return parts


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
    out["result"] = project_result(stored)
    out["message"] = scrub_text(record.get("message") or "")
    out["error"] = scrub_text(record.get("error") or "") or None
    out["gpu_touching"] = bool(record.get("gpu_touching"))
    outcome, message = derive_outcome(out.get("status"), out["error"], out["result"],
                                      record.get("detail_state"), job_id=record.get("job_id"))
    out["outcome"] = outcome
    out["outcome_message"] = (scrub_text(message)[:_MAX_STR]
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


# One entry per background_jobs.DRAMA_JOB_PREFIXES prefix (a test fails when
# the two drift apart). Ids with no entry, or not scoped to a drama, are "other".
JOB_KIND_BY_PREFIX = {
    "translate_": "translate", "bulk_translate_": "translate",
    "novel_glossary_": "translate", "lines_glossary_": "translate",
    "spelling_suggest_": "translate",
    "flag_": "review", "fixflag_": "review", "timingchk_": "review", "consistency_": "review",
    "emotion_": "review", "notes_": "review", "bulk_consistency_": "review",
    "bulk_emotion_": "review", "bulk_notes_": "review", "bulk_flag_": "review",
    "transcribe_": "transcribe", "retranscribe_": "transcribe",
    "autotune_": "transcribe", "sensevoice_": "transcribe", "comparetx_": "transcribe",
    "speechcov_": "transcribe",
    "diarize_": "transcribe", "ocrchapter_": "transcribe",
    "resegment_": "align", "resplit_": "align", "retime_": "align", "resegpreview_": "align",
    "dub_": "dub", "narration_": "dub", "audiobook_": "dub", "voiceref_": "dub",
    "burned_video_": "export", "softsub_video_": "export",
    "dubbed_video_": "export", "burnpreview_": "export",
    "sourceimport_": "import", "urlmedia_": "import", "lncrawl_": "import",
    "extract_audio_": "import",
    "scanlate_": "other", "restructure_": "other",
}


def job_kind(job_id) -> str:
    """The kind of a job id, from the same exact-prefix-plus-digits rule
    ownership_service.drama_id_of_job uses; "other" when it has no drama."""
    job_id = str(job_id or "")
    for prefix, kind in JOB_KIND_BY_PREFIX.items():
        if job_id.startswith(prefix) and ownership_service.drama_id_of_job(job_id) is not None:
            return kind
    return "other"


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
    job_kind), else the page its id names, else None."""
    job_id = str(job_id or "")
    if ownership_service.drama_id_of_job(job_id) is not None:
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
    out["drama_id"] = ownership_service.drama_id_of_job(record.get("job_id"))
    out["kind"] = job_kind(record.get("job_id"))
    out["page"] = job_page(record.get("job_id"))
    out["owned_by_me"] = ownership_service.owns_job(principal, record.get("job_id"),
                                                    record.get("owner_user_id"),
                                                    drama_rows=drama_rows)
    return out


def sweep_stale_job_records(records=None) -> int:
    """Closes every queued/running row left behind by a crashed, killed or
    restarted process (jobs/job_store.sweep_dead_owners). Returns how many."""
    return job_store.sweep_dead_owners(STALE_JOB_SECONDS, records=records)


def _still_active_here(job_id) -> bool:
    return (background_jobs.get_status(job_id) or {}).get("status") in job_store.ACTIVE


# GET /api/jobs is polled while the Jobs page is open. The sweep probes each
# active row's owner process; when the rows and this process's active jobs
# are unchanged since a sweep that closed nothing, the answer is the same, so
# the probes wait until something changes or this many seconds pass.
_SWEEP_REPEAT_SECONDS = 10.0
_quiet_sweep = (None, 0.0)      # (what that sweep saw, time.monotonic() then)


def list_jobs(principal=None) -> list:
    """Every job_records row `principal` may see (
    ownership_service.can_see_job; None = auth off, all), newest-started
    first, redacted for HTTP. Stale rows of dead owners are closed first
    (sweep_stale_job_records), so they don't list as running forever."""
    records = db.list_job_records()
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
            records = db.list_job_records()
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
    for run in runs:
        for stage in run["stages"]:
            stage["stage"] = scrub_text(stage["stage"])
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
