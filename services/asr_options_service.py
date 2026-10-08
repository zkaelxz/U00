"""
services/asr_options_service.py -- the experimental transcription
settings, stored in db.app_settings like use_gpu.

- qwen_asr_batch_size: how many Whisper segments go to Qwen3-ASR
  in one call when a drama's ASR backend is Qwen3-ASR. 1 (the default) keeps
  the original one-segment-at-a-time behaviour; batching is opt-in until the
  user's real GPU comparison shows it doesn't change the text.
- qwen_vad_refine_timing: with the "Qwen3 ASR with speech detection" backend, also
  tightens each line's times with Qwen3-ForcedAligner. Off by default.
- mixed_languages: detect the spoken language per speech span and write it on
  each line that differs from the title's language (mixed_language.py). Off by
  default; slower (one detection per span).
- stored_asr_backend: a title's saved backend, Whisper when none is saved.

UI-free. Writes are PC-only (the router uses local_only()).
"""

import importlib.util

import db
import qwen3_native
from services.service_errors import InvalidInputError

QWEN_ASR_BATCH_KEY = "qwen_asr_batch_size"
VAD_REFINE_KEY = "qwen_vad_refine_timing"
MIXED_LANGUAGES_KEY = "mixed_languages"
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 16

ASR_BACKEND_CHOICES = ("whisper", "qwen3_asr", "qwen3_asr_vad", "qwen3_asr_long")
# Backends that used to be offered. A title may still have one saved, so it
# loads as the default backend with a notice; the saved value is left alone.
REMOVED_ASR_BACKENDS = {"moss_td": "MOSS-Transcribe-Diarize"}


def get_qwen_asr_batch_size() -> int:
    """The saved batch size, clamped to 1..16. A DB hiccup or a bad stored
    value means 1 (batching off)."""
    try:
        value = int(db.get_app_setting(QWEN_ASR_BATCH_KEY, MIN_BATCH_SIZE))
    except Exception:
        return MIN_BATCH_SIZE
    return min(MAX_BATCH_SIZE, max(MIN_BATCH_SIZE, value))


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


def _qwen_batching_status() -> tuple:
    """(installed transformers version or None, whether Qwen3-ASR can run with it)."""
    version = qwen3_native.installed_transformers_version()
    return version, version is not None and qwen3_native.transformers_problem() is None


def get_asr_options() -> dict:
    qwen_version, batching_available = _qwen_batching_status()
    return {
        "qwen_asr_batch_size": get_qwen_asr_batch_size(),
        # Qwen3-ASR (and so batching) runs on transformers 5.15+; with an
        # older one it can't run at all. The field names predate the move
        # off the qwen-asr package and are kept for the frontend.
        "qwen_asr_version": qwen_version,
        "qwen_asr_batching_available": batching_available,
        "qwen_asr_batch_min": MIN_BATCH_SIZE,
        "qwen_asr_batch_max": MAX_BATCH_SIZE,
        "qwen_vad_refine_timing": get_vad_refine_timing(),
        "mixed_languages": get_mixed_languages(),
    }


def set_asr_options(qwen_asr_batch_size=None, qwen_vad_refine_timing=None,
                    mixed_languages=None) -> dict:
    """Saves whichever option is passed (None = unchanged). Raises
    InvalidInputError for a batch size outside 1..16 or a non-boolean
    toggle. Returns get_asr_options()."""
    if qwen_asr_batch_size is not None:
        if isinstance(qwen_asr_batch_size, bool) or not isinstance(qwen_asr_batch_size, int) \
                or not MIN_BATCH_SIZE <= qwen_asr_batch_size <= MAX_BATCH_SIZE:
            raise InvalidInputError(
                f"Qwen3-ASR batch size must be a whole number from {MIN_BATCH_SIZE} "
                f"to {MAX_BATCH_SIZE}.")
    if qwen_vad_refine_timing is not None and not isinstance(qwen_vad_refine_timing, bool):
        raise InvalidInputError("qwen_vad_refine_timing must be true or false.")
    if mixed_languages is not None and not isinstance(mixed_languages, bool):
        raise InvalidInputError("mixed_languages must be true or false.")
    if qwen_asr_batch_size is not None:
        db.set_app_setting(QWEN_ASR_BATCH_KEY, qwen_asr_batch_size)
    if qwen_vad_refine_timing is not None:
        db.set_app_setting(VAD_REFINE_KEY, qwen_vad_refine_timing)
    if mixed_languages is not None:
        db.set_app_setting(MIXED_LANGUAGES_KEY, mixed_languages)
    return get_asr_options()


def removed_asr_backend_notice(drama):
    """A plain notice when the title's saved backend was removed, else None."""
    label = REMOVED_ASR_BACKENDS.get(drama.get("asr_backend_choice"))
    if not label:
        return None
    return f"The {label} backend was removed, so this title now uses its default backend."


def stored_asr_backend(drama) -> str:
    """The drama's saved backend, else Whisper. A title that never chose Qwen3
    stays on Whisper: Qwen3-ASR downloads several GB of weights on first use,
    and transformers 5.15+ alone (which other features install) is no sign the
    user wants that. A saved backend that has been removed also reads as
    Whisper."""
    saved = drama.get("asr_backend_choice")
    return saved if saved and saved not in REMOVED_ASR_BACKENDS else "whisper"
