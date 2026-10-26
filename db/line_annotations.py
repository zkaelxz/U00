"""Per-line annotations a translation run produces: translation notes (idioms,
wordplay, names, allusions), consistency-check results and emotion tags."""

import contextlib
from lib import clock
import json

import db


def save_translation_notes(drama_id: int, notes, id_by_idx: dict = None):
    """notes: list of {line_idx, term, note_type, note} (or with line_id).
    Stored against the line's permanent id. id_by_idx: {line_idx: line_id}
    as of when the notes were generated -- a background job passes its
    own copy's mapping, since the user may have merged lines since. Without
    it, line_idx is resolved against the drama's lines as they are now.
    Existing notes for the same (drama, line, term) are updated rather
    than duplicated."""
    with contextlib.closing(db.get_conn()) as conn:
        now = clock.utcnow().isoformat()
        for n in notes:
            line_idx = n.get("line_idx")
            line_id = n.get("line_id")
            if line_id is None:
                line_id = (id_by_idx.get(line_idx) if id_by_idx is not None
                           else db._line_id_for_idx(conn, drama_id, line_idx))
            if line_id is None:
                # The line this note was about no longer exists (same case
                # save_emotions skips). Inserting it with line_id = NULL
                # would accumulate orphaned duplicates, since SQLite treats
                # every NULL as distinct for the (drama_id, line_id, term)
                # uniqueness this ON CONFLICT relies on, so it would never
                # match an earlier NULL row to update instead of insert.
                continue
            conn.execute("""
                INSERT INTO translation_notes (drama_id, line_id, line_idx, term, note_type, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drama_id, line_id, term) DO UPDATE SET
                    note_type = excluded.note_type,
                    note = excluded.note
            """, (drama_id, line_id, line_idx, n.get("term", ""), n.get("note_type", "cultural"),
                  n.get("note", ""), now))
        conn.commit()


def list_translation_notes(drama_id: int):
    """line_idx is the note's line's CURRENT position (it follows the line
    through merges/splits); the stored position is only a fallback for a
    note whose line couldn't be matched when it was migrated."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT n.id, n.drama_id, n.line_id, COALESCE(l.idx, n.line_idx) AS line_idx,
                   n.term, n.note_type, n.note, n.created_at
            FROM translation_notes n LEFT JOIN lines l ON l.id = n.line_id
            WHERE n.drama_id = ? ORDER BY 4, n.id
        """, (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_translation_note(note_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM translation_notes WHERE id = ?", (note_id,))
        conn.commit()


def save_consistency_issues(drama_id: int, issues):
    """Replaces the drama's consistency-check results wholesale -- each
    run is a fresh full check of the current translation, not something
    to accumulate across runs the way translation notes do. Persisted so
    an LLM call that already cost real money survives a page refresh
    instead of only living in session state."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM consistency_issues WHERE drama_id = ?", (drama_id,))
        now = clock.utcnow().isoformat()
        for issue in issues:
            conn.execute("""
                INSERT INTO consistency_issues (drama_id, term, variants, note, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (drama_id, issue.get("term", ""),
                  json.dumps(issue.get("variants", []), ensure_ascii=False),
                  issue.get("note", ""), now))
        conn.commit()


def load_consistency_issues(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM consistency_issues WHERE drama_id = ?", (drama_id,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["variants"] = json.loads(d["variants"]) if d["variants"] else []
        except (json.JSONDecodeError, TypeError):
            d["variants"] = []
        out.append(d)
    return out


def save_emotions(drama_id: int, emotion_map: dict, id_by_idx: dict = None):
    """emotion_map: {line_idx: {"emotion", "intensity", "note"}}, the shape
    emotion.detect_emotions() returns. Persisted so a whole-drama emotion
    detection run (a real LLM batch job, same cost scale as translation)
    survives a page refresh and an app restart, rather than needing a
    re-run (and re-paying for) the whole thing.

    Stored against each line's permanent id; id_by_idx works as in
    save_translation_notes."""
    with contextlib.closing(db.get_conn()) as conn:
        now = clock.utcnow().isoformat()
        for line_idx, tag in emotion_map.items():
            line_idx = int(line_idx)
            line_id = (id_by_idx.get(line_idx) if id_by_idx is not None
                       else db._line_id_for_idx(conn, drama_id, line_idx))
            if line_id is None:
                continue  # the line it was about no longer exists
            conn.execute("""
                INSERT INTO line_emotions (drama_id, line_id, line_idx, emotion, intensity, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(drama_id, line_id) DO UPDATE SET
                    emotion = excluded.emotion,
                    intensity = excluded.intensity,
                    note = excluded.note
            """, (drama_id, line_id, line_idx, tag.get("emotion", "neutral"),
                  tag.get("intensity", 0.5), tag.get("note", ""), now))
        conn.commit()


def line_emotion_ids(drama_id: int) -> set:
    """Row ids of the drama's emotion tags, for save_lines(known_emotion_ids=)."""
    with contextlib.closing(db.get_conn()) as conn:
        return {r["id"] for r in conn.execute(
            "SELECT id FROM line_emotions WHERE drama_id = ?", (drama_id,))}


def load_emotions(drama_id: int) -> dict:
    """Returns the same {line_idx: {"emotion", "intensity", "note"}} shape
    save_emotions() takes, keyed by each line's CURRENT position, so it
    drops straight back into emotion.emotion_summary()/
    build_emotion_guidance() unchanged."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT COALESCE(l.idx, e.line_idx) AS line_idx, e.emotion, e.intensity, e.note
            FROM line_emotions e LEFT JOIN lines l ON l.id = e.line_id
            WHERE e.drama_id = ?
        """, (drama_id,)).fetchall()
    return {r["line_idx"]: {"emotion": r["emotion"], "intensity": r["intensity"],
                             "note": r["note"] or ""} for r in rows if r["line_idx"] is not None}
