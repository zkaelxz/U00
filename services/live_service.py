"""
services/live_service.py -- Live capture sessions (spec
docs/specs/discover-sources-live-api-spec.md section 4, L-1, polling only).

A session is one background job (`live_<uuid>`) running
live_translate.run_live_job in its own folder under the library temp
folder (storage.new_workdir, owned by the session id), which is
removed when the job ends (done, error, cancel -- including a cancel while
still queued). Every start gets its own id and directory, use_gpu reaches the
pipeline, and max_minutes is a hard stop.

Decisions (spec): any public http(s) URL yt-dlp can resolve is accepted
(host checked by services.url_guard.resolve_public, no fetch here). The
job runs yt-dlp and the stream fetcher (live_fetch, which pipes the
stream into ffmpeg; ffmpeg itself opens nothing) through a
services.egress_proxy.GuardedProxy, so every connection they make
afterwards (redirects, playlist variants, segments, keys) is checked and
pinned to a public address too; no
browser cookies over the API (a start at the PC uses the saved Settings
cookies; see start_session); keys are resolved server-side, never taken
from the caller. No FastAPI import.

Router contract: start/get are gated like media.import_url, and an engine
outside translate_engines.FREE_ENGINES (Gemini counts as paid: whether a key
is free-tier isn't known server-side) additionally needs the engines.paid
capability; stop is gated like jobs.cancel.
"""
import functools
import re
import shutil
import threading
import uuid
from typing import Optional

import background_jobs
import live_cue_feed
import live_translate
import storage
import translate_engines
from core import SOURCE_LANGUAGES
from services import (egress_proxy, job_stage_service, jobs_service, ownership_service,
                      run_settings_service, settings_service, translate_service, url_guard)
from services.service_errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    ServiceError,
)

WHISPER_SIZES = ("tiny", "base", "small", "medium")
SEGMENT_RANGE = (10, 60)
OVERLAP_RANGE = (0, 8)
MAX_MINUTES_RANGE = (1, 240)
DEFAULT_MAX_MINUTES = 60
LIVE_DEFAULT_ENGINE = "ollama"
MAX_SESSIONS = 32
MAX_URL_LEN = 2000

_lock = threading.Lock()
# session_id -> {"dir": str or None, "engine": str}
_sessions = {}

# A filesystem path: at line start or after whitespace/quote/paren, a
# drive or root, then at least one more separator (so "and/or" survives).
_PATH_RE = re.compile(r"""(?:(?<=^)|(?<=[\s'"(=]))(?:[A-Za-z]:[\\/]|\\\\|/)[^\s'"]*""")


def clean_message(text) -> str:
    """Redacted, URL- and path-stripped, single-line text safe to return to
    a client (a yt-dlp or proxy error can name the stream or proxy URL)."""
    if not text:
        return ""
    # Not jobs_service.scrub_text: its username redaction rewrites ordinary
    # words in transcripts and errors ("li" -> "[USER]kely").
    text = jobs_service.URL_PATTERN.sub("[URL]", str(text))
    text = _PATH_RE.sub("<path>", translate_engines.redact_secrets(text))
    return text.splitlines()[0][:500] if text.strip() else ""


def _cue_text(text) -> str:
    """Cue text keeps its wording; only a failure note is path-stripped."""
    text = translate_engines.redact_secrets(str(text or ""))
    return clean_message(text) if text.startswith("[translation failed") else text


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


def _require_offered_model(engine_name: str, model: Optional[str]):
    """Only models the Live picker offers (the Translate list) are accepted."""
    if not model:
        return
    entry = next((e for e in translate_service.list_engines() if e["name"] == engine_name), None)
    if entry is None or model not in (entry["models"] or ()):
        raise InvalidInputError("That model isn't offered for this engine.")


