"""services/second_hearing_service.py -- the optional second hearing of a transcription's doubtful lines.

After a transcription saves its lines, the lowest-confidence ones are heard again by the
other ASR engine (Whisper <-> Qwen3-ASR) and flagged where the two disagree. Flag only:
merging two engines' text failed in testing, so no line text ever changes here.
"""
import applog
import db
from engine_backends.shared import redact_secrets
from lib.errors import ConflictError, UnsupportedOperationError
from services import asr_options_service, compare_transcription_service, ownership_service, settings_service
from services.review_lines_service import SHAKY_BELOW

# Each extra line is another window for the second engine to hear, so a bad run (a noisy
# title where most lines are doubtful) is capped to its worst lines.
MAX_LINES = 30


def doubtful_line_ids(lines) -> list:
    """Ids of the lowest-confidence lines, worst first, up to MAX_LINES. A line with no
    confidence (Qwen3, imported text) or an already-set flag is not doubtful here."""
    shaky = [ln for ln in lines
             if ln.confidence is not None and ln.confidence < SHAKY_BELOW
             and not ln.flag and not ln.reviewed and (ln.zh or "").strip()]
    shaky.sort(key=lambda ln: ln.confidence)
    return [ln.id for ln in shaky[:MAX_LINES]]


def other_engine(saved_backend: str) -> str:
    return "whisper" if saved_backend != "whisper" else "qwen3_asr"


def after_run(drama_id: int) -> bool:
    """Called after every saved transcription. Starts the second hearing when the setting is
    on and there are doubtful lines. Never raises: the transcription already succeeded and
    this is an extra. The hearing is a queued GPU job, so the GPU guard holds it like any other."""
    try:
        if not settings_service.get("second_hearing"):
            return False
        ids = doubtful_line_ids(db.load_line_objects(drama_id))
        if not ids:
            return False
        drama = ownership_service.require_drama(drama_id)
        backend = other_engine(asr_options_service.stored_asr_backend(drama))
        if compare_transcription_service._backend_problem(
                backend, drama.get("source_language") or "zh"):
            return False
        compare_transcription_service.start_compare(
            drama_id, {"kind": "line_ids", "line_ids": ids}, asr_backend_choice=backend,
            after_transcription=True)
        return True
    except (UnsupportedOperationError, ConflictError):
        return False
    except Exception as exc:
        applog.get_logger().warning(
            f"second hearing not started for drama {drama_id}: {redact_secrets(str(exc))}")
        return False
