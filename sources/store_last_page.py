"""sources/store_last_page.py -- The last page each account read on each site (Grab's pick-up-where-you-left-off)."""

import time

from sources.store import connect

MAX_PER_USER = 100


def save(user_id: int, host: str, url: str, title: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO grab_last_pages(user_id, host, url, title, updated_at) VALUES(?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id, host) DO UPDATE SET url=excluded.url, title=excluded.title, "
                     "updated_at=excluded.updated_at", (user_id, host, url, title, time.time()))
        # Visiting many sites must not grow the table without bound. rowid breaks a tie on a coarse
        # clock so the row just saved is never the one dropped.
        conn.execute("DELETE FROM grab_last_pages WHERE user_id=? AND host NOT IN ("
                     "SELECT host FROM grab_last_pages WHERE user_id=? ORDER BY updated_at DESC, rowid DESC LIMIT ?)",
                     (user_id, user_id, MAX_PER_USER))


def list_for(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT host, url, title, updated_at FROM grab_last_pages WHERE user_id=? "
                            "ORDER BY updated_at DESC, rowid DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def forget(user_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM grab_last_pages WHERE user_id=?", (user_id,))
