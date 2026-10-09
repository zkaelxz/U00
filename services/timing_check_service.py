"""
services/timing_check_service.py -- the Review "Check timing" job: compares
each line's start/end with the speech found in the title's audio and flags
the lines that disagree (timing_drift.py has the rules and thresholds).

A thread job without the GPU slot. It reuses the speech coverage check's
chunked ffmpeg + Silero scan (CPU only, cancellable between chunks, never the
whole file in memory) rather than loading the VAD in a process of its own.
It writes only flag/flag_note, by permanent line id and only while the line's
timing is unchanged since it was read. Lines the user dismissed stay
unflagged. The per-line "Snap to speech" suggestions, the dismissed ids and
the last result live in timing_check.json beside the title's audio, so they
survive a restart; the job record alone would not.

Runs by itself after a transcription whose backend is Qwen-only
(after_run), and on demand for any title.
"""
import datetime
import json
import os
import subprocess
import threading
from typing import Optional

import background_jobs
import db
import timing_drift
from services import speech_coverage_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from translate_engines import redact_secrets

# Backends whose line times are Silero's spans or the forced aligner's, with no Whisper pass behind them.
QWEN_ONLY_BACKENDS = ("qwen3_asr_vad", "qwen3_asr_long")
SNAP_HISTORY_LABEL = "before snapping lines to speech"
_STATE_FILE = "timing_check.json"
_MAX_SNAP_IDS = 5000
_state_lock = threading.Lock()


def job_id_for(drama_id: int) -> str:
    return f"timingchk_{drama_id}"


def _state_path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), _STATE_FILE)


def _read_state(drama_id: int) -> dict:
    try:
        with open(_state_path(drama_id), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"dismissed": [i for i in data.get("dismissed", []) if isinstance(i, int)],
            "suggestions": data.get("suggestions") if isinstance(data.get("suggestions"), dict) else {},
            "last_check": data.get("last_check") if isinstance(data.get("last_check"), dict) else None}


def _write_state(drama_id: int, state: dict) -> None:
    path = _state_path(drama_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh)
    os.replace(tmp, path)


def note_dismissed(drama_id: int, line_id: int) -> None:
    """Remembers that the user dismissed this line's timing flag, so the next
    check doesn't raise it again. Only called for a timing_drift flag."""
    with _state_lock:
        state = _read_state(drama_id)
        if line_id not in state["dismissed"]:
            state["dismissed"].append(line_id)
        state["suggestions"].pop(str(line_id), None)
        _write_state(drama_id, state)


def _audio_path(drama_id: int, drama: dict) -> Optional[str]:
    name = drama.get("audio_filename")
    path = os.path.join(db.drama_dir(drama_id), name) if name else None
    return path if path and os.path.exists(path) else None


def _flag_changes(lines, report) -> tuple:
    """(lines whose flag/flag_note change, ids newly or still flagged, count
    of drifting lines left alone because another flag is on them)."""
    found = {f.line_id: f for f in report.findings}
    changed, flagged, skipped = [], set(), 0
    for ln in lines:
        finding = found.get(ln.id)
        if finding is None:
            if ln.flag == timing_drift.TIMING_DRIFT_FLAG:
                ln.flag, ln.flag_note = None, ""
                changed.append(ln)
        elif ln.flag and ln.flag != timing_drift.TIMING_DRIFT_FLAG:
            skipped += 1
        else:
            flagged.add(ln.id)
            if ln.flag != timing_drift.TIMING_DRIFT_FLAG or ln.flag_note != finding.note:
                ln.flag, ln.flag_note = timing_drift.TIMING_DRIFT_FLAG, finding.note
                changed.append(ln)
    return changed, flagged, skipped


