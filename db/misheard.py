"""db/misheard.py -- the mis-heard suggestions a person answered "Keep as heard" for a title."""
import contextlib

from lib import clock
import db


def list_misheard_keeps(drama_id: int) -> list:
    """[(heard, suggested)] kept for this title."""
    with contextlib.closing(db.get_conn()) as conn:
        return [(r["heard"], r["suggested"]) for r in conn.execute(
            "SELECT heard, suggested FROM misheard_keeps WHERE drama_id = ? ORDER BY id", (drama_id,))]


def add_misheard_keep(drama_id: int, heard: str, suggested: str) -> None:
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO misheard_keeps (drama_id, heard, suggested, created_at) "
            "VALUES (?, ?, ?, ?)", (drama_id, heard, suggested, clock.utcnow().isoformat()))
        conn.commit()


def clear_misheard_keeps(drama_id: int) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("DELETE FROM misheard_keeps WHERE drama_id = ?", (drama_id,))
        conn.commit()
        return cur.rowcount
