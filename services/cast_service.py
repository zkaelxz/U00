"""services/cast_service.py -- the short-lived links a TV fetches the subtitled copy of an episode
by, since a TV has no sign-in. A link is a random secret naming one title; only its hash is kept,
in memory, so a restart ends every link."""

import hashlib
import os
import secrets
import threading
import time
from typing import Optional

import background_jobs
from lib.errors import ConflictError, NotFoundError
from services import artifact_service, ownership_service

LINK_SECONDS = 6 * 3600     # a long film plus pauses; the TV only needs the link while it plays
MAX_LINKS_PER_USER = 5
MAX_LINKS = 200
# What a TV's built-in player opens; a .mkv copy is made by the export when the source is one.
PLAYABLE = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm"}

READY, WORKING, MISSING, UNSUPPORTED = "ready", "working", "missing", "unsupported"

_lock = threading.Lock()
_links: dict = {}     # sha256 of the secret -> {"drama_id", "user_id", "expires"}


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _prune(now: float) -> None:
    for key in [k for k, v in _links.items() if v["expires"] <= now]:
        del _links[key]


def _burned(drama_id: int) -> Optional[dict]:
    return artifact_service.find_artifact(drama_id, "video")


def status(drama_id: int) -> dict:
    ownership_service.require_drama(drama_id)
    job = background_jobs.get_status(f"burned_video_{drama_id}")
    if job and job["status"] in ("running", "queued"):
        return {"state": WORKING}
    art = _burned(drama_id)
    # A copy burned with the original language alone is no use on a TV for English viewers.
    if art is None or art["language"] not in ("", "en") and not art["language"].endswith("+en"):
        return {"state": MISSING}
    if os.path.splitext(art["name"])[1].lower() not in PLAYABLE:
        return {"state": UNSUPPORTED}
    return {"state": READY}


def create_link(principal, drama_id: int) -> dict:
    """A link to the title's subtitled copy for the caller; the title is visible to them already
    (the route's path guard) and the copy must exist and be in a format a TV opens."""
    if status(drama_id)["state"] != READY:
        raise ConflictError("The subtitled copy of this title is not ready to cast.")
    secret = secrets.token_urlsafe(32)
    uid = ownership_service.user_id(principal)
    now = time.time()
    with _lock:
        _prune(now)
        mine = sorted((k for k, v in _links.items() if v["user_id"] == uid), key=lambda k: _links[k]["expires"])
        # The oldest goes, so casting again always works.
        for key in mine[:max(0, len(mine) - MAX_LINKS_PER_USER + 1)]:
            del _links[key]
        if len(_links) >= MAX_LINKS:
            raise ConflictError("Too many cast links are open. Try again later.")
        _links[_digest(secret)] = {"drama_id": drama_id, "user_id": uid, "expires": now + LINK_SECONDS}
    return {"path": f"/api/cast/video?k={secret}", "expires_in": LINK_SECONDS}


def resolve(secret: str) -> tuple:
    """(file path, content type) for a live link. Every failure is the same 404, so a guess learns
    nothing; the file is looked up afresh, so a title deleted or re-exported meanwhile is followed."""
    now = time.time()
    with _lock:
        _prune(now)
        entry = _links.get(_digest(secret))
    if entry is None:
        raise NotFoundError("This cast link has ended.")
    art = _burned(entry["drama_id"])
    ctype = PLAYABLE.get(os.path.splitext(art["name"])[1].lower()) if art else None
    if art is None or ctype is None:
        raise NotFoundError("This cast link has ended.")
    return art["path"], ctype
