"""
services/review_extras_service.py -- the Review tab's optional AI/media
extras, UI-free (inventory rows R46, R37, R35, R03):

- Merge short adjacent lines (R46): a read-only preview of
  `core.merge_adjacent_short_lines` over COPIES of the drama's lines, then an
  apply that recomputes the merge on the fresh lines under
  restructure_service's lock and refuses (409) unless the drama's line ids AND
  the merge groups are exactly what the preview showed. A "before merge"
  line-history snapshot is taken first; refused while a job runs on the drama.
- Learn my style (R37): `adaptive_style.analyze_edit_patterns` over every
  recorded edit (one synchronous LLM call, like the tab's spinner), saved per
  scope (series, else global). The apply toggle is stored on the profile as
  `"apply": false`, which `adaptive_style.profile_to_prompt_block` honours, so
  every translate path (API run, CLI, line AI, Streamlit) skips a paused
  profile the same way. Reset stores an empty profile, as the tab does (PC
  only). Learn re-reads the stored profile after the LLM call: a pause made
  meanwhile is kept, and a reset/re-learn made meanwhile makes it a 409.
- SenseVoice audio tags (R35): `workspace_job_service.run_sensevoice_job` as a
  `sensevoice_<id>` job (funasr optional; 503 when missing), and the
  `sensevoice_tags.side_by_side` rows.
- Burned-subtitle preview clip (R03): `video_export.render_preview_clip`
  around one line (addressed by permanent id), as a `burnpreview_<id>` job.
  The clip is capped at MAX_CLIP_SECONDS, written to a fixed name in the
  drama's own folder, and served only through `preview_clip_path` (404, and
  the stale clip dropped, once the drama's source video is gone). ffmpeg is
  killed after BURN_PREVIEW_TIMEOUT_SECONDS.

No Streamlit/FastAPI import. Messages never echo keys or paths.
"""
import dataclasses
import datetime
import importlib.util
import json
import math
import os
import shutil
import subprocess
import threading
from typing import Optional

import adaptive_style
import background_jobs
import core as core_module
import db
import sensevoice_tags
import subtitle_formats
import translate_engines
from services import (media_playback_service, restructure_service, settings_service,
                      translate_service, workspace_job_service)
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                      InvalidInputError, NotFoundError, ServiceError,
                                      UnsupportedOperationError)

# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _bounded(name: str, value, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidInputError(f"{name} must be a number.")
    if not lo <= value <= hi:
        raise InvalidInputError(f"{name} must be between {lo:g} and {hi:g}.")
    return float(value)


# ---------------------------------------------------------------------------
# R46: merge short adjacent lines
# ---------------------------------------------------------------------------

MERGE_DEFAULTS = {"min_duration": 1.2, "max_gap": 0.5, "max_chars": 80}
MERGE_LIMITS = {"min_duration": (0.1, 10.0), "max_gap": (0.0, 5.0), "max_chars": (10, 500)}


def _merge_options(min_duration=None, max_gap=None, max_chars=None) -> dict:
    given = {"min_duration": min_duration, "max_gap": max_gap, "max_chars": max_chars}
    out = {}
    for name, value in given.items():
        value = MERGE_DEFAULTS[name] if value is None else value
        lo, hi = MERGE_LIMITS[name]
        out[name] = _bounded(name, value, lo, hi)
    if out["max_chars"] != int(out["max_chars"]):
        raise InvalidInputError("max_chars must be a whole number.")
    out["max_chars"] = int(out["max_chars"])
    return out


def _copies(lines):
    # merge_adjacent_short_lines mutates the Line objects it merges (and
    # renumbers .idx): always hand it copies, never the loaded objects.
    return [dataclasses.replace(ln, orig=dict(ln.orig), merged_ids=[]) for ln in lines]


def _merge_plan(lines, opts):
    """(merged lines, groups) where each group is the ids of 2+ lines that
    become one, first id kept."""
    merged = core_module.merge_adjacent_short_lines(_copies(lines), **opts)
    groups = [[ln.id] + list(ln.merged_ids) for ln in merged if ln.merged_ids]
    return merged, groups


