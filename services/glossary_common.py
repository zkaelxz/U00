"""
services/glossary_common.py -- helpers shared by glossary_service and
glossary_extract_service.

They live below both so the two modules never import each other: a
cycle there made the import order matter. No FastAPI import.
"""
import secrets
import sqlite3
from typing import Optional

import db

from lib.errors import ConflictError, InvalidInputError
from services.ownership_service import require_drama as _drama  # noqa: F401  (the glossary services import it from here)

MAX_TERM_LEN = 200
MAX_NOTES_LEN = 1000


def _series_id(drama: dict, *, required: bool) -> Optional[int]:
    sid = drama.get("series_id")
    if sid or not required:
        return sid or None
    return _own_series(drama)


def _own_series(drama: dict) -> int:
    """A title with no series gets one of its own on its first glossary write, so
    a single novel can have a glossary. The title id in the name keeps it from
    colliding with another person's series; it carries the title's owner and
    privacy, and the title's own privacy flag moves to it like on any series
    assignment."""
    title = (drama.get("title_en") or drama.get("title_zh") or "").strip()[:100]
    name = f"{title} (title {drama['id']})" if title else f"Title {drama['id']}"
    # A series someone else already named this is never joined: retry with a random
    # suffix, which a person can't pre-create to block the title.
    for attempt in range(5):
        try:
            sid = db.create_series(name if attempt == 0 else f"{name} {secrets.token_hex(3)}",
                                   owner_user_id=drama.get("owner_user_id"),
                                   is_private=bool(drama.get("is_private")))
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise ConflictError("Could not create a series for this title; try again.")
    if not db.dramas.assign_series_if_none(drama["id"], sid):
        # A series was chosen in the meantime: that one wins, and the new one is empty.
        db.delete_series(sid)
        sid = (db.get_drama(drama["id"]) or {}).get("series_id")
        if not sid:
            raise ConflictError("Could not create a series for this title; try again.")
    drama["series_id"], drama["is_private"] = sid, 0
    return sid


def _text(value, field: str, max_len: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise InvalidInputError(f"{field} must be text.")
    value = value.strip()
    if required and not value:
        raise InvalidInputError(f"{field} is required.")
    if len(value) > max_len:
        raise InvalidInputError(f"{field} is too long (max {max_len} characters).")
    return value
