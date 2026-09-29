"""
services/live_service.py -- Live capture sessions (spec
docs/specs/discover-sources-live-api-spec.md section 4, L-1, polling only).

A session is one background job (`live_<uuid>`) running
live_translate.run_live_job in its own tempfile.mkdtemp directory, which is
removed when the job ends (done, error, cancel -- including a cancel while
still queued). Unlike the Streamlit tab (fixed job id, fixed shared temp
dir, use_gpu never passed), every start gets its own id and directory,
use_gpu reaches the pipeline, and max_minutes is a hard stop.

Decisions (spec): any public http(s) URL yt-dlp can resolve is accepted
(host checked by services.url_guard.resolve_public, no fetch here); no
browser cookies over the API; keys are resolved server-side, never taken
from the caller. No Streamlit/FastAPI import.

Router contract: start/get are gated like media.import_url, and a paid
engine (is_paid_engine) additionally needs the engines.paid capability;
stop is gated like jobs.cancel.
"""
import os
import re
import shutil
import tempfile
import threading
import uuid
from typing import Optional

import background_jobs
import live_translate
import translate_engines
from services import settings_service, translate_service, url_guard
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError, ServiceError)

SOURCE_LANGUAGES = ("zh", "ja", "ko")
WHISPER_SIZES = ("tiny", "base", "small", "medium")
SEGMENT_RANGE = (10, 60)
OVERLAP_RANGE = (0, 8)
MAX_MINUTES_RANGE = (1, 240)
DEFAULT_MAX_MINUTES = 60
MAX_SESSIONS = 32
MAX_URL_LEN = 2000
STREAMLIT_JOB_ID = "live_capture"
# ffmpeg input protocols for a resolved live stream (HLS over https needs
# tcp, tls and crypto for encrypted segments); no file, pipe, data, etc.
FFMPEG_PROTOCOL_WHITELIST = "http,https,tcp,tls,crypto"

_lock = threading.Lock()
# session_id -> {"dir": str or None, "engine": str}
_sessions = {}

# A filesystem path: at line start or after whitespace/quote/paren, a
# drive or root, then at least one more separator (so "and/or" survives).
_PATH_RE = re.compile(r"""(?:(?<=^)|(?<=[\s'"(=]))(?:[A-Za-z]:[\\/]|\\\\|/)[^\s'"]*""")


def clean_message(text) -> str:
    """Redacted, path-stripped, single-line text safe to return to a client."""
    if not text:
        return ""
    text = translate_engines.redact_secrets(str(text))
    text = _PATH_RE.sub("<path>", text)
    return text.splitlines()[0][:500] if text.strip() else ""


def _cue_text(text) -> str:
    """Cue text keeps its wording; only a failure note is path-stripped."""
    text = translate_engines.redact_secrets(str(text or ""))
    return clean_message(text) if text.startswith("[translation failed") else text


def is_paid_engine(engine_name: str) -> bool:
    """True if a live session on this engine can spend money. Gemini is
    treated as paid: whether a key is free-tier isn't known server-side."""
    return engine_name not in translate_engines.FREE_ENGINES


def _num(name, value, lo, hi, cast=float):
    if isinstance(value, bool):
        raise InvalidInputError(f"{name} must be a number.")
    try:
        value = cast(value)
    except (TypeError, ValueError):
        raise InvalidInputError(f"{name} must be a number.") from None
    if value != value:  # NaN
        raise InvalidInputError(f"{name} must be a number.")
    return max(lo, min(hi, value))


def _build_engine(engine_name: Optional[str], model: Optional[str]):
    engine_name = engine_name or "claude"
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown engine.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None and engine_name != "nllb":
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    try:
        engine = translate_engines.get_engine(
            engine_name, api_key, model or None,
            base_url=(settings_service.resolve_key("ollama_url") or None)
            if engine_name == "ollama" else None)
    except ServiceError:
        raise
    except Exception as exc:
        raise DependencyUnavailableError(
            clean_message(f"Could not start {engine_name}: {exc}")) from None
    return engine_name, engine


