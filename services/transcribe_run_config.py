"""
services/transcribe_run_config.py -- the options a transcription ran with,
as the plain dict stored beside its result.
"""


def run_config(o, vad_run: bool) -> dict:
    """`o` holds the _transcribe_pipeline arguments by name. Groq is reported
    off for the VAD backends because they never call it."""
    return {
        "asr_backend": o.asr_backend_choice, "whisper_size": o.whisper_size,
        "local_model_path": o.local_model_path, "language": o.source_language,
        "transcript_mode": o.transcript_mode, "alignment_method": o.alignment_method,
        "min_silence_ms": o.min_silence_ms, "vad_threshold": o.vad_threshold,
        "beam_size": o.beam_size, "hallucination_silence_sec": o.hallucination_silence_sec,
        "min_pause_sec": o.min_pause_sec, "sensitivity_preset": o.sensitivity_preset,
        "whisper_fast_mode": o.whisper_fast_mode,
        "use_groq": o.use_groq and not vad_run,
        "whisper_repeat_guard": o.repeat_guard, "split_by_sentences": o.split_by_sentences,
        "vocabulary_hint": bool(o.qwen_prompt),
        "separate_vocals_first": o.separate_vocals_first,
        "separation_backend": o.separation_backend,
        "realign_long_segments": o.realign_long_segments,
        "mixed_languages": o.mixed_languages,
        "vad_refine_timing": (o.vad_refine_timing
                              or o.asr_backend_choice == "qwen3_asr_long"),
        "use_gpu": o.use_gpu, "initial_prompt": o.initial_prompt}
