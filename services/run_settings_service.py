"""The settings a job run used, shown in the Jobs page Details panel.

Job args are never exposed (they hold paths, keys and URLs). A job instead
passes an allow-listed view of its settings to background_jobs.start_job,
which keeps it on the job's own entry (so it can never attach to another
run); jobs_service.project_result_json merges it into the stored result as
"run_settings" when the job's record is written, and project_result
re-sanitises it on every read, so an older or hand-edited row can't leak a
key this module doesn't allow.

Allow-list only. Each key has one kind, and a value of the wrong kind is
dropped rather than coerced. Strings are limited to short identifier-like
tokens (model names, engine ids, codes) that survive scrub_text unchanged;
anything that looks like a path, a URL or a credential is dropped, never
masked. Prompts and other free text have no key here.
"""
import re

_NAME = "name"
_INT = "int"
_NUM = "num"
_BOOL = "bool"

ALLOWED_KEYS = {
    # Engines and models
    "engine": _NAME, "model": _NAME, "asr_backend": _NAME, "alignment_method": _NAME,
    "whisper_size": _NAME, "separation_backend": _NAME, "sensitivity_preset": _NAME,
    "transcript_mode": _NAME, "style_preset": _NAME, "locale": _NAME,
    "source_language": _NAME, "chinese_script": _NAME,
    # Numbers
    "beam_size": _INT, "batch_size": _INT, "context_window": _INT, "context_window_ahead": _INT,
    "min_silence_ms": _INT, "qwen_batch_size": _INT, "expected_speakers": _INT,
    "min_speakers": _INT, "max_speakers": _INT, "segment_seconds": _NUM, "overlap_seconds": _NUM,
    "max_minutes": _NUM, "vad_threshold": _NUM,
    "hallucination_silence_sec": _NUM, "min_pause_sec": _NUM,
    # On/off toggles
    "use_gpu": _BOOL, "diarize": _BOOL, "use_groq": _BOOL, "separate_vocals_first": _BOOL,
    "realign_long_segments": _BOOL, "whisper_fast_mode": _BOOL, "whisper_repeat_guard": _BOOL,
    "split_by_sentences": _BOOL, "force_retranslate": _BOOL, "reflect": _BOOL, "bulk": _BOOL,
    "thinking": _BOOL, "own_lines_only": _BOOL, "glossary": _BOOL, "style_guide": _BOOL,
    "style_note": _BOOL, "pronoun_hint": _BOOL, "genre_notes": _BOOL, "overwrite_manual": _BOOL,
    "reply_without_thinking": _BOOL,
}

# Letters, digits and . _ : + - / @ only, starting alphanumeric: "large-v3",
# "qwen2.5:7b", "Org/Model-1.7B". No backslash, drive colon, space or "://".
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@/-]{0,63}$")


def _clean_name(value):
    if not isinstance(value, str) or not _NAME_RE.match(value):
        return None
    if ".." in value or "//" in value or ":/" in value or value.startswith("/"):
        return None
    # Imported lazily: jobs_service imports this module.
    from services.jobs_service import scrub_text
    return value if scrub_text(value) == value else None


def _without_registry_host(value):
    """An Ollama tag may carry a registry host ("nas.lan:5000/me/qwen:7b"),
    which names a machine on the user's network: keep what follows the last
    "/". Anything shaped like a path is left whole so _clean_name drops it
    instead of reducing it to a file name."""
    if (not isinstance(value, str) or value.startswith(("/", "~")) or ".." in value
            or "\\" in value or "//" in value or ":/" in value):
        return value
    return value.rsplit("/", 1)[-1]


def _clean(kind, value):
    if kind == _BOOL:
        return value if isinstance(value, bool) else None
    if kind == _INT:
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if kind == _NUM:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if value == value and abs(value) != float("inf") else None
    return _clean_name(value)


def sanitise(raw) -> dict:
    """The allow-listed, scalar-only view of raw ({} for anything else)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, kind in ALLOWED_KEYS.items():
        if key in raw:
            value = raw[key]
            if key == "model":
                value = _without_registry_host(value)
            value = _clean(kind, value)
            if value is not None:
                out[key] = value
    return out


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
        return sanitise(merged)
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
                   transcript_mode, diarize, min_speakers, max_speakers) -> dict:
    """build() for a transcription run: the values the run resolved, plus the
    drama's 0/1 toggle columns as booleans (an int would be dropped)."""
    toggles = ("separate_vocals_first", "realign_long_segments", "whisper_fast_mode",
               "use_groq", "whisper_repeat_guard", "split_by_sentences")
    return build(
        {k: bool(drama.get(k)) for k in toggles}, source_language=source_language,
        chinese_script=chinese_script, whisper_size=whisper_size, beam_size=beam_size,
        min_silence_ms=min_silence_ms, vad_threshold=vad_threshold, sensitivity_preset=preset,
        hallucination_silence_sec=hallucination_silence_sec, min_pause_sec=min_pause_sec,
        separation_backend=separation_backend, use_gpu=use_gpu, asr_backend=asr_backend,
        alignment_method=alignment_method, transcript_mode=transcript_mode, diarize=diarize,
        min_speakers=min_speakers, max_speakers=max_speakers)


def for_diarize(expected_speakers, min_speakers, max_speakers, use_gpu, **settings) -> dict:
    return build(expected_speakers=expected_speakers, min_speakers=min_speakers,
                 max_speakers=max_speakers, use_gpu=use_gpu, **settings)
