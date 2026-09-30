"""
services/asr_options_service.py -- the two experimental transcription
settings (Steps 103 and 104), stored in db.app_settings like use_gpu.

- qwen_asr_batch_size (Step 103): how many Whisper segments go to Qwen3-ASR
  in one call when a drama's ASR backend is Qwen3-ASR. 1 (the default) keeps
  the original one-segment-at-a-time behaviour; batching is opt-in until the
  user's real GPU comparison shows it doesn't change the text.
- moss_experimental (Step 104): allows MOSS-Transcribe-Diarize as a drama's
  ASR backend. Off by default; with it off the backend can't be chosen or run.

UI-free. Writes are PC-only (the router uses local_only()).
"""

import importlib.util

import db
from services.service_errors import InvalidInputError

QWEN_ASR_BATCH_KEY = "qwen_asr_batch_size"
MOSS_EXPERIMENTAL_KEY = "moss_experimental"
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 16


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


def moss_installed() -> bool:
    """Whether the moss_transcribe_diarize package can be imported (checked
    without importing it)."""
    try:
        return importlib.util.find_spec("moss_transcribe_diarize") is not None
    except (ImportError, ValueError):
        return False


def get_asr_options() -> dict:
    return {
        "qwen_asr_batch_size": get_qwen_asr_batch_size(),
        "qwen_asr_batch_min": MIN_BATCH_SIZE,
        "qwen_asr_batch_max": MAX_BATCH_SIZE,
        "moss_experimental": get_moss_experimental(),
        "moss_installed": moss_installed(),
    }


def set_asr_options(qwen_asr_batch_size=None, moss_experimental=None) -> dict:
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
    if qwen_asr_batch_size is not None:
        db.set_app_setting(QWEN_ASR_BATCH_KEY, qwen_asr_batch_size)
    if moss_experimental is not None:
        db.set_app_setting(MOSS_EXPERIMENTAL_KEY, moss_experimental)
    return get_asr_options()