def preview_merge_short(drama_id: int, min_duration=None, max_gap=None,
                        max_chars=None) -> dict:
    """Read-only: what merging would do to the drama's saved lines. Nothing
    is written and the loaded lines are never mutated."""
    _require_drama(drama_id)
    opts = _merge_options(min_duration, max_gap, max_chars)
    lines = db.load_line_objects(drama_id)
    merged, groups = _merge_plan(lines, opts)
    by_id = {ln.id: ln for ln in lines}
    return {
        "drama_id": drama_id, "options": opts,
        "source_line_ids": [ln.id for ln in lines],
        "line_count_before": len(lines), "line_count_after": len(merged),
        "groups": groups,
        "merges": [{"line_id": ln.id, "idx": by_id[ln.id].idx, "merged_line_ids": list(ln.merged_ids),
                    "start": ln.start, "end": ln.end, "zh": ln.zh, "en": ln.en}
                   for ln in merged if ln.merged_ids],
    }


def apply_merge_short(drama_id: int, expected_line_ids, expected_groups, min_duration=None,
                      max_gap=None, max_chars=None) -> dict:
    """Recomputes the merge on the drama's fresh lines and saves it, after a
    "before merge" snapshot. 409 (nothing written) when the line ids or the
    merge groups differ from the preview's, or while a job runs."""
    opts = _merge_options(min_duration, max_gap, max_chars)
    if not isinstance(expected_groups, (list, tuple)) or any(
            not isinstance(g, (list, tuple)) or len(g) < 2
            or any(isinstance(i, bool) or not isinstance(i, int) for i in g)
            for g in expected_groups):
        raise InvalidInputError("expected_groups must be lists of 2 or more line ids.")
    expected_groups = [list(g) for g in expected_groups]
    if not expected_groups:
        raise InvalidInputError("Nothing to merge -- the preview found no short lines to join.")

    def build(work):
        merged, groups = _merge_plan(work, opts)
        if groups != expected_groups:
            raise ConflictError("The lines to merge changed since the preview -- preview again.")
        return merged, [ln for ln in merged if ln.merged_ids]
    out = restructure_service._structural_write(drama_id, expected_line_ids, "before merge", build)
    return {"line_ids": out["line_ids"], "lines": out["lines"], "merged_groups": len(expected_groups)}


# ---------------------------------------------------------------------------
# R37: learn my style
# ---------------------------------------------------------------------------

# Serializes this module's read-modify-writes of a stored style profile
# (learn's final save, the apply toggle, reset) within this process.
_STYLE_LOCK = threading.Lock()


def _scope(drama: dict) -> str:
    return f"series:{drama['series_id']}" if drama.get("series_id") else "global"


def _style_view(drama_id: int, drama: dict, message: Optional[str] = None) -> dict:
    scope = _scope(drama)
    stored = db.get_style_profile(scope)
    pr = (stored or {}).get("profile") or {}
    profile = None
    if pr.get("preferences"):
        profile = {"summary": pr.get("summary") or "", "confidence": pr.get("confidence"),
                   "preferences": [str(p) for p in pr["preferences"]],
                   "sample_count": stored.get("sample_count") or 0,
                   "updated_at": stored.get("updated_at"),
                   "applied": pr.get("apply") is not False}
    return {"drama_id": drama_id, "scope": "series" if drama.get("series_id") else "global",
            "edit_count": len(db.list_edit_samples()),
            "drama_edit_count": len(db.list_edit_samples(drama_id)),
            "min_samples": adaptive_style.MIN_SAMPLES_TO_LEARN,
            "profile": profile, "message": message}


def get_style(drama_id: int) -> dict:
    """The stored learned profile for this drama's scope (never runs the LLM)."""
    return _style_view(drama_id, _require_drama(drama_id))


def _style_engine(drama: dict, engine_name, model, gemini_free_tier):
    gemini_free_tier = settings_service.resolve_gemini_free_tier(gemini_free_tier)
    engine_name = engine_name or drama.get("translation_engine") or "claude"
    if engine_name not in translate_engines.ENGINES:
        raise InvalidInputError("Unknown engine.")
    if engine_name in translate_engines.TRANSLATION_ONLY_ENGINES:
        raise UnsupportedOperationError(
            f"{engine_name} is a translation-only engine and can't learn a style.")
    if (gemini_free_tier and engine_name == "gemini"
            and model in translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS):
        raise UnsupportedOperationError("That model isn't available on Gemini's free tier.")
    api_key = translate_service.resolve_api_key(engine_name)
    if api_key is None:
        raise DependencyUnavailableError(
            f"No {engine_name} key is configured. Set one in Settings first.")
    engine = translate_engines.get_engine(
        engine_name, api_key, model,
        free_tier=engine_name == "gemini" and gemini_free_tier,
        base_url=(settings_service.resolve_key("ollama_url") or None)
        if engine_name == "ollama" else None)
    if not getattr(engine, "supports_reference", False):
        raise UnsupportedOperationError(
            f"{engine_name} can't do this; use an LLM engine (Claude, DeepSeek, Ollama...).")
    return engine_name, engine


