"""Translation versions: complete alternate translations of a drama kept side by
side, one of them active."""

import contextlib
from lib import clock
import json

import db


def save_translation_version(drama_id: int, lines, label: str, engine: str = "",
                              model: str = "", make_active: bool = False):
    """Stores a complete translation as a named version, so re-translating
    with a different model never destroys the previous attempt."""
    payload = [{"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start, "end": ln.end,
                "zh": ln.zh, "en": ln.en,
                "speaker": getattr(ln, "speaker", None),
                "speaker_manual": bool(getattr(ln, "speaker_manual", False)),
                "flag": getattr(ln, "flag", None), "flag_note": getattr(ln, "flag_note", "") or "",
                "sfx": bool(getattr(ln, "sfx", False)), "lang": getattr(ln, "lang", None)}
               for ln in lines]
    with contextlib.closing(db.get_conn()) as conn:
        if make_active:
            conn.execute("UPDATE translation_versions SET is_active = 0 WHERE drama_id = ?", (drama_id,))
        cur = conn.execute("""
            INSERT INTO translation_versions (drama_id, label, engine, model, is_active,
                                               lines_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, label, engine, model, int(make_active),
              json.dumps(payload, ensure_ascii=False), clock.utcnow().isoformat()))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


def list_translation_versions(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, drama_id, label, engine, model, is_active, created_at "
            "FROM translation_versions WHERE drama_id = ? ORDER BY created_at DESC",
            (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def get_translation_version(version_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM translation_versions WHERE id = ?", (version_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["lines"] = json.loads(d["lines_json"]) if d["lines_json"] else []
    return d


def set_active_translation_version(drama_id: int, version_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE translation_versions SET is_active = 0 WHERE drama_id = ?", (drama_id,))
        conn.execute("UPDATE translation_versions SET is_active = 1 WHERE id = ?", (version_id,))
        conn.commit()


def delete_translation_version(version_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM translation_versions WHERE id = ?", (version_id,))
        conn.commit()
