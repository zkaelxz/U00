"""
services/dub_service.py -- Dub-stage services for one drama, used by
api/routers/dub_routes.py: the config summary, the last run's pacing, the
Generate job (start_dub_run, with apply_dub_result as its on_done hook) and
the finished track (get_dub_track). Voice/character config is
characters_service. Speakers and lines come from the database, not the
browser's unsaved session lines.

No FastAPI import: plain dicts out. Nothing secret or
location-revealing is returned (D2): no filesystem path.
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
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)

NARRATION_LANGUAGE_OPTIONS = ["translation", "original"]

TTS_ENGINES = [{"key": key, "label": label} for key, label in dub.CLONE_ENGINES.items()]

NO_ENGINE_INSTALLED_MESSAGE = "No voice engine is installed. Install one in Diagnostics."

# Python module and display name of each engine's package.
_ENGINE_PACKAGES = {"omnivoice": ("omnivoice", "OmniVoice")}
# OmniVoice needs transformers 5.3+; the install can exist but not load.
_OMNIVOICE_MIN_TRANSFORMERS = (5, 3)


def _drama_path(drama_id: int) -> str:
    """The drama's folder path WITHOUT creating it (db.drama_dir makes the
    directory, which a read-only GET must never do)."""
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def _get_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def get_dub_config(drama_id: int) -> dict:
    """Read-only Dub-stage summary: engine/pacing options, each speaker's
    resolved voice and engine (same logic the Generate button uses),
    whether generating needs the GPU, how many lines are speakable, and
    whether a finished track exists. Raises NotFoundError for an unknown
    drama id."""
    drama = _get_drama(drama_id)
    is_narration = drama.get("content_mode") == "novel_narration"
    narration_language = drama.get("narration_language") or "translation"
    if narration_language not in NARRATION_LANGUAGE_OPTIONS:
        narration_language = "translation"
    narrate_original = is_narration and narration_language == "original"
    source_language = drama.get("source_language") or "zh"

    ddir = _drama_path(drama_id)
    lines = db.load_line_objects(drama_id)
    chars = db.list_characters(drama_id)
    by_label = {c["speaker_label"]: c for c in chars}

    speaker_labels = sorted({ln.speaker for ln in lines if ln.speaker})
    clone_map = dub.clone_map_from_characters(chars, ddir, speaker_labels=speaker_labels)

    speakers = []
    for label in speaker_labels:
        clone = clone_map.get(label)
        character = by_label.get(label)
        speakers.append({
            "speaker_label": label,
            "character_name": (character or {}).get("character_name"),
            "engine": _shown_engine(character, clone),
            "has_clone_ref": bool(clone and clone.get("ref_audio") and os.path.exists(clone["ref_audio"])),
            "clone_warning": clone_setup_warning(character, clone),
        })

    text_field = "zh" if narrate_original else "en"
    track_name = "narration_track.wav" if is_narration else "dub_track.wav"
    return {
        "drama_id": drama_id,
        "content_mode": drama.get("content_mode"),
        "is_narration": is_narration,
        "narration_language": narration_language,
        "narration_language_options": list(NARRATION_LANGUAGE_OPTIONS),
        "source_language": source_language,
        "tts_engines": [{**e, "unavailable_reason": _engine_unavailable_reason(e["key"], narrate_original,
                                                                               source_language)}
                        for e in TTS_ENGINES],
        "default_engine": dub.DEFAULT_CLONE_ENGINE,
        "blocker": config_blocker(chars),
        "defaults": None if is_narration else {
            "max_speedup": dub.DUB_MAX_SPEEDUP, "max_slowdown": dub.DUB_MAX_SLOWDOWN,
            "speedup_range": [1.0, 2.0], "slowdown_range": [0.5, 1.0]},
        "speakers": speakers,
        "gpu_required": dub.clone_map_uses_local_model(clone_map),
        "speakable_line_count": sum(1 for ln in lines if (getattr(ln, text_field) or "").strip()),
        "track_available": os.path.exists(os.path.join(ddir, track_name)),
        "can_keep_background": (not is_narration and _source_audio_path(drama) is not None
                                and _missing_separation_dependency(
                                    drama.get("separation_backend") or "auto") is None),
    }


def _shown_engine(character, clone) -> str:
    """The engine a speaker will use, except that a stored engine that was
    removed is shown as stored so the Dub stage can say which setting is
    stale."""
    stored = (character or {}).get("clone_engine") or ""
    if stored and dub.engine_refusal(stored):
        return stored
    return clone["engine"] if clone else stored


MISSING_CLIP_WARNING = ("This speaker's reference clip is missing from the drama folder, so "
                        "cloning it will fail. Upload or extract a new clip.")


def clone_setup_warning(character, clone):
    """Fixed-text reason a speaker won't be cloned as set up, or None
    (voice-clone setup, parity C09): the stored clip file is gone, or the
    stored engine was removed. A speaker with no clip is not a problem: it
    gets a designed voice. Never names a path or filename."""
    removed = dub.removed_engine_message((character or {}).get("clone_engine") or "")
    if removed:
        return removed
    ref = (clone or {}).get("ref_audio")
    if ref and not os.path.isfile(ref):
        return MISSING_CLIP_WARNING
    return None


def get_dub_track(drama_id: int) -> dict:
    """Server-side file lookup for the finished dub/narration track:
    {path (never returned to clients), name}. Raises NotFoundError for an
    unknown drama or when no track exists (symlinks/escapes count as missing)."""
    drama = _get_drama(drama_id)
    name = "narration_track.wav" if drama.get("content_mode") == "novel_narration" else "dub_track.wav"
    root = _drama_path(drama_id)
    path = os.path.join(root, name)
    real_root = os.path.realpath(root)
    if (os.path.islink(path) or not os.path.isfile(path)
            or os.path.commonpath([real_root, os.path.realpath(path)]) != real_root):
        raise NotFoundError("No dub track available.")
    return {"path": path, "name": name}


def get_dub_pacing(drama_id: int) -> dict:
    """Per-line fit against original timing from the last dub run, as
    `_render_dub_pacing` shows it. status is one of "fit" / "stretched" /
    "overflow" (dub.PACING_*); stale records (line re-dubbed since) are
    excluded. Narration has no pacing, so it reports available False.
    Raises NotFoundError for an unknown drama id."""
    drama = _get_drama(drama_id)
    counts = {dub.PACING_FIT: 0, dub.PACING_STRETCHED: 0, dub.PACING_OVERFLOW: 0}
    out_lines = []
    if drama.get("content_mode") != "novel_narration":
        try:
            pacing = dub.load_pacing(_drama_path(drama_id))
        except Exception:  # corrupt/odd pacing file: treat as unavailable
            pacing = {}
        for ln in db.load_line_objects(drama_id):
            try:
                rec = dub.pacing_for_line(ln, pacing)
                if not rec:
                    continue
                status = rec["status"]
                factor = rec.get("factor") or 1.0
                clip_ms, window_ms = rec.get("clip_ms"), rec.get("window_ms")
            except (AttributeError, KeyError, TypeError):
                continue  # malformed record (not a dict / no status): skip it
            if status in counts:
                counts[status] += 1
            out_lines.append({"idx": ln.idx, "status": status, "factor": factor,
                              "clip_ms": clip_ms, "window_ms": window_ms})
    return {"available": bool(out_lines), "counts": counts, "lines": out_lines}


def _transformers_version():
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
    """Plain reason Generate can't run whatever the engine picked: no engine
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
    missing; shared with `cli.py dub` so both fail with the same text."""
    missing = _missing_engine_dependency(tts_engine)
    if missing:
        raise DependencyUnavailableError(missing)


def require_can_generate(tts_engine: str, characters, narrate_original: bool = False,
                         source_language: str = "zh") -> None:
    """Refuses a run that cannot work, in this order: a removed or unknown
    engine (the run's or a character's) is InvalidInputError; no engine
    installed, or the picked one missing/unable to speak the original
    language, is DependencyUnavailableError. Shared with `cli.py dub`."""
    blockers = dub.engine_blockers(characters, tts_engine)
    if blockers:
        raise InvalidInputError(" ".join(blockers))
    reason = _engine_unavailable_reason(tts_engine, narrate_original, source_language)
    if reason:
        raise DependencyUnavailableError(reason)


def resolve_pacing_limits(max_speedup, max_slowdown) -> tuple:
    """(max_speedup, max_slowdown) with None -> the defaults; InvalidInputError
    when out of range. None, not falsiness, picks the default so an explicit 0
    is refused rather than silently replaced."""
    max_speedup = dub.DUB_MAX_SPEEDUP if max_speedup is None else max_speedup
    max_slowdown = dub.DUB_MAX_SLOWDOWN if max_slowdown is None else max_slowdown
    if not (1.0 <= max_speedup <= 2.0 and 0.5 <= max_slowdown <= 1.0):
        raise InvalidInputError("Pacing limits are out of range.")
    return max_speedup, max_slowdown


def _missing_separation_dependency(backend: str):
    """Fixed-text reason background separation can't run here, or None.
    Never names a path."""
    if importlib.util.find_spec("soundfile") is None or importlib.util.find_spec("numpy") is None:
        return "Keeping background music needs the soundfile and numpy packages."
    modules = {"audio_separator": ("audio_separator",), "demucs": ("demucs",)}.get(
        backend, ("audio_separator", "demucs"))
    if not any(importlib.util.find_spec(m) is not None for m in modules):
        return "Keeping background music needs vocal separation: install audio-separator or demucs."
    return None


def _source_audio_path(drama: dict):
    """Path of the drama's stored audio if the file exists, else None."""
    name = drama.get("audio_filename")
    path = os.path.join(_drama_path(drama["id"]), name) if name else None
    return path if path and os.path.exists(path) else None


