"""Adaptive style: the lines a translator rewrote, and the style profile learned
from them with its restorable history."""

import contextlib
from lib import clock
import json

import db


def record_edit_sample(drama_id: int, zh: str, ai_version: str, user_version: str):
    """Records a line you rewrote, so the system can learn what you
    consistently change. Only stores genuine edits, not untouched lines."""
    if not user_version or ai_version.strip() == user_version.strip():
        return
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(
            "INSERT INTO edit_samples (drama_id, zh, ai_version, user_version, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (drama_id, zh, ai_version, user_version, clock.utcnow().isoformat()))
        conn.commit()


def list_edit_samples(drama_id: int | None = None, limit: int = 100):
    with contextlib.closing(db.get_conn()) as conn:
        if drama_id:
            rows = conn.execute(
                "SELECT * FROM edit_samples WHERE drama_id = ? ORDER BY created_at DESC LIMIT ?",
                (drama_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM edit_samples ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_style_profile(scope: str, profile: dict, sample_count: int = 0):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO style_profile (scope, profile_json, sample_count, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                profile_json = excluded.profile_json,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
        """, (scope, json.dumps(profile, ensure_ascii=False), sample_count,
              clock.utcnow().isoformat()))
        conn.commit()


STYLE_HISTORY_KEEP = 5


def _style_history(raw) -> list:
    try:
        hist = json.loads(raw) if raw else []
    except (json.JSONDecodeError, TypeError):
        return []
    return [h for h in hist if isinstance(h, dict)] if isinstance(hist, list) else []


def replace_style_profile(scope: str, profile: dict, sample_count: int = 0):
    """save_style_profile for a learn or reset: in one transaction, the
    profile being replaced (when it had preferences) is kept first in the
    scope's history (at most STYLE_HISTORY_KEEP), so restore_style_profile
    can bring it back."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT profile_json, sample_count, updated_at, history_json "
                           "FROM style_profile WHERE scope = ?", (scope,)).fetchone()
        hist = _style_history(row["history_json"]) if row else []
        if row:
            try:
                old = json.loads(row["profile_json"]) if row["profile_json"] else {}
            except (json.JSONDecodeError, TypeError):
                old = {}
            if isinstance(old, dict) and old.get("preferences"):
                hist.insert(0, {"profile": old, "sample_count": row["sample_count"] or 0,
                                "updated_at": row["updated_at"]})
        conn.execute("""
            INSERT INTO style_profile (scope, profile_json, sample_count, updated_at, history_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope) DO UPDATE SET
                profile_json = excluded.profile_json,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at,
                history_json = excluded.history_json
        """, (scope, json.dumps(profile, ensure_ascii=False), sample_count,
              clock.utcnow().isoformat(),
              json.dumps(hist[:db.STYLE_HISTORY_KEEP], ensure_ascii=False)))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def restore_style_profile(scope: str, index: int = 0):
    """Makes history entry `index` (0 = the most recent earlier profile)
    current again, in one transaction; the profile it replaces (when it had
    preferences) takes its place first in the history, so nothing is lost.
    Returns False when there is no such entry."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT profile_json, sample_count, updated_at, history_json "
                           "FROM style_profile WHERE scope = ?", (scope,)).fetchone()
        hist = _style_history(row["history_json"]) if row else []
        if not 0 <= index < len(hist):
            conn.rollback()
            return False
        chosen = hist.pop(index)
        try:
            cur = json.loads(row["profile_json"]) if row["profile_json"] else {}
        except (json.JSONDecodeError, TypeError):
            cur = {}
        if isinstance(cur, dict) and cur.get("preferences"):
            hist.insert(0, {"profile": cur, "sample_count": row["sample_count"] or 0,
                            "updated_at": row["updated_at"]})
        conn.execute("UPDATE style_profile SET profile_json = ?, sample_count = ?, "
                     "updated_at = ?, history_json = ? WHERE scope = ?",
                     (json.dumps(chosen.get("profile") or {}, ensure_ascii=False),
                      int(chosen.get("sample_count") or 0),
                      clock.utcnow().isoformat(),
                      json.dumps(hist[:db.STYLE_HISTORY_KEEP], ensure_ascii=False), scope))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_style_profile(scope: str = "global"):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM style_profile WHERE scope = ?", (scope,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["profile"] = json.loads(d["profile_json"]) if d["profile_json"] else {}
    except (json.JSONDecodeError, TypeError):
        d["profile"] = {}
    d["history"] = _style_history(d.get("history_json"))
    return d
