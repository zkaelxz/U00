"""
services/dub_service.py -- read-only Dub-stage services for one drama,
shared by a later FastAPI /api/dub router (not this file) and mirroring the
Streamlit Dub tab (`tabs/workspace_tab.py`'s `with tab_dub:` block, lines
~5502-5654, and `_render_dub_pacing`, lines ~722-749).

Migration Slice 25. Deliberately out of scope: the Generate job (Slice 26),
voice/character CRUD (lives in the Translate tab, a separate slice),
per-line preview/regenerate (a new feature), and downloading the track (a
later binary-endpoint slice). Speakers and lines come from the database,
not the browser's unsaved session lines.

No Streamlit or FastAPI import: plain dicts out. Nothing secret or
location-revealing is returned (D2): no filesystem path, no GPT-SoVITS URL,
only booleans such as `gpt_sovits_configured`.
"""
import os

import db
import dub
from services import settings_service
from services.service_errors import NotFoundError

NARRATION_LANGUAGE_OPTIONS = ["translation", "original"]

TTS_ENGINES = [
    {"key": "edge_tts", "label": "edge-tts (free, online, more natural)", "requires_internet": True},
    {"key": "offline", "label": "Offline / Piper (fully local, no internet, lower quality)",
     "requires_internet": False},
]


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

    ddir = db.drama_dir(drama_id)
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
    }


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
        pacing = dub.load_pacing(db.drama_dir(drama_id))
        for ln in db.load_line_objects(drama_id):
            rec = dub.pacing_for_line(ln, pacing)
            if not rec:
                continue
            if rec["status"] in counts:
                counts[rec["status"]] += 1
            out_lines.append({"idx": ln.idx, "status": rec["status"], "factor": rec.get("factor") or 1.0,
                              "clip_ms": rec.get("clip_ms"), "window_ms": rec.get("window_ms")})
    return {"available": bool(out_lines), "counts": counts, "lines": out_lines}
