"""
services/live_service.py -- Live capture sessions.

A session is one background job (`live_<uuid>`) running
live_translate.run_live_job in its own folder under the library temp
folder (storage.new_workdir, owned by the session id), which is
removed when the job ends (done, error, cancel -- including a cancel while
still queued). Every start gets its own id and directory, use_gpu reaches the
pipeline, and max_minutes is a hard stop.

Decisions (spec): any public http(s) URL yt-dlp can resolve is accepted
(host checked by lib.url_guard.resolve_public, no fetch here). The
job runs yt-dlp and the stream fetcher (live_fetch, which pipes the
stream into ffmpeg; ffmpeg itself opens nothing) through a
services.egress_proxy.GuardedProxy, so every connection they make
afterwards (redirects, playlist variants, segments, keys) is checked and
pinned to a public address too; no
browser cookies over the API (a start at the PC uses the saved Settings
cookies; see start_session); keys are resolved server-side, never taken
from the caller. No FastAPI import.

Router contract: start/get are gated like media.import_url, and an engine
outside engine_registry.FREE_ENGINES (Gemini counts as paid: whether a key
is free-tier isn't known server-side) additionally needs the engines.paid
capability; stop is gated like jobs.cancel.
"""
import functools
import inspect
import re
import shutil
import threading
import uuid
from typing import Optional
from urllib.parse import urlsplit

import background_jobs
from jobs import job_launch
import db
import diagnostics_torch
from live import live_chat
from live import live_cue_feed
from live import live_cue_translation
from live import live_pipeline
from live import live_player
import language_packs
from live import live_translate
import storage
from engine_backends import engine_registry, shared, llm_tasks
from engine_backends import local as engine_local
import whisper_models
from engine_backends import thinking
from core import LANGUAGE_NAMES, SOURCE_LANGUAGES
from lib import url_guard
from services import (egress_proxy, job_stage_service, jobs_service, language_pack_service,
                      live_spend_service, ownership_service, run_settings_service, settings_service,
                      translate_run_service, translate_service)
from lib.errors import (
    ConflictError,
    DependencyUnavailableError,
    InvalidInputError,
    MissingKeyError,
    NotFoundError,
    ServiceError,
)
from lib import acting

WHISPER_SIZES = whisper_models.LIVE_WHISPER_SIZES
SEGMENT_RANGE = (3, 60)
OVERLAP_RANGE = (0, 8)
MAX_MINUTES_RANGE = (1, 240)
DEFAULT_MAX_MINUTES = 60
LIVE_DEFAULT_ENGINE = "ollama"
# Live must keep up with speech, so a card that cannot hold TranslateGemma 27B
# starts on 12B, which never fell behind once warm on a 12 GB card.
LIVE_SMALL_CARD_MODEL = "translategemma:12b"
LIVE_LARGE_CARD_MODEL = "translategemma:27b"
LARGE_CARD_MB = 20 * 1024
# A glossary note is advice for a translator, not text to repeat per chunk.
HINT_NOTE_CHARS = 120
MAX_SESSIONS = 32
MAX_URL_LEN = 2000
# Kept per session, newest last: the skipped-chunk and catch-up events the status
# line would otherwise overwrite a second later.
MAX_NOTES = 6
# live_pipeline.LagStats figures, the only numbers a session status carries
# from the job: whatever else a caller passes is dropped.
STAT_FIELDS = {"lag_seconds": float, "processing_seconds": float, "translations_skipped": int,
               "translate_queue": int, "chunks_measured": int, "reconnect_attempt": int}
FFMPEG_MISSING = "Live capture needs ffmpeg, which isn't installed or isn't on PATH on this PC."
YTDLP_MISSING = "Live capture needs yt-dlp, which isn't installed on this PC."
FINISHING_NOTE = ("Stopping: the audio already captured is being captioned. "
                  "Press Stop now to end at once.")

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
    text = _PATH_RE.sub("<path>", shared.redact_secrets(text))
    return text.splitlines()[0][:500] if text.strip() else ""


_TWITCH_HOSTS = ("twitch.tv", "www.twitch.tv", "m.twitch.tv")
_TWITCH_NAME = re.compile(r"[A-Za-z0-9_]{1,25}")
_TWITCH_VIDEO = re.compile(r"[0-9]{1,15}")


