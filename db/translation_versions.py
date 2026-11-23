"""Translation versions: complete alternate translations of a drama kept side by
side, one of them active."""

import contextlib
from lib import clock
import json
import logging
import sqlite3

from lib import gzip_text

import db

log = logging.getLogger(__name__)

# Every translate run keeps a full copy of the lines here, with no cap, so the
# copies are kept gzip-compressed once they are no longer among the newest few
# of their title (a BLOB in the same column; get_translation_version reads both
# forms). Nothing is ever deleted for space: each copy is a translation someone
# may want back.
PLAIN_VERSIONS = 3


def save_translation_version(drama_id: int, lines, label: str, engine: str = "",
                              model: str = "", make_active: bool = False):
    """Stores a complete translation as a named version, so re-translating
    with a different model never destroys the previous attempt."""
    payload = [{"id": getattr(ln, "id", None), "idx": ln.idx, "start": ln.start, "end": ln.end,
                "zh": ln.zh, "en": ln.en,
                "speaker": getattr(ln, "speaker", None),
                "speaker_manual": bool(getattr(ln, "speaker_manual", False)),
                "flag": getattr(ln, "flag", None), "flag_note": getattr(ln, "flag_note", "") or "",
                "sfx": bool(getattr(ln, "sfx", False)), "lang": getattr(ln, "lang", None),
                "reviewed": bool(getattr(ln, "reviewed", False)),
                "ass_event": getattr(ln, "ass_event", None),
                "confidence": getattr(ln, "confidence", None), "mark": getattr(ln, "mark", None)}
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
        new_id = cur.lastrowid
        _pack_old(conn, drama_id)
        conn.commit()
    return new_id


def _pack_old(conn, drama_id=None) -> int:
    """Compresses the plain copies past each title's newest PLAIN_VERSIONS (by
    id, so a clock set back can't make the copy just written look old). A copy
    is replaced only once its packed form reads back identical."""
    where, args = ("WHERE drama_id = ?", (drama_id,)) if drama_id is not None else ("", ())
    ids = conn.execute(
        "SELECT id FROM (SELECT id, typeof(lines_json) AS kind, ROW_NUMBER() OVER "
        f"(PARTITION BY drama_id ORDER BY id DESC) AS n FROM translation_versions {where}) "
        "WHERE n > ? AND kind = 'text'", (*args, db.PLAIN_VERSIONS)).fetchall()
    packed_n = 0
    for (version_id,) in ids:
        text = conn.execute("SELECT lines_json FROM translation_versions WHERE id = ?",
                            (version_id,)).fetchone()[0]
        packed = gzip_text.pack(text)
        if gzip_text.unpack(packed) == text:
            conn.execute("UPDATE translation_versions SET lines_json = ? WHERE id = ?",
                         (packed, version_id))
            packed_n += 1
    return packed_n


def pack_old_translation_versions() -> int:
    """Compresses the old copies every title already has (they were stored plain
    before compression existed). Runs at startup, so it never raises: a busy
    library just stays as it is until the next start. Returns how many were packed."""
    try:
        with contextlib.closing(db.get_conn()) as conn:
            n = _pack_old(conn)
            conn.commit()
        return n
    except sqlite3.Error:
        log.warning("Could not compress old translation versions", exc_info=True)
        return 0


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
    text = gzip_text.unpack(d["lines_json"])
    d["lines_json"] = text
    d["lines"] = json.loads(text) if text else []
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
