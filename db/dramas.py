"""Dramas: create, update, delete and list a library title, the visibility
filter every drama query shares, and the library-wide stats and line search."""

import contextlib
import datetime
import os
import shutil

import db


def create_drama(**fields) -> int:
    if fields.get("series_id") is not None:
        # User decision: only a whole series, or a drama
        # with no series, can be private.
        fields["is_private"] = 0
    with contextlib.closing(db.get_conn()) as conn:
        fields.setdefault("status", "not started")
        fields.setdefault("hallucination_silence_sec", 0)  # older databases' column default is 2
        now = datetime.datetime.utcnow().isoformat()
        fields["created_at"] = now
        fields["updated_at"] = now
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO dramas ({cols}) VALUES ({placeholders})",
                            list(fields.values()))
        new_id = cur.lastrowid
        db.claim_new_drama_folder(new_id)   # raising here rolls the insert back
        conn.commit()
    return new_id


def update_drama(drama_id: int, **fields):
    if not fields:
        return
    if fields.get("series_id") is not None:
        fields["is_private"] = 0    # as in create_drama
    fields["updated_at"] = datetime.datetime.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE dramas SET {set_clause} WHERE id = ?",
                     list(fields.values()) + [drama_id])
        conn.commit()


def set_status_if(drama_id: int, expected: str, new: str) -> bool:
    """Changes the status only while it is still `expected`, in one statement,
    so a concurrent dub or export that set a later status is never undone."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (new, datetime.datetime.utcnow().isoformat(), drama_id, expected))
        conn.commit()
        return cur.rowcount > 0


def delete_drama(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM dramas WHERE id = ?", (drama_id,))
        # The checkpoint/cache/provenance tables are keyed by drama (no foreign key): a checkpoint scope
        # is "<kind>:<drama_id>:<digest>".
        conn.execute("DELETE FROM line_provenance WHERE drama_id = ?", (drama_id,))
        conn.execute("DELETE FROM job_checkpoints WHERE scope LIKE ?", (f"%:{int(drama_id)}:%",))
        conn.commit()
    d = os.path.join(db.DRAMAS_DIR, str(drama_id))
    if os.path.isdir(d):
        shutil.rmtree(d)


# Every drama row also carries its series' instructions as
# series_instructions, so any drama_meta handed to the translation prompt
# already has both levels -- Workspace, CLI and bulk translate all build
# drama_meta from get_drama()/list_dramas(), and nothing else has to thread
# the series lookup through. A subquery rather than a JOIN keeps every
# existing unqualified column name in list_dramas' filters unambiguous.
#
# Same trick for previous_episode_summary -- the immediately
# preceding episode's stored running summary (the sibling row in the same
# series whose episode_number is the largest one strictly less than this
# row's own). Only resolves when BOTH this row and a sibling have
# episode_number set -- a drama with no ordering has no reliable
# "previous" to feed forward, so it gets NULL/"" here rather than guessing.
_DRAMA_SELECT = ("SELECT dramas.*, (SELECT series.instructions FROM series "
                 "WHERE series.id = dramas.series_id) AS series_instructions, "
                 "(SELECT d2.episode_summary FROM dramas d2 "
                 "WHERE d2.series_id = dramas.series_id AND d2.episode_number IS NOT NULL "
                 "AND dramas.episode_number IS NOT NULL AND d2.episode_number < dramas.episode_number "
                 "ORDER BY d2.episode_number DESC LIMIT 1) AS previous_episode_summary "
                 "FROM dramas")


def get_drama(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute(f"{_DRAMA_SELECT} WHERE id = ?", (drama_id,)).fetchone()
    return dict(row) if row else None


def drama_visible_sql(alias: str, visible_to: int):
    """(sql, params): a WHERE fragment true when the drama row `alias` is
    visible to user `visible_to`. Same rule as
    ownership_service._visible: the series owner sees every drama in their
    series; a private series hides its dramas from everyone else (drama
    ownership doesn't override it); otherwise the drama's owner, or anyone
    when it isn't private. `alias` is a trusted literal, never user input."""
    return ((f"(EXISTS (SELECT 1 FROM series s WHERE s.id = {alias}.series_id"
             " AND s.owner_user_id = ?)"
             f" OR (NOT EXISTS (SELECT 1 FROM series s WHERE s.id = {alias}.series_id"
             " AND COALESCE(s.is_private, 0) = 1)"
             f" AND ({alias}.owner_user_id = ? OR COALESCE({alias}.is_private, 0) = 0)))"),
            [visible_to, visible_to])


def _contains_pattern(term: str) -> str:
    """LIKE pattern for "contains term" where % and _ in the term are literal;
    pair with ESCAPE '\\' in the query."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_dramas(search: str = "", studio: str = "", author: str = "",
                 voice_actor: str = "", status: str = "", source_language: str = "",
                 media_type: str = "", visible_to: int = None):
    """`visible_to`: a non-admin user id; when set, only
    dramas that user may see are returned (any drama in a series they own;
    otherwise, outside a private series, their own or non-private ones).
    None = unfiltered. The rule itself lives in
    services/ownership_service.py; admins/local owner pass None."""
    with contextlib.closing(db.get_conn()) as conn:
        query = f"{_DRAMA_SELECT} WHERE 1=1"
        params = []
        if visible_to is not None:
            clause, extra = db.drama_visible_sql("dramas", visible_to)
            query += " AND " + clause
            params.extend(extra)
        if search:
            query += (" AND (title_zh LIKE ? ESCAPE '\\' OR title_en LIKE ? ESCAPE '\\'"
                      " OR summary LIKE ? ESCAPE '\\')")
            like = _contains_pattern(search)
            params += [like, like, like]
        if studio:
            query += " AND studio = ?"
            params.append(studio)
        if author:
            query += " AND author = ?"
            params.append(author)
        if voice_actor:
            query += " AND voice_actors LIKE ?"
            params.append(f"%{voice_actor}%")
        if status:
            query += " AND status = ?"
            params.append(status)
        if source_language:
            query += " AND source_language = ?"
            params.append(source_language)
        if media_type:
            query += " AND media_type = ?"
            params.append(media_type)
        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def list_dramas_by_series(series_id: int):
    """Every drama in a series, across every media_type -- the
    series-level view: a manga project and a video project already share
    one glossary/character list under the hood (series_characters and
    glossary_terms are keyed by series_id, not drama_id/media_type); this
    is the query that surfaces that sharing as one grouped list instead
    of unrelated Library rows. Uses _DRAMA_SELECT like every other
    drama-list function, so a row from here carries series_instructions
    too if it's ever used to build a drama_meta, same guarantee
    list_dramas()/get_drama() already give.

    Order: newest first (created_at DESC), same as list_dramas() -- UNLESS
    the episode_number is actually in use somewhere in this series,
    in which case it's the real ordering signal and takes over (ascending,
    reading order), with any not-yet-numbered sibling sorted after the
    numbered ones rather than crashing or getting silently interleaved.
    An existing series with no episode numbers set at all keeps its old
    created_at order exactly -- this never reorders a series that hasn't
    opted in."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            f"{_DRAMA_SELECT} WHERE series_id = ? ORDER BY created_at DESC", (series_id,)).fetchall()
    dramas = [dict(r) for r in rows]
    if any(d.get("episode_number") is not None for d in dramas):
        dramas.sort(key=lambda d: (d.get("episode_number") is None, d.get("episode_number")))
    return dramas


