"""
services/dub_service.py -- read-only Dub-stage services for one drama,
shared by a later FastAPI /api/dub router (not this file) and mirroring the
Streamlit Dub tab (`tabs/workspace_tab.py`'s `with tab_dub:` block, lines
~5502-5654, and `_render_dub_pacing`, lines ~722-749).

Migration Slice 25. Deliberately out of scope: the Generate job (Slice 26),
voice/character CRUD (lives in the Translate tab, a separate slice),
per-line preview/regenerate (a new feature). Track download is Slice 53
(`get_dub_track`). Speakers and lines come from the database,
not the browser's unsaved session lines.

No Streamlit or FastAPI import: plain dicts out. Nothing secret or
location-revealing is returned (D2): no filesystem path, no GPT-SoVITS URL,
only booleans such as `gpt_sovits_configured`.
"""
import functools
import importlib.util
import os
import shutil

import background_jobs
import db
import dub
from services import settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError)

NARRATION_LANGUAGE_OPTIONS = ["translation", "original"]

TTS_ENGINES = [
    {"key": "edge_tts", "label": "edge-tts (free, online, more natural)", "requires_internet": True},
    {"key": "offline", "label": "Offline / Piper (fully local, no internet, lower quality)",
     "requires_internet": False},
]


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

    voice_pool = (dub.DEFAULT_VOICE_POOL_BY_LANGUAGE.get(source_language, dub.DEFAULT_VOICE_POOL)
                  if narrate_original else dub.DEFAULT_VOICE_POOL)
    voice_map = {c["speaker_label"]: c["tts_voice"] for c in chars if c.get("tts_voice")}
    offline_voice_map = {c["speaker_label"]: c["offline_voice"] for c in chars if c.get("offline_voice")}
    speaker_labels = sorted({ln.speaker for ln in lines if ln.speaker})
    voice_map = dub.fill_missing_voices(voice_map, speaker_labels, voice_pool)
    offline_voice_map = dub.fill_missing_voices(
        offline_voice_map, speaker_labels, dub.DEFAULT_OFFLINE_VOICE_POOL)
    gpt_sovits_url = settings_service.resolve_key("gpt_sovits_url")
    clone_map = dub.clone_map_from_characters(
        chars, ddir, gpt_sovits_url=gpt_sovits_url or None, ref_language=source_language)

    speakers = []
    for label in speaker_labels:
        clone = clone_map.get(label)
        speakers.append({
            "speaker_label": label,
            "character_name": (by_label.get(label) or {}).get("character_name"),
            "edge_voice": voice_map.get(label),
            "offline_voice": offline_voice_map.get(label),
            "engine": clone["engine"] if clone else "edge",
            "has_clone_ref": bool(clone and clone.get("ref_audio") and os.path.exists(clone["ref_audio"])),
            "clone_warning": clone_setup_warning(by_label.get(label), clone),
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
        "tts_engines": [dict(e) for e in TTS_ENGINES],
        "defaults": None if is_narration else {
            "max_speedup": dub.DUB_MAX_SPEEDUP, "max_slowdown": dub.DUB_MAX_SLOWDOWN,
            "speedup_range": [1.0, 2.0], "slowdown_range": [0.5, 1.0]},
        "speakers": speakers,
        "gpu_required": dub.clone_map_uses_local_model(clone_map),
        "speakable_line_count": sum(1 for ln in lines if (getattr(ln, text_field) or "").strip()),
        "track_available": os.path.exists(os.path.join(ddir, track_name)),
        "gpt_sovits_configured": bool(gpt_sovits_url),
        "can_keep_background": (not is_narration and _source_audio_path(drama) is not None
                                and _missing_separation_dependency(
                                    drama.get("separation_backend") or "auto") is None),
    }


NO_CLONE_SOURCE_WARNING = ("{engine} is chosen, but this speaker has no reference clip or voice "
                           "description, so it will use the plain TTS voice. Upload or extract a "
                           "clip, apply one from the voice bank, or describe a voice.")
MISSING_CLIP_WARNING = ("This speaker's reference clip is missing from the drama folder, so "
                        "cloning it will fail. Upload or extract a new clip.")


def clone_setup_warning(character, clone):
    """Fixed-text reason a speaker won't be cloned as set up, or None
    (voice-clone setup, parity C09): a clone engine is chosen but
    dub.clone_map_from_characters found no clip or voice design (it
    silently falls back to plain TTS), or the stored clip file is gone.
    Chatterbox with no clip is a real voice, not a fallback. Never
    names a path or filename."""
    engine = (character or {}).get("clone_engine") or ""
    if clone is None:
        if engine and engine in dub.CLONE_ENGINES:
            return NO_CLONE_SOURCE_WARNING.format(engine=dub.CLONE_ENGINES[engine])
        return None
    ref = clone.get("ref_audio")
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


def _missing_engine_dependency(tts_engine: str):
    """Fixed-text reason the requested fallback engine (or ffmpeg) can't run
    here, or None. Never names a path."""
    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not installed or not on PATH, which dubbing requires."
    module, label = ("edge_tts", "edge-tts") if tts_engine == "edge_tts" else ("piper", "piper-tts")
    if importlib.util.find_spec(module) is None:
        return f"The {label} package is not installed."
    return None


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
    """on_done hook body (Migration Slice 26): persists a finished dub run
    the way the Dub tab's "done" branch does -- field-scoped
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


def start_dub_run(drama_id: int, tts_engine: str = "edge_tts", max_speedup=None,
                  max_slowdown=None, narration_language=None,
                  keep_background: bool = False) -> dict:
    """Starts the Dub tab's "Generate dub/narration track" as a background
    process job (`dub_<drama_id>`), with the same voice/clone/emotion/pacing
    inputs as the tab and `cli dub` (per-speaker voices, filled from the
    pools; GPT-SoVITS URL from settings; drama glossary/locale are not used
    by TTS). The result is applied by an on_done hook, not by a UI render
    loop. Raises NotFoundError (unknown drama), InvalidInputError (bad
    engine/pacing/narration language, or nothing speakable),
    DependencyUnavailableError (ffmpeg/engine package missing),
    ConflictError (already running). keep_background (Step 95, video dub
    only): after the track is built, the original's separated background
    music/ambience is mixed back under it; needs the drama's stored audio
    and a separation backend (503 with fixed text otherwise). Returns
    {"job_id": ...}."""
    drama = _get_drama(drama_id)
    if tts_engine not in {e["key"] for e in TTS_ENGINES}:
        raise InvalidInputError("Unknown TTS engine.")
    is_narration = drama.get("content_mode") == "novel_narration"
    if narration_language is not None:
        if narration_language not in NARRATION_LANGUAGE_OPTIONS:
            raise InvalidInputError("Unknown narration language.")
        if not is_narration:
            raise InvalidInputError("Narration language only applies to narration dramas.")
    max_speedup = dub.DUB_MAX_SPEEDUP if max_speedup is None else max_speedup
    max_slowdown = dub.DUB_MAX_SLOWDOWN if max_slowdown is None else max_slowdown
    if not (1.0 <= max_speedup <= 2.0 and 0.5 <= max_slowdown <= 1.0):
        raise InvalidInputError("Pacing limits are out of range.")

    if narration_language is None:
        narration_language = drama.get("narration_language") or "translation"
        if narration_language not in NARRATION_LANGUAGE_OPTIONS:
            narration_language = "translation"
    narrate_original = is_narration and narration_language == "original"
    lines = db.load_line_objects(drama_id)
    if not any((getattr(ln, "zh" if narrate_original else "en") or "").strip() for ln in lines):
        raise InvalidInputError("No source text to narrate." if narrate_original
                                else "No translated lines to dub yet.")
    missing = _missing_engine_dependency(tts_engine)
    if missing:
        raise DependencyUnavailableError(missing)

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
    pool = (dub.DEFAULT_VOICE_POOL_BY_LANGUAGE.get(source_lang, dub.DEFAULT_VOICE_POOL)
            if narrate_original else dub.DEFAULT_VOICE_POOL)
    chars = db.list_characters(drama_id)
    voice_map = {c["speaker_label"]: c["tts_voice"] for c in chars if c.get("tts_voice")}
    offline_voice_map = {c["speaker_label"]: c["offline_voice"] for c in chars
                         if c.get("offline_voice")}
    speakers = {ln.speaker for ln in lines if ln.speaker}
    voice_map = dub.fill_missing_voices(voice_map, speakers, pool)
    offline_voice_map = dub.fill_missing_voices(
        offline_voice_map, speakers, dub.DEFAULT_OFFLINE_VOICE_POOL)
    clone_map = dub.clone_map_from_characters(
        chars, ddir, gpt_sovits_url=settings_service.resolve_key("gpt_sovits_url") or None,
        ref_language=source_lang)

    started = background_jobs.start_process_job(
        job_id,
        # Keyword-bound so background_jobs' trailing result_queue lands on the
        # worker's result_queue parameter (declared before these options).
        functools.partial(dub.build_track_subprocess_worker, narrate_original=narrate_original,
                          source_language=source_lang, background_source=background_source,
                          separation_backend=separation_backend),
        args=(lines, ddir, voice_map, pool[0], clone_map, tts_engine, is_narration,
              db.load_emotions(drama_id), max_speedup, max_slowdown, offline_voice_map),
        gpu_touching=dub.clone_map_uses_local_model(clone_map),
        description=f"Dub generation (drama #{drama_id})",
        on_done=lambda _jid, result: apply_dub_result(drama_id, result))
    if not started:
        raise ConflictError(f"A dub job is already running for drama {drama_id}.")
    return {"job_id": job_id}
