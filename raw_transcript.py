"""
raw_transcript.py -- the untouched output of each transcription run, kept
on disk next to the drama (R1-lite).

Lines get edited, merged, split and re-transcribed; this file never does.
The first run writes drama_dir/raw_transcript.json; every later run writes
raw_transcript.<timestamp>.json instead -- nothing here ever overwrites an
existing file. The Review step reads the newest one back to show "what did
the transcription originally say for this line?" and to restore a single
line's text from it.

Retention: so re-transcribing a title many times can't grow its folder
without limit, while every run stays reachable, the first run (the true
original) and the newest KEEP_NEWEST_PLAIN stay plain JSON and the runs in
between are gzip-compressed in place to <name>.json.gz -- never deleted. A
run is compressed into a hidden temp file, renamed to its .gz name and read
back; only when it matches the plain file byte for byte is the plain file
removed. Writing, retention and the readers here take the title's lock, so
nothing is compressed while this process reads it; a plain file another
process still has open is simply removed at a later run.

Each file also carries a "settings" object (build_run_settings): what the
run was configured with, so two runs can be compared later. Files written
before it existed have no "settings" key; every reader must treat it as
optional.
"""

import contextlib
import datetime
import functools
import glob
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid

import sensitivity_preset as presets
from lib import gzip_text

log = logging.getLogger(__name__)

RAW_NAME = "raw_transcript.json"
KEEP_NEWEST_PLAIN = 5
GZ_SUFFIX = ".gz"
_TMP_PREFIX = ".raw_transcript."
# A temp file this old belongs to no running compression (one takes seconds).
_STALE_TMP_SECONDS = 3600
_RUN_NAME_RE = re.compile(r"raw_transcript(\.[^/\\]+)?\.json(\.gz)?")

_locks_guard = threading.Lock()
_locks = {}


def title_lock(drama_dir: str) -> threading.RLock:
    """The one lock for a title's raw transcripts: held while a run is
    written and retention runs, and while a reader opens a run."""
    key = os.path.normcase(os.path.abspath(drama_dir))
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())


def is_run_name(name: str) -> bool:
    """A raw transcript run's file name, plain or compressed."""
    return bool(_RUN_NAME_RE.fullmatch(name))

# The only keys a run's "settings" object may hold. Everything is a number,
# a boolean, a short enum-like string or a length: never a key, token, path,
# URL or prompt text.
SETTINGS_KEYS = (
    "asr_backend", "whisper_size", "local_model_path_set", "language", "transcript_mode",
    "alignment_method", "min_silence_ms", "vad_threshold", "sensitivity_preset", "beam_size",
    "hallucination_silence_sec", "min_pause_sec", "whisper_fast_mode", "whisper_repeat_guard",
    "split_by_sentences", "use_groq", "separate_vocals_first",
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3, check=False)
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
                       hallucination_silence_sec=None, min_pause_sec=None, whisper_fast_mode=False,
                       whisper_repeat_guard=False, split_by_sentences=False, use_groq=False,
                       separate_vocals_first=False,
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
        "whisper_repeat_guard": bool(whisper_repeat_guard),
        "split_by_sentences": bool(split_by_sentences),
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
    with title_lock(drama_dir):
        path = _write_new(drama_dir, payload)
        try:
            apply_retention(drama_dir)
        except Exception as exc:
            # The run itself is saved; retention is tried again after the next one.
            log.warning("Could not compress older raw transcripts: %s", type(exc).__name__)
    return path


