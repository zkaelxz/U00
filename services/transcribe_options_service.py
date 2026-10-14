"""
services/transcribe_options_service.py -- what a transcribe run may be
asked to do: the per-drama tuning defaults, the Whisper model sizes a title
may store, and the validation of saved config fields and of run choices
(transcript mode, ASR backend, alignment method, language, speaker range).

Shared by transcribe_service (config and run start) and
retranscribe_line_service, which resolve the same saved values.
"""
import importlib.util
from typing import Optional
import core as core_module
import whisper_models
import segment_splitting
import db
import diagnostics
import sensitivity_preset as presets
from core import SOURCE_LANGUAGES
from ocr import HARDSUB_OCR_BACKEND_OPTIONS
from services import asr_options_service, diarization_service, settings_service, transcribe_pipeline
from services.qwen3_requirements_service import (MISSING_VAD_MESSAGE, require_qwen3_packages,
                                                 require_vad_backend_packages)
from services.service_errors import DependencyUnavailableError, InvalidInputError, NotFoundError, UnsupportedOperationError

# The per-drama tuning values used when the drama has none stored.
_DEFAULT_TUNING = {
    "whisper_size": whisper_models.DEFAULT_WHISPER_SIZE,
    "beam_size": 5,
    "min_silence_ms": 300,
    "vad_threshold": 0.5,
    "hallucination_silence_sec": core_module.DEFAULT_HALLUCINATION_SILENCE_SEC,
    "min_pause_sec": segment_splitting.MIN_WORD_GAP_SECONDS,
    "separate_vocals_first": False,
    "separation_backend": "auto",
    "realign_long_segments": False,
    "whisper_fast_mode": False,
    "use_groq": False,
}
ASR_BACKEND_CHOICES = asr_options_service.ASR_BACKEND_CHOICES


def stored_hallucination_silence_sec(drama) -> float:
    """The drama's saved value; 0 means off, so only a missing one gets the default."""
    value = drama.get("hallucination_silence_sec")
    return _DEFAULT_TUNING["hallucination_silence_sec"] if value is None else float(value)


def stored_min_pause_sec(drama) -> float:
    """The drama's saved pause a long line may be cut at; a title that never
    saved one uses the current default."""
    value = drama.get("min_pause_sec")
    return _DEFAULT_TUNING["min_pause_sec"] if value is None else float(value)


_BOOL_FIELDS = ("separate_vocals_first", "realign_long_segments", "whisper_fast_mode",
                "whisper_repeat_guard", "split_by_sentences", "vocabulary_hint", "use_groq",
                "difficult_audio")
_SEPARATION_BACKENDS = ("auto", "audio_separator", "demucs")


