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
- voice_detector: which speech detector the "with speech detection" Qwen3
  backends use. "auto" picks the ASMR-trained one for an asmr title and the
  standard (Silero) one otherwise; "asmr" and "standard" force one. The ASMR
  model is a separate, user-requested download (asmr_vad.py); without it, or
  without onnxruntime, a run falls back to standard and says so.
- stored_asr_backend: a title's saved backend, Whisper when none is saved.

UI-free. Writes are PC-only (the router uses local_only()).
"""

import importlib.util

import background_jobs
import db
import qwen3_native
from services.service_errors import ConflictError, InvalidInputError

ASMR_VAD_JOB_ID = "asmr_vad_download"

QWEN_ASR_BATCH_KEY = "qwen_asr_batch_size"
VAD_REFINE_KEY = "qwen_vad_refine_timing"
MIXED_LANGUAGES_KEY = "mixed_languages"
VOICE_DETECTOR_KEY = "voice_detector"
VOICE_DETECTOR_CHOICES = ("auto", "asmr", "standard")
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


def get_voice_detector() -> str:
    """The saved choice; "auto" on a DB hiccup or an unknown stored value."""
    try:
        value = db.get_app_setting(VOICE_DETECTOR_KEY, "auto")
    except Exception:
        return "auto"
    return value if value in VOICE_DETECTOR_CHOICES else "auto"


def resolve_voice_detector(drama, source_language=None) -> str:
    """"asmr" (the user chose it), "auto_asmr" or "standard" for this run.

    Auto uses the ASMR detector only for a Japanese ASMR title with
    onnxruntime and the model both present: the model is trained on Japanese,
    and a user who never opted in must see no notice or warning when it is
    missing."""
    choice = get_voice_detector()
    if choice != "auto":
        return choice
    language = source_language or drama.get("source_language") or "zh"
    if drama.get("media_type") != "asmr" or language != "ja":
        return "standard"
    try:
        found = _asmr_vad_status()
    except Exception:
        return "standard"
    ready = found["asmr_vad_onnxruntime_installed"] and found["asmr_vad_model_downloaded"]
    return "auto_asmr" if ready else "standard"


def _qwen_batching_status() -> tuple:
    """(installed transformers version or None, whether Qwen3-ASR can run with it)."""
    version = qwen3_native.installed_transformers_version()
    return version, version is not None and qwen3_native.transformers_problem() is None


def _asmr_vad_status() -> dict:
    import asmr_vad
    found = asmr_vad.status()
    return {"asmr_vad_onnxruntime_installed": found["onnxruntime_installed"],
            "asmr_vad_model_downloaded": found["model_downloaded"],
            "asmr_vad_download_job_id": ASMR_VAD_JOB_ID}


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
        "voice_detector": get_voice_detector(),
        **_asmr_vad_status(),
    }


def set_asr_options(qwen_asr_batch_size=None, qwen_vad_refine_timing=None,
                    mixed_languages=None, voice_detector=None) -> dict:
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
    if voice_detector is not None and voice_detector not in VOICE_DETECTOR_CHOICES:
        raise InvalidInputError(
            f"voice_detector must be one of {', '.join(VOICE_DETECTOR_CHOICES)}.")
    if voice_detector is not None:
        db.set_app_setting(VOICE_DETECTOR_KEY, voice_detector)
    if qwen_asr_batch_size is not None:
        db.set_app_setting(QWEN_ASR_BATCH_KEY, qwen_asr_batch_size)
    if qwen_vad_refine_timing is not None:
        db.set_app_setting(VAD_REFINE_KEY, qwen_vad_refine_timing)
    if mixed_languages is not None:
        db.set_app_setting(MIXED_LANGUAGES_KEY, mixed_languages)
    return get_asr_options()


def start_asmr_vad_download() -> dict:
    """Starts the model download (PC only; the route is local_only()). Never
    runs unless the user pressed the button. Raises ConflictError when it is
    already downloaded or a download is running."""
    import asmr_vad
    if asmr_vad.status()["model_downloaded"]:
        raise ConflictError("The ASMR voice detector model is already downloaded.")
    if not background_jobs.start_job(ASMR_VAD_JOB_ID, _download_job,
                                     description="Downloading the ASMR voice detector"):
        raise ConflictError("The ASMR voice detector is already downloading.")
    return {"job_id": ASMR_VAD_JOB_ID, "started": True}


def _download_job():
    import asmr_vad
    try:
        asmr_vad.download_model(
            progress_cb=lambda f: background_jobs.update_progress(
                ASMR_VAD_JOB_ID, f * 0.99, f"Downloading... {f * 100:.0f}%"),
            cancel_check=_raise_if_cancelled)
    except asmr_vad.AsmrVadUnavailable as exc:
        background_jobs.set_result(ASMR_VAD_JOB_ID, {"status": "failed", "detail": str(exc)})
        raise
    background_jobs.set_result(ASMR_VAD_JOB_ID, {"status": "ok"})


def _raise_if_cancelled():
    if background_jobs.is_cancel_requested(ASMR_VAD_JOB_ID):
        raise background_jobs.JobCancelled()


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