def learn_style(drama_id: int, engine_name: str = None, model: str = None,
                gemini_free_tier: bool = None) -> dict:
    """Analyzes every recorded edit (all dramas, as the tab does) and saves
    the learned preferences for this drama's scope. Keeps the profile's
    apply toggle. Nothing is saved when no clear pattern is found."""
    drama = _require_drama(drama_id)
    samples = db.list_edit_samples()
    if len(samples) < adaptive_style.MIN_SAMPLES_TO_LEARN:
        raise UnsupportedOperationError(
            f"Only {len(samples)} edit(s) recorded; at least "
            f"{adaptive_style.MIN_SAMPLES_TO_LEARN} are needed.")
    name, engine = _style_engine(drama, engine_name, model, gemini_free_tier)
    scope = _scope(drama)
    existing = (db.get_style_profile(scope) or {}).get("profile") or {}

    def usage(inp, out):
        db.log_usage(None, name, getattr(engine, "model", name), "adaptive_style", inp, out,
                     translate_engines.estimate_cost_for_engine(engine, inp, out))
    try:
        result = adaptive_style.analyze_edit_patterns(samples, engine, existing_profile=existing,
                                                      usage_cb=usage)
    except ServiceError:
        raise
    except Exception as e:  # engine/network failure: never leak a key
        raise ServiceError("The engine call failed: "
                           + translate_engines.redact_secrets(str(e))[:300]) from None
    prefs = [str(p) for p in (result.get("preferences") or []) if str(p).strip()]
    if not prefs:
        return _style_view(drama_id, drama, message=str(result.get("summary")
                                                        or "No clear patterns found yet.")[:500])
    profile = {"preferences": prefs, "summary": str(result.get("summary") or ""),
               "confidence": str(result.get("confidence") or "low")}
    with _STYLE_LOCK:
        # Re-read after the (slow) LLM call: a pause/resume made meanwhile
        # wins, and a reset (or another learn) made meanwhile is not undone
        # by a result that was built on the profile it replaced.
        current = (db.get_style_profile(scope) or {}).get("profile") or {}
        if (current.get("preferences") or []) != (existing.get("preferences") or []):
            raise ConflictError("The learned style was reset or changed while learning; "
                                "nothing was saved. Learn again if you still want to.")
        if current.get("apply") is False:
            profile["apply"] = False
        db.save_style_profile(scope, profile, sample_count=len(samples))
    return _style_view(drama_id, drama, message=f"Learned {len(prefs)} preference(s).")


def set_style_applied(drama_id: int, apply: bool) -> dict:
    """Turns the learned profile on or off for future translations."""
    if not isinstance(apply, bool):
        raise InvalidInputError("apply must be true or false.")
    drama = _require_drama(drama_id)
    scope = _scope(drama)
    with _STYLE_LOCK:
        stored = db.get_style_profile(scope)
        profile = dict((stored or {}).get("profile") or {})
        if not profile.get("preferences"):
            raise NotFoundError("No learned style to turn on or off yet.")
        if apply:
            profile.pop("apply", None)
        else:
            profile["apply"] = False
        db.save_style_profile(scope, profile, sample_count=stored.get("sample_count") or 0)
    return _style_view(drama_id, drama)


def reset_style(drama_id: int) -> dict:
    """Forgets the learned profile for this drama's scope (as the tab's Reset)."""
    drama = _require_drama(drama_id)
    with _STYLE_LOCK:
        db.save_style_profile(_scope(drama), {"preferences": []}, 0)
    return _style_view(drama_id, drama)


# ---------------------------------------------------------------------------
# R35: SenseVoice audio tags
# ---------------------------------------------------------------------------

SENSEVOICE_JOB_PREFIX = "sensevoice_"  # already in background_jobs.DRAMA_JOB_PREFIXES


def _sensevoice_installed() -> bool:
    return importlib.util.find_spec("funasr") is not None


def _audio_path(drama_id: int) -> Optional[str]:
    try:
        return media_playback_service.resolve_media(drama_id, "audio")[0]
    except NotFoundError:
        return None