def stream_ref(url: str) -> Optional[dict]:
    """The embeddable video a start link points at, as a kind and an id only
    (Kae, 2026-11-17: other signed-in devices may show the video, but the
    fetched URL itself never leaves the server); None for any other link."""
    vid = live_chat.video_id(url)
    if vid:
        return {"kind": "youtube", "id": vid}
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if (parts.hostname or "").lower() not in _TWITCH_HOSTS:
        return None
    path = [p for p in parts.path.split("/") if p]
    if len(path) == 2 and path[0] == "videos" and _TWITCH_VIDEO.fullmatch(path[1]):
        return {"kind": "twitch-video", "id": path[1]}
    if len(path) == 1 and _TWITCH_NAME.fullmatch(path[0]):
        return {"kind": "twitch-channel", "id": path[0]}
    return None


def _cue_text(text) -> str:
    """Cue text keeps its wording; only a failure note is path-stripped."""
    text = shared.redact_secrets(str(text or ""))
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
    """Only models the Live picker offers (the Translate list, which for Ollama
    also holds the models pulled on the server) are accepted."""
    if not model:
        return
    entry = next((e for e in translate_service.list_engines() if e["name"] == engine_name), None)
    offered = list((entry or {}).get("models") or ())
    if engine_name == "ollama" and entry is not None and model not in offered:
        offered += engine_local.pulled_ollama_models(settings_service.ollama_url())
    if entry is None or model not in offered:
        raise InvalidInputError("That model isn't offered for this engine.")


def live_default_model(engine_name: str = LIVE_DEFAULT_ENGINE):
    """The model Live runs when none is chosen. For Ollama with no model picked
    in Settings it goes by the card's graphics memory; an unreadable card gets
    the small model, since 27B on the CPU cannot keep up with a stream. A PC
    that has only 27B pulled keeps it: a slow stream beats one that won't start."""
    if engine_name != "ollama" or engine_registry.model_override_for_default("ollama"):
        return engine_registry.effective_default_model(engine_name)
    import memory_headroom
    total = memory_headroom.total_mb("vram")
    if total is not None and total >= LARGE_CARD_MB:
        return LIVE_LARGE_CARD_MODEL
    pulled = engine_local.pulled_ollama_models(settings_service.ollama_url())
    if LIVE_LARGE_CARD_MODEL in pulled and LIVE_SMALL_CARD_MODEL not in pulled:
        return LIVE_LARGE_CARD_MODEL
    return LIVE_SMALL_CARD_MODEL


def _build_engine(engine_name: Optional[str], model: Optional[str]):
    # Never the Settings default: that may be a hosted engine, and a Live
    # request without an engine (API client, extension, stale bundle) must not
    # send the stream's text off this PC unasked.
    engine_name = engine_name or LIVE_DEFAULT_ENGINE
    if engine_name not in engine_registry.ENGINES:
        raise InvalidInputError(engine_registry.unknown_engine_message(engine_name))
    _require_offered_model(engine_name, model)
    if engine_name == "ollama" and not model:
        model = live_default_model("ollama")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise MissingKeyError(engine_name)
    try:
        engine = engine_registry.get_engine(
            engine_name, api_key, model or None,
            free_tier=bool(settings_service.get("gemini_free_tier")),
            base_url=settings_service.ollama_url()
            if engine_name == "ollama" else None)
        if engine_name == "ollama":
            engine_local.check_ollama_model_installed(engine.base_url, engine.model)
    except ServiceError:
        raise
    except engine_local.OllamaUnavailableError as exc:
        raise DependencyUnavailableError(
            shared.redact_secrets(exc.message), details={"reason": exc.reason}) from None
    except Exception as exc:
        raise DependencyUnavailableError(
            clean_message(f"Could not start {engine_name}: {exc}")) from None
    return engine_name, engine