def _remove_dir(session_id: str):
    with _lock:
        entry = _sessions.get(session_id)
        path = entry.pop("dir", None) if entry else None
    if path:
        shutil.rmtree(path, ignore_errors=True)


def _active_session_locked():
    """The id of a session that is reserved, queued or running, or the
    Streamlit tab's fixed `live_capture` job if it runs in this process;
    else None. Call with _lock held."""
    for sid, entry in _sessions.items():
        if not entry.get("dir"):
            continue    # finished: its job removed the directory
        job = background_jobs.get_status(sid)
        if job is None or job.get("status") in ("queued", "running"):
            return sid
    legacy = background_jobs.get_status(STREAMLIT_JOB_ID)
    if legacy and legacy.get("status") in ("queued", "running"):
        return STREAMLIT_JOB_ID
    return None


def check_stream_url(stream_url) -> None:
    """Run on the direct stream URL yt-dlp resolved, before ffmpeg opens
    it: services.url_guard.resolve_public (http/https only, no userinfo,
    every resolved address public), on the full URL (no length cap: a
    signed stream URL can be long). The error never echoes the URL, which
    can carry a signed token."""
    _require_public(stream_url, "The stream address the site returned is not a public "
                                "http(s) address, so it was not opened.")


def _require_public(url, bad_message: str) -> None:
    """url_guard.resolve_public, the one public-address policy (B-25),
    mapped to service errors with fixed text."""
    try:
        url_guard.resolve_public(url)
    except url_guard.URLResolveError:
        raise DependencyUnavailableError(
            "The address could not be resolved. Check the URL and your connection.") from None
    except url_guard.UnsafeURLError:
        raise InvalidInputError(bad_message) from None


def _make_target(session_id: str):
    def _target(*args, **kwargs):
        try:
            live_translate.run_live_job(*args, **kwargs)
        finally:
            _remove_dir(session_id)
    return _target


def _reap():
    """Removes the directory of any session whose job record is gone
    (cancelled while queued elsewhere, e.g. via the jobs router) --
    a running job removes its own directory when it ends. A session still
    being started ("starting": reserved, job not registered yet) is
    skipped, or its fresh reservation would be torn down."""
    with _lock:
        ids = [sid for sid, e in _sessions.items() if e.get("dir") and not e.get("starting")]
    for sid in ids:
        job = background_jobs.get_status(sid)
        if job is None:
            _remove_dir(sid)


