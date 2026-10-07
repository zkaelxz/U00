"""
services/asr_options_service.py -- the two experimental transcription
settings, stored in db.app_settings like use_gpu.

- qwen_asr_batch_size: how many Whisper segments go to Qwen3-ASR
  in one call when a drama's ASR backend is Qwen3-ASR. 1 (the default) keeps
  the original one-segment-at-a-time behaviour; batching is opt-in until the
  user's real GPU comparison shows it doesn't change the text.
- moss_experimental: allows MOSS-Transcribe-Diarize as a drama's
  ASR backend. Off by default; with it off the backend can't be chosen or run.
- qwen_vad_refine_timing: with the "Qwen3 ASR with speech detection" backend, also
  tightens each line's times with Qwen3-ForcedAligner. Off by default.
- mixed_languages: detect the spoken language per speech span and write it on
  each line that differs from the title's language (mixed_language.py). Off by
  default; slower (one detection per span).
- stored_asr_backend: a title's backend, or the default for its language.

UI-free. Writes are PC-only (the router uses local_only()).
"""

import importlib.util

import db
from services.service_errors import InvalidInputError

QWEN_ASR_BATCH_KEY = "qwen_asr_batch_size"
MOSS_EXPERIMENTAL_KEY = "moss_experimental"
VAD_REFINE_KEY = "qwen_vad_refine_timing"
MIXED_LANGUAGES_KEY = "mixed_languages"
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 16

ASR_BACKEND_CHOICES = ("whisper", "qwen3_asr", "qwen3_asr_vad", "qwen3_asr_long", "moss_td")
# Languages whose default backend is Qwen3-ASR on long windows, when it is
# installed: the Qwen3-ASR model card reports about a third of Whisper
# large-v3's character error rate on Chinese benchmarks and a lower one on
# Japanese. The only Japanese clip measured here (docs/asr-experiments.md) was
# run before this backend existed, on the short-span one.
QWEN_LONG_DEFAULT_LANGUAGES = ("zh", "ja")


def get_qwen_asr_batch_size() -> int:
    """The saved batch size, clamped to 1..16. A DB hiccup or a bad stored
    value means 1 (batching off)."""
    try:
        value = int(db.get_app_setting(QWEN_ASR_BATCH_KEY, MIN_BATCH_SIZE))
    except Exception:
        return MIN_BATCH_SIZE
    return min(MAX_BATCH_SIZE, max(MIN_BATCH_SIZE, value))


def get_moss_experimental() -> bool:
    """Whether the experimental MOSS-Transcribe-Diarize backend is allowed.
    Fails closed (off) on a DB hiccup."""
    try:
        return db.get_app_setting(MOSS_EXPERIMENTAL_KEY, False) is True
    except Exception:
        return False


def get_vad_refine_timing() -> bool:
    """Whether the speech-detection Qwen3 backend refines timing with the
    forced aligner. Off on a DB hiccup."""
    try:
        return db.get_app_setting(VAD_REFINE_KEY, False) is True
    except Exception:
        return False


def get_mixed_languages() -> bool:
    """Whether a transcription detects the language of each speech span. Off
    on a DB hiccup."""
    try:
        return db.get_app_setting(MIXED_LANGUAGES_KEY, False) is True
    except Exception:
        return False


def moss_installed() -> bool:
    """Whether the moss_transcribe_diarize package can be imported (checked
    without importing it)."""
    try:
        return importlib.util.find_spec("moss_transcribe_diarize") is not None
    except (ImportError, ValueError):
        return False


def _qwen_batching_status() -> tuple:
    """(installed qwen-asr version or None, whether batching can run with it)."""
    try:
        import asr_backend
        version = asr_backend.installed_qwen_asr_version()
        return version, version == asr_backend.QWEN_ASR_BATCH_TESTED_VERSION
    except Exception:
        return None, False


def get_asr_options() -> dict:
    qwen_version, batching_available = _qwen_batching_status()
    return {
        "qwen_asr_batch_size": get_qwen_asr_batch_size(),
        # Batching runs only with the tested qwen-asr; any other version
        # sends one segment at a time whatever the saved size.
        "qwen_asr_version": qwen_version,
        "qwen_asr_batching_available": batching_available,
        "qwen_asr_batch_min": MIN_BATCH_SIZE,
        "qwen_asr_batch_max": MAX_BATCH_SIZE,
        "moss_experimental": get_moss_experimental(),
        "qwen_vad_refine_timing": get_vad_refine_timing(),
        "mixed_languages": get_mixed_languages(),
        "moss_installed": moss_installed(),
    }


def set_asr_options(qwen_asr_batch_size=None, moss_experimental=None,
                    qwen_vad_refine_timing=None, mixed_languages=None) -> dict:
    """Saves whichever option is passed (None = unchanged). Raises
    InvalidInputError for a batch size outside 1..16 or a non-boolean
    toggle. Returns get_asr_options()."""
    if qwen_asr_batch_size is not None:
        if isinstance(qwen_asr_batch_size, bool) or not isinstance(qwen_asr_batch_size, int) \
                or not MIN_BATCH_SIZE <= qwen_asr_batch_size <= MAX_BATCH_SIZE:
            raise InvalidInputError(
                f"Qwen3-ASR batch size must be a whole number from {MIN_BATCH_SIZE} "
                f"to {MAX_BATCH_SIZE}.")
    if moss_experimental is not None and not isinstance(moss_experimental, bool):
        raise InvalidInputError("moss_experimental must be true or false.")
    if qwen_vad_refine_timing is not None and not isinstance(qwen_vad_refine_timing, bool):
        raise InvalidInputError("qwen_vad_refine_timing must be true or false.")
    if mixed_languages is not None and not isinstance(mixed_languages, bool):
        raise InvalidInputError("mixed_languages must be true or false.")
    if qwen_asr_batch_size is not None:
        db.set_app_setting(QWEN_ASR_BATCH_KEY, qwen_asr_batch_size)
    if moss_experimental is not None:
        db.set_app_setting(MOSS_EXPERIMENTAL_KEY, moss_experimental)
    if qwen_vad_refine_timing is not None:
        db.set_app_setting(VAD_REFINE_KEY, qwen_vad_refine_timing)
    if mixed_languages is not None:
        db.set_app_setting(MIXED_LANGUAGES_KEY, mixed_languages)
    return get_asr_options()


def stored_asr_backend(drama) -> str:
    """The drama's saved backend; one that never saved a choice gets Qwen3-ASR
    on long windows for Chinese and Japanese when qwen-asr, torch and
    faster-whisper (its speech detector) are installed, else Whisper."""
    saved = drama.get("asr_backend_choice")
    if saved:
        return saved
    if ((drama.get("source_language") or "zh") in QWEN_LONG_DEFAULT_LANGUAGES
            and all(importlib.util.find_spec(m) is not None
                    for m in ("qwen_asr", "torch", "faster_whisper"))):
        return "qwen3_asr_long"
    return "whisper"
