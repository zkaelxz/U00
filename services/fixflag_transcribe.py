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

import background_jobs
import core as core_module
import sensitivity_preset as presets
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
    {"lines": [{"idx", "text"} or {"idx", "error"}]}); a line that fails keeps
    going, as the in-process loop did. Writes nothing to the database."""
    slice_path = os.path.join(scratch_dir, "line.wav")
    lines = []
    for n, (idx, start, end) in enumerate(windows):
        background_jobs.report_progress(
            result_queue, n / len(windows), f"Hearing flagged line {n + 1} of {len(windows)}")
        try:
            core_module.extract_audio_slice(audio_path, start, end, slice_path)
            segments = core_module.transcribe_for_timing(slice_path, **settings)
            lines.append({"idx": idx, "text": " ".join(s["text"] for s in segments).strip()})
        except Exception as exc:
            lines.append({"idx": idx, "error": redact_secrets(
                f"line {idx + 1} re-transcription: {exc}")})
        finally:
            if os.path.exists(slice_path):
                os.remove(slice_path)
    result_queue.put(("ok", {"lines": lines}))

