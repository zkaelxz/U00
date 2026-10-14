"""
member_keys.py -- the `member_api_keys` table in library.db: a signed-in
member's own API key for a provider (today only Ollama's hosted models), so
their runs spend their own account rather than the PC's. Rules, encryption
and who may touch a row are in services/member_key_service.py.

Only the encrypted key and its last four characters are stored. Backups
leave the table out and a restore keeps the live rows (auth_service's
RESTORE_LIVE_TABLES), like the other per-user secrets.
"""

import contextlib

import db


def create_tables(conn):
    """Called from db.init_db with its connection. Additive only."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS member_api_keys (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            last4 TEXT NOT NULL,
            added_at REAL NOT NULL,
            PRIMARY KEY (user_id, provider)
        );
    """)


def get(user_id: int, provider: str):
    """The row including its ciphertext, or None."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM member_api_keys WHERE user_id = ? AND provider = ?",
                           (user_id, provider)).fetchone()
        return dict(row) if row else None


def put(user_id: int, provider: str, ciphertext: str, last4: str, now: float):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(
            "INSERT INTO member_api_keys (user_id, provider, ciphertext, last4, added_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, provider) DO UPDATE SET "
            "ciphertext = excluded.ciphertext, last4 = excluded.last4, added_at = excluded.added_at",
            (user_id, provider, ciphertext, last4, now))
        conn.commit()


def delete(user_id: int, provider: str) -> bool:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("DELETE FROM member_api_keys WHERE user_id = ? AND provider = ?",
                           (user_id, provider))
        conn.commit()
        return cur.rowcount > 0


def user_ids_with(provider: str) -> set:
    with contextlib.closing(db.get_conn()) as conn:
        return {r[0] for r in conn.execute(
            "SELECT user_id FROM member_api_keys WHERE provider = ?", (provider,))}
