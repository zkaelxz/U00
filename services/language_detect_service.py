"""services/language_detect_service.py -- hears a title's opening with Whisper large-v3 and says which
source language it is, for a run that was told to pick the language itself."""

import core as core_module
import whisper_models
from services import gpu_process_job
from lib.errors import InvalidInputError, UnsupportedOperationError

# Never turbo: it is worse on every language the app has tested, and a wrong pick here would
# pick the wrong model for the whole run.
DETECT_MODEL = "large-v3"
# Model load plus a 2-minute decode of the opening; a cold first load downloads the model first.
DETECT_TIMEOUT_S = 600
# Below this the opening is probably music or silence; guessing would send the whole run
# through the wrong language's pipeline.
MIN_PROBABILITY = 0.60


def _worker(audio_path, use_gpu, scratch_dir, result_queue):
    import whisper_transcribe
    model = whisper_models.load_whisper_model(DETECT_MODEL, use_gpu)
    audio = whisper_transcribe._decode_opening(audio_path, whisper_transcribe.LANGUAGE_CHECK_WINDOW_S)
    language, probability, _all = model.detect_language(audio, vad_filter=True)
    result_queue.put(("ok", {"language": language, "probability": float(probability)}))


def pick_language(found: dict) -> str:
    """The app source language for a detection result, or an error saying why there is none."""
    language, probability = str(found.get("language") or ""), found.get("probability") or 0.0
    if language not in core_module.SOURCE_LANGUAGES:
        heard = core_module.LANGUAGE_NAMES.get(language) or language or "no clear language"
        raise UnsupportedOperationError(
            f"The audio sounds like {heard}, which Baihe can't transcribe yet. "
            "Pick Chinese, Japanese, Korean, Indonesian or Thai yourself.")
    if probability < MIN_PROBABILITY:
        raise InvalidInputError(
            "The language could not be told from the opening of the audio. Pick it yourself.")
    return language


def detect(drama_id: int, audio_path: str, use_gpu: bool) -> str:
    """The source language heard in the opening of `audio_path`. Runs large-v3 in its own process so
    the model is gone from memory afterwards."""
    try:
        found = gpu_process_job.run_in_child(
            f"detect_language_{drama_id}", _worker, (audio_path, use_gpu), timeout_s=DETECT_TIMEOUT_S)
    except gpu_process_job.ChildFailed as exc:
        raise UnsupportedOperationError(f"The language could not be detected: {exc}") from None
    return pick_language(found)
