"""
services/fixflag_transcribe.py -- the Whisper pass behind "fix flagged lines".

Each flagged line's slice is heard with the title's saved Transcribe settings,
as the single-line re-transcribe and a full run hear it, so a line is not
re-decoded with different beam size, prompt, VAD, repeat guard or preset than
the rest of the title.
"""
import core as core_module
import sensitivity_preset as presets
from services import transcribe_service as ts


def text_for_slice(slice_path, drama_id, drama, whisper_size, source_language, use_gpu) -> str:
    segments = core_module.transcribe_for_timing(
        slice_path, model_size=whisper_size, language=source_language, use_gpu=use_gpu,
        initial_prompt=ts._resolve_initial_prompt(drama_id, "", ""),
        beam_size=drama.get("beam_size") or ts._DEFAULT_TUNING["beam_size"],
        min_silence_duration_ms=drama.get("min_silence_ms") or ts._DEFAULT_TUNING["min_silence_ms"],
        vad_threshold=presets.stored_vad_threshold(drama),
        fast_mode=bool(drama.get("whisper_fast_mode")),
        hallucination_silence_sec=ts.stored_hallucination_silence_sec(drama),
        repeat_guard=bool(drama.get("whisper_repeat_guard")),
        sensitivity_preset=presets.normalize(drama.get("sensitivity_preset")))
    return " ".join(s["text"] for s in segments).strip()
