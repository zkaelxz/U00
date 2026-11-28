"""Builds the settings a job run records at start; the allow-list they pass through is jobs/run_settings.py."""
from jobs import run_settings as job_run_settings


def build(*sources: dict, **settings) -> dict:
    """The sanitised settings of a run about to start, for start_job's
    run_settings. Later sources and keyword settings override earlier ones.
    Never raises: a job must not fail because its settings couldn't be
    built."""
    try:
        merged = {}
        for source in sources:
            if isinstance(source, dict):
                merged.update(source)
        merged.update(settings)
        return job_run_settings.sanitise(merged)
    except Exception:
        return {}


def for_translate(drama, include_genre_notes, default_female_pronouns, glossary_terms,
                  style_guidelines, style_note, *sources, **settings) -> dict:
    """build() for a translation run: shows the toggles as the run resolved
    them (the title's saved choice, else the defaults) and whether a glossary,
    style guide and style note were sent, never their text."""
    from services.workspace_job_service import resolve_style_toggles
    genre, pronouns = resolve_style_toggles(drama, include_genre_notes, default_female_pronouns)
    return build(*sources, genre_notes=genre, pronoun_hint=pronouns,
                 glossary=bool(glossary_terms), style_guide=bool(style_guidelines),
                 style_note=bool((style_note or "").strip()), **settings)


def for_transcribe(drama, source_language, chinese_script, whisper_size, beam_size,
                   min_silence_ms, vad_threshold, preset, hallucination_silence_sec,
                   min_pause_sec, separation_backend, use_gpu, asr_backend, alignment_method,
                   transcript_mode, diarize, min_speakers, max_speakers, timing=None) -> dict:
    """build() for a transcription run: the values the run resolved, plus the
    drama's 0/1 toggle columns as booleans (an int would be dropped). `timing`:
    the Timing and splitting values that differ from the default."""
    from services import transcribe_options_service  # imports this module at load
    toggles = ("separate_vocals_first", "realign_long_segments", "whisper_fast_mode",
               "use_groq", "whisper_repeat_guard", "split_by_sentences", "difficult_audio")
    return build(
        {**{k: bool(drama.get(k)) for k in toggles},
         "separate_vocals_first": transcribe_options_service.effective_separate_vocals(drama)},
        timing or {}, source_language=source_language,
        chinese_script=chinese_script, whisper_size=whisper_size, beam_size=beam_size,
        min_silence_ms=min_silence_ms, vad_threshold=vad_threshold, sensitivity_preset=preset,
        hallucination_silence_sec=hallucination_silence_sec, min_pause_sec=min_pause_sec,
        separation_backend=separation_backend, use_gpu=use_gpu, asr_backend=asr_backend,
        alignment_method=alignment_method, transcript_mode=transcript_mode, diarize=diarize,
        min_speakers=min_speakers, max_speakers=max_speakers)


def for_diarize(expected_speakers, min_speakers, max_speakers, use_gpu, **settings) -> dict:
    return build(expected_speakers=expected_speakers, min_speakers=min_speakers,
                 max_speakers=max_speakers, use_gpu=use_gpu, **settings)
