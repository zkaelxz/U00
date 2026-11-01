"""
services/listen_service.py -- the Reader's "Listen" for a narrated title: whether
a narration can be made, starting it, and the finished track. One voice engine,
the title's narration language, no pacing limits. The job (`dub_<id>`), its
process worker (dub_narration) and the track file (narration_track.wav) keep
the names the narration path always had, so existing jobs and tracks still work.

No FastAPI import: plain dicts out, no filesystem path.
"""
import functools
import importlib.metadata
import importlib.util
import os
import shutil

import background_jobs
import db
import dub
import dub_narration
from jobs import job_launch
from services import ownership_service
from lib.errors import ConflictError, DependencyUnavailableError, InvalidInputError, NotFoundError

NARRATION_LANGUAGE_OPTIONS = ["translation", "original"]

TTS_ENGINES = [{"key": key, "label": label} for key, label in dub.CLONE_ENGINES.items()]

NO_ENGINE_INSTALLED_MESSAGE = "No voice engine is installed. Install one in Diagnostics."

_NOT_NARRATED = "Listen is only for narrated novels."
TRACK_NAME = "narration_track.wav"

# Python module and display name of each engine's package.
_ENGINE_PACKAGES = {"omnivoice": ("omnivoice", "OmniVoice")}
# OmniVoice needs transformers 5.3+; the install can exist but not load.
_OMNIVOICE_MIN_TRANSFORMERS: tuple[int, ...] = (5, 3)


def _drama_path(drama_id: int) -> str:
    """The drama's folder path WITHOUT creating it (db.drama_dir makes the
    directory, which a read-only GET must never do)."""
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def _require_narrated(drama_id: int) -> dict:
    drama = ownership_service.require_drama(drama_id)
    if drama.get("content_mode") != "novel_narration":
        raise NotFoundError(_NOT_NARRATED)
    return drama


def _narration_language(drama: dict) -> str:
    language = drama.get("narration_language") or "translation"
    return language if language in NARRATION_LANGUAGE_OPTIONS else "translation"


def get_listen_config(drama_id: int) -> dict:
    """What Listen needs to show: the engines (each with why it can't run here),
    the language options, a blocker that applies whatever engine is picked, how
    many lines are speakable, whether a track exists and whether the GPU is used.
    NotFoundError for an unknown title or one that is not a narrated novel."""
    drama = _require_narrated(drama_id)
    narration_language = _narration_language(drama)
    narrate_original = narration_language == "original"
    source_language = drama.get("source_language") or "zh"

    ddir = _drama_path(drama_id)
    lines = db.load_line_objects(drama_id)
    chars = db.list_characters(drama_id)
    speaker_labels = sorted({ln.speaker for ln in lines if ln.speaker})
    clone_map = dub.clone_map_from_characters(chars, ddir, speaker_labels=speaker_labels)

    text_field = "zh" if narrate_original else "en"
    return {
        "drama_id": drama_id,
        "narration_language": narration_language,
        "narration_language_options": list(NARRATION_LANGUAGE_OPTIONS),
        "tts_engines": [{**e, "unavailable_reason": _engine_unavailable_reason(e["key"], narrate_original,
                                                                               source_language)}
                        for e in TTS_ENGINES],
        "default_engine": dub.DEFAULT_CLONE_ENGINE,
        "blocker": config_blocker(chars),
        "gpu_required": dub.clone_map_uses_local_model(clone_map),
        "speakable_line_count": sum(1 for ln in lines if (getattr(ln, text_field) or "").strip()),
        "track_available": os.path.exists(os.path.join(ddir, TRACK_NAME)),
    }


def get_listen_track(drama_id: int) -> dict:
    """Server-side file lookup for the finished narration track: {path (never
    returned to clients), name}. NotFoundError for an unknown or non-narrated
    title, or when no track exists (symlinks/escapes count as missing)."""
    _require_narrated(drama_id)
    root = _drama_path(drama_id)
    path = os.path.join(root, TRACK_NAME)
    real_root = os.path.realpath(root)
    if (os.path.islink(path) or not os.path.isfile(path)
            or os.path.commonpath([real_root, os.path.realpath(path)]) != real_root):
        raise NotFoundError("No narration track available.")
    return {"path": path, "name": TRACK_NAME}


def _transformers_version() -> tuple[int, ...] | None:
    try:
        parts = importlib.metadata.version("transformers").split(".")[:2]
        return tuple(int("".join(ch for ch in p if ch.isdigit()) or 0) for p in parts)
    except importlib.metadata.PackageNotFoundError:
        return None


def _engine_install_problem(engine: str):
    """Fixed-text reason `engine` can't run on this PC, or None. Never names
    a path."""
    module, label = _ENGINE_PACKAGES[engine]
    if importlib.util.find_spec(module) is None:
        return f"{label} is not installed. Install it in Diagnostics."
    if engine == "omnivoice" and (_transformers_version() or (0, 0)) < _OMNIVOICE_MIN_TRANSFORMERS:
        return "OmniVoice needs a newer transformers than is installed. Check Diagnostics."
    return None


def engine_install_problem(engine: str):
    """Public name of the install check, for the self-test."""
    return _engine_install_problem(engine)


def _missing_engine_dependency(tts_engine: str):
    """Fixed-text reason the requested engine (or ffmpeg) can't run here, or
    None. Never names a path."""
    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not installed or not on PATH, which dubbing requires."
    return _engine_install_problem(tts_engine)