def start_session(url, source_language="zh", whisper_size="small", segment_seconds=20,
                  overlap_seconds=live_translate.DEFAULT_OVERLAP_SECONDS,
                  engine: Optional[str] = None, model: Optional[str] = None,
                  max_minutes=DEFAULT_MAX_MINUTES, use_gpu: bool = False) -> dict:
    """Starts one live capture session; returns {"session_id": ...}.
    InvalidInputError (422) for a bad/private URL or bad option,
    DependencyUnavailableError (503) for an unresolvable host or missing
    engine key."""
    if not isinstance(url, str) or not url.strip():
        raise InvalidInputError("url is required.")
    url = url.strip()
    if len(url) > MAX_URL_LEN:
        raise InvalidInputError("url is too long.")
    _require_public(url, "url must be a public http(s) web address.")
    if source_language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"source_language must be one of {', '.join(SOURCE_LANGUAGES)}.")
    if whisper_size not in WHISPER_SIZES:
        raise InvalidInputError(f"whisper_size must be one of {', '.join(WHISPER_SIZES)}.")
    segment_seconds = int(_num("segment_seconds", segment_seconds, *SEGMENT_RANGE))
    overlap_seconds = _num("overlap_seconds", overlap_seconds, *OVERLAP_RANGE)
    overlap_seconds = min(overlap_seconds, segment_seconds / 2)
    max_minutes = _num("max_minutes", max_minutes, *MAX_MINUTES_RANGE)
    if model is not None and not isinstance(model, str):
        raise InvalidInputError("model must be a string.")
    engine_name, eng = _build_engine(engine, model)

    _reap()
    session_id = f"live_{uuid.uuid4().hex}"
    out_dir = tempfile.mkdtemp(prefix="baihe_live_")
    with _lock:
        # One session at a time (the Streamlit tab allowed exactly one):
        # each holds the GPU and an engine for up to max_minutes. The
        # check and the reservation share one lock hold, so two starts
        # can't both pass.
        if _active_session_locked() is not None:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise ConflictError("A live session is already running. Stop it first.")
        if len(_sessions) >= MAX_SESSIONS:
            # Forget the oldest finished sessions (dicts keep insertion order).
            for sid in list(_sessions):
                job = background_jobs.get_status(sid)
                if not _sessions[sid].get("dir") and (
                        job is None or job["status"] not in ("queued", "running")):
                    del _sessions[sid]
                if len(_sessions) < MAX_SESSIONS:
                    break
        _sessions[session_id] = {"dir": out_dir, "engine": engine_name, "starting": True}
    try:
        started = background_jobs.start_job(
            session_id, _make_target(session_id),
            session_id, url, out_dir, segment_seconds, source_language, whisper_size, eng,
            use_gpu=bool(use_gpu), overlap_seconds=overlap_seconds,
            max_seconds=max_minutes * 60,
            stream_url_check=check_stream_url, protocol_whitelist=FFMPEG_PROTOCOL_WHITELIST,
            gpu_touching=True, description="Live capture (local Whisper)")
    except Exception:
        _remove_dir(session_id)
        with _lock:
            _sessions.pop(session_id, None)
        raise
    if not started:
        _remove_dir(session_id)
        with _lock:
            _sessions.pop(session_id, None)
        raise ServiceError("Could not start the live session.")
    with _lock:
        entry = _sessions.get(session_id)
        if entry is not None:
            entry.pop("starting", None)
    return {"session_id": session_id}


def _require(session_id) -> dict:
    with _lock:
        known = isinstance(session_id, str) and session_id in _sessions
    if not known:
        raise NotFoundError("No such live session.")
    return background_jobs.get_status(session_id)


def stop_session(session_id) -> dict:
    """Bumps the generation first (so an in-flight chunk's result is
    discarded), then cancels: cancel_queued if still queued, else
    request_cancel. Idempotent on a finished session."""
    job = _require(session_id)
    live_translate.bump_generation(session_id)
    if job is None or background_jobs.cancel_queued(session_id):
        _remove_dir(session_id)
    else:
        background_jobs.request_cancel(session_id)
    return {"session_id": session_id, "stopping": True}


def _status(job) -> str:
    if job is None:
        return "cancelled"
    status = job.get("status")
    if status == "done" and job.get("cancel_requested"):
        return "cancelled"
    return status if status in ("queued", "running", "done", "error", "cancelled") else "error"


def get_session(session_id, after=0) -> dict:
    """{status, message, progress, cues[after:], next_index}. Never a
    traceback, a filesystem path or a key."""
    job = _require(session_id)
    _reap()
    after = int(_num("after", after, 0, 10 ** 9))
    status = _status(job)
    if status == "error":
        message = clean_message((job or {}).get("error")) or "The live session failed."
    elif job is None:
        message = "Cancelled."
    else:
        message = clean_message(job.get("message"))
    cues = (job or {}).get("result") or []
    if not isinstance(cues, list):
        cues = []
    out = []
    for c in cues[after:]:
        out.append({"start": float(c.get("start", 0)), "end": float(c.get("end", 0)),
                    "text": _cue_text(c.get("text")),
                    "translated": _cue_text(c.get("translated"))})
    return {"session_id": session_id, "status": status, "message": message,
            "progress": float((job or {}).get("progress") or 0.0),
            "cues": out, "next_index": max(after, len(cues))}


def list_sessions() -> list:
    _reap()
    with _lock:
        items = list(_sessions.items())
    result = []
    for sid, entry in items:
        job = background_jobs.get_status(sid)
        cues = (job or {}).get("result") or []
        result.append({"session_id": sid, "status": _status(job), "engine": entry.get("engine"),
                       "cue_count": len(cues) if isinstance(cues, list) else 0})
    return result
