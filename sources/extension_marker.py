"""
sources/extension_marker.py -- the person's "this source works only through the browser
extension" marker, one row per source in sources.db.

The marker is a user-recorded fact, not a test result: it never touches the capability
record, so the automated tiers' failures stay on show. Imports and scheduled checks consult
it so they stop before scraping a site that is known to need the extension.
"""

import re
import time

from . import store

MAX_NOTE_LEN = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# The automated tiers; a success on either after the marker was set means the
# marker may be stale.
_AUTOMATED_TIERS = ("STATIC_HTTP", "RENDERED_BROWSER")


def clean_note(note) -> str:
    """The note with control characters dropped; ValueError when it is not text or too long."""
    if note is None:
        return ""
    if not isinstance(note, str):
        raise ValueError("The note must be text.")
    text = _CONTROL.sub(" ", note).strip()
    if len(text) > MAX_NOTE_LEN:
        raise ValueError(f"The note can be at most {MAX_NOTE_LEN} characters.")
    return text


def get(source: str):
    with store.connect() as conn:
        row = conn.execute("SELECT marked_at, marked_by_user_id, note FROM source_extension_only "
                           "WHERE source=?", (source,)).fetchone()
    return dict(row) if row else None


def marked_sources() -> dict:
    """{source: marked_at} for every marked source, in one query."""
    with store.connect() as conn:
        return {r["source"]: r["marked_at"] for r in
                conn.execute("SELECT source, marked_at FROM source_extension_only")}


def is_marked(source: str) -> bool:
    return get(source) is not None


def mark(source: str, user_id=None, note: str = "") -> dict:
    """Marks the source (or updates its note). The original date and user are kept when it
    was already marked."""
    note = clean_note(note)
    with store.connect() as conn:
        conn.execute("INSERT INTO source_extension_only(source, marked_at, marked_by_user_id, note) "
                     "VALUES(?, ?, ?, ?) ON CONFLICT(source) DO UPDATE SET note=excluded.note",
                     (source, time.time(), user_id, note))
    return get(source)


def clear(source: str) -> None:
    with store.connect() as conn:
        conn.execute("DELETE FROM source_extension_only WHERE source=?", (source,))


def works_without_extension(marked_at, tiers: dict) -> bool:
    """Whether a Static or Browser test passed after the marker was set."""
    for key in _AUTOMATED_TIERS:
        t = (tiers or {}).get(key) or {}
        if t.get("ok") and (t.get("at") or 0) > (marked_at or 0):
            return True
    return False


def view(source: str, tiers: dict = None) -> dict:
    """The marker fields a source detail carries (booleans, a date and the note)."""
    row = get(source)
    if row is None:
        return {"extension_only": False, "extension_marked_at": None, "extension_note": "",
                "extension_works_without": False}
    return {"extension_only": True, "extension_marked_at": row["marked_at"],
            "extension_note": row["note"],
            "extension_works_without": works_without_extension(row["marked_at"], tiers)}
