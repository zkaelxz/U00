"""
raw_transcript.py -- the untouched output of each transcription run, kept
on disk next to the drama (R1-lite).

Lines get edited, merged, split and re-transcribed; this file never does.
The first run writes drama_dir/raw_transcript.json; every later run writes
raw_transcript.<timestamp>.json instead -- nothing here ever overwrites or
rewrites an existing file. The Review step reads the newest one back to
show "what did the transcription originally say for this line?" and to
restore a single line's text from it.

Each file also carries a "settings" object (build_run_settings): what the
run was configured with, so two runs can be compared later. Files written
before it existed have no "settings" key; every reader must treat it as
optional.
"""

import datetime
import functools
import glob
import json
import os
import subprocess

import sensitivity_preset as presets

RAW_NAME = "raw_transcript.json"

# The only keys a run's "settings" object may hold. Everything is a number,
# a boolean, a short enum-like string or a length: never a key, token, path,
# URL or prompt text.
SETTINGS_KEYS = (
    "asr_backend", "whisper_size", "local_model_path_set", "language", "transcript_mode",
    "alignment_method", "min_silence_ms", "vad_threshold", "sensitivity_preset", "beam_size",
    "hallucination_silence_sec", "min_pause_sec", "whisper_fast_mode", "use_groq", "separate_vocals_first",
    "separation_backend", "realign_long_segments", "mixed_languages", "vad_refine_timing",
    "expected_speakers", "min_speakers", "max_speakers", "gpu_requested", "gpu_used",
    "gpu_fell_back_to_cpu", "gpu_max_parallel", "gpu_limit_enabled", "initial_prompt_used",
    "initial_prompt_chars", "git_commit", "faster_whisper_version", "ctranslate2_version",
    "stage_seconds",
)