def apply_spans(drama_id: int, spans, audio_seconds: float, conservative: bool) -> dict:
    """Flags (and unflags) lines against `spans` and stores the snap
    suggestions. Returns the job's result dict."""
    with _state_lock:
        state = _read_state(drama_id)
    lines = db.load_line_objects(drama_id)
    report = timing_drift.find_drift(lines, spans, audio_seconds, conservative=conservative,
                                     dismissed=set(state["dismissed"]))
    checked_at = datetime.datetime.utcnow().isoformat()
    if report.broken_audio:
        # Keep earlier flags and suggestions: nothing was judged, so nothing is now known to be fine.
        notice = timing_drift.BROKEN_AUDIO_NOTICE
        result = {"checked": 0, "flagged": 0, "cleared": 0, "skipped_flagged": 0, "notice": notice}
        with _state_lock:
            fresh = _read_state(drama_id)
            fresh["last_check"] = {"checked_at": checked_at, "flagged": 0, "notice": notice}
            _write_state(drama_id, fresh)
        return result

    changed, flagged, skipped = _flag_changes(lines, report)
    unwritten = set()
    if changed:
        # Flag fields only, and only while the line's times are what they were judged on.
        unwritten = set(db.save_lines(drama_id, changed, fields=("flag", "flag_note"),
                                      only_if_unchanged=True, guard_fields=("start", "end")))
    flagged -= {i for i in unwritten if i in flagged}
    cleared = sum(1 for ln in changed if not ln.flag and ln.id not in unwritten)
    by_id = {ln.id: ln for ln in lines}
    suggestions = {
        str(f.line_id): {"start": by_id[f.line_id].start, "end": by_id[f.line_id].end,
                         "new_start": f.suggestion[0], "new_end": f.suggestion[1]}
        for f in report.findings if f.line_id in flagged and f.suggestion}
    with _state_lock:
        fresh = _read_state(drama_id)
        fresh["suggestions"] = suggestions
        fresh["last_check"] = {"checked_at": checked_at, "flagged": len(flagged), "notice": None}
        _write_state(drama_id, fresh)
    return {"checked": report.checked, "flagged": len(flagged), "cleared": cleared,
            "skipped_flagged": skipped, "notice": None}


def _run_job(job_id: str, drama_id: int, audio_path: str, conservative: bool) -> None:
    total = speech_coverage_service._audio_seconds(audio_path)
    if not total:
        background_jobs.set_result(job_id, {"failed_reason": "unreadable"})
        return
    try:
        spans = speech_coverage_service._scan_speech(job_id, audio_path, total)
    except background_jobs.JobCancelled:
        raise
    except ImportError:
        background_jobs.set_result(job_id, {
            "failed_reason": "dependency_missing",
            "detail": "The timing check needs faster-whisper, which isn't installed."})
        return
    except (subprocess.SubprocessError, OSError) as exc:
        background_jobs.set_result(job_id, {"failed_reason": "decode",
                                            "detail": redact_secrets(str(exc))[:200]})
        return
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    background_jobs.set_result(job_id, apply_spans(drama_id, spans, total, conservative))


