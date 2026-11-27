"""services/next_episode_service.py -- the "Next" card at the end of an episode on Watch: which title
follows in its series, how far along it is, and putting its steps on the queue ahead of time."""

from typing import Optional

import db
from lib.errors import ConflictError, NotFoundError
from engine_backends import engine_registry
from services import ownership_service, queue_service, title_copy_service

READY, WORKING, IDLE, NO_MEDIA = "ready", "working", "idle", "no_media"


def _reading_order(dramas: list) -> list:
    # Without episode numbers the library lists newest first; the episode after this one is the
    # next one created.
    if any(d.get("episode_number") is None for d in dramas) and any(d.get("episode_number") is not None for d in dramas):
        # The library lists the unnumbered tail newest first; it reads oldest first.
        numbered = [d for d in dramas if d.get("episode_number") is not None]
        return numbered + list(reversed([d for d in dramas if d.get("episode_number") is None]))
    if any(d.get("episode_number") is not None for d in dramas):
        return dramas
    return list(reversed(dramas))


def _following(principal, drama_id: int) -> Optional[dict]:
    drama = ownership_service.require_drama(drama_id)
    if not drama.get("series_id"):
        return None
    ordered = [d for d in _reading_order(db.list_dramas_by_series(drama["series_id"]))
               if not db.has_custom_tag(d, title_copy_service.COPY_TAG)]
    ids = [d["id"] for d in ordered]
    if drama_id not in ids:
        return None
    for d in ordered[ids.index(drama_id) + 1:]:
        if ownership_service.can_see_drama(principal, d["id"]):
            return d
    return None


def _state(principal, drama: dict) -> str:
    if any((ln.get("en") or "").strip() for ln in db.load_lines(drama["id"])):
        return READY
    queued = [i for i in queue_service.get_queue(principal)["items"]
              if i["drama_id"] == drama["id"] and i["status"] in (queue_service.WAITING, queue_service.RUNNING)]
    if queued:
        return WORKING
    if drama.get("audio_filename") or drama.get("source_video_filename"):
        return IDLE
    return NO_MEDIA


def _is_free(drama: dict) -> bool:
    """Free the way the queue prices a run."""
    return queue_service._translate_engine(drama) in engine_registry.FREE_ENGINES


def get_next(principal, drama_id: int) -> dict:
    nxt = _following(principal, drama_id)
    if nxt is None:
        return {"drama_id": None, "title": "", "state": NO_MEDIA, "free": False}
    return {"drama_id": nxt["id"], "title": nxt.get("title_en") or nxt.get("title_zh") or f"Title {nxt['id']}",
            "state": _state(principal, nxt),
            # The page starts a free run by itself near the end of an episode; a paid one waits for a tap.
            "free": _is_free(nxt)}


def prepare_next(principal, drama_id: int, holds_paid: bool, *, at_pc: bool) -> dict:
    """Queues Transcribe, Translate and Export for the next title as the caller, so a member's
    limits, paid-engine rule and the shared queue apply as for a click on the Jobs page."""
    nxt = _following(principal, drama_id)
    if nxt is None:
        raise NotFoundError("There is no next title in this series.")
    state = _state(principal, nxt)
    if state == IDLE:
        if db.load_lines(nxt["id"]):
            # Transcribing again would replace lines that may be corrected by hand.
            for step in ("translate", "export"):
                queue_service.add_item(principal, nxt["id"], step, holds_paid, at_pc=at_pc)
        else:
            queue_service.add_all_steps(principal, nxt["id"], holds_paid, at_pc=at_pc)
    elif state == NO_MEDIA:
        raise ConflictError("The next title has no sound or video to work on yet.")
    return get_next(principal, drama_id)
