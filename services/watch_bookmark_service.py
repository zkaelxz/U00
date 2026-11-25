"""A person's bookmarks on a title's sound or video (the Watch page): save a moment with a note, list, edit the note, delete."""

import db
from lib import ids as lib_ids
from lib.errors import ConflictError, NotFoundError
from services import ownership_service

# A tap that keeps adding would otherwise grow one title's list (and every load of it) without end.
MAX_BOOKMARKS_PER_TITLE = 200


def _require(drama_id: int) -> None:
    lib_ids.check_id(drama_id, "A title id is a positive whole number.")
    ownership_service.require_drama(drama_id)


def list_bookmarks(drama_id: int, user_id: int = 0) -> dict:
    """user_id 0 is the PC itself (no sign-in)."""
    _require(drama_id)
    return {"drama_id": drama_id, "bookmarks": db.list_watch_bookmarks(drama_id, user_id)}


def add_bookmark(drama_id: int, at_seconds: float, note: str, user_id: int = 0) -> dict:
    _require(drama_id)
    saved = db.add_watch_bookmark(drama_id, user_id, at_seconds, note.strip(), MAX_BOOKMARKS_PER_TITLE)
    if saved is None:
        raise ConflictError(f"A title holds at most {MAX_BOOKMARKS_PER_TITLE} bookmarks each. Delete one first.")
    return saved


def set_note(drama_id: int, bookmark_id: int, note: str, user_id: int = 0) -> dict:
    _require(drama_id)
    note = note.strip()
    if not db.set_watch_bookmark_note(drama_id, user_id, bookmark_id, note):
        raise NotFoundError("No such bookmark.")
    saved = next((b for b in db.list_watch_bookmarks(drama_id, user_id) if b["id"] == bookmark_id), None)
    if saved is None:
        raise NotFoundError("No such bookmark.")
    return saved


def delete_bookmark(drama_id: int, bookmark_id: int, user_id: int = 0) -> None:
    _require(drama_id)
    if not db.delete_watch_bookmark(drama_id, user_id, bookmark_id):
        raise NotFoundError("No such bookmark.")
