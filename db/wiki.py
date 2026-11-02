"""Universe wiki: the per-drama encyclopedia of characters, places and terms, each entry
bounded by how far into the story it was built from so it never shows spoilers."""

import contextlib
from lib import clock
import json

import db


def upsert_wiki_entry(drama_id: int, entry_type: str, name: str, description: str | None = None,
                       aliases: str | None = None, attributes: dict | None = None,
                       first_seen_line_idx: int | None = None, known_through_line_idx: int | None = None,
                       first_seen_line_id: int | None = None, known_through_line_id: int | None = None):
    """Adds or updates an encyclopedia entry. known_through_line_idx records
    how far into the story this entry was built from -- the spoiler boundary,
    so an entry is never shown to someone who hasn't read that far. The line
    is kept by id too; the furthest of the old and new boundary wins, judged
    by where each line is now."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO wiki_entries (drama_id, entry_type, name, aliases, description,
                                       attributes_json, first_seen_line_idx, first_seen_line_id,
                                       known_through_line_idx, known_through_line_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, entry_type, name) DO UPDATE SET
                aliases = COALESCE(excluded.aliases, wiki_entries.aliases),
                description = COALESCE(excluded.description, wiki_entries.description),
                attributes_json = COALESCE(excluded.attributes_json, wiki_entries.attributes_json),
                first_seen_line_idx = COALESCE(wiki_entries.first_seen_line_idx, excluded.first_seen_line_idx),
                -- An entry saved before ids existed keeps its position: giving it
                -- the new batch's line id would move it later in the story.
                first_seen_line_id = CASE WHEN wiki_entries.first_seen_line_idx IS NULL
                    THEN COALESCE(wiki_entries.first_seen_line_id, excluded.first_seen_line_id)
                    ELSE wiki_entries.first_seen_line_id END,
                known_through_line_idx = MAX(COALESCE(excluded.known_through_line_idx, 0),
                                              COALESCE(wiki_entries.known_through_line_idx, 0)),
                known_through_line_id = CASE
                    WHEN excluded.known_through_line_id IS NULL THEN wiki_entries.known_through_line_id
                    WHEN wiki_entries.known_through_line_id IS NULL THEN excluded.known_through_line_id
                    WHEN COALESCE((SELECT idx FROM lines WHERE id = excluded.known_through_line_id), -1)
                         >= COALESCE((SELECT idx FROM lines WHERE id = wiki_entries.known_through_line_id), -1)
                        THEN excluded.known_through_line_id
                    ELSE wiki_entries.known_through_line_id END,
                updated_at = excluded.updated_at
        """, (drama_id, entry_type, name, aliases, description,
              json.dumps(attributes, ensure_ascii=False) if attributes else None,
              first_seen_line_idx, first_seen_line_id, known_through_line_idx, known_through_line_id,
              clock.utcnow().isoformat()))
        conn.commit()


def list_wiki_entries(drama_id: int, entry_type: str | None = None, spoiler_limit_line_idx: int | None = None):
    """spoiler_limit_line_idx: when set, only returns entries first introduced
    at or before that line -- so the wiki never reveals something you haven't
    reached yet."""
    with contextlib.closing(db.get_conn()) as conn:
        # Where the entry first appears is read from its line's id, so a
        # split, merge or delete elsewhere in the title can't move it (and
        # let a spoiler through); the stored position is only for entries
        # saved without an id.
        # An anchor whose line is gone falls back to the stored position
        # rather than to "no position", which would show the entry to a reader
        # who hasn't reached it.
        seen_at = "COALESCE(l.idx, w.first_seen_line_idx)"
        q = (f"SELECT w.*, {seen_at} AS current_idx, "
             "COALESCE(k.idx, w.known_through_line_idx) AS current_known_idx FROM wiki_entries w "
             "LEFT JOIN lines l ON l.id = w.first_seen_line_id AND l.drama_id = w.drama_id "
             "LEFT JOIN lines k ON k.id = w.known_through_line_id AND k.drama_id = w.drama_id "
             "WHERE w.drama_id = ?")
        params: list[object] = [drama_id]
        if entry_type:
            q += " AND w.entry_type = ?"
            params.append(entry_type)
        if spoiler_limit_line_idx is not None:
            q += f" AND (({seen_at}) IS NULL OR ({seen_at}) <= ?)"
            params.append(spoiler_limit_line_idx)
        q += " ORDER BY w.entry_type, w.name"
        rows = conn.execute(q, params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["first_seen_line_idx"] = d.pop("current_idx")
        d["known_through_line_idx"] = d.pop("current_known_idx")
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