def check_ollama(model: Optional[str] = None, whisper_size: Optional[str] = None,
                 use_gpu: bool = False) -> dict:
    """{ok, model, message, gpu_fit}: whether Ollama answers and has the model,
    and whether the model (with Whisper too when use_gpu) fits in graphics
    memory, so the Live form can say so before Start. gpu_fit marks a message
    that is only that warning: Start still goes ahead. The message is the plain
    text the chat call uses and never carries the Ollama address."""
    from services import translate_run_service
    # The model Start would run when none is chosen, so the note describes it.
    model = model or live_default_model("ollama")
    if model is None or not translate_run_service._is_safe_ollama_model(model):
        raise InvalidInputError("That model isn't offered for this engine.")
    try:
        engine_local.check_ollama_model_installed(
            settings_service.ollama_url(), model)
    except engine_local.OllamaUnavailableError as exc:
        return {"ok": False, "model": model, "message": shared.redact_secrets(exc.message), "gpu_fit": False}
    # Ollama is its own program and puts the model on the GPU whatever Use GPU
    # says; Use GPU only moves Whisper there too.
    room = _gpu_room_note(model, whisper_size if use_gpu else None, bool(use_gpu))
    return {"ok": room is None, "model": model, "message": room, "gpu_fit": room is not None}


_GPU_PAIR_OVERHEAD_MB = 1024


def _gpu_room_note(model: str, whisper_size: Optional[str], use_gpu: bool = True) -> Optional[str]:
    """Why the model (and Whisper, when it runs on the GPU) may not fit in graphics
    memory, or None. Without it a model that does not fit (TranslateGemma 27B on a
    12 GB card) only showed after Start, as lines skipped to keep up. It counts
    what Start does: with Use GPU, Whisper's load first unloads every Ollama model
    (when Settings lets it), then the model loads again. It warns with the
    keep-free reserve at 0 too: a model that spills onto the CPU is too slow for Live."""
    import memory_headroom
    import ollama_unload
    footprint = memory_headroom.ollama_footprint(settings_service.ollama_url(), model)
    reading = memory_headroom.read_memory_mb("vram", at_load=True)
    if not footprint or reading is None:
        return None
    load_mb, loaded, resident_mb = footprint
    unloads = use_gpu and ollama_unload.is_enabled()
    free_mb = reading[1] + (resident_mb if unloads else 0)
    ollama_mb = load_mb if unloads or not loaded else 0
    key = memory_headroom.whisper_key(whisper_size or "")
    # A Whisper an earlier session left loaded in this process already holds its memory.
    cached = f"{whisper_size}_gpu" in whisper_models._whisper_model_cache
    whisper_mb = 0 if cached or not whisper_size else (
        memory_headroom.estimate_mb("whisper", whisper_size) or (0, 0))[0]
    label = f"{model} and Whisper {key}" if whisper_mb else model
    # Two programs on the card each hold their own CUDA runtime; on the V100 ja-1.5b
    # beside TranslateGemma 12B took about 1 GB more than its Whisper estimate.
    overhead_mb = _GPU_PAIR_OVERHEAD_MB if whisper_mb and ollama_mb else 0
    need_mb = ollama_mb + whisper_mb + overhead_mb
    reserved = memory_headroom.reserved_mb("vram")
    if not need_mb or free_mb - need_mb >= reserved:
        return None
    kept = f", {_gb(reserved)} kept free for other programs" if reserved > 0 else ""
    advice = ("Pick a smaller model, such as translategemma:12b" if model != LIVE_SMALL_CARD_MODEL
              else "Pick a smaller model")
    if whisper_mb:
        advice += ", or turn off Use GPU"
    return (f"{label} may not fit in graphics memory (need about {_gb(need_mb)}, {_gb(free_mb)} free{kept}). "
            f"{advice}.")


def _gb(mb: float) -> str:
    return f"{max(mb, 0) / 1024:.1f} GB"


def _language_whisper_defaults() -> dict:
    """Models the form starts on instead of its default for a language, so a stream in that
    language needs no change. Only models already on this PC: Live never starts a big download
    on its own. Japanese gets whisper-ja only where an NVIDIA GPU is present, since it cannot
    keep up with a stream on a CPU."""
    out = {}
    if whisper_models.is_whisper_model_cached(whisper_models.BREEZE_KEY):
        out["zh"] = whisper_models.BREEZE_KEY
    try:
        has_gpu = diagnostics_torch.nvidia_driver_info() is not None
    except Exception:
        has_gpu = False
    if has_gpu and whisper_models.is_whisper_model_cached("ja-1.5b"):
        out["ja"] = "ja-1.5b"
    return out


