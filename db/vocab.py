"""Vocab lookups: the Reader's persisted click-to-define history per drama, and
which words are queued for the richer sentence+audio Anki export."""

import contextlib
from lib import clock
import json

import db


def save_vocab_lookup(drama_id: int, word: str, reading: str, definitions, language: str,
                       first_seen_line_idx: int | None = None):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO vocab_lookups (drama_id, word, reading, definitions, language,
                                        first_seen_line_idx, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, word) DO NOTHING
        """, (drama_id, word, reading, json.dumps(definitions, ensure_ascii=False), language,
              first_seen_line_idx, clock.utcnow().isoformat()))
        conn.commit()


def list_vocab_lookups(drama_id: int | None = None, rich_only: bool = False):
    """rich_only=True returns only lookups queued (via set_vocab_export_rich)
    for the richer sentence+audio Anki card type -- see vocab_export.
    export_vocab_apkg_sentence."""
    with contextlib.closing(db.get_conn()) as conn:
        conditions, params = [], []
        if drama_id:
            conditions.append("drama_id = ?")
            params.append(drama_id)
        if rich_only:
            conditions.append("export_rich = 1")
        query = "SELECT * FROM vocab_lookups"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at"
        rows = conn.execute(query, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["definitions"] = json.loads(d["definitions"]) if d["definitions"] else []
        except (json.JSONDecodeError, TypeError):
            d["definitions"] = []
        out.append(d)
    return out


def set_vocab_export_rich(drama_id: int, word: str, flag: bool = True):
    """Queues (or un-queues) a single already-looked-up word for the
    richer sentence+audio Anki card type -- called from the Reader's
    definitions right where the word was looked up."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE vocab_lookups SET export_rich = ? WHERE drama_id = ? AND word = ?",
                     (1 if flag else 0, drama_id, word))
        conn.commit()