def _build_engine(engine_name: Optional[str], model: Optional[str]):
    # Never the Settings default: that may be a hosted engine, and a Live
    # request without an engine (API client, extension, stale bundle) must not
    # send the stream's text off this PC unasked.
    engine_name = engine_name or LIVE_DEFAULT_ENGINE
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError(translate_engines.unknown_engine_message(engine_name))
    _require_offered_model(engine_name, model)
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    try:
        engine = translate_engines.get_engine(
            engine_name, api_key, model or None,
            free_tier=settings_service.get_gemini_free_tier(),
            base_url=(settings_service.resolve_key("ollama_url") or None)
            if engine_name == "ollama" else None)
        if engine_name == "ollama":
            translate_engines.check_ollama_model_installed(engine.base_url, engine.model)
    except ServiceError:
        raise
    except translate_engines.OllamaUnavailableError as exc:
        raise DependencyUnavailableError(
            translate_engines.redact_secrets(exc.message), details={"reason": exc.reason}) from None
    except Exception as exc:
        raise DependencyUnavailableError(
            clean_message(f"Could not start {engine_name}: {exc}")) from None
    return engine_name, engine


def check_ollama(model: Optional[str] = None) -> dict:
    """{ok, model, message}: whether Ollama answers and has the model, so the
    Live form can say so before Start. The message is the plain text the chat
    call uses and never carries the Ollama address."""
    from services import translate_run_service
    # The model Start would run when none is chosen, so the note describes it.
    model = model or translate_engines.effective_default_model("ollama")
    if not translate_run_service._is_safe_ollama_model(model):
        raise InvalidInputError("That model isn't offered for this engine.")
    try:
        translate_engines.check_ollama_model_installed(
            settings_service.resolve_key("ollama_url") or "http://localhost:11434", model)
    except translate_engines.OllamaUnavailableError as exc:
        return {"ok": False, "model": model, "message": translate_engines.redact_secrets(exc.message)}
    return {"ok": True, "model": model, "message": None}


def _remove_dir(session_id: str):
    with _lock:
        entry = _sessions.get(session_id)
        path = entry.pop("dir", None) if entry else None
    if path:
        shutil.rmtree(path, ignore_errors=True)


def _active_session_locked():
    """The id of a session that is reserved, queued or running,
    else None. Call with _lock held."""
    for sid, entry in _sessions.items():
        if not entry.get("dir"):
            continue    # finished: its job removed the directory
        job = background_jobs.get_status(sid)
        if job is None or job.get("status") in ("queued", "running"):
            return sid
    return None


def check_stream_url(stream_url) -> None:
    """Run on the direct stream URL yt-dlp resolved, before it is
    fetched: services.url_guard.resolve_public (http/https only, no userinfo,
    every resolved address public), on the full URL (no length cap: a
    signed stream URL can be long). The error never echoes the URL, which
    can carry a signed token."""
    _require_public(stream_url, "The stream address the site returned is not a public "
                                "http(s) address, so it was not opened.")


def _require_public(url, bad_message: str) -> None:
    """url_guard.resolve_public, the one public-address policy,
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
            # run_live_job stops the stream fetcher (and ffmpeg) before
            # returning, so the proxy outlives every connection it serves.
            with egress_proxy.GuardedProxy() as proxy:
                live_translate.run_live_job(
                    *args, proxy=proxy.url,
                    report_stage=functools.partial(job_stage_service.set_stage, session_id),
                    **kwargs)
        finally:
            job_stage_service.clear_stage(session_id)
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
                  max_minutes=DEFAULT_MAX_MINUTES, use_gpu: bool = False,
                  use_saved_cookies: bool = False,
                  reply_without_thinking: bool = True) -> dict:
    """Starts one live capture session; returns {"session_id": ...}.
    use_saved_cookies: pass yt-dlp the saved Settings cookies (browser or
    cookies.txt). The router sets it only for a request made at the PC, so
    another device never reads streams as the owner's signed-in account.
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
    _reap()
    # Refuse a second session before building the engine, so a start that
    # would be refused costs no key lookup or model load. Re-checked below
    # under the same lock hold as the reservation.
    with _lock:
        if _active_session_locked() is not None:
            raise ConflictError("A live session is already running. Stop it first.")
    engine_name, eng = _build_engine(engine, model)

    session_id = f"live_{uuid.uuid4().hex}"
    out_dir = storage.new_workdir(session_id)
    # Held until the job is registered: before that nothing owns the folder, so
    # a "clean temp now" in the gap would delete it.
    with storage.holding(out_dir):
        return _start_registered(session_id, out_dir, url, source_language, whisper_size,
                                 segment_seconds, overlap_seconds, max_minutes, eng, engine_name,
                                 use_gpu, use_saved_cookies, reply_without_thinking)


