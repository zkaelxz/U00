"""Universe wiki: the per-drama encyclopedia of characters, places and terms, each entry
bounded by how far into the story it was built from so it never shows spoilers."""

import contextlib
import datetime
import json

import db


def upsert_wiki_entry(drama_id: int, entry_type: str, name: str, description: str = None,
                       aliases: str = None, attributes: dict = None,
                       first_seen_line_idx: int = None, known_through_line_idx: int = None):
    """Adds or updates an encyclopedia entry. known_through_line_idx records
    how far into the story this entry was built from -- the spoiler boundary,
    so an entry is never shown to someone who hasn't read that far."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO wiki_entries (drama_id, entry_type, name, aliases, description,
                                       attributes_json, first_seen_line_idx,
                                       known_through_line_idx, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, entry_type, name) DO UPDATE SET
                aliases = COALESCE(excluded.aliases, wiki_entries.aliases),
                description = COALESCE(excluded.description, wiki_entries.description),
                attributes_json = COALESCE(excluded.attributes_json, wiki_entries.attributes_json),
                first_seen_line_idx = COALESCE(wiki_entries.first_seen_line_idx, excluded.first_seen_line_idx),
                known_through_line_idx = MAX(COALESCE(excluded.known_through_line_idx, 0),
                                              COALESCE(wiki_entries.known_through_line_idx, 0)),
                updated_at = excluded.updated_at
        """, (drama_id, entry_type, name, aliases, description,
              json.dumps(attributes, ensure_ascii=False) if attributes else None,
              first_seen_line_idx, known_through_line_idx,
              datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_wiki_entries(drama_id: int, entry_type: str = None, spoiler_limit_line_idx: int = None):
    """spoiler_limit_line_idx: when set, only returns entries first introduced
    at or before that line -- so the wiki never reveals something you haven't
    reached yet."""
    with contextlib.closing(db.get_conn()) as conn:
        q = "SELECT * FROM wiki_entries WHERE drama_id = ?"
        params = [drama_id]
        if entry_type:
            q += " AND entry_type = ?"
            params.append(entry_type)
        if spoiler_limit_line_idx is not None:
            q += " AND (first_seen_line_idx IS NULL OR first_seen_line_idx <= ?)"
            params.append(spoiler_limit_line_idx)
        q += " ORDER BY entry_type, name"
        rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["attributes"] = json.loads(d["attributes_json"]) if d["attributes_json"] else {}
        except (json.JSONDecodeError, TypeError):
            d["attributes"] = {}
        out.append(d)
    return out


def clear_wiki(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM wiki_entries WHERE drama_id = ?", (drama_id,))
        conn.commit()
