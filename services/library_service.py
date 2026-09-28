"""
services/library_service.py -- read-only Library queries, shared by the
Streamlit Library tab and the FastAPI `/api/library` routes.

This is the first piece of the React + FastAPI migration's service layer
(see `docs/migration-react-fastapi.md`). Before it existed, the Library
tab's "All dramas" list did part of its filtering in `db.list_dramas`
(SQL) and the rest inline in the widget code (the Quick-filter pill and
the Custom-tags multiselect), so any second UI would have had to copy
that logic and hope it stayed in sync. Now both UIs call
`list_library_dramas` and get the same answer for the same filters.

Deliberately thin: it composes existing `db.py` functions and keeps
their semantics exactly -- the Quick filter is case-insensitive (via
`db.has_custom_tag`, so a hand-typed "favorite" still counts), the
Custom-tags filter is an exact match on each comma-separated tag, and
every tag picked must be present. No Streamlit import, no HTTP types:
it takes plain values and returns plain dicts, so `cli.py` could call it
too.
"""

import db
from services.service_errors import InvalidInputError, NotFoundError


def split_custom_tags(drama: dict) -> list:
    """A drama's `custom_tags` column (comma-separated text) as a list of
    trimmed, non-empty tags, in stored order."""
    return [t.strip() for t in (drama.get("custom_tags") or "").split(",") if t.strip()]


def list_library_dramas(search: str = "", studio: str = "", author: str = "",
                        voice_actor: str = "", status: str = "", source_language: str = "",
                        media_type: str = "", quick_filter: str = None, custom_tags=()):
    """Every drama matching all the given filters, newest first -- the
    Library tab's "All dramas" list.

    Empty-string filters mean "any" (same as `db.list_dramas`).
    `quick_filter` must be one of `db.ORGANIZATIONAL_TAGS` or None;
    anything else raises `InvalidInputError` rather than silently
    matching nothing, since those tags are a fixed set the UI offers.
    `custom_tags` is free-form: a drama must carry every one listed."""
    if quick_filter and quick_filter not in db.ORGANIZATIONAL_TAGS:
        raise InvalidInputError(
            f"Unknown quick filter {quick_filter!r}.",
            details={"allowed": list(db.ORGANIZATIONAL_TAGS)})
    dramas = db.list_dramas(search=search, studio=studio, author=author,
                            voice_actor=voice_actor, status=status,
                            source_language=source_language, media_type=media_type)
    if quick_filter:
        dramas = [d for d in dramas if db.has_custom_tag(d, quick_filter)]
    if custom_tags:
        dramas = [d for d in dramas
                  if all(t in [x.strip() for x in (d.get("custom_tags") or "").split(",")]
                         for t in custom_tags)]
    return dramas


def get_library_drama(drama_id: int) -> dict:
    """One drama's full row. Raises `InvalidInputError` for a
    non-positive id and `NotFoundError` if no such drama exists, instead
    of `db.get_drama`'s bare None, so every caller reports the same
    thing the same way."""
    if not isinstance(drama_id, int) or isinstance(drama_id, bool) or drama_id < 1:
        raise InvalidInputError("A drama id is a positive whole number.")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama
