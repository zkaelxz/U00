"""Profiles and what each one has read: resume points, reading history and
personal notes per drama."""

import contextlib
import datetime

import db


def _default_profile_id(conn) -> int:
    """The profile progress/history/notes falls back to when no profile_id
    is given -- the first profile ever created (lowest id), which is the
    one _migrate_step26e_profiles creates on every install (a fresh one or
    an upgrade alike). Lets every caller/test that predates profiles keep
    working completely unchanged, and is what a single-profile household
    transparently keeps using forever if it never adds a second one."""
    row = conn.execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()
    return row["id"] if row else None


def create_profile(name: str, color: str = None) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("INSERT INTO profiles (name, color, created_at) VALUES (?, ?, ?)",
                           (name, color, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def list_profiles():
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get_profile(profile_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return dict(row) if row else None


def delete_profile(profile_id: int):
    """Refuses to remove the last remaining profile -- the app always
    needs at least one to attach progress/history/notes to (and for
    _migrate_step26e_profiles's own "profiles is empty" check to stay a
    true one-time marker). progress and personal_notes cascade via their
    own FOREIGN KEY ... ON DELETE CASCADE; reading_history predates
    profiles and only gained the column via ALTER TABLE (which can't add
    a FK), so its rows for this profile are cleaned up explicitly here
    instead of being left orphaned."""
    with contextlib.closing(db.get_conn()) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
        if count <= 1:
            raise ValueError("Can't delete the last remaining profile.")
        conn.execute("DELETE FROM reading_history WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        conn.commit()


def get_personal_notes(drama_id: int, profile_id: int = None) -> str:
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        row = conn.execute("SELECT notes FROM personal_notes WHERE profile_id = ? AND drama_id = ?",
                           (profile_id, drama_id)).fetchone()
    return (row["notes"] or "") if row else ""


def save_personal_notes(drama_id: int, notes: str, profile_id: int = None):
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        conn.execute("""
            INSERT INTO personal_notes (profile_id, drama_id, notes, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id, drama_id) DO UPDATE SET
                notes = excluded.notes, updated_at = excluded.updated_at
        """, (profile_id, drama_id, notes, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def save_progress(drama_id: int, profile_id: int = None, last_line_idx: int = None,
                   audio_position_seconds: float = None, last_page: int = None,
                   percent_complete: float = None, record_history: bool = True):
    """Upserts the resume point for a drama, for one profile. Only the
    fields you pass are updated, so saving an audio position doesn't
    clobber the reading page. profile_id defaults to the household's
    first/only profile (_default_profile_id) -- every pre-profiles caller
    keeps working unchanged against that one profile."""
    now = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        # Raw values (possibly NULL) go in deliberately: coalescing them here would
        # make excluded.<col> a real 0 and defeat the COALESCE in the conflict clause,
        # so a partial update would silently zero out the fields it didn't touch.
        conn.execute("""
            INSERT INTO progress (drama_id, profile_id, last_line_idx, audio_position_seconds,
                                   last_page, percent_complete, last_accessed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drama_id, profile_id) DO UPDATE SET
                last_line_idx = COALESCE(excluded.last_line_idx, progress.last_line_idx),
                audio_position_seconds = COALESCE(excluded.audio_position_seconds, progress.audio_position_seconds),
                last_page = COALESCE(excluded.last_page, progress.last_page),
                percent_complete = COALESCE(excluded.percent_complete, progress.percent_complete),
                last_accessed_at = excluded.last_accessed_at
        """, (drama_id, profile_id, last_line_idx, audio_position_seconds, last_page,
             percent_complete, now))
        if record_history and (last_line_idx is not None or percent_complete is not None):
            conn.execute(
                "INSERT INTO reading_history (drama_id, profile_id, line_id, line_idx, "
                "percent_complete, accessed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (drama_id, profile_id, db._line_id_for_idx(conn, drama_id, last_line_idx), last_line_idx,
                 percent_complete, now))
        conn.commit()


def get_progress(drama_id: int, profile_id: int = None):
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        row = conn.execute("SELECT * FROM progress WHERE drama_id = ? AND profile_id = ?",
                           (drama_id, profile_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    # Normalize NULLs (from partial first writes) to sensible defaults here,
    # rather than at write time where it would break partial updates.
    d["last_line_idx"] = d["last_line_idx"] or 0
    d["audio_position_seconds"] = d["audio_position_seconds"] or 0.0
    d["last_page"] = d["last_page"] or 1
    d["percent_complete"] = d["percent_complete"] or 0.0
    return d


def list_continue_reading(limit: int = 8, profile_id: int = None, visible_to: int = None):
    """Dramas with partial progress, most recently touched first -- the
    'Continue' shelf. Excludes anything finished (>=99%) or untouched.
    `limit` stays the first positional parameter (profile_id was added
    later) so `list_continue_reading(8)` keeps meaning "limit=8".
    `visible_to` (auth B2): only dramas that user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = db.drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        rows = conn.execute("""
            SELECT d.*, p.last_line_idx, p.audio_position_seconds, p.last_page,
                   p.percent_complete, p.last_accessed_at
            FROM progress p JOIN dramas d ON d.id = p.drama_id
            WHERE p.profile_id = ? AND p.percent_complete > 0 AND p.percent_complete < 99""" + visible + """
            ORDER BY p.last_accessed_at DESC LIMIT ?
        """, [profile_id] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]


def list_reading_history(drama_id: int = None, limit: int = 50, profile_id: int = None,
                         visible_to: int = None):
    """`visible_to` (auth B2, all-dramas listing only): only dramas that
    user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = db.drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        if drama_id:
            rows = conn.execute(
                "SELECT h.id, h.drama_id, h.line_id, COALESCE(l.idx, h.line_idx) AS line_idx, "
                "h.percent_complete, h.accessed_at, d.title_en, d.title_zh FROM reading_history h "
                "JOIN dramas d ON d.id = h.drama_id LEFT JOIN lines l ON l.id = h.line_id "
                "WHERE h.drama_id = ? AND h.profile_id = ? "
                "ORDER BY h.accessed_at DESC LIMIT ?", (drama_id, profile_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT h.id, h.drama_id, h.line_id, COALESCE(l.idx, h.line_idx) AS line_idx, "
                "h.percent_complete, h.accessed_at, d.title_en, d.title_zh FROM reading_history h "
                "JOIN dramas d ON d.id = h.drama_id LEFT JOIN lines l ON l.id = h.line_id "
                "WHERE h.profile_id = ?" + visible + " "
                "ORDER BY h.accessed_at DESC LIMIT ?",
                [profile_id] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]


def clear_reading_history(drama_id: int = None, profile_id: int = None):
    with contextlib.closing(db.get_conn()) as conn:
        if profile_id is None:
            profile_id = _default_profile_id(conn)
        if drama_id:
            conn.execute("DELETE FROM reading_history WHERE drama_id = ? AND profile_id = ?",
                         (drama_id, profile_id))
        else:
            conn.execute("DELETE FROM reading_history WHERE profile_id = ?", (profile_id,))
        conn.commit()