def _start_registered(session_id, out_dir, url, source_language, whisper_size, segment_seconds,
                      overlap_seconds, max_minutes, eng, engine_name, use_gpu,
                      use_saved_cookies, reply_without_thinking):
    with _lock:
        # One session at a time (a design limit):
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
        _sessions[session_id] = {"dir": out_dir, "engine": engine_name,
                                 "model": getattr(eng, "model", None), "starting": True,
                                 "owner_user_id": ownership_service.acting_user_id()}
    try:
        started = background_jobs.start_job(
            session_id, _make_target(session_id),
            session_id, url, out_dir, segment_seconds, source_language, whisper_size, eng,
            use_gpu=bool(use_gpu), overlap_seconds=overlap_seconds,
            reply_without_thinking=bool(reply_without_thinking),
            max_seconds=max_minutes * 60,
            stream_url_check=check_stream_url,
            **(settings_service.get_cookie_settings() if use_saved_cookies else {}),
            run_settings=run_settings_service.build(
                engine=engine_name, model=getattr(eng, "model", None),
                whisper_size=whisper_size, source_language=source_language,
                segment_seconds=segment_seconds, overlap_seconds=overlap_seconds,
                use_gpu=bool(use_gpu), reply_without_thinking=bool(reply_without_thinking),
                max_minutes=max_minutes),
            gpu_touching=bool(use_gpu), description=f"Live capture (local Whisper, {engine_name}"
            f"{' ' + eng.model if getattr(eng, 'model', None) else ''})")
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


def _visible(principal, session_id, entry) -> bool:
    return ownership_service.can_see_job(principal, session_id, entry.get("owner_user_id"))


def _require(session_id, principal=None) -> dict:
    """Another user's session is a 404 like an unknown one (auth B2)."""
    with _lock:
        entry = _sessions.get(session_id) if isinstance(session_id, str) else None
    if entry is None or not _visible(principal, session_id, entry):
        raise NotFoundError("No such live session.")
    return job_stage_service.annotate(background_jobs.get_status(session_id))


def stop_session(session_id, principal=None) -> dict:
    """Bumps the generation first (so an in-flight chunk's result is
    discarded), then cancels: cancel_queued if still queued, else
    request_cancel. Idempotent on a finished session."""
    job = _require(session_id, principal)
    with _lock:
        entry = _sessions.get(session_id) or {}
    ownership_service.require_job_changeable(principal, session_id, entry.get("owner_user_id"))
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


def get_session(session_id, after=0, principal=None) -> dict:
    """{status, message, progress, cues[after:], next_index}. Never a
    traceback, a filesystem path or a key. A cue is added untranslated and
    changes in place when its translation lands, so a client that wants those
    updates asks again from its oldest still-pending cue's id."""
    job = _require(session_id, principal)
    _reap()
    with _lock:
        entry = _sessions.get(session_id) or {}
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
    for pos, c in enumerate(cues[after:], start=after):
        translated = _cue_text(c.get("translated"))
        out.append({"id": int(c.get("id", pos)),
                    "start": float(c.get("start", 0)), "end": float(c.get("end", 0)),
                    "text": _cue_text(c.get("text")), "translated": translated,
                    "translation": c.get("translation") or (
                        live_cue_feed.FAILED if translated.startswith(live_cue_feed.FAILED_PREFIX)
                        else live_cue_feed.DONE)})
    return {"session_id": session_id, "status": status, "message": message,
            "engine": entry.get("engine"), "model": entry.get("model"),
            "progress": float((job or {}).get("progress") or 0.0),
            "cues": out, "next_index": max(after, len(cues))}


def list_sessions(principal=None) -> list:
    _reap()
    with _lock:
        items = list(_sessions.items())
    result = []
    for sid, entry in items:
        if not _visible(principal, sid, entry):
            continue
        job = background_jobs.get_status(sid)
        cues = (job or {}).get("result") or []
        result.append({"session_id": sid, "status": _status(job), "engine": entry.get("engine"),
                       "model": entry.get("model"), "cue_count": len(cues) if isinstance(cues, list) else 0})
    return result