def form_options() -> dict:
    """What the Live form offers and starts on, from the values Start checks
    against, so the form never keeps its own copy that can drift."""
    defaults = inspect.signature(start_session).parameters
    return {
        "languages": [{"code": c, "label": LANGUAGE_NAMES[c]} for c in SOURCE_LANGUAGES],
        "whisper_sizes": list(WHISPER_SIZES),
        "whisper_exclusions": whisper_models.whisper_language_exclusions(),
        "segment_range": list(SEGMENT_RANGE),
        "overlap_range": list(OVERLAP_RANGE),
        "max_minutes_range": list(MAX_MINUTES_RANGE),
        "max_url_len": MAX_URL_LEN,
        "player_heights": list(live_player.HEIGHTS),
        "default_engine": LIVE_DEFAULT_ENGINE,
        # The model Start runs when none is chosen, which Settings or the card's memory changes.
        "default_model": live_default_model(LIVE_DEFAULT_ENGINE),
        "thinking_switch_engines": list(thinking.NO_THINKING_ENGINES),
        # Per language, only where a better-than-default model is already downloaded.
        "language_whisper_defaults": _language_whisper_defaults(),
        "spend_limit_range": list(live_spend_service.SESSION_LIMIT_RANGE),
        # A monthly cap bounds every session already, so the form hides the session limit then.
        "monthly_cap_set": bool(translate_run_service.month_cap_usd()),
        "defaults": {k: defaults[k].default for k in (
            "source_language", "whisper_size", "segment_seconds", "overlap_seconds",
            "max_minutes", "player_height", "spend_limit_usd")},
    }


def add_note(session_id: str, text: str, key: Optional[str] = None) -> None:
    """Keeps a short event for the session's status. The text is fixed wording
    the job composes from numbers, passed through clean_message anyway. A note
    with a key replaces the earlier one with that key and becomes the newest,
    so a running count of skips is never the first note to scroll out."""
    with _lock:
        entry = _sessions.get(session_id)
        if entry is not None:
            notes = entry.setdefault("notes", [])
            if key is not None:
                keyed = entry.setdefault("note_keys", {})
                if keyed.get(key) in notes:
                    notes.remove(keyed[key])
                keyed[key] = clean_message(text)
            notes.append(clean_message(text))
            del notes[:-MAX_NOTES]