@functools.lru_cache(maxsize=1)
def _git_commit() -> str:
    """Short sha of the code, or "" when git or the checkout isn't there
    (an installed copy): a missing sha must never fail a run."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"], cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=3, check=False)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _package_version(name: str) -> str:
    try:
        from importlib import metadata
        return metadata.version(name)
    except Exception:
        return ""


def current_gpu_app_settings() -> dict:
    """The two app-level GPU settings, read now so a run can freeze them at
    its start. Falls back to the defaults on any error."""
    try:
        import background_jobs
        return {"gpu_max_parallel": background_jobs.get_gpu_max_parallel(),
                "gpu_limit_enabled": background_jobs.get_gpu_limit_enabled()}
    except Exception:
        return {"gpu_max_parallel": 1, "gpu_limit_enabled": True}


def build_run_settings(*, asr_backend="", whisper_size="", local_model_path=None, language="",
                       transcript_mode="", alignment_method="", min_silence_ms=None,
                       vad_threshold=None, sensitivity_preset="normal", beam_size=None,
                       hallucination_silence_sec=None, min_pause_sec=None, whisper_fast_mode=False, use_groq=False, separate_vocals_first=False,
                       separation_backend="", realign_long_segments=False, mixed_languages=False,
                       vad_refine_timing=False, expected_speakers=None, min_speakers=None,
                       max_speakers=None, use_gpu=False, gpu_fallback_msgs=(),
                       gpu_max_parallel=None, gpu_limit_enabled=None, initial_prompt="",
                       stage_seconds=None) -> dict:
    """The "settings" object for one run. Every key is written out here by
    hand (never from a drama row or a **dict), so a secret, a path or the
    prompt text cannot reach the file by accident: the prompt is reduced to
    its length, the model folder to a flag, and the GPU fallback reasons
    (which can name paths) to one boolean."""
    fell_back = bool(use_gpu) and bool(gpu_fallback_msgs)
    return {
        "asr_backend": asr_backend or "",
        "whisper_size": whisper_size or "",
        "local_model_path_set": bool(local_model_path),
        "language": language or "",
        "transcript_mode": transcript_mode or "",
        "alignment_method": alignment_method or "",
        "min_silence_ms": min_silence_ms,
        "vad_threshold": vad_threshold,
        "sensitivity_preset": presets.normalize(sensitivity_preset),
        "beam_size": beam_size,
        "hallucination_silence_sec": hallucination_silence_sec,
        "min_pause_sec": min_pause_sec,
        "whisper_fast_mode": bool(whisper_fast_mode),
        "use_groq": bool(use_groq),
        "separate_vocals_first": bool(separate_vocals_first),
        "separation_backend": separation_backend or "",
        "realign_long_segments": bool(realign_long_segments),
        "mixed_languages": bool(mixed_languages),
        "vad_refine_timing": bool(vad_refine_timing),
        "expected_speakers": expected_speakers,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
        "gpu_requested": bool(use_gpu),
        "gpu_used": bool(use_gpu) and not fell_back and not use_groq,
        "gpu_fell_back_to_cpu": fell_back,
        "gpu_max_parallel": gpu_max_parallel,
        "gpu_limit_enabled": gpu_limit_enabled,
        "initial_prompt_used": bool((initial_prompt or "").strip()),
        "initial_prompt_chars": len(initial_prompt or ""),
        "git_commit": _git_commit(),
        "faster_whisper_version": _package_version("faster-whisper"),
        "ctranslate2_version": _package_version("ctranslate2"),
        "stage_seconds": {str(k): round(float(v), 2) for k, v in (stage_seconds or {}).items()},
    }


def write_raw_transcript(drama_dir: str, segments, lines, backend: str, model: str = "",
                         language: str = "", mode: str = "", settings: dict = None) -> str:
    """segments: the transcription backend's own output (start/end/text per
    segment). lines: the lines as first created from it (after alignment,
    before any edit) -- saved already, so each has its permanent id.
    Returns the path written. Opens with mode "x", so an existing file is
    never overwritten even if two runs race on the same timestamp.
    settings: build_run_settings()'s result; omitted from the file when None."""
    os.makedirs(drama_dir, exist_ok=True)
    payload = {
        "created_at": datetime.datetime.utcnow().isoformat(),
        "backend": backend,
        "model": model or "",
        "language": language or "",
        "mode": mode or "",
        "text": "\n".join((s.get("text") or "").strip() for s in segments),
        # Word timings are for the split step only; they are not part of the saved transcript.
        "segments": [{k: v for k, v in s.items() if k != "words"} for s in segments],
        "lines": [{"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start,
                   "end": ln.end, "text": ln.zh} for ln in lines],
    }
    if settings is not None:
        payload["settings"] = settings
    path = os.path.join(drama_dir, RAW_NAME)
    n = 0
    while True:
        try:
            with open(path, "x", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            return path
        except FileExistsError:
            stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
            n += 1
            path = os.path.join(drama_dir, f"raw_transcript.{stamp}{'' if n == 1 else f'-{n}'}.json")


def list_raw_transcripts(drama_dir: str) -> list:
    """Paths oldest first: raw_transcript.json, then the timestamped runs
    (their names sort chronologically)."""
    first = os.path.join(drama_dir, RAW_NAME)
    later = sorted(glob.glob(os.path.join(drama_dir, "raw_transcript.*.json")))
    return ([first] if os.path.exists(first) else []) + later


def load_latest(drama_dir: str):
    """The most recent run's transcript -- the one the drama's current
    lines came from -- or None if there isn't one."""
    paths = list_raw_transcripts(drama_dir)
    if not paths:
        return None
    with open(paths[-1], encoding="utf-8") as f:
        return json.load(f)


def original_text_for_line(raw: dict, line) -> str:
    """The originally transcribed text for `line`: every original line whose
    midpoint falls inside this line's time range, joined -- so a line merged
    from two shows both parts -- falling back to the original line with the
    same permanent id if nothing overlaps (e.g. its timing was moved).
    None if neither finds anything."""
    if not raw:
        return None
    originals = raw.get("lines") or []
    hits = [o for o in originals
            if line.start - 0.01 <= (o["start"] + o["end"]) / 2 <= line.end + 0.01]
    if hits:
        return "".join((o.get("text") or "").strip() for o in hits)
    line_id = getattr(line, "id", None)
    if line_id is not None:
        for o in originals:
            if o.get("id") == line_id:
                return o.get("text") or ""
    return None