def get_library_stats(visible_to: int = None):
    """`visible_to` (auth B2): count only the dramas (and their lines) that
    user may see; None = the whole library."""
    where, params = "", []
    if visible_to is not None:
        clause, params = db.drama_visible_sql("d", visible_to)
        where = " WHERE " + clause
    lines_from = ("FROM lines JOIN dramas d ON d.id = lines.drama_id" + where) if where \
        else "FROM lines"
    lines_where = " AND " if where else " WHERE "
    with contextlib.closing(db.get_conn()) as conn:
        total_dramas = conn.execute(f"SELECT COUNT(*) FROM dramas d{where}", params).fetchone()[0]
        by_status = conn.execute(f"SELECT status, COUNT(*) as n FROM dramas d{where} GROUP BY status",
                                 params).fetchall()
        by_media_type = conn.execute(f"SELECT media_type, COUNT(*) as n FROM dramas d{where} "
                                     "GROUP BY media_type", params).fetchall()
        total_lines = conn.execute(f"SELECT COUNT(*) {lines_from}", params).fetchone()[0]
        translated_lines = conn.execute(
            f"SELECT COUNT(*) {lines_from}{lines_where}lines.en IS NOT NULL AND lines.en != ''",
            params).fetchone()[0]
    return {
        "total_dramas": total_dramas,
        "by_status": {r["status"]: r["n"] for r in by_status},
        "by_media_type": {r["media_type"]: r["n"] for r in by_media_type},
        "total_lines": total_lines,
        "translated_lines": translated_lines,
    }


def list_dramas_recently_active(n: int = 10, visible_to: int = None):
    where, params = "", []
    if visible_to is not None:
        clause, params = db.drama_visible_sql("dramas", visible_to)
        where = " WHERE " + clause
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(f"SELECT * FROM dramas{where} ORDER BY updated_at DESC LIMIT ?",
                            params + [n]).fetchall()
    return [dict(r) for r in rows]


def drama_has_translated_lines(drama_id: int) -> bool:
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT 1 FROM lines WHERE drama_id = ? AND en IS NOT NULL AND en != '' "
                            "LIMIT 1", (drama_id,)).fetchone() is not None


def search_lines_globally(query: str, limit: int = 100, visible_to: int = None):
    """Searches zh/en text across every drama's lines, returns results
    with the parent drama's title attached, for the Library tab's
    global search -- not scoped to one drama like the Reader tab is.
    `visible_to` (auth B2): only dramas that user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = db.drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(db.get_conn()) as conn:
        like = _contains_pattern(query)
        rows = conn.execute(f"""
            SELECT l.drama_id, l.idx, l.zh, l.en, d.title_en, d.title_zh
            FROM lines l JOIN dramas d ON d.id = l.drama_id
            WHERE (l.zh LIKE ? ESCAPE '\\' OR l.en LIKE ? ESCAPE '\\'){visible}
            LIMIT ?
        """, [like, like] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]