def set_stats(session_id: str, numbers: dict) -> None:
    """Keeps the job's lag figures for the session status, numbers only."""
    clean = {}
    for name, cast in STAT_FIELDS.items():
        value = numbers.get(name) if isinstance(numbers, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
            clean[name] = cast(value)
    with _lock:
        entry = _sessions.get(session_id)
        if entry is not None:
            entry.setdefault("stats", {}).update(clean)


def set_player_running(session_id: str, running: bool) -> None:
    """Whether the session's delayed player records now (it stops when the
    stream breaks and starts again after a reconnect)."""
    with _lock:
        entry = _sessions.get(session_id)
        if entry is not None:
            entry["player_running"] = bool(running)


def _remove_dir(session_id: str):
    with _lock:
        entry = _sessions.get(session_id)
        path = entry.pop("dir", None) if entry else None
    if path:
        shutil.rmtree(path, ignore_errors=True)


def _previous_whisper_call():
    """Label of an abandoned Whisper call of an earlier session that is still
    decoding: starting beside it would load a second model copy."""
    from live import live_whisper
    return live_whisper.outstanding_label()


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


def missing_tool():
    """The fixed message for a tool Live capture runs that this PC lacks, else
    None. Checked at Start: inside the job it would only surface after the
    stream lookup and the Whisper load, as a raw exception."""
    from services import url_media_service
    if shutil.which("ffmpeg") is None:
        return FFMPEG_MISSING
    if not url_media_service.yt_dlp_installed():
        return YTDLP_MISSING
    return None


def check_stream_url(stream_url) -> None:
    """Run on the direct stream URL yt-dlp resolved, before it is
    fetched: lib.url_guard.resolve_public (http/https only, no userinfo,
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


def _stop_for_spend(session_id: str, reason: str, label: str) -> None:
    """Ends the session the way a plain cancel does, keeping the cues shown.
    The label becomes the status line (the job's own final message is
    replaced when the job ends); the longer reason stays in the notes."""
    with _lock:
        entry = _sessions.get(session_id)
        if entry is not None:
            entry["stop_reason"] = clean_message(label)
    add_note(session_id, reason, key="spend-stop")
    background_jobs.request_cancel(session_id)


def _make_target(session_id: str, chat=None):
    """`chat`: the arguments of the session's chat worker, started with the job so it ends with it."""
    def _target(*args, **kwargs):
        worker = _start_chat(session_id, chat) if chat else None
        try:
            # run_live_job stops the stream fetcher (and ffmpeg) before
            # returning, so the proxy outlives every connection it serves.
            with egress_proxy.GuardedProxy() as proxy:
                live_translate.run_live_job(
                    *args, proxy=proxy.url,
                    report_stage=functools.partial(job_stage_service.set_stage, session_id),
                    report_note=functools.partial(add_note, session_id),
                    report_stats=functools.partial(set_stats, session_id),
                    report_player=functools.partial(set_player_running, session_id), **kwargs)
        finally:
            if worker is not None:
                worker.stop()
            job_stage_service.clear_stage(session_id)
            _remove_dir(session_id)
    return _target


def _start_chat(session_id: str, chat: dict):
    with _lock:
        board = (_sessions.get(session_id) or {}).get("chat")
    if board is None:
        return None
    # A translator of its own: the cues' recent-lines context must not fill with chat.
    translator = live_cue_translation.CueTranslator(
        chat["engine"], chat["source_language"], chat["reply_without_thinking"], chat["term_hints"],
        live_chat.NoStreakGuard(chat["spend_guard"]) if chat["spend_guard"] else None)
    worker = live_chat.ChatWorker(session_id, chat["video_id"], board, translator,
                                  chat["source_language"], report_note=functools.partial(add_note, session_id))
    worker.start()
    return worker


def _reap():
    """Removes the directory of a session whose job ended without running
    its target (cancelled while queued, via the jobs page or shutdown: the
    record stays, status "cancelled") or whose record is gone. A job that
    ran removes its own directory when it ends, and a queued or running one
    keeps its directory. A session still being started ("starting":
    reserved, job not registered yet) is skipped, or its fresh reservation
    would be torn down."""
    with _lock:
        ids = [sid for sid, e in _sessions.items() if e.get("dir") and not e.get("starting")]
    for sid in ids:
        job = background_jobs.get_status(sid)
        if job is None or _status(job) not in ("queued", "running"):
            _remove_dir(sid)


def _title_term_hints(principal, drama_id, source_language):
    """A hint function for a chosen title, read-only: a lookup of the
    title's glossary and enabled pack entries that occur in the texts it is
    given. Raises NotFoundError for a title the caller cannot see, like every
    other title-scoped route."""
    ownership_service.require_visible(principal, "drama", drama_id)
    drama = db.get_drama(int(drama_id))
    if drama is None:
        raise NotFoundError("Title not found.")
    return _hint_function(drama.get("series_id"), drama, source_language, glossary_title=drama)


def _series_term_hints(principal, series_id, source_language):
    """Like _title_term_hints for a chosen series: its glossary, and the packs
    of its newest visible title that has its own pack choice (a language
    default alone is not a choice, so with none there are no packs). Raises
    NotFoundError for a series the caller cannot see."""
    ownership_service.require_visible(principal, "series", series_id)
    titles = sorted(db.list_dramas_by_series(int(series_id)),
                    key=lambda d: (d.get("created_at") or "", d["id"]), reverse=True)
    pack_title = next((d for d in titles
                       if language_pack_service.has_title_choice(d["id"])
                       and ownership_service.can_see_drama(principal, d["id"])), None)
    return _hint_function(int(series_id), pack_title, source_language)


def _hint_function(series_id, pack_title, source_language, glossary_title=None):
    """`pack_title`: the title whose packs apply, or None for glossary only.
    `glossary_title`: set when the hints are for one title, so its own terms count too."""
    terms = (db.list_run_glossary_terms(glossary_title) if glossary_title
             else db.list_effective_glossary_terms(series_id) if series_id else [])
    glossary = [t for t in terms
                if t.get("term_original") and t.get("term_translation")]
    # A pack is written for its own source language; a stream in another one
    # would match nothing meaningful.
    packs_apply = pack_title is not None and (pack_title.get("source_language") or "zh") == source_language
    drama = pack_title

    def hints(texts):
        text = "\n".join(t for t in texts if isinstance(t, str))
        found = [(t["term_original"], t["term_translation"], (t.get("notes") or "")[:HINT_NOTE_CHARS])
                 for t in llm_tasks.matching_glossary_terms(text, glossary)]
        if packs_apply and drama is not None:
            for e in language_pack_service.entries_for_text(drama, text, glossary):
                found.append((e["source"], e["en"], "; ".join(
                    p for p in (e["context"], e["note"]) if p)[:HINT_NOTE_CHARS]))
        # Same cap the packs have; a glossary term is listed first, so it is
        # the one kept when the cap cuts.
        return [tuple(re.sub(r"\s+", " ", v) for v in row) for row in found[:language_packs.MAX_PROMPT_ENTRIES]]
    return hints


def start_session(url, source_language="zh", whisper_size="small", segment_seconds: float = 20,
                  overlap_seconds: float = live_translate.DEFAULT_OVERLAP_SECONDS,
                  engine: Optional[str] = None, model: Optional[str] = None,
                  max_minutes: float = DEFAULT_MAX_MINUTES, use_gpu: bool = False,
                  use_saved_cookies: bool = False,
                  reply_without_thinking: bool = True, glossary_drama_id: Optional[int] = None,
                  series_id: Optional[int] = None, delayed_player: bool = False,
                  player_height: int = live_player.DEFAULT_HEIGHT, read_chat: bool = False,
                  spend_limit_usd: float = live_spend_service.DEFAULT_SESSION_LIMIT_USD,
                  principal=None) -> dict:
    """Starts one live capture session; returns {"session_id": ...}.
    glossary_drama_id: a title whose glossary and language packs the stream's
    translation may use (read-only); NotFoundError when the caller cannot see it.
    series_id: instead, a series: its glossary, and the packs of its
    newest title with a pack choice. Not both.
    spend_limit_usd: what a paid engine may cost this session when no monthly
    cap is set (live_spend_service.SessionBudget).
    use_saved_cookies: pass yt-dlp the saved Settings cookies (browser or
    cookies.txt). The router sets it only for a request made at the PC, so
    another device never reads streams as the owner's signed-in account.
    InvalidInputError (422) for a bad/private URL or bad option,
    DependencyUnavailableError (503) for an unresolvable host or missing
    engine key."""
    if player_height not in live_player.HEIGHTS:
        raise InvalidInputError(f"player_height must be one of {', '.join(map(str, live_player.HEIGHTS))}.")
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
    language_problem = whisper_models.whisper_model_language_problem(whisper_size, source_language)
    if language_problem:
        raise InvalidInputError(language_problem)
    segment_seconds = int(_num("segment_seconds", segment_seconds, *SEGMENT_RANGE))
    overlap_seconds = _num("overlap_seconds", overlap_seconds, *OVERLAP_RANGE)
    overlap_seconds = min(overlap_seconds, segment_seconds / 2)
    max_minutes = _num("max_minutes", max_minutes, *MAX_MINUTES_RANGE)
    spend_limit_usd = _num("spend_limit_usd", spend_limit_usd, *live_spend_service.SESSION_LIMIT_RANGE)
    if model is not None and not isinstance(model, str):
        raise InvalidInputError("model must be a string.")
    if glossary_drama_id is not None and series_id is not None:
        raise InvalidInputError("Pick a title or a series for the glossary, not both.")
    chat_video = live_chat.require_video_id(url) if read_chat else None
    missing = missing_tool()
    if missing:
        raise DependencyUnavailableError(missing)
    term_hints = None
    if glossary_drama_id is not None:
        term_hints = _title_term_hints(principal, glossary_drama_id, source_language)
    elif series_id is not None:
        term_hints = _series_term_hints(principal, series_id, source_language)
    _reap()
    # Refuse a second session before building the engine, so a start that
    # would be refused costs no key lookup or model load. Re-checked below
    # under the same lock hold as the reservation.
    with _lock:
        if _active_session_locked() is not None:
            raise ConflictError("A live session is already running. Stop it first.")
        if _previous_whisper_call() is not None:
            raise ConflictError("The previous session's Whisper call is still finishing. "
                                "Try again in a moment.")
    engine_name, eng = _build_engine(engine, model)
    translate_run_service.refuse_when_cap_spent(
        engine_name, bool(settings_service.get("gemini_free_tier")), getattr(eng, "model", None))

    session_id = f"live_{uuid.uuid4().hex}"
    out_dir = storage.new_workdir(session_id)
    # Held until the job is registered: before that nothing owns the folder, so
    # a "clean temp now" in the gap would delete it.
    with storage.holding(out_dir):
        return _start_registered(session_id, out_dir, url, source_language, whisper_size,
                                 segment_seconds, overlap_seconds, max_minutes, eng, engine_name,
                                 use_gpu, use_saved_cookies, reply_without_thinking, term_hints,
                                 player_height if delayed_player else 0, chat_video, model,
                                 spend_limit_usd)


def _start_registered(session_id, out_dir, url, source_language, whisper_size, segment_seconds,
                      overlap_seconds, max_minutes, eng, engine_name, use_gpu,
                      use_saved_cookies, reply_without_thinking, term_hints=None,
                      player_height=0, chat_video=None, requested_model=None,
                      spend_limit_usd=live_spend_service.DEFAULT_SESSION_LIMIT_USD):
    budget = live_spend_service.session_budget(eng, engine_name, spend_limit_usd)
    spend_guard = live_spend_service.LiveSpendGuard(
        eng, engine_name, functools.partial(_stop_for_spend, session_id), budget)
    chat = None
    if chat_video:
        # Its own engine and guard: engines keep per-request usage on the instance, so sharing the
        # cues' one would let overlapping requests miscount the spend the monthly cap reads.
        chat_engine = _build_engine(engine_name, requested_model)[1]
        chat = {"video_id": chat_video, "engine": chat_engine, "source_language": source_language,
                "reply_without_thinking": bool(reply_without_thinking), "term_hints": term_hints,
                "spend_guard": live_spend_service.LiveSpendGuard(
                    chat_engine, engine_name, functools.partial(_stop_for_spend, session_id), budget)}
    with _lock:
        # One session at a time (a design limit):
        # each holds the GPU and an engine for up to max_minutes. The
        # check and the reservation share one lock hold, so two starts
        # can't both pass.
        if _active_session_locked() is not None:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise ConflictError("A live session is already running. Stop it first.")
        if _previous_whisper_call() is not None:
            shutil.rmtree(out_dir, ignore_errors=True)
            raise ConflictError("The previous session's Whisper call is still finishing. "
                                "Try again in a moment.")
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
                                 "segment_seconds": segment_seconds, "delayed_player": bool(player_height),
                                 "source_language": source_language, "stream": stream_ref(url),
                                 "owner_user_id": acting.acting_user_id(),
                                 "chat": live_chat.ChatBoard() if chat else None}
    try:
        started = job_launch.start_job(
            session_id, _make_target(session_id, chat),
            session_id, url, out_dir, segment_seconds, source_language, whisper_size, eng,
            use_gpu=bool(use_gpu), overlap_seconds=overlap_seconds,
            reply_without_thinking=bool(reply_without_thinking), term_hints=term_hints,
            delayed_player=player_height, max_seconds=max_minutes * 60,
            spend_guard=spend_guard,
            stream_url_check=check_stream_url,
            **(settings_service.get_cookie_settings() if use_saved_cookies else {}),
            run_settings=run_settings_service.build(
                engine=engine_name, model=getattr(eng, "model", None),
                whisper_size=whisper_size, source_language=source_language,
                segment_seconds=segment_seconds, overlap_seconds=overlap_seconds,
                use_gpu=bool(use_gpu), reply_without_thinking=bool(reply_without_thinking),
                max_minutes=max_minutes),
            gpu_touching=bool(use_gpu), description=f"Live capture (local Whisper, {engine_name}"
            f"{' ' + eng.model if getattr(eng, 'model', None) else ''})", line_fields=())
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
    """Another user's session is a 404 like an unknown one."""
    with _lock:
        entry = _sessions.get(session_id) if isinstance(session_id, str) else None
    if entry is None or not _visible(principal, session_id, entry):
        raise NotFoundError("No such live session.")
    return job_stage_service.annotate(background_jobs.get_status(session_id))


