"""
services/listen_service.py -- the Reader's "Listen" for a narrated title: whether
a narration can be made, starting it, and the finished track. It is the narration
side of dub_service behind its own narrow shape, so the Reader needs no Dub route
or Dub screen: one voice engine, the title's narration language, no pacing limits
and no background music. The job, its id and the track file are the same ones the
Dub stage uses until that stage is removed.

No FastAPI import: plain dicts out, no filesystem path.
"""
from services import dub_service
from services.service_errors import InvalidInputError, NotFoundError

_NOT_NARRATED = "Listen is only for narrated novels."


def _require_narrated(config: dict) -> dict:
    if not config["is_narration"]:
        raise NotFoundError(_NOT_NARRATED)
    return config


def get_listen_config(drama_id: int) -> dict:
    """What Listen needs to show: the engines (each with why it can't run here),
    the language options, a blocker that applies whatever engine is picked, how
    many lines are speakable, whether a track exists and whether the GPU is used.
    NotFoundError for an unknown title or one that is not a narrated novel."""
    cfg = _require_narrated(dub_service.get_dub_config(drama_id))
    return {
        "drama_id": drama_id,
        "narration_language": cfg["narration_language"],
        "narration_language_options": cfg["narration_language_options"],
        "tts_engines": cfg["tts_engines"],
        "default_engine": cfg["default_engine"],
        "blocker": cfg["blocker"],
        "gpu_required": cfg["gpu_required"],
        "speakable_line_count": cfg["speakable_line_count"],
        "track_available": cfg["track_available"],
    }


def start_listen(drama_id: int, tts_engine: str, narration_language=None) -> dict:
    """Starts the narration track job; {"job_id"}. Same refusals as the Dub run
    (unknown engine or language, nothing to narrate, engine or ffmpeg missing,
    already running)."""
    _require_narrated(dub_service.get_dub_config(drama_id))
    if not tts_engine:
        raise InvalidInputError("No voice engine is available.")
    return dub_service.start_dub_run(drama_id, tts_engine=tts_engine, narration_language=narration_language)


def get_listen_track(drama_id: int) -> dict:
    """{path (never sent to clients), name} of the finished narration track.
    NotFoundError when there is none."""
    _require_narrated(dub_service.get_dub_config(drama_id))
    return dub_service.get_dub_track(drama_id)