def _write_new(drama_dir: str, payload: dict) -> str:
    path = os.path.join(drama_dir, RAW_NAME)
    n = 0
    while True:
        # A .gz of the same name counts as taken: retention may have
        # compressed the run that held it.
        if not os.path.exists(path + GZ_SUFFIX):
            try:
                with open(path, "x", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
                return path
            except FileExistsError:
                pass
        stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        n += 1
        path = os.path.join(drama_dir, f"raw_transcript.{stamp}{'' if n == 1 else f'-{n}'}.json")


def _run_key(path: str) -> str:
    name = os.path.basename(path)
    return name[:-len(GZ_SUFFIX)] if name.endswith(GZ_SUFFIX) else name


def list_raw_transcripts(drama_dir: str) -> list:
    """Paths oldest first, plain (.json) or compressed (.json.gz):
    raw_transcript.json, then the timestamped runs (their names sort
    chronologically). A run present in both forms (its plain file couldn't
    be removed yet) is listed once, as the plain file."""
    first = os.path.join(drama_dir, RAW_NAME)
    by_run = {}
    for path in (glob.glob(os.path.join(drama_dir, "raw_transcript.*.json"))
                 + glob.glob(os.path.join(drama_dir, "raw_transcript.*.json" + GZ_SUFFIX))):
        key = _run_key(path)
        if key not in by_run or not path.endswith(GZ_SUFFIX):
            by_run[key] = path
    if os.path.exists(first):
        head = [first]
    elif os.path.exists(first + GZ_SUFFIX):
        head = [first + GZ_SUFFIX]
    else:
        head = []
    return head + [by_run[k] for k in sorted(by_run)]


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    return gzip_text.gunzip_bytes(data) if path.endswith(GZ_SUFFIX) else data


def read_raw_transcript(path: str) -> dict:
    """One run's transcript, plain or compressed."""
    return json.loads(_read_bytes(path).decode("utf-8"))


def load_latest(drama_dir: str):
    """The most recent run's transcript -- the one the drama's current
    lines came from -- or None if there isn't one."""
    with title_lock(drama_dir):
        paths = list_raw_transcripts(drama_dir)
        if not paths:
            return None
        return read_raw_transcript(paths[-1])


def _same_content(packed_path: str, plain: bytes) -> bool:
    try:
        return _read_bytes(packed_path) == plain
    except (OSError, ValueError):
        return False


def _compress(plain_path: str) -> None:
    """Compresses one plain run next to itself, then removes the plain file
    only once the .gz reads back identical. An existing .gz (left by an
    earlier run whose plain file couldn't be removed) is reused if it
    matches and replaced if it doesn't."""
    with open(plain_path, "rb") as f:
        plain = f.read()
    packed_path = plain_path + GZ_SUFFIX
    if not _same_content(packed_path, plain):
        folder = os.path.dirname(plain_path)
        tmp = os.path.join(folder, f"{_TMP_PREFIX}{uuid.uuid4().hex}.json.gz.tmp")
        try:
            with open(tmp, "xb") as f:
                f.write(gzip_text.pack_bytes(plain))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, packed_path)
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        if not _same_content(packed_path, plain):
            with contextlib.suppress(OSError):
                os.remove(packed_path)
            log.warning("A compressed raw transcript didn't read back the same; kept it plain")
            return
    try:
        os.remove(plain_path)
    except OSError:
        # In use elsewhere (another process reading it on Windows): both
        # forms stay, readers take the plain one, a later run removes it.
        pass


def _remove_stale_temp(drama_dir: str) -> None:
    now = time.time()
    for path in glob.glob(os.path.join(drama_dir, _TMP_PREFIX + "*.tmp")):
        with contextlib.suppress(OSError):
            if now - os.path.getmtime(path) > _STALE_TMP_SECONDS:
                os.remove(path)


def apply_retention(drama_dir: str) -> None:
    """Compresses every run but the first and the newest KEEP_NEWEST_PLAIN.
    Deletes no run. Takes the title's lock."""
    with title_lock(drama_dir):
        _remove_stale_temp(drama_dir)
        paths = list_raw_transcripts(drama_dir)
        for path in paths[1:-KEEP_NEWEST_PLAIN]:
            if not path.endswith(GZ_SUFFIX):
                _compress(path)


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