def player_file(session_id, name, principal=None) -> str:
    """The path of one file of the session's delayed player (a playlist or a
    segment); a 404 for a finished session or any other name. The route is
    local_only(), so the caller is the local owner, who sees every session."""
    _require(session_id, principal)
    with _lock:
        folder = (_sessions.get(session_id) or {}).get("dir")
    path = live_player.served_file(folder, name) if folder else None
    if path is None:
        raise NotFoundError("No such file.")
    return path


def stop_session(session_id, principal=None) -> dict:
    """The first Stop on a capturing session asks it to finish: the audio
    already captured is captioned, then it ends ("finishing": true). Any other
    Stop (a second press, or a session not capturing yet) stops at once: the
    generation is bumped first (so an in-flight chunk's result is discarded),
    then cancel_queued if still queued, else request_cancel. Idempotent on a
    finished session."""
    job = _require(session_id, principal)
    with _lock:
        entry = _sessions.get(session_id) or {}
    ownership_service.require_job_changeable(principal, session_id, entry.get("owner_user_id"))
    if (job or {}).get("status") == "running" and live_pipeline.request_finish(session_id):
        add_note(session_id, FINISHING_NOTE, key="finishing")
        return {"session_id": session_id, "stopping": True, "finishing": True}
    live_translate.bump_generation(session_id)
    if job is None or background_jobs.cancel_queued(session_id):
        _remove_dir(session_id)
    else:
        background_jobs.request_cancel(session_id)
    return {"session_id": session_id, "stopping": True, "finishing": False}