def check_transcribe_fields(fields: dict, source_language) -> dict:
    """update_transcribe_config's validation: the columns to write for the
    fields passed (not None), as stored (booleans as 0/1). InvalidInputError
    for an unknown or out-of-range value. `source_language` None skips the
    checks that depend on a title's language (a series' defaults, checked
    again per title when applied)."""
    updates = {}
    if "whisper_size" in fields and fields["whisper_size"] is not None:
        # A fixed set: faster-whisper downloads whatever repo name it is given.
        if fields["whisper_size"] not in _allowed_whisper_sizes():
            raise InvalidInputError(f"Unknown whisper_size {fields['whisper_size']!r}.")
        if source_language is not None:
            check_whisper_size_for_language(fields["whisper_size"], source_language)
        updates["whisper_size"] = fields["whisper_size"]
    if "alignment_method" in fields and fields["alignment_method"] is not None:
        if fields["alignment_method"] not in ("whisper_diff", "qwen3_forced_align"):
            raise InvalidInputError(f"Unknown alignment_method {fields['alignment_method']!r}.")
        updates["alignment_method"] = fields["alignment_method"]
    if "asr_backend_choice" in fields and fields["asr_backend_choice"] is not None:
        if fields["asr_backend_choice"] not in ASR_BACKEND_CHOICES:
            raise InvalidInputError(f"Unknown asr_backend_choice {fields['asr_backend_choice']!r}.")
        problem = source_language is not None and asr_options_service.asr_backend_language_problem(
            fields["asr_backend_choice"], source_language)
        if problem:
            raise InvalidInputError(problem)
        updates["asr_backend_choice"] = fields["asr_backend_choice"]
    if "beam_size" in fields and fields["beam_size"] is not None:
        if not 1 <= fields["beam_size"] <= 10:
            raise InvalidInputError("beam_size must be between 1 and 10.")
        updates["beam_size"] = fields["beam_size"]
    if "min_silence_ms" in fields and fields["min_silence_ms"] is not None:
        if not core_module.MIN_SILENCE_MS_MIN <= fields["min_silence_ms"] <= core_module.MIN_SILENCE_MS_MAX:
            raise InvalidInputError(
                f"min_silence_ms must be between {core_module.MIN_SILENCE_MS_MIN} "
                f"and {core_module.MIN_SILENCE_MS_MAX}.")
        updates["min_silence_ms"] = fields["min_silence_ms"]
    if "vad_threshold" in fields and fields["vad_threshold"] is not None:
        if not 0.1 <= fields["vad_threshold"] <= 0.9:
            raise InvalidInputError("vad_threshold must be between 0.1 and 0.9.")
        updates["vad_threshold"] = fields["vad_threshold"]
    if "sensitivity_preset" in fields and fields["sensitivity_preset"] is not None:
        if fields["sensitivity_preset"] not in presets.PRESETS:
            raise InvalidInputError(f"Unknown sensitivity_preset {fields['sensitivity_preset']!r}.")
        updates["sensitivity_preset"] = fields["sensitivity_preset"]
    if "hallucination_silence_sec" in fields and fields["hallucination_silence_sec"] is not None:
        value = fields["hallucination_silence_sec"]
        if value != 0 and not 0.5 <= value <= 10:
            raise InvalidInputError("hallucination_silence_sec must be 0 (off) or between 0.5 and 10.")
        updates["hallucination_silence_sec"] = value
    if "min_pause_sec" in fields and fields["min_pause_sec"] is not None:
        value = fields["min_pause_sec"]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not segment_splitting.MIN_WORD_GAP_SECONDS_MIN <= value
                <= segment_splitting.MIN_WORD_GAP_SECONDS_MAX):
            raise InvalidInputError(
                f"min_pause_sec must be a number between {segment_splitting.MIN_WORD_GAP_SECONDS_MIN:g} "
                f"and {segment_splitting.MIN_WORD_GAP_SECONDS_MAX:g}.")
        updates["min_pause_sec"] = float(value)
    if "separation_backend" in fields and fields["separation_backend"] is not None:
        if fields["separation_backend"] not in _SEPARATION_BACKENDS:
            raise InvalidInputError(f"Unknown separation_backend {fields['separation_backend']!r}.")
        updates["separation_backend"] = fields["separation_backend"]
    if "hardsub_ocr_backend" in fields and fields["hardsub_ocr_backend"] is not None:
        if fields["hardsub_ocr_backend"] not in HARDSUB_OCR_BACKEND_OPTIONS:
            raise InvalidInputError(f"Unknown hardsub_ocr_backend {fields['hardsub_ocr_backend']!r}.")
        updates["hardsub_ocr_backend"] = fields["hardsub_ocr_backend"]
    if "hardsub_interval_sec" in fields and fields["hardsub_interval_sec"] is not None:
        if not 0.5 <= fields["hardsub_interval_sec"] <= 3.0:
            raise InvalidInputError("hardsub_interval_sec must be between 0.5 and 3.0.")
        updates["hardsub_interval_sec"] = fields["hardsub_interval_sec"]
    for bf in _BOOL_FIELDS:
        if bf in fields and fields[bf] is not None:
            updates[bf] = 1 if fields[bf] else 0
    return updates


def _check_run_choices(transcript_mode, asr_backend_choice, alignment_method, language) -> None:
    """Refusals shared by start_transcribe_run and validate_transcribe_options."""
    if transcript_mode == "whisper":
        if alignment_method == "qwen3_forced_align":
            raise InvalidInputError(
                "Qwen3 forced alignment needs a transcript to align, but this title is in "
                "Whisper-text-only mode. Supply a transcript, or set alignment_method back "
                "to 'whisper_diff'.")
        problem = asr_options_service.asr_backend_language_problem(asr_backend_choice, language)
        if problem:
            raise InvalidInputError(problem)
        if asr_backend_choice == "qwen3_asr":
            require_qwen3_packages("Qwen3-ASR")
        elif asr_backend_choice == "anime_whisper":
            _require_vad_packages()
            problem = anime_whisper_package_problem()
            if problem:
                raise DependencyUnavailableError(problem)
        elif asr_backend_choice in transcribe_pipeline.VAD_BACKENDS:
            require_vad_backend_packages(asr_backend_choice, language)
            _require_vad_packages()
    elif transcript_mode == "have_transcript" and alignment_method == "qwen3_forced_align":
        require_qwen3_packages("Qwen3 forced alignment", language)


