"""
services/transcribe_checkpoint_service.py -- lets a transcription that died part
way (a crash, a power cut, a Cancel) pick up at the last finished unit when the
user starts it again, instead of decoding hours of audio twice.

The worker process reports each finished unit of Whisper segments over the
result queue; the job watcher stores it (db.record_checkpoint_unit). This module
decides whether a run may use checkpoints, names the scope, and reads back the
units a new run can continue from. Only the plain Whisper decode is covered:
speaker detection and every step after it still run once, over the whole file.
"""
import hashlib
import importlib.metadata
import inspect
import os

from services import job_checkpoint_service
from services.transcribe_pipeline import VAD_BACKENDS, _transcribe_worker

JOB_KIND = "transcribe"
# Bump when the decode or resume logic changes what a stored unit means.
CHECKPOINT_FORMAT_VERSION = 1

# A segment may start a little before the previous unit's last end (overlap settling).
_OVERLAP_SLACK_SEC = 0.5
_HASH_CHUNK_BYTES = 4 * 1024 * 1024

# What the worker is given that does not change what it transcribes.
_NOT_SETTINGS = {"audio_path", "transcript_text", "checkpoint", "scratch_dir", "result_queue"}


def applies(transcript_mode, asr_backend_choice, whisper_fast_mode, use_groq, mixed_languages) -> bool:
    """The backends that decode in other ways (batched fast mode, per-span language
    detection, Groq, the Qwen3 speech-detection backends) are not resumed."""
    return (transcript_mode == "whisper" and not whisper_fast_mode and not use_groq
            and not mixed_languages and asr_backend_choice not in VAD_BACKENDS)


def media_hash(path) -> str:
    """The file's exact content, so a different file of the same name or size never matches."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return f"{os.path.getsize(path)}:{digest.hexdigest()}"


def _library_versions() -> dict:
    """A different faster-whisper or ctranslate2 can decode the same audio differently."""
    out = {}
    for name in ("faster-whisper", "ctranslate2"):
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def _model_stamp(path) -> list:
    """A local model folder replaced in place keeps its path but not its weights."""
    if not path:
        return []
    try:
        st = os.stat(os.path.join(path, "model.bin"))
    except OSError:
        return []
    return [st.st_size, int(st.st_mtime)]


def scope_for(drama_id, worker_args: tuple, audio_path) -> str:
    """Names the units of one input under one setting: everything the worker is given
    that can change its output (read from its own signature, so a new option is
    included without anyone remembering to list it), plus the library versions."""
    names = list(inspect.signature(_transcribe_worker).parameters)
    # worker_args is the worker's arguments before the launcher adds the queue.
    settings = {n: v for n, v in zip(names, worker_args) if n not in _NOT_SETTINGS}
    settings["format"] = CHECKPOINT_FORMAT_VERSION
    settings["libraries"] = _library_versions()
    settings["model_stamp"] = _model_stamp(settings.get("local_model_path"))
    return job_checkpoint_service.checkpoint_scope(
        JOB_KIND, drama_id, input_hash=media_hash(audio_path),
        model=settings.get("local_model_path") or settings.get("whisper_size") or "", settings=settings)


def _words_usable(seg) -> bool:
    """Word times are read when resuming (the resume point is the last word's end)."""
    words = seg.get("words")
    if words is None:
        return True
    if not isinstance(words, list):
        return False
    return all(isinstance(w, dict) and isinstance(w.get("start"), (int, float))
               and isinstance(w.get("end"), (int, float)) and w["start"] <= w["end"]
               and seg["start"] - _OVERLAP_SLACK_SEC <= w["start"] and w["end"] <= seg["end"] + _OVERLAP_SLACK_SEC
               for w in words)


def _usable(batch, previous_end) -> bool:
    if not isinstance(batch, list) or not batch:
        return False
    for seg in batch:
        if not (isinstance(seg, dict) and isinstance(seg.get("text"), str)
                and isinstance(seg.get("start"), (int, float)) and isinstance(seg.get("end"), (int, float))
                and seg["start"] <= seg["end"]):
            return False
        if not _words_usable(seg):
            return False
        if seg["start"] < previous_end - _OVERLAP_SLACK_SEC:
            return False
        previous_end = max(previous_end, seg["end"])
    return True


def resume_from(scope: str) -> dict:
    """{"scope", "segments", "resumed_units", "record"}: the units 0, 1, 2... of an earlier run, up to
    the first one that is missing, damaged or out of order (a retry that started over can
    leave later units of a different cut, which must not be mixed in)."""
    units = job_checkpoint_service.done_units(scope)
    segments, count = [], 0
    while str(count) in units:
        batch = units[str(count)]
        if not _usable(batch, segments[-1]["end"] if segments else 0.0):
            break
        segments.extend(batch)
        count += 1
    return {"scope": scope, "segments": segments, "resumed_units": count, "record": True}


def for_run(drama_id, worker_args: tuple, audio_path) -> dict | None:
    """The `checkpoint` argument of the transcription worker, or None when this run is not
    resumable (see applies) or its input cannot be read."""
    names = list(inspect.signature(_transcribe_worker).parameters)
    given = dict(zip(names, worker_args))
    if not applies(given["transcript_mode"], given["asr_backend_choice"], given["whisper_fast_mode"],
                   given["use_groq"], given["mixed_languages"]):
        return None
    try:
        return resume_from(scope_for(drama_id, worker_args, audio_path))
    except Exception:   # noqa: BLE001  a checkpoint problem (unreadable media, locked database) must never fail the run
        return None


def clear(drama_id) -> None:
    """The run saved its lines: its units, and those of any older differently-keyed attempt, are moot."""
    job_checkpoint_service.clear_prefix(JOB_KIND, drama_id)