def start_timing_check(drama_id: int, after_transcription: bool = False) -> dict:
    """Starts the check over this title's stored audio. NotFoundError,
    UnsupportedOperationError (no stored audio or no lines),
    ConflictError (a transcription is running, unless this is the automatic
    run it starts itself, or a check is already running)."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    audio_path = _audio_path(drama_id, drama)
    if audio_path is None:
        raise UnsupportedOperationError(f"No audio available for drama {drama_id}.")
    if not db.load_line_ids(drama_id):
        raise UnsupportedOperationError("This title has no lines yet.")
    if not after_transcription and background_jobs.is_running(f"transcribe_{drama_id}"):
        raise ConflictError("A transcription is running for this title. "
                            "Check the timing when it finishes.")
    job_id = job_id_for(drama_id)
    conservative = (drama.get("content_mode") or "") == "streamer_vod"
    if not background_jobs.start_job(
            job_id, _run_job, job_id, drama_id, audio_path, conservative,
            description=f"Timing check (drama #{drama_id})"):
        raise ConflictError("A timing check is already running for this title.")
    return {"job_id": job_id}


def after_run(drama_id: int, outcome: dict) -> bool:
    """Starts the check for a finished Qwen-only transcription (`outcome` is the
    run's result, whose raw_backend names the backend). Never raises: the
    transcription already succeeded and this is an extra."""
    if outcome.get("raw_backend") not in QWEN_ONLY_BACKENDS:
        return False
    try:
        with _state_lock:
            # New lines carry new ids; an old dismissal or suggestion means nothing for them.
            _write_state(drama_id, {"dismissed": [], "suggestions": {}, "last_check": None})
        start_timing_check(drama_id, after_transcription=True)
        return True
    except Exception as exc:
        import applog
        applog.get_logger().warning(
            f"timing check not started for drama {drama_id}: {redact_secrets(str(exc))}")
        return False


def get_timing_check(drama_id: int) -> dict:
    """{job_id, status, progress, message, result, last_check, suggestions}.
    status is "idle" (job_id "") when no check is held in this app session;
    last_check is the newest finished check, kept across restarts;
    suggestions lists each flagged line's saved corrected times."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    with _state_lock:
        state = _read_state(drama_id)
    suggestions = [{"line_id": int(k), "start": v["start"], "end": v["end"],
                    "new_start": v["new_start"], "new_end": v["new_end"]}
                   for k, v in state["suggestions"].items() if str(k).isdigit()]
    out = {"job_id": "", "status": "idle", "progress": 0.0, "message": "", "result": None,
           "last_check": state["last_check"], "suggestions": suggestions}
    job = background_jobs.get_status(job_id_for(drama_id))
    if job:
        status = job.get("status")
        message = job.get("error") if status == "error" else job.get("message")
        out.update(job_id=job_id_for(drama_id), status=status, progress=job.get("progress"),
                   message=redact_secrets(str(message)) if message else "",
                   result=job.get("result") if status == "done" and isinstance(job.get("result"), dict) else None)
    return out


def snap_to_speech(drama_id: int, line_ids=None) -> dict:
    """Applies the saved suggestions to the flagged lines (all of them, or
    `line_ids`), after a line-history snapshot. A line is snapped only while
    its start, end and timing flag are what the check saw, so a time the user
    edited since wins and is reported in `stale_ids`; the write is one
    compare-and-set per line. Writes start, end, flag and flag_note only.
    Returns {"snapped", "stale_ids", "history_id"}."""
    if line_ids is not None:
        if (not isinstance(line_ids, (list, tuple)) or len(line_ids) > _MAX_SNAP_IDS
                or any(isinstance(i, bool) or not isinstance(i, int) for i in line_ids)):
            raise InvalidInputError("line_ids must be a list of integer line ids.")
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    with _state_lock:
        state = _read_state(drama_id)
        wanted = set(line_ids) if line_ids is not None else None
        items = []
        for key, s in state["suggestions"].items():
            lid = int(key) if str(key).isdigit() else None
            if lid is None or (wanted is not None and lid not in wanted):
                continue
            items.append((lid, {"start": s["new_start"], "end": s["new_end"],
                                "flag": None, "flag_note": ""},
                          {"start": s["start"], "end": s["end"],
                           "flag": timing_drift.TIMING_DRIFT_FLAG}))
        if not items:
            return {"snapped": 0, "stale_ids": [], "history_id": None}
        history_id = db.save_line_history_snapshot(
            drama_id, db.load_line_objects(drama_id), SNAP_HISTORY_LABEL)
        stale = db.update_lines_fields_if_many(drama_id, items)
        # A stale suggestion would only fail again, so it goes too.
        for lid, _, _ in items:
            state["suggestions"].pop(str(lid), None)
        _write_state(drama_id, state)
    return {"snapped": len(items) - len(stale), "stale_ids": list(stale), "history_id": history_id}