# What anime-whisper needs beyond the speech-detection backends: huggingface_hub before 0.23
# downloads through links, which the file check refuses; faster-whisper before 1.1 has an
# older find_alignment, so every window would come back as one untimed line.
_ANIME_WHISPER_MINIMUMS = (("faster-whisper", "1.1"), ("huggingface-hub", "0.23"))


def anime_whisper_package_problem():
    """A plain sentence naming a package too old for anime-whisper, else None."""
    import importlib.metadata
    for package, minimum in _ANIME_WHISPER_MINIMUMS:
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue  # a missing package has its own message
        if diagnostics.below_min_version(installed, minimum):
            return (f"anime-whisper needs {package} {minimum} or newer, but {installed} is "
                    "installed. Update it in Diagnostics.")
    return None


def _require_vad_packages() -> None:
    """The speech-detection backends need faster-whisper (it bundles the
    Silero VAD and decodes the audio for it)."""
    if importlib.util.find_spec("faster_whisper") is None:
        raise DependencyUnavailableError(MISSING_VAD_MESSAGE)


_CHINESE_SCRIPTS = ("simplified", "traditional")


def validate_transcribe_options(drama_id: int, source_language: Optional[str] = None,
                                chinese_script: Optional[str] = None,
                                transcript_text: Optional[str] = None,
                                expected_speakers: Optional[int] = None,
                                min_speakers: Optional[int] = None,
                                max_speakers: Optional[int] = None, **_ignored) -> None:
    """Validate-only pre-check for a run that starts after the audio exists
    (upload-and-transcribe with a video). Raises the same errors as
    start_transcribe_run for everything that doesn't depend on the audio or
    video file being on disk yet; starts nothing. Keep in step with
    start_transcribe_run's checks."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    diarization_service.speaker_range(expected_speakers, min_speakers, max_speakers)
    if (drama.get("content_mode") or "audio_drama") not in ("audio_drama", "streamer_vod"):
        raise UnsupportedOperationError(
            f"Drama {drama_id} has no audio pipeline (content mode "
            f"{drama.get('content_mode')!r}); novel chunking isn't available via this API yet.")
    language = source_language or drama.get("source_language") or "zh"
    if language not in SOURCE_LANGUAGES:
        raise InvalidInputError(f"Unknown source_language {source_language!r}.")
    if (chinese_script or drama.get("chinese_script") or "simplified") not in _CHINESE_SCRIPTS:
        raise InvalidInputError(f"Unknown chinese_script {chinese_script!r}.")
    transcript_mode = drama.get("transcript_mode") or "have_transcript"
    if transcript_mode == "have_transcript" and not (transcript_text or "").strip():
        raise UnsupportedOperationError(
            "transcript_mode is 'have_transcript' but no transcript_text was supplied.")
    asr_backend_choice = asr_options_service.stored_asr_backend(drama)
    alignment_method = drama.get("alignment_method") or "whisper_diff"
    _check_run_choices(transcript_mode, asr_backend_choice, alignment_method, language)
    if drama.get("use_groq") and not settings_service.resolve_key("groq"):
        raise DependencyUnavailableError(
            "use_groq is on but no Groq API key is configured. Set one in Settings first.")


def _allowed_whisper_sizes() -> frozenset:
    """Whisper model names a drama may store: the Workspace picker's
    (whisper_models.WHISPER_MODELS, plus tiny/base in the React picker) and the known
    download sizes above. Anything else is refused by update_transcribe_config."""
    return frozenset(whisper_models.WHISPER_MODELS) | frozenset(transcribe_pipeline._MODEL_DOWNLOAD_SIZES)


def selectable_whisper_sizes(context: str) -> list:
    return whisper_models.selectable_whisper_sizes(context)


def default_whisper_size() -> str:
    """The model used when a drama has none saved: large-v3 by the owner's choice
    (readability on his streamer comparisons; docs/asr-experiments.md has the
    measured CER, where turbo is no worse). The same on CPU and GPU. A model the
    user saved is never replaced, including one the pickers now hide."""
    return _DEFAULT_TUNING["whisper_size"]


def check_whisper_size_for_language(size: str, source_language: str) -> None:
    """Refuses a model that whisper_models.WHISPER_LANGUAGE_RULES excludes for this language."""
    problem = whisper_models.whisper_model_language_problem(size, source_language)
    if problem:
        raise InvalidInputError(problem)


def stored_whisper_size(drama: dict) -> str:
    """The drama's saved whisper_size, or the default when it is empty or
    not one of _allowed_whisper_sizes() (e.g. a value planted in the DB by
    hand): an arbitrary string must never reach WhisperModel, where it
    would be read as a Hugging Face repo id or a local path. Logs a warning
    (without the value) when it falls back. A model that is not recommended for
    the title's language (saved before the rule, or after a language change)
    falls back the same way; whisper_size_notice says so in the UI."""
    size = drama.get("whisper_size")
    if not size:
        return default_whisper_size()
    if size not in _allowed_whisper_sizes():
        import applog
        applog.get_logger().warning(
            f"drama {drama.get('id')}: stored whisper_size is not a known model size; "
            f"using the default {default_whisper_size()}")
        return default_whisper_size()
    if not whisper_models.whisper_model_fits_language(size, drama.get("source_language") or "zh"):
        import applog
        applog.get_logger().warning(
            f"drama {drama.get('id')}: stored whisper_size is not recommended for the title's "
            f"language; using the default {default_whisper_size()}")
        return default_whisper_size()
    return size


def whisper_size_notice(drama: dict):
    """Plain sentence when the saved model is replaced by the default because it is not
    recommended for the title's language, else None. Shown with the picker until the
    user saves a choice, so the change is never silent."""
    size = drama.get("whisper_size")
    if not size or size not in _allowed_whisper_sizes():
        return None
    problem = whisper_models.whisper_model_language_problem(size, drama.get("source_language") or "zh")
    if not problem:
        return None
    return f"This title was saved with {size}. {problem} It now uses {default_whisper_size()}."


# --- Advisor: which models and switches not to combine -----------------------
# One table, read by the Transcribe form, the Jobs details of a finished run and
# the "Do not combine" table in docs/asr-experiments.md (a test keeps them in sync).
_SEVERITY_ORDER = {"blocked": 0, "warn": 1, "tip": 2}
_WHISPER_FREE = ("qwen3_asr_vad", "qwen3_asr_long", "anime_whisper")
_LONG_AUDIO_QWEN = ("qwen3_asr_vad", "qwen3_asr_long")
_WEAK_WHISPER_SIZES = ("small", "medium")
_RECOMMENDED = "Recommended: Whisper large-v3 with Separate vocals first off."


def _runs_whisper(c) -> bool:
    return c["backend"] not in _WHISPER_FREE


_ADVICE_RULES = (
    # (id, severity, applies(ctx), text, fix: settings to change or None)
    ("groq_no_key", "blocked",
     lambda c: c["use_groq"] and c["backend"] == "whisper" and c["token"] is False,
     "Groq is on but no Groq key is saved.",
     lambda c: {"use_groq": False} if c["packages"].get("faster_whisper", True) else None),
    ("whisper_missing", "blocked",
     lambda c: _runs_whisper(c) and not c["use_groq"] and c["packages"].get("faster_whisper") is False,
     "Whisper is not installed, so this run cannot start.", lambda c: None),
    ("anime_whisper_language", "blocked",
     lambda c: c["backend"] == "anime_whisper" and c["language"] not in ("", "ja"),
     "Anime-whisper is for Japanese only.", lambda c: {"asr_backend_choice": "whisper"}),
    ("ja_model_language", "blocked",
     lambda c: c["model"] in ("ja-1.5b", "kotoba-v2") and _runs_whisper(c) and c["language"] not in ("", "ja"),
     "This Whisper model is for Japanese only.", lambda c: {"whisper_size": "large-v3"}),
    ("qwen_long_audio", "warn",
     lambda c: c["backend"] in _LONG_AUDIO_QWEN,
     "Qwen3 ASR on long audio gives no word timings, fewer lines and \"Timing uncertain · Split evenly\" flags. Use Whisper.",
     lambda c: {"asr_backend_choice": "whisper"}),
    ("forced_align_needs_transcript", "warn",
     lambda c: c["alignment_method"] == "qwen3_forced_align" and not c["has_transcript"],
     "Qwen3 forced alignment needs a transcript to line up; without one it is not used.",
     lambda c: None),
    ("ja_15b_needs_gpu", "warn",
     lambda c: c["model"] == "ja-1.5b" and _runs_whisper(c) and not c["use_groq"] and c["gpu_on"] is False,
     "Whisper ja-1.5B needs a GPU and is meant for anime and games.", lambda c: {"whisper_size": "large-v3"}),
    ("weak_whisper_size", "warn",
     lambda c: c["model"] in _WEAK_WHISPER_SIZES and _runs_whisper(c) and not c["use_groq"],
     "Whisper small and medium read worse than large-v3.", lambda c: {"whisper_size": "large-v3"}),
    ("separate_vocals", "warn",
     lambda c: c["separate_vocals_first"],
     "Separate vocals first can drop quiet speech. Use it only if background music makes Whisper invent text, and compare line counts.",
     lambda c: {"separate_vocals_first": False}),
    ("fast_mode", "warn",
     lambda c: c["whisper_fast_mode"] and _runs_whisper(c),
     "Fast mode gives fewer, longer lines and a higher error rate.",
     lambda c: {"whisper_fast_mode": False}),
    ("anime_whisper_scope", "tip",
     lambda c: c["backend"] == "anime_whisper" and c["language"] in ("", "ja"),
     "Anime-whisper is Japanese only, for ASMR and vocalisations, and its licence is non-commercial.",
     lambda c: None),
    ("ja_15b_scope", "tip",
     lambda c: c["model"] == "ja-1.5b" and _runs_whisper(c) and not c["use_groq"] and c["gpu_on"] is not False
     and c["language"] in ("", "ja"),
     "Whisper ja-1.5B is meant for anime and games.", lambda c: None),
)
ADVICE_RULE_IDS = tuple(rule[0] for rule in _ADVICE_RULES)


def asr_advice(language: str, media_type: str, backend: str, model: str, switches: dict,
               gpu_on: Optional[bool], packages: Optional[dict] = None, token: Optional[bool] = None) -> list:
    """The ordered notes for one combination of transcription choices:
    [{"id", "severity": blocked|warn|tip, "text", "fix"}], blocked first. `fix` is a dict of
    plain setting changes (keys as in the transcribe config) or None when the fix is not one.
    `packages` maps a package to installed (faster_whisper) and `token` is whether a Groq key
    is saved; None skips the notes that need them, as when describing a run that already
    finished. `media_type` is accepted for callers that know it; no rule needs it yet."""
    ctx = {
        "language": language or "", "media_type": media_type or "", "backend": backend or "whisper",
        "model": model or "", "gpu_on": gpu_on, "packages": packages or {}, "token": token,
        "use_groq": bool(switches.get("use_groq")),
        "separate_vocals_first": bool(switches.get("separate_vocals_first")),
        "whisper_fast_mode": bool(switches.get("whisper_fast_mode")),
        "has_transcript": bool(switches.get("has_transcript")),
        "alignment_method": switches.get("alignment_method") or "",
    }
    notes = [{"id": rule_id, "severity": severity, "text": text, "fix": fix(ctx)}
             for rule_id, severity, applies, text, fix in _ADVICE_RULES if applies(ctx)]
    notes.sort(key=lambda n: _SEVERITY_ORDER[n["severity"]])
    if not notes and ctx["backend"] == "whisper" and ctx["model"] in ("", "large-v3"):
        notes.append({"id": "recommended", "severity": "tip", "text": _RECOMMENDED, "fix": None})
    return notes


def asr_advice_for_form(language: str, media_type: str, backend: str, model: str, switches: dict,
                        gpu_on: Optional[bool], check_environment: bool) -> list:
    """asr_advice for the form's current choices; `check_environment` adds what this PC has
    installed and whether a Groq key is saved (false for a run that already finished)."""
    packages = token = None
    if check_environment:
        packages = {"faster_whisper": diagnostics.check_dependency("faster_whisper")}
        token = bool(settings_service.resolve_key("groq"))
    return asr_advice(language, media_type, backend, model, switches, gpu_on, packages, token)
