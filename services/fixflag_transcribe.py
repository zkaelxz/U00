"""
services/fixflag_transcribe.py -- the Whisper pass behind "fix flagged lines".

Each flagged line's slice is heard with the title's saved Transcribe settings,
as the single-line re-transcribe and a full run hear it, so a line is not
re-decoded with different beam size, prompt, VAD, repeat guard or preset than
the rest of the title. The hearing runs in a spawned child (gpu_process_job)
so Cancel can kill it; the child gets plain settings resolved here, in the
parent, because it must not read the database.
"""
import os
import subprocess

import background_jobs
import core as core_module
import sensitivity_preset as presets
from services import gpu_process_job, retranscribe_worker
from services import transcribe_service as ts
from translate_engines import redact_secrets


def hearing_settings(drama_id, drama, whisper_size, source_language, use_gpu) -> dict:
    return {
        "model_size": whisper_size, "language": source_language, "use_gpu": use_gpu,
        "initial_prompt": ts._resolve_initial_prompt(drama_id, "", ""),
        "beam_size": drama.get("beam_size") or ts._DEFAULT_TUNING["beam_size"],
        "min_silence_duration_ms": drama.get("min_silence_ms")
        or ts._DEFAULT_TUNING["min_silence_ms"],
        "vad_threshold": presets.stored_vad_threshold(drama),
        "fast_mode": bool(drama.get("whisper_fast_mode")),
        "hallucination_silence_sec": ts.stored_hallucination_silence_sec(drama),
        "repeat_guard": bool(drama.get("whisper_repeat_guard")),
        "sensitivity_preset": presets.normalize(drama.get("sensitivity_preset"))}


def hear_flagged_worker(audio_path, windows, settings, scratch_dir, result_queue):
    """gpu_process_job body. `windows` is [(line_idx, start, end)]. Puts ("ok",
    {}) after putting each line as ("item", {"idx", "text"} or {"idx",
    "error"}) when it is heard, so a cancel or timeout keeps the earlier ones;
    a line that fails keeps going. Writes nothing to the database."""
    slice_path = os.path.join(scratch_dir, "line.wav")
    for n, (idx, start, end) in enumerate(windows):
        background_jobs.report_progress(
            result_queue, n / len(windows), f"Hearing flagged line {n + 1} of {len(windows)}")
        try:
            core_module.extract_audio_slice(audio_path, start, end, slice_path)
            segments = core_module.transcribe_for_timing(slice_path, **settings)
            entry = {"idx": idx, "text": " ".join(s["text"] for s in segments).strip()}
        except subprocess.CalledProcessError:
            # str() of it carries the ffmpeg command line, i.e. absolute paths.
            entry = {"idx": idx, "error": f"line {idx + 1}: couldn't cut this line's audio"}
        except Exception as exc:
            entry = {"idx": idx, "error": redact_secrets(
                f"line {idx + 1} re-transcription: {exc}")}
        finally:
            if os.path.exists(slice_path):
                os.remove(slice_path)
        result_queue.put(("item", entry))
    result_queue.put(("ok", {}))



def hear_flagged(job_id, flagged, audio_path, settings):
    """Hears the flagged lines in a killable child. Returns (entries heard,
    cancelled, plain error text or None); a cancel or timeout keeps the
    entries heard so far, and a timeout with none heard raises."""
    entries = []
    try:
        gpu_process_job.run_in_child(
            job_id, hear_flagged_worker,
            (audio_path, [(ln.idx, ln.start, ln.end) for ln in flagged], settings),
            timeout_s=retranscribe_worker.retranscribe_timeout_s(
                sum(max(0.0, ln.end - ln.start) for ln in flagged), len(flagged)),
            on_item=entries.append)
    except background_jobs.JobCancelled:
        return entries, True, None
    except gpu_process_job.ChildFailed as exc:
        if not entries:
            raise
        return entries, False, str(exc)
    return entries, False, None