def start_sensevoice(drama_id: int) -> dict:
    """Starts the SenseVoice tagging job (writes only audio_tags.json)."""
    _require_drama(drama_id)
    if not _sensevoice_installed():
        raise DependencyUnavailableError("Audio emotion tags need: pip install funasr")
    audio = _audio_path(drama_id)
    if audio is None:
        raise UnsupportedOperationError("This drama has no audio to tag.")
    lines = db.load_line_objects(drama_id)
    if not lines:
        raise UnsupportedOperationError("This drama has no lines yet.")
    job_id = f"{SENSEVOICE_JOB_PREFIX}{drama_id}"
    started = background_jobs.start_job(
        job_id, workspace_job_service.run_sensevoice_job, job_id, drama_id, lines, audio,
        db.drama_dir(drama_id), settings_service.get_use_gpu(), gpu_touching=True,
        description=f"SenseVoice audio tagging (drama #{drama_id})")
    if not started:
        raise ConflictError("Already tagging this drama's audio.")
    return {"job_id": job_id, "drama_id": drama_id, "line_count": len(lines)}


def get_sensevoice(drama_id: int) -> dict:
    """Text-based vs audio tags per line, side by side (never merged)."""
    _require_drama(drama_id)
    lines = db.load_line_objects(drama_id)
    tags = sensevoice_tags.load_audio_tags(db.drama_dir(drama_id))
    rows = sensevoice_tags.side_by_side(lines, db.load_emotions(drama_id), tags) if tags else []
    id_by_number = {ln.idx + 1: ln.id for ln in lines}
    out = [{"line_id": id_by_number.get(r["line"]), "idx": r["line"] - 1, "text": r["text"],
            "text_emotion": r["text_emotion"], "audio_emotion": r["audio_emotion"],
            "audio_events": r["audio_events"], "disagree": bool(r["disagree"])} for r in rows]
    return {"drama_id": drama_id, "installed": _sensevoice_installed(),
            "has_audio": _audio_path(drama_id) is not None,
            "license_note": sensevoice_tags.LICENSE_NOTE, "tagged": len(tags),
            "disagree": sum(1 for r in out if r["disagree"]), "rows": out}


# ---------------------------------------------------------------------------
# R03: burned-subtitle preview clip
# ---------------------------------------------------------------------------

BURN_PREVIEW_JOB_PREFIX = "burnpreview_"  # in background_jobs.DRAMA_JOB_PREFIXES (delete waits for it)
BURN_PREVIEW_FILE = "_burn_preview.mp4"
BURN_PREVIEW_META = "_burn_preview.json"
MAX_CLIP_SECONDS = 30.0
MAX_PAD_SECONDS = 5.0
DEFAULT_PAD_SECONDS = 2.0
BURN_PREVIEW_TIMEOUT_SECONDS = 120.0   # ffmpeg is killed after this (a hung one would block delete)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _video_path(drama_id: int) -> Optional[str]:
    try:
        return media_playback_service.resolve_media(drama_id, "video")[0]
    except NotFoundError:
        return None


def _run_burn_preview_job(job_id, drama_id, video, ass, start, end, meta):
    import video_export
    ddir = db.drama_dir(drama_id)
    out = os.path.join(ddir, BURN_PREVIEW_FILE)
    tmp = os.path.join(ddir, "_burn_preview.part.mp4")
    background_jobs.update_progress(job_id, 0.1, "Rendering the preview clip...")
    try:
        video_export.render_preview_clip(video, ass, tmp, start, end,
                                         timeout=BURN_PREVIEW_TIMEOUT_SECONDS)
        os.replace(tmp, out)
    except TimeoutError:   # an OSError subclass: before the generic clause
        raise RuntimeError("ffmpeg took too long rendering the preview clip and was "
                           "stopped.") from None
    except (subprocess.CalledProcessError, OSError):
        # fixed text: ffmpeg's own error carries paths
        raise RuntimeError("ffmpeg couldn't render the preview clip (it needs libass).") from None
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    with open(os.path.join(ddir, BURN_PREVIEW_META), "w", encoding="utf-8") as f:
        json.dump(meta, f)
    background_jobs.set_result(job_id, {"line_id": meta["line_id"]})


def start_burn_preview(drama_id: int, line_id: int, pad_seconds: float = None,
                       preset: str = None) -> dict:
    """Renders a short clip of the source video around one line with its
    subtitles burned in (an ASS preset, default Clean). The clip is capped at
    MAX_CLIP_SECONDS and replaces any earlier preview of this drama."""
    _require_drama(drama_id)
    if isinstance(line_id, bool) or not isinstance(line_id, int):
        raise InvalidInputError("line_id must be an integer.")
    pad = _bounded("pad_seconds", DEFAULT_PAD_SECONDS if pad_seconds is None else pad_seconds,
                   0.0, MAX_PAD_SECONDS)
    preset = preset or "Clean"
    if preset not in subtitle_formats.ASS_PRESETS:
        raise InvalidInputError("Unknown subtitle style preset.")
    video = _video_path(drama_id)
    if video is None:
        raise UnsupportedOperationError("This drama has no source video to preview on.")
    if not _ffmpeg_available():
        raise DependencyUnavailableError("The preview needs ffmpeg (with libass) installed.")
    lines = db.load_line_objects(drama_id)
    line = next((ln for ln in lines if ln.id == line_id), None)
    if line is None:
        raise NotFoundError(f"No line with id {line_id} in this drama.")
    start, end, ass = media_playback_service.burn_preview_ass(
        lines, line, {"style": subtitle_formats.ASS_PRESETS[preset]}, pad=pad)
    end = min(end, start + MAX_CLIP_SECONDS)
    meta = {"line_id": line.id, "idx": line.idx, "start": start, "end": end, "preset": preset,
            "created_at": datetime.datetime.utcnow().isoformat()}
    job_id = f"{BURN_PREVIEW_JOB_PREFIX}{drama_id}"
    started = background_jobs.start_job(
        job_id, _run_burn_preview_job, job_id, drama_id, video, ass, start, end, meta,
        description=f"Burned-subtitle preview (drama #{drama_id})")
    if not started:
        raise ConflictError("A preview clip is already rendering for this drama.")
    return {"job_id": job_id, "drama_id": drama_id, "line_id": line.id,
            "start": start, "end": end}


def _preview_file(drama_id: int) -> Optional[str]:
    base = os.path.realpath(db.drama_dir(drama_id))
    path = os.path.join(base, BURN_PREVIEW_FILE)
    return path if os.path.isfile(path) and not os.path.islink(path) else None


def _drop_stale_preview(drama_id: int) -> None:
    """The source video was removed: its preview clip must not stay
    streamable, so forget it (best effort; never while one renders)."""
    job = background_jobs.get_status(f"{BURN_PREVIEW_JOB_PREFIX}{drama_id}")
    if job and job.get("status") in ("running", "queued"):
        return
    ddir = db.drama_dir(drama_id)
    for name in (BURN_PREVIEW_FILE, BURN_PREVIEW_META):
        path = os.path.join(ddir, name)
        try:
            if os.path.isfile(path) and not os.path.islink(path):
                os.remove(path)
        except OSError:
            pass


def get_burn_preview_info(drama_id: int) -> dict:
    _require_drama(drama_id)
    meta = None
    has_video = _video_path(drama_id) is not None
    if not has_video:
        _drop_stale_preview(drama_id)
    elif _preview_file(drama_id):
        try:
            with open(os.path.join(db.drama_dir(drama_id), BURN_PREVIEW_META),
                      encoding="utf-8") as f:
                raw = json.load(f)
            meta = {k: raw.get(k) for k in ("line_id", "idx", "start", "end", "preset",
                                            "created_at")}
        except (OSError, ValueError, AttributeError):
            meta = {"line_id": None, "idx": None, "start": None, "end": None, "preset": None,
                    "created_at": None}
    return {"drama_id": drama_id, "has_video": has_video,
            "ffmpeg_available": _ffmpeg_available(),
            "presets": list(subtitle_formats.ASS_PRESETS), "max_clip_seconds": MAX_CLIP_SECONDS,
            "max_pad_seconds": MAX_PAD_SECONDS, "clip": meta}


def preview_clip_path(drama_id: int) -> str:
    """The rendered clip's path, for the router's FileResponse only. 404
    once the drama's source video is gone (the stale clip is dropped)."""
    _require_drama(drama_id)
    if _video_path(drama_id) is None:
        _drop_stale_preview(drama_id)
        raise NotFoundError("No preview clip has been rendered for this drama.")
    path = _preview_file(drama_id)
    if path is None:
        raise NotFoundError("No preview clip has been rendered for this drama.")
    return path
