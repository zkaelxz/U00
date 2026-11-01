"""Line history / undo: full snapshots of a drama's lines taken before risky bulk
operations, with their word timings, pruned and gzip-packed as they age."""

import contextlib
from lib import clock
import json

from lib import gzip_text

import db


# Word timings a snapshot carries, in total: about 3 MB for a three-hour,
# 9,000-line title. Past it the remaining lines are saved without words, so a
# restore re-splits them proportionally rather than growing every snapshot.
MAX_SNAPSHOT_WORD_BYTES = 4_000_000
# Older snapshots are rarely opened, so they are kept gzip-compressed (a BLOB
# in the same column; get_line_history_snapshot reads both forms).
PLAIN_SNAPSHOTS = 3


def save_line_history_snapshot(drama_id: int, lines, label: str, keep_last: int = 10):
    """Saves a full snapshot of the current lines before a risky bulk
    operation. Keeps only the most recent `keep_last` snapshots per
    drama to avoid unbounded growth -- older ones are pruned. Returns the
    new snapshot's id.

    Each line's stored word timings are read from its row and kept only when
    they were computed for the snapshot's own text (up to
    MAX_SNAPSHOT_WORD_BYTES in all), so a restore brings back real pauses."""
    from segment_splitting import words_for_text
    snapshot = [
        {"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start, "end": ln.end,
         "zh": ln.zh, "en": ln.en,
         "speaker": getattr(ln, "speaker", None), "dub_filename": getattr(ln, "dub_filename", None),
         "speaker_manual": bool(getattr(ln, "speaker_manual", False)),
         "flag": getattr(ln, "flag", None), "flag_note": getattr(ln, "flag_note", "") or "",
         "sfx": bool(getattr(ln, "sfx", False)), "lang": getattr(ln, "lang", None),
         "reviewed": bool(getattr(ln, "reviewed", False))}
        for ln in lines
    ]
    conn = db.get_conn()
    try:
        # IMMEDIATE: this reads the lines' words before it writes, and a
        # deferred BEGIN's upgrade to a write fails at once ("database is
        # locked") if another connection commits in between.
        conn.execute("BEGIN IMMEDIATE")
        stored = dict(conn.execute(
            "SELECT id, word_timings FROM lines WHERE drama_id = ? AND word_timings IS NOT NULL",
            (drama_id,)).fetchall())
        budget = db.MAX_SNAPSHOT_WORD_BYTES
        for row in snapshot:
            words = words_for_text(stored.get(row["id"]), row["zh"])
            if words is not None and len(words) <= budget:
                row["word_timings"] = words
                budget -= len(words)
        history_id = conn.execute(
            "INSERT INTO line_history (drama_id, label, snapshot_json, created_at) VALUES (?, ?, ?, ?)",
            (drama_id, label, json.dumps(snapshot, ensure_ascii=False),
             clock.utcnow().isoformat())
        ).lastrowid
        # Prune old snapshots. Inside the same transaction so a failure here
        # can't leave the prune applied without the new snapshot written.
        # By id, not created_at: after the clock is set back the snapshot
        # just written would sort oldest and be pruned at once.
        ids = conn.execute(
            "SELECT id FROM line_history WHERE drama_id = ? ORDER BY id DESC", (drama_id,)
        ).fetchall()
        if len(ids) > keep_last:
            old_ids = [r["id"] for r in ids[keep_last:]]
            conn.executemany("DELETE FROM line_history WHERE id = ?", [(i,) for i in old_ids])
        for old_id, text in conn.execute(
                "SELECT id, snapshot_json FROM line_history WHERE typeof(snapshot_json) = 'text' "
                "AND id IN (SELECT id FROM line_history WHERE drama_id = ? ORDER BY id DESC "
                "LIMIT -1 OFFSET ?)", (drama_id, db.PLAIN_SNAPSHOTS)).fetchall():
            packed = gzip_text.pack(text)
            if gzip_text.unpack(packed) == text:
                conn.execute("UPDATE line_history SET snapshot_json = ? WHERE id = ?", (packed, old_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return history_id


def list_line_history(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, drama_id, label, created_at FROM line_history WHERE drama_id = ? ORDER BY id DESC",
            (drama_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_line_history_snapshot(history_id: int):
    """Returns the list of line dicts from a saved snapshot, ready to
    be turned back into Line objects and saved via save_lines()."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT snapshot_json FROM line_history WHERE id = ?", (history_id,)).fetchone()
    text = gzip_text.unpack(row["snapshot_json"]) if row else None
    if text is None:
        return None
    return json.loads(text)
