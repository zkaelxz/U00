"""
services/series_people_service.py -- add and edit a series' people
(series_characters): name, pronouns, aliases and notes. The UI-free half
of the Translate tab's "People & pronouns" editor in `tabs/workspace_tab.py`
(parity audit B1 #8, X15-X17). Listing stays in characters_service and
deleting in delete_service.

Series pronouns feed translation gender hints
(translation_guide.build_character_gender_hints), so a wrong one has to be
fixable here, not only deletable.

A person is always addressed by (series_id, character_id) and must belong
to that series; nothing is matched by name or list position. Updates are
field-scoped (db.update_series_character, one statement): None leaves a
field alone, "" clears it (the name can't be blank).

Differences from the tab, on purpose:
  - Adding a name the series already has is a 409. The tab's add form
    calls db.upsert_series_character, which silently resets that person's
    aliases and notes to "".
  - Renaming to a taken name is a 409 (the tab lets the sqlite
    IntegrityError escape).
  - The tab never edits aliases; this does (they feed Auto QC name checks).

No Streamlit or FastAPI import.
"""
import sqlite3
import unicodedata

import db
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

MAX_ID = 2**31 - 1
MAX_NAME_LEN = 200
MAX_PRONOUNS_LEN = 40
MAX_ALIASES_LEN = 1000
MAX_NOTES_LEN = 2000


def _check_id(name: str, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"{name} must be a whole number.")
    if value < 1 or value > MAX_ID:
        raise InvalidInputError(f"{name} is out of range.")


def _clean(name: str, value, cap: int, *, multiline: bool = False) -> str:
    """Stripped text, length capped, no control characters (a name or
    pronoun ends up on its own line of the translation prompt, so a
    newline in it could forge an extra hint line). Notes may keep
    newlines and tabs."""
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be a string.")
    value = value.strip()
    if len(value) > cap:
        raise InvalidInputError(f"{name} is too long (max {cap} characters).")
    allowed = {"\n", "\t"} if multiline else set()
    if any(unicodedata.category(ch) == "Cc" and ch not in allowed for ch in value):
        raise InvalidInputError(f"{name} can't contain control characters.")
    return value


def _entry(row: dict) -> dict:
    """Same shape as characters_service.list_series_characters (no voice
    fingerprint data)."""
    return {
        "id": row["id"],
        "character_name": row["character_name"],
        "aliases": row.get("aliases") or "",
        "notes": row.get("notes") or "",
        "pronouns": row.get("gender") or "",
    }


def _require_series(series_id) -> None:
    _check_id("series_id", series_id)
    if not any(s["id"] == series_id for s in db.list_series()):
        raise NotFoundError(f"No series with id {series_id}.")


def _find(series_id: int, character_id: int):
    return next((c for c in db.list_series_characters(series_id) if c["id"] == character_id), None)


def add_person(series_id: int, character_name: str, *, pronouns: str = "", aliases: str = "",
               notes: str = "") -> dict:
    """Adds one person to a series. NotFoundError (unknown series),
    InvalidInputError (blank/too long/control chars), ConflictError (the
    series already has that exact name). Returns the new entry."""
    _require_series(series_id)
    name = _clean("character_name", character_name, MAX_NAME_LEN)
    if not name:
        raise InvalidInputError("character_name can't be blank.")
    pronouns = _clean("pronouns", pronouns, MAX_PRONOUNS_LEN)
    aliases = _clean("aliases", aliases, MAX_ALIASES_LEN)
    notes = _clean("notes", notes, MAX_NOTES_LEN, multiline=True)
    try:
        new_id = db.insert_series_character(series_id, name, aliases=aliases, notes=notes,
                                            gender=pronouns)
    except sqlite3.IntegrityError:
        raise ConflictError("This series already has someone with that name.") from None
    row = _find(series_id, new_id)
    if row is None:  # removed between the insert and the read
        raise NotFoundError("That person no longer exists.")
    return _entry(row)


def update_person(series_id: int, character_id: int, *, character_name: str = None,
                  pronouns: str = None, aliases: str = None, notes: str = None) -> dict:
    """Field-scoped update of one person, matched by id within the series.
    None = leave alone, "" = clear (character_name can't be blank).
    A rename keeps the id, so every drama character linked to this person
    picks up the new name. NotFoundError (unknown series, or a person not
    in this series), InvalidInputError, ConflictError (name taken).
    Returns the updated entry."""
    _require_series(series_id)
    _check_id("character_id", character_id)
    if _find(series_id, character_id) is None:
        raise NotFoundError(f"No person {character_id} in series {series_id}.")

    fields = {}
    if character_name is not None:
        name = _clean("character_name", character_name, MAX_NAME_LEN)
        if not name:
            raise InvalidInputError("character_name can't be blank.")
        fields["character_name"] = name
    if pronouns is not None:
        fields["gender"] = _clean("pronouns", pronouns, MAX_PRONOUNS_LEN)
    if aliases is not None:
        fields["aliases"] = _clean("aliases", aliases, MAX_ALIASES_LEN)
    if notes is not None:
        fields["notes"] = _clean("notes", notes, MAX_NOTES_LEN, multiline=True)

    if fields:
        try:
            db.update_series_character(character_id, **fields)
        except sqlite3.IntegrityError:
            raise ConflictError("This series already has someone with that name.") from None
    row = _find(series_id, character_id)
    if row is None:
        raise NotFoundError(f"No person {character_id} in series {series_id}.")
    return _entry(row)
