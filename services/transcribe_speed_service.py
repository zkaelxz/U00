"""
services/transcribe_speed_service.py -- the remembered speed of finished
transcription and speaker-detection runs, per (model, device), that
get_transcribe_config turns into a time estimate.

Every function here is best-effort: a settings hiccup must never fail a run.
"""
from typing import Optional
import db

_SPEED_SETTING = "transcribe_speed"
# Outside this range a reading is a clock glitch or a near-empty file, not a speed.
_SPEED_BOUNDS = (0.01, 1000.0)
_SPEED_MIN_WORK_SECONDS = 5.0
# The median of this many recent runs is the estimate: one slow run (a busy
# PC) doesn't move it, and an old run from before a driver update ages out.
_SPEED_RUNS_KEPT = 5
# Stages of a run that are worth remembering; anything else is dropped.
_STAGE_KEYS = ("separate", "load", "decode_vad", "transcribe", "align")
# Diarization history lives in the same setting under this stand-in for the
# model name (never a Whisper size), so it is kept per device like the rest.
_DIARIZE_SPEED_KEY = "diarize"
# Speaker detection time swings with speaker count and overlap more than
# Whisper's does, so fewer runs than this keep the fixed range.
_DIARIZE_MIN_RUNS = 3


def _speed_key(model: str, on_gpu: bool) -> str:
    return f"{model}|{'gpu' if on_gpu else 'cpu'}"


def _valid_speed(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and _SPEED_BOUNDS[0] <= value <= _SPEED_BOUNDS[1])


def _recorded_runs(model: str, on_gpu: bool) -> list:
    """The remembered runs of this (model, device), oldest first, each
    {"speed", "stages"}. A bare number is the single-value format earlier
    versions stored. Never raises."""
    try:
        stored = db.get_app_setting(_SPEED_SETTING, {})
        entry = stored.get(_speed_key(model, on_gpu)) if isinstance(stored, dict) else None
        if isinstance(entry, dict):
            entry = entry.get("runs")
        elif entry is not None:
            entry = [{"speed": entry}]
        runs = []
        for run in entry if isinstance(entry, list) else []:
            if isinstance(run, dict) and _valid_speed(run.get("speed")):
                stages = run.get("stages")
                runs.append({"speed": float(run["speed"]),
                             "stages": {k: float(v) for k, v in stages.items()
                                        if k in _STAGE_KEYS and isinstance(v, (int, float))
                                        and not isinstance(v, bool) and v >= 0}
                             if isinstance(stages, dict) else {}})
        return runs[-_SPEED_RUNS_KEPT:]
    except Exception:
        return []


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def measured_transcribe_speed(model: str, on_gpu: bool) -> Optional[float]:
    """Seconds of audio transcribed per second of work: the median of the
    last few finished runs of this (model, device), or None. Never raises."""
    runs = _recorded_runs(model, on_gpu)
    return round(_median([r["speed"] for r in runs]), 4) if runs else None


def measured_transcribe_runs(model: str, on_gpu: bool) -> int:
    """How many recorded runs back measured_transcribe_speed."""
    return len(_recorded_runs(model, on_gpu))


def measured_stage_seconds(model: str, on_gpu: bool) -> dict:
    """Median seconds per stage over the recorded runs that have it."""
    runs = _recorded_runs(model, on_gpu)
    return {k: round(_median([r["stages"][k] for r in runs if k in r["stages"]]), 3)
            for k in _STAGE_KEYS if any(k in r["stages"] for r in runs)}


def measured_diarize_speed(on_gpu: bool) -> Optional[float]:
    """Seconds of audio diarized per second of work (median of the recent
    runs on this device), or None until there are enough runs to trust."""
    runs = _recorded_runs(_DIARIZE_SPEED_KEY, on_gpu)
    if len(runs) < _DIARIZE_MIN_RUNS:
        return None
    return round(_median([r["speed"] for r in runs]), 4)


def measured_diarize_runs(on_gpu: bool) -> int:
    """How many recorded runs back measured_diarize_speed."""
    return len(_recorded_runs(_DIARIZE_SPEED_KEY, on_gpu))


def record_diarize_speed(on_gpu: bool, audio_seconds, work_seconds) -> None:
    """Appends a finished speaker-detection run; same rules and failure
    behaviour as record_transcribe_speed."""
    record_transcribe_speed(_DIARIZE_SPEED_KEY, on_gpu, audio_seconds, work_seconds)


def record_transcribe_speed(model: str, on_gpu: bool, audio_seconds, work_seconds,
                            stage_seconds=None) -> None:
    """Appends a finished run's speed (and its per-stage seconds) to the
    last few for this (model, device). Ignores non-numeric or out-of-range
    readings; never raises, so a settings hiccup cannot fail a finished
    transcription."""
    try:
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (audio_seconds, work_seconds)):
            return
        if work_seconds < _SPEED_MIN_WORK_SECONDS or audio_seconds <= 0:
            return
        speed = float(audio_seconds) / float(work_seconds)
        if not _valid_speed(speed):
            return
        stages = {k: round(float(v), 3) for k, v in (stage_seconds or {}).items()
                  if k in _STAGE_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool)
                  and v >= 0}
        runs = _recorded_runs(model, on_gpu) + [{"speed": round(speed, 4), "stages": stages}]
        stored = db.get_app_setting(_SPEED_SETTING, {})
        stored = dict(stored) if isinstance(stored, dict) else {}
        stored[_speed_key(model, on_gpu)] = {"runs": runs[-_SPEED_RUNS_KEPT:]}
        db.set_app_setting(_SPEED_SETTING, stored)
    except Exception:
        pass