def _engine_unavailable_reason(engine: str, narrate_original: bool, source_language: str):
    """_missing_engine_dependency plus, for original-language narration, the
    engines not confirmed to speak that language."""
    missing = _missing_engine_dependency(engine)
    if missing:
        return missing
    if narrate_original and not dub.clone_engine_supports_language(engine, source_language):
        return (f"{_ENGINE_PACKAGES[engine][1]} can't speak this title's original language. "
                "Narrate the translation instead.")
    return None


def config_blocker(characters):
    """Plain reason Listen can't run whatever the engine picked: no engine
    installed at all, or a character stored with a removed engine. None when
    the choice of engine decides it."""
    removed = dub.engine_blockers(characters, dub.DEFAULT_CLONE_ENGINE)
    if removed:
        return " ".join(removed)
    if all(_engine_install_problem(e["key"]) for e in TTS_ENGINES):
        return NO_ENGINE_INSTALLED_MESSAGE
    return None


def require_engine_dependency(tts_engine: str) -> None:
    """DependencyUnavailableError when ffmpeg or the engine's package is
    missing, so every caller fails with the same text."""
    missing = _missing_engine_dependency(tts_engine)
    if missing:
        raise DependencyUnavailableError(missing)


def require_can_generate(tts_engine: str, characters, narrate_original: bool = False,
                         source_language: str = "zh") -> None:
    """Refuses a run that cannot work, in this order: a removed or unknown
    engine (the run's or a character's) is InvalidInputError; no engine
    installed, or the picked one missing/unable to speak the original
    language, is DependencyUnavailableError."""
    blockers = dub.engine_blockers(characters, tts_engine)
    if blockers:
        raise InvalidInputError(" ".join(blockers))
    reason = _engine_unavailable_reason(tts_engine, narrate_original, source_language)
    if reason:
        raise DependencyUnavailableError(reason)


def apply_narration_result(drama_id: int, result: dict) -> None:
    """on_done hook body: persists a finished narration run -- field-scoped
    save_lines(dub_filename, start, end) and status "dubbed". The subprocess's
    returned lines are only a source of those fields, matched by permanent line
    id onto the CURRENT database lines, so edits made while the job ran (text,
    flag, flag_note, speaker) are never overwritten and a line merged away is
    skipped. A run whose lines were all replaced meanwhile (Chunk & tag) matches
    nothing and leaves the title's status alone: "dubbed" would hide the new
    lines from the translate flow."""
    if db.get_drama(drama_id) is None:
        return
    fields = ("dub_filename", "start", "end")
    produced = {ln.id: ln for ln in (result or {}).get("lines") or [] if ln.id is not None}
    current = db.load_line_objects(drama_id)
    matched = False
    for ln in current:
        src = produced.get(ln.id)
        if src is None:
            continue
        matched = True
        for f in fields:
            setattr(ln, f, getattr(src, f))
    if not matched:
        return
    db.save_lines(drama_id, current, fields=fields)
    db.update_drama(drama_id, status="dubbed")


def narration_job_id(drama_id: int) -> str:
    # Kept as dub_<id> so jobs started before the Dub stage was removed still resolve.
    return f"dub_{drama_id}"


def start_listen(drama_id: int, tts_engine: str, narration_language=None) -> dict:
    """Starts the narration track as a background process job (`dub_<id>`);
    the result is applied by an on_done hook. {"job_id"}. NotFoundError (unknown
    or non-narrated title), InvalidInputError (unknown engine or language, a
    removed engine, nothing to narrate), DependencyUnavailableError (ffmpeg or
    the engine package missing), ConflictError (already running)."""
    drama = _require_narrated(drama_id)
    if not tts_engine:
        raise InvalidInputError("No voice engine is available.")
    if narration_language is not None and narration_language not in NARRATION_LANGUAGE_OPTIONS:
        raise InvalidInputError("Unknown narration language.")
    if narration_language is None:
        narration_language = _narration_language(drama)
    narrate_original = narration_language == "original"
    chars = db.list_characters(drama_id)
    source_lang = drama.get("source_language") or "zh"
    require_can_generate(tts_engine, chars, narrate_original, source_lang)
    lines = db.load_line_objects(drama_id)
    if not any((getattr(ln, "zh" if narrate_original else "en") or "").strip() for ln in lines):
        raise InvalidInputError("No source text to narrate." if narrate_original
                                else "No translated lines to dub yet.")

    job_id = narration_job_id(drama_id)
    job = background_jobs.get_status(job_id)
    if job and job["status"] in ("running", "queued"):
        raise ConflictError(f"A dub job is already running for title {drama_id}.")
    if narration_language != (drama.get("narration_language") or "translation"):
        db.update_drama(drama_id, narration_language=narration_language)

    ddir = db.drama_dir(drama_id)
    clone_map = dub.clone_map_from_characters(
        chars, ddir, default_engine=tts_engine, speaker_labels={ln.speaker or None for ln in lines})
    # Keyword-bound so background_jobs' trailing result_queue lands on the
    # worker's result_queue parameter (declared before these options).
    worker = functools.partial(dub_narration.build_narration_subprocess_worker,
                               narrate_original=narrate_original, source_language=source_lang)
    started = job_launch.start_process_job(
        job_id, worker, args=(lines, ddir, clone_map),
        gpu_touching=dub.clone_map_uses_local_model(clone_map),
        description=f"Dub generation (title #{drama_id})",
        kill_whole_tree=True, start_method="spawn",
        on_done=lambda _jid, result: apply_narration_result(drama_id, result),
        line_fields=("dub_filename", "start", "end"))
    if not started:
        raise ConflictError(f"A dub job is already running for title {drama_id}.")
    return {"job_id": job_id}
