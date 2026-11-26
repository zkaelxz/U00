"""services/cast_service.py -- the short-lived links a TV or a friend fetches the subtitled copy of an
episode by, since neither has a sign-in. A link is a random secret naming one title; only its hash is
kept, in memory, so a restart ends every link."""

import hashlib
import os
import secrets
import threading
import time
from typing import Optional

import background_jobs
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import artifact_service, auth_service, ownership_service

LINK_SECONDS = 6 * 3600     # a long film plus pauses; the TV only needs the link while it plays
MAX_LINKS_PER_USER = 5
MAX_LINKS = 200
# A link given to a person stays open for days, so it has its own count: casting again must not push
# a shared link out, and a pile of shares must not push out the cast link a TV is playing from.
SHARE_DAYS = (1, 3, 7)
MAX_SHARES_PER_USER = 10
# What a TV's built-in player opens; a .mkv copy is made by the export when the source is one.
PLAYABLE = {".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm"}

READY, WORKING, MISSING, UNSUPPORTED = "ready", "working", "missing", "unsupported"

_lock = threading.Lock()
_links: dict = {}     # sha256 of the secret -> {"drama_id", "user_id", "expires", "share"}


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _prune(now: float) -> None:
    for key in [k for k, v in _links.items() if v["expires"] <= now]:
        del _links[key]


def _burned(drama_id: int) -> Optional[dict]:
    """The title's newest English-subtitled copy a TV can open, else None."""
    try:
        art = artifact_service.find_artifact(drama_id, "video")
    except NotFoundError:
        return None     # a deleted title reads as no copy, not as a message naming its id
    # A copy burned with the original language alone is no use on a TV for English viewers.
    if art is None or art["language"] not in ("", "en") and not art["language"].endswith("+en"):
        return None
    return art


def status(drama_id: int) -> dict:
    ownership_service.require_drama(drama_id)
    job = background_jobs.get_status(f"burned_video_{drama_id}")
    if job and job["status"] in ("running", "queued"):
        return {"state": WORKING}
    art = _burned(drama_id)
    if art is None:
        return {"state": MISSING}
    if os.path.splitext(art["name"])[1].lower() not in PLAYABLE:
        return {"state": UNSUPPORTED}
    return {"state": READY}


def create_link(principal, drama_id: int, share_days: Optional[int] = None) -> dict:
    """A link to the title's subtitled copy for the caller; the title is visible to them already
    (the route's path guard) and the copy must exist and be in a format a TV opens. `share_days` makes
    it a link to hand to a person: it lasts that many days instead of the TV's hours."""
    if share_days is not None and share_days not in SHARE_DAYS:
        raise InvalidInputError(f"A shared link lasts {', '.join(map(str, SHARE_DAYS))} days.")
    share = share_days is not None
    seconds = share_days * 86400 if share else LINK_SECONDS
    cap = MAX_SHARES_PER_USER if share else MAX_LINKS_PER_USER
    # Visible to the caller in the Guest sense too: the route's path guard asks only that they may edit.
    ownership_service.require_visible(principal, "drama", drama_id)
    uid = ownership_service.user_id(principal)
    # Fetches see the title as the member (no admin override): refuse here what could never play.
    if uid is not None:
        member = auth_service.member_principal(uid)
        # None would read as "auth off" to can_see_drama, so an unknown account is refused first.
        if member is None or not ownership_service.can_see_drama(member, drama_id):
            raise NotFoundError("Title not found.")
    if status(drama_id)["state"] != READY:
        raise ConflictError("The subtitled copy of this title is not ready to cast.")
    secret = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        _prune(now)
        mine = sorted((k for k, v in _links.items() if v["user_id"] == uid and v["share"] == share),
                      key=lambda k: _links[k]["expires"])
        # The oldest goes, so casting or sharing again always works.
        for key in mine[:max(0, len(mine) - cap + 1)]:
            del _links[key]
        if len(_links) >= MAX_LINKS:
            raise ConflictError("Too many cast links are open. Try again later.")
        _links[_digest(secret)] = {"drama_id": drama_id, "user_id": uid, "expires": now + seconds,
                                "share": share}
    return {"path": f"/api/cast/video?k={secret}", "expires_in": seconds}


def end_shares_of(principal, drama_id: int) -> int:
    """Ends the caller's shared links to one title (a person who handed out a link can take it back);
    returns how many ended. Cast links are left alone: a TV is playing from those."""
    ownership_service.require_visible(principal, "drama", drama_id)
    uid = ownership_service.user_id(principal)
    with _lock:
        gone = [k for k, v in _links.items() if v["share"] and v["user_id"] == uid and v["drama_id"] == drama_id]
        for key in gone:
            del _links[key]
    return len(gone)


def end_links_for(user_id: int) -> None:
    with _lock:
        for key in [k for k, v in _links.items() if v["user_id"] == user_id]:
            del _links[key]


def _may_still(entry: dict) -> bool:
    """The link's account is as able as when it made the link: signed-in accounts only, active, still
    holding media.stream and still seeing the title, so a deactivation, a revoked right or a title made
    private ends its links at once. Auth off (no account) has nothing to re-check."""
    uid = entry["user_id"]
    if uid is None:
        return True
    member = auth_service.member_principal(uid)
    return (member is not None and "media.stream" in auth_service.working_permissions(uid)
            and ownership_service.can_see_drama(member, entry["drama_id"]))


def resolve(secret: str) -> tuple:
    """(file path, content type, account id) for a live link. Every failure is the same 404, so a guess
    learns nothing; the file is looked up afresh, so a title deleted or re-exported meanwhile is followed."""
    now = time.time()
    with _lock:
        _prune(now)
        entry = _links.get(_digest(secret))
    art = _burned(entry["drama_id"]) if entry is not None and _may_still(entry) else None
    ctype = PLAYABLE.get(os.path.splitext(art["name"])[1].lower()) if art else None
    if entry is None or art is None or ctype is None:
        raise NotFoundError("This cast link has ended.")
    return art["path"], ctype, entry["user_id"]
