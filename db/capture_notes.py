"""The closing line of each extension chapter capture, kept per title for the details popup."""

import contextlib

from lib import clock

import db

KEPT_PER_TITLE = 5


def add_capture_note(drama_id: int, summary: str) -> None:
    """Stores one line and drops all but the newest KEPT_PER_TITLE for the title."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("INSERT INTO capture_notes (drama_id, summary, created_at) VALUES (?, ?, ?)",
                     (drama_id, summary, clock.utcnow().isoformat() + "Z"))   # zone marked: the popup shows it in local time
        conn.execute("DELETE FROM capture_notes WHERE drama_id = ? AND id NOT IN "
                     "(SELECT id FROM capture_notes WHERE drama_id = ? ORDER BY id DESC LIMIT ?)",
                     (drama_id, drama_id, KEPT_PER_TITLE))
        conn.commit()


def list_capture_notes(drama_id: int) -> list:
    """Newest first: [{summary, created_at}]."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT summary, created_at FROM capture_notes WHERE drama_id = ? "
                            "ORDER BY id DESC", (drama_id,)).fetchall()
    return [dict(r) for r in rows]
