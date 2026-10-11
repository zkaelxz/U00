"""
device_tokens.py -- the `extension_device_tokens` table in library.db: one
row per browser-extension device token a household member created
(services/device_token_service.py holds the rules; docs/browser-extension.md
and docs/remote-access-decision.md the design).

Only the token's SHA-256 is stored, never the token. Of where it was used,
only the coarse IP prefix (`auth_service.ip_prefix`) is kept. Rows are
revoked (revoked_at set), not deleted, so the owner sees what was revoked;
`prune_old` drops revoked or expired rows after a while.
"""

import contextlib

import db

_COLUMNS = ("id, user_id, label, created_at, last_used_at, last_used_ip_prefix, "
            "revoked_at, expires_at")


def create_tables(conn):
    """Called from db.init_db with its connection. Additive only."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS extension_device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            last_used_at REAL,
            last_used_ip_prefix TEXT DEFAULT '',
            revoked_at REAL,
            expires_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_extension_device_tokens_user
            ON extension_device_tokens(user_id);
    """)


def _live_sql(now: float) -> tuple:
    return "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)", (now,)


def insert_under_cap(user_id: int, label: str, token_hash: str, now: float,
                     expires_at, cap: int):
    """Inserts a token unless the user already holds `cap` live ones, in one
    write transaction so two concurrent creates can't both pass the count.
    Returns the new id, or None when at the cap."""
    live, args = _live_sql(now)
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM extension_device_tokens "
                             f"WHERE user_id = ? AND {live}", (user_id, *args)).fetchone()[0]
            if n >= cap:
                conn.rollback()
                return None
            cur = conn.execute(
                "INSERT INTO extension_device_tokens (user_id, label, token_hash, created_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, label, token_hash, now, expires_at))
            conn.commit()
            return cur.lastrowid
        except Exception:
            conn.rollback()
            raise


def get_by_hash(token_hash: str):
    """The row including its hash, for the caller to re-compare."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM extension_device_tokens WHERE token_hash = ?",
                           (token_hash,)).fetchone()
        return dict(row) if row else None


def get(token_id: int):
    """One row without its hash."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM extension_device_tokens WHERE id = ?",
                           (token_id,)).fetchone()
        return dict(row) if row else None


def list_for_user(user_id: int) -> list:
    """Newest first; no hashes."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM extension_device_tokens "
                            "WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
        return [dict(r) for r in rows]


def list_all() -> list:
    """Every user's tokens with the owner's name fields, newest first; no hashes."""
    cols = ", ".join(f"t.{c.strip()}" for c in _COLUMNS.split(","))
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            f"SELECT {cols}, u.display_name AS user_display_name, u.email AS user_email "
            "FROM extension_device_tokens t JOIN users u ON u.id = t.user_id "
            "ORDER BY t.id DESC").fetchall()
        return [dict(r) for r in rows]


def revoke(token_id: int, now: float, user_id: int = None) -> bool:
    """Revokes one live (not yet revoked) token; `user_id` scopes it to that
    user's own. False when there is no such unrevoked token."""
    sql = "UPDATE extension_device_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL"
    args = [now, token_id]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.rowcount > 0


def revoke_all_for_user(user_id: int, now: float) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("UPDATE extension_device_tokens SET revoked_at = ? "
                           "WHERE user_id = ? AND revoked_at IS NULL", (now, user_id))
        conn.commit()
        return cur.rowcount


def touch(token_id: int, now: float, ip_prefix: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE extension_device_tokens SET last_used_at = ?, "
                     "last_used_ip_prefix = ? WHERE id = ?", (now, ip_prefix, token_id))
        conn.commit()


def prune_old(user_id: int, before: float) -> int:
    """Deletes the user's rows revoked or expired before `before`."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "DELETE FROM extension_device_tokens WHERE user_id = ? AND "
            "((revoked_at IS NOT NULL AND revoked_at < ?) OR "
            "(expires_at IS NOT NULL AND expires_at < ?))", (user_id, before, before))
        conn.commit()
        return cur.rowcount
