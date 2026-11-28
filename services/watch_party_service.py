"""services/watch_party_service.py -- watch parties: one account's phones playing the same title in step.

A room is memory only (a restart ends every party) and belongs to the account that made it: another account gets the
same "not found" as a code that never existed, so a guessed code reveals nothing. Joining needs no new access to the
title, because only the owner's own sessions can reach the room. The room keeps one play state (playing, a position and
the server time it was true); clients work out where the sound should be from it and correct their own drift.

No FastAPI import.
"""
import math
import secrets
import threading
import time
import weakref

from lib import ids as lib_ids
from lib.errors import InvalidInputError, NotFoundError, RateLimitedError
from services import event_stream_service, ownership_service

MAX_ROOMS_PER_PRINCIPAL = 3
MAX_ROOMS = 50
MAX_STREAMS_PER_PRINCIPAL = 6
MAX_STREAMS = 32
ROOM_IDLE_SECONDS = 6 * 3600
# A film is not longer than this; it only bounds what a client may claim.
MAX_POSITION_SECONDS = 24 * 3600

_lock = threading.Lock()
_rooms: dict = {}
# Weak, so a stream whose response never started (the phone left first) cannot hold a slot for ever; a running
# stream's generator holds its Stream and closes it in `finally`.
_streams = weakref.WeakSet()


class Stream:
    """One open listener; counted while something holds it."""

    def __init__(self, owner: str, code: str):
        self.owner = owner
        self.code = code


def _now() -> float:
    return time.time()


def _view(room: dict) -> dict:
    return {"code": room["code"], "drama_id": room["drama_id"], "playing": room["playing"],
            "position": room["position"], "at": room["at"], "now": _now(), "seq": room["seq"],
            "watching": sum(1 for st in _streams if st.code == room["code"])}


def _drop_idle(now: float) -> None:
    for code in [c for c, r in _rooms.items() if now - r["touched"] > ROOM_IDLE_SECONDS]:
        del _rooms[code]


def _room(principal, code: str) -> dict:
    room = _rooms.get(code) if isinstance(code, str) else None
    if room is None or room["owner"] != event_stream_service.principal_key(principal):
        raise NotFoundError("That watch party does not exist or has ended.")
    return room


def create_room(principal, drama_id: int) -> dict:
    lib_ids.check_id(drama_id, "A title id is a positive whole number.")
    ownership_service.require_visible(principal, "drama", drama_id)
    owner = event_stream_service.principal_key(principal)
    now = _now()
    with _lock:
        _drop_idle(now)
        if sum(1 for r in _rooms.values() if r["owner"] == owner) >= MAX_ROOMS_PER_PRINCIPAL or len(_rooms) >= MAX_ROOMS:
            raise RateLimitedError("Too many watch parties are open. End one first.")
        code = secrets.token_urlsafe(9)
        _rooms[code] = {"code": code, "drama_id": drama_id, "owner": owner, "playing": False, "position": 0.0,
                        "at": now, "seq": 1, "touched": now}
        return _view(_rooms[code])


def set_state(principal, code: str, playing: bool, position: float) -> dict:
    if not isinstance(playing, bool) or isinstance(position, bool) or not isinstance(position, (int, float)) \
            or not math.isfinite(position) or not 0 <= position <= MAX_POSITION_SECONDS:
        raise InvalidInputError("A play state is playing or paused and a position within the title.")
    now = _now()
    with _lock:
        room = _room(principal, code)
        room.update(playing=playing, position=float(position), at=now, seq=room["seq"] + 1, touched=now)
        return _view(room)


def end_room(principal, code: str) -> None:
    with _lock:
        _room(principal, code)
        del _rooms[code]


def open_stream(principal, code: str):
    """(Stream, room): one more listener; RateLimitedError at the caps. The caller closes the Stream when done."""
    owner = event_stream_service.principal_key(principal)
    with _lock:
        room = _room(principal, code)
        mine = sum(1 for st in _streams if st.owner == owner)
        if mine >= MAX_STREAMS_PER_PRINCIPAL or len(_streams) >= MAX_STREAMS:
            raise RateLimitedError("Too many watch party connections are open.")
        stream = Stream(owner, code)
        _streams.add(stream)
        room["seq"] += 1  # the others see the new count
        return stream, _view(room)


def close_stream(stream: Stream) -> None:
    with _lock:
        _streams.discard(stream)
        room = _rooms.get(stream.code)
        if room is not None:
            room["seq"] += 1


def poll(principal, code: str, seen_seq: int):
    """The room's view when it moved past `seen_seq`, "same" when it did not, None once it has ended or is no
    longer this caller's. One lock, so a room ending between a check and a read cannot split them."""
    with _lock:
        try:
            room = _room(principal, code)
        except NotFoundError:
            return None
        return _view(room) if room["seq"] != seen_seq else "same"