def _status(job) -> str:
    if job is None:
        return "cancelled"
    status = job.get("status")
    if status == "done" and job.get("cancel_requested"):
        return "cancelled"
    return status if status in ("queued", "running", "done", "error", "cancelled") else "error"


_CAPTURE_ERROR_PREFIX = f"{live_translate.LiveCaptureError.__name__}: "


def get_session(session_id, after=0, principal=None) -> dict:
    """{status, message, progress, cues[after:], next_index, the lag figures}.
    Never a traceback, a filesystem path or a key. A cue is added untranslated and
    changes in place when its translation lands, so a client that wants those
    updates asks again from its oldest still-pending cue's id."""
    job = _require(session_id, principal)
    _reap()
    with _lock:
        entry = _sessions.get(session_id) or {}
    after = int(_num("after", after, 0, 10 ** 9))
    status = _status(job)
    if status == "error":
        error = str((job or {}).get("error") or "")
        # The job record names the exception class; for Live's own error the
        # text after it is the whole message.
        if error.startswith(_CAPTURE_ERROR_PREFIX):
            error = error[len(_CAPTURE_ERROR_PREFIX):]
        message = clean_message(error) or "The live session failed."
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
    stop_reason = entry.get("stop_reason")
    return {"session_id": session_id, "status": status, "message": stop_reason or message,
            "stop_reason": stop_reason, "engine": entry.get("engine"), "model": entry.get("model"),
            "progress": float((job or {}).get("progress") or 0.0),
            "notes": list(entry.get("notes") or ()),
            "segment_seconds": int(entry.get("segment_seconds") or 0),
            "source_language": entry.get("source_language"),
            "delayed_player": bool(entry.get("delayed_player")),
            "player_ready": live_player.ready(entry.get("dir")),
            # The page shows the site's own embed while this is true.
            "player_stopped": bool(entry.get("delayed_player")) and entry.get("player_running") is False,
            "chat": entry.get("chat") is not None,
            "stream": entry.get("stream"),
            **{name: cast((entry.get("stats") or {}).get(name, 0)) for name, cast in STAT_FIELDS.items()},
            "cues": out, "next_index": max(after, len(cues))}


def get_chat(session_id, after=0, principal=None) -> dict:
    """{state, message, messages[id > after], next_id}. A message is added untranslated and changes in
    place when its translation lands, so a client asks again from before its oldest pending message."""
    _require(session_id, principal)
    with _lock:
        board = (_sessions.get(session_id) or {}).get("chat")
    if board is None:
        raise NotFoundError("This session is not reading the chat.")
    return board.snapshot(int(_num("after", after, 0, 10 ** 9)))


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
