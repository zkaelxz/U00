"""
sources/live_follow_store.py -- the `followed_channels` rows in sources.db
(schema in sources/store.py), for services/live_follow_service.py and its
poller. Plain dicts in and out; callers validate and bound every value
before it gets here, and decide who may see a row.
"""

import time

from sources import store

# Columns the service may change after a row is created; anything else
# (id, owner, created_at, the canonical URL) is fixed for the row's life.
_UPDATABLE = frozenset({
    "channel_name", "mode", "auto_check", "options", "stream_title", "pc_owner", "poll_minutes", "last_checked_at",
    "last_state", "last_status", "starts_at", "last_stream_id", "notified_state",
    "started_stream_id",
})


def _row(r) -> dict | None:
    return dict(r) if r is not None else None


def add(owner_user_id, pc_owner: bool, channel_url: str, channel_name: str, mode: str,
        options: str, poll_minutes: int, auto_check: bool = False) -> int:
    with store.connect() as conn:
        cur = conn.execute(
            "INSERT INTO followed_channels (owner_user_id, pc_owner, channel_url, channel_name, "
            "mode, auto_check, options, poll_minutes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (owner_user_id, int(bool(pc_owner)), channel_url, channel_name, mode,
             int(bool(auto_check)), options, int(poll_minutes), time.time()))
        if cur.lastrowid is None:       # sqlite3 always sets it after an INSERT
            raise RuntimeError("INSERT into followed_channels returned no row id")
        return cur.lastrowid


def get(follow_id: int):
    with store.connect() as conn:
        return _row(conn.execute("SELECT * FROM followed_channels WHERE id = ?",
                                 (int(follow_id),)).fetchone())


def list_all() -> list:
    with store.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM followed_channels ORDER BY id")]


def any_auto_check() -> bool:
    with store.connect() as conn:
        return conn.execute(
            "SELECT 1 FROM followed_channels WHERE auto_check = 1 LIMIT 1").fetchone() is not None


def count(owner_user_id=None, any_owner: bool = False) -> int:
    with store.connect() as conn:
        if any_owner:
            return conn.execute("SELECT COUNT(*) FROM followed_channels").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM followed_channels WHERE owner_user_id IS ?",
                            (owner_user_id,)).fetchone()[0]


def find(owner_user_id, channel_url: str):
    with store.connect() as conn:
        return _row(conn.execute(
            "SELECT * FROM followed_channels WHERE owner_user_id IS ? AND channel_url = ?",
            (owner_user_id, channel_url)).fetchone())


def update(follow_id: int, **fields) -> None:
    bad = set(fields) - _UPDATABLE
    if bad:
        raise ValueError(f"not an updatable column: {sorted(bad)}")
    if not fields:
        return
    names = sorted(fields)
    with store.connect() as conn:
        conn.execute(f"UPDATE followed_channels SET {', '.join(f'{n} = ?' for n in names)} "
                     "WHERE id = ?", [fields[n] for n in names] + [int(follow_id)])


def delete(follow_id: int) -> bool:
    with store.connect() as conn:
        return conn.execute("DELETE FROM followed_channels WHERE id = ?",
                            (int(follow_id),)).rowcount > 0