def apply_dub_result(drama_id: int, result: dict) -> None:
    """on_done hook body persists a finished dub run
    -- field-scoped
    save_lines(dub_filename [+ start/end for narration]) and status
    "dubbed". The subprocess's returned lines are used only as a source of
    those fields, matched by permanent line id onto the CURRENT database
    lines, so edits made while the job ran (text, flag, flag_note,
    speaker) are never overwritten and a line merged away is skipped."""
    drama = db.get_drama(drama_id)
    if drama is None:
        return
    is_narration = drama.get("content_mode") == "novel_narration"
    fields = ("dub_filename", "start", "end") if is_narration else ("dub_filename",)
    produced = {ln.id: ln for ln in (result or {}).get("lines") or [] if ln.id is not None}
    current = db.load_line_objects(drama_id)
    for ln in current:
        src = produced.get(ln.id)
        if src is None:
            continue
        for f in fields:
            setattr(ln, f, getattr(src, f))
    db.save_lines(drama_id, current, fields=fields)
    db.update_drama(drama_id, status="dubbed")


def track_builder(is_narration: bool):
    """The track-rendering function, looked up per call so the CLI sees
    monkeypatched module attributes."""
    return dub_narration.build_narration_track if is_narration else dub.build_dub_track


def start_dub_run(drama_id: int, tts_engine: str = dub.DEFAULT_CLONE_ENGINE, max_speedup=None,
                  max_slowdown=None, narration_language=None,
                  keep_background: bool = False) -> dict:
    """Starts "Generate dub/narration track" as a background process job
    (`dub_<drama_id>`), with the same voice/clone/pacing inputs as
    `cli dub` (per-speaker voices; drama glossary/locale are not used
    by TTS). The result is applied by an
    on_done hook, not by a UI render loop. Raises NotFoundError (unknown drama), InvalidInputError (bad
    engine/pacing/narration language, a removed engine, a speaker the
    engine can't voice, or nothing speakable),
    DependencyUnavailableError (ffmpeg/engine package missing),
    ConflictError (already running). keep_background (video dub
    only): after the track is built, the original's separated background
    music/ambience is mixed back under it; needs the drama's stored audio
    and a separation backend (503 with fixed text otherwise). Returns
    {"job_id": ...}."""
    drama = _get_drama(drama_id)
    is_narration = drama.get("content_mode") == "novel_narration"
    if narration_language is not None:
        if narration_language not in NARRATION_LANGUAGE_OPTIONS:
            raise InvalidInputError("Unknown narration language.")
        if not is_narration:
            raise InvalidInputError("Narration language only applies to narration dramas.")
    max_speedup, max_slowdown = resolve_pacing_limits(max_speedup, max_slowdown)

    if narration_language is None:
        narration_language = drama.get("narration_language") or "translation"
        if narration_language not in NARRATION_LANGUAGE_OPTIONS:
            narration_language = "translation"
    narrate_original = is_narration and narration_language == "original"
    chars = db.list_characters(drama_id)
    require_can_generate(tts_engine, chars, narrate_original, drama.get("source_language") or "zh")
    lines = db.load_line_objects(drama_id)
    if not any((getattr(ln, "zh" if narrate_original else "en") or "").strip() for ln in lines):
        raise InvalidInputError("No source text to narrate." if narrate_original
                                else "No translated lines to dub yet.")

    background_source = None
    separation_backend = drama.get("separation_backend") or "auto"
    if keep_background:
        if is_narration:
            raise InvalidInputError("Keeping background music only applies to video dubs.")
        background_source = _source_audio_path(drama)
        if background_source is None:
            raise InvalidInputError("This drama has no source audio to take background music from.")
        missing_bg = _missing_separation_dependency(separation_backend)
        if missing_bg:
            raise DependencyUnavailableError(missing_bg)

    job_id = f"dub_{drama_id}"
    job = background_jobs.get_status(job_id)
    if job and job["status"] in ("running", "queued"):
        raise ConflictError(f"A dub job is already running for drama {drama_id}.")
    if is_narration and narration_language != (drama.get("narration_language") or "translation"):
        db.update_drama(drama_id, narration_language=narration_language)

    ddir = db.drama_dir(drama_id)
    source_lang = drama.get("source_language") or "zh"
    clone_map = dub.clone_map_from_characters(
        chars, ddir, default_engine=tts_engine, speaker_labels={ln.speaker or None for ln in lines})

    if is_narration:
        # Keyword-bound so background_jobs' trailing result_queue lands on the
        # worker's result_queue parameter (declared before these options).
        worker = functools.partial(dub_narration.build_narration_subprocess_worker,
                                   narrate_original=narrate_original, source_language=source_lang)
        worker_args = (lines, ddir, clone_map)
    else:
        worker = functools.partial(dub.build_track_subprocess_worker,
                                   background_source=background_source,
                                   separation_backend=separation_backend)
        worker_args = (lines, ddir, clone_map, max_speedup, max_slowdown)

    started = background_jobs.start_process_job(
        job_id, worker, args=worker_args,
        gpu_touching=dub.clone_map_uses_local_model(clone_map),
        description=f"Dub generation (drama #{drama_id})",
        on_done=lambda _jid, result: apply_dub_result(drama_id, result))
    if not started:
        raise ConflictError(f"A dub job is already running for drama {drama_id}.")
    return {"job_id": job_id}
