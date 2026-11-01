"""Dramas: create, update, delete and list a library title, the visibility
filter every drama query shares, the library-wide stats and line search, and
a title's custom tags."""

import contextlib
from lib import clock
import os
import re
import shutil

import db


_COLUMN_NAME = re.compile(r"[a-z_][a-z0-9_]*")
_UPDATE_REFUSED = ("id", "created_at")


def _check_columns(conn, keys, refused=()):
    """Raises ValueError naming the first key that is not a plain identifier
    or a real `dramas` column, so kwarg keys are safe to interpolate even if
    a caller forgot its whitelist. Columns are read per call so one added by
    an `init_db` migration is always seen."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(dramas)")}
    for key in keys:
        if (not _COLUMN_NAME.fullmatch(key) or key not in columns
                or key in refused):
            raise ValueError(f"not a drama column: {key!r}")


def create_drama(**fields) -> int:
    if fields.get("series_id") is not None:
        # User decision: only a whole series, or a drama
        # with no series, can be private.
        fields["is_private"] = 0
    with contextlib.closing(db.get_conn()) as conn:
        fields.setdefault("status", "not started")
        fields.setdefault("hallucination_silence_sec", 0)  # older databases' column default is 2
        now = clock.utcnow().isoformat()
        fields["created_at"] = now
        fields["updated_at"] = now
        _check_columns(conn, fields)
        cols = ", ".join(fields.keys())
        placeholders = ", ".join("?" for _ in fields)
        cur = conn.execute(f"INSERT INTO dramas ({cols}) VALUES ({placeholders})",
                            list(fields.values()))
        new_id = db.inserted_id(cur)
        db.claim_new_drama_folder(new_id)   # raising here rolls the insert back
        conn.commit()
    return new_id


def update_drama(drama_id: int, **fields):
    if not fields:
        return
    if fields.get("series_id") is not None:
        fields["is_private"] = 0    # as in create_drama
    fields["updated_at"] = clock.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        _check_columns(conn, fields, _UPDATE_REFUSED)
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE dramas SET {set_clause} WHERE id = ?",
                     list(fields.values()) + [drama_id])
        conn.commit()


# Fixed literal columns: the names are interpolated into the statement.
BLANK_FILLABLE = ("title_zh", "title_en", "author", "release_date")


def fill_blank_fields(drama_id: int, values: dict) -> list:
    """Writes each BLANK_FILLABLE value only where that column is still
    blank, each in one conditional statement, so text typed between a read
    and this write is never overwritten. Returns the columns written."""
    filled = []
    now = clock.utcnow().isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        for col in BLANK_FILLABLE:
            value = values.get(col)
            if not value:
                continue
            cur = conn.execute(
                f"UPDATE dramas SET {col} = ?, updated_at = ? "
                f"WHERE id = ? AND TRIM(COALESCE({col}, '')) = ''", (value, now, drama_id))
            if cur.rowcount:
                filled.append(col)
        conn.commit()
    return filled


def assign_series_if_none(drama_id: int, series_id: int) -> bool:
    """Puts a drama in a series only while it is in none, in one statement,
    so a series chosen meanwhile elsewhere is never replaced. Clears the
    drama's own private flag, as every series assignment does."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET series_id = ?, is_private = 0, updated_at = ? "
            "WHERE id = ? AND series_id IS NULL",
            (series_id, clock.utcnow().isoformat(), drama_id))
        conn.commit()
        return cur.rowcount > 0


def set_status_if(drama_id: int, expected: str, new: str) -> bool:
    """Changes the status only while it is still `expected`, in one statement,
    so a concurrent dub or export that set a later status is never undone."""
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "UPDATE dramas SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
            (new, clock.utcnow().isoformat(), drama_id, expected))
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


def _fold_if_needed(conn, term: str) -> tuple:
    """(term, SQL column wrapper). SQLite's LIKE only ignores ASCII case, so
    a term with a cased non-ASCII letter (é, Ä, Ж) compares casefolded
    text; anything else, CJK included, keeps the plain fast LIKE."""
    if not any(not c.isascii() and c.lower() != c.upper() for c in term):
        return term, "{}"
    conn.create_function("baihe_casefold", 1,
                         lambda s: s.casefold() if isinstance(s, str) else s,
                         deterministic=True)
    return term.casefold(), "baihe_casefold({})"


def list_dramas(search: str = "", studio: str = "", author: str = "",
                 voice_actor: str = "", status: str = "", source_language: str = "",
                 media_type: str = "", visible_to: int | None = None):
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
            search, col = _fold_if_needed(conn, search)
            query += " AND (" + " OR ".join(
                f"{col.format(c)} LIKE ? ESCAPE '\\'" for c in ("title_zh", "title_en", "summary")) + ")"
            like = _contains_pattern(search)
            params += [like, like, like]
        if studio:
            query += " AND studio = ?"
            params.append(studio)
        if author:
            query += " AND author = ?"
            params.append(author)
        if voice_actor:
            # A narrowing pass only: the exact entry is matched below.
            query += " AND voice_actors LIKE ? ESCAPE '\\'"
            params.append(_contains_pattern(voice_actor))
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
    dramas = [dict(r) for r in rows]
    if voice_actor:
        # The filter is picked from the comma-separated names, so "Li" must
        # not also match "Lin Yu".
        dramas = [d for d in dramas
                  if voice_actor in {v.strip() for v in (d.get("voice_actors") or "").split(",")}]
    return dramas


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
    return _order_series_dramas([dict(r) for r in rows])


def _order_series_dramas(dramas: list) -> list:
    if any(d.get("episode_number") is not None for d in dramas):
        dramas.sort(key=lambda d: (d.get("episode_number") is None, d.get("episode_number")))
    return dramas


def list_dramas_by_series_ids(series_ids) -> dict:
    """`list_dramas_by_series` for many series in one query (and the same
    per-series order): {series_id: [drama, ...]}. A series with no dramas has
    no key."""
    series_ids = list(dict.fromkeys(series_ids))
    grouped = {}
    # Chunked to stay under SQLite's bound-variable limit.
    with contextlib.closing(db.get_conn()) as conn:
        for i in range(0, len(series_ids), 500):
            chunk = series_ids[i:i + 500]
            rows = conn.execute(
                f"{_DRAMA_SELECT} WHERE series_id IN ({','.join('?' * len(chunk))}) "
                "ORDER BY created_at DESC", chunk).fetchall()
            for r in rows:
                grouped.setdefault(r["series_id"], []).append(dict(r))
    return {sid: _order_series_dramas(ds) for sid, ds in grouped.items()}


def get_library_stats(visible_to: int | None = None):
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


def list_dramas_recently_active(n: int = 10, visible_to: int | None = None):
    where, params = "", []
    if visible_to is not None:
        clause, params = db.drama_visible_sql("dramas", visible_to)
        where = " WHERE " + clause
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(f"SELECT * FROM dramas{where} ORDER BY updated_at DESC LIMIT ?",
                            params + [n]).fetchall()
    return [dict(r) for r in rows]


def line_progress_by_drama() -> dict:
    """{drama_id: (line_count, translated_count)} for every drama with lines,
    in one query, for the Library list's per-title progress."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT drama_id, COUNT(*) AS n, "
            "SUM(CASE WHEN TRIM(COALESCE(en, '')) != '' THEN 1 ELSE 0 END) AS done "
            "FROM lines GROUP BY drama_id").fetchall()
    return {r["drama_id"]: (r["n"], r["done"] or 0) for r in rows}


def drama_has_translated_lines(drama_id: int) -> bool:
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT 1 FROM lines WHERE drama_id = ? AND en IS NOT NULL AND en != '' "
                            "LIMIT 1", (drama_id,)).fetchone() is not None


def search_lines_globally(query: str, limit: int = 100, visible_to: int | None = None):
    """Searches zh/en text across every drama's lines, returns results
    with the parent drama's title attached, for the Library tab's
    global search -- not scoped to one drama like the Reader tab is.
    `visible_to` (auth B2): only dramas that user may see."""
    visible, vparams = "", []
    if visible_to is not None:
        clause, vparams = db.drama_visible_sql("d", visible_to)
        visible = " AND " + clause
    with contextlib.closing(db.get_conn()) as conn:
        query, col = _fold_if_needed(conn, query)
        like = _contains_pattern(query)
        rows = conn.execute(f"""
            SELECT l.drama_id, l.idx, l.zh, l.en, d.title_en, d.title_zh
            FROM lines l JOIN dramas d ON d.id = l.drama_id
            WHERE ({col.format("l.zh")} LIKE ? ESCAPE '\\'
                   OR {col.format("l.en")} LIKE ? ESCAPE '\\'){visible}
            LIMIT ?
        """, [like, like] + vparams + [limit]).fetchall()
    return [dict(r) for r in rows]


# Personal organizational tags, kept in custom_tags alongside any
# user-defined ones -- deliberately separate from dramas.status, which
# tracks pipeline progress, not how the person is organizing their list.
ORGANIZATIONAL_TAGS = ("Favorite", "On Hold", "Plan to Translate")
# Set on a title whose chapters were all merged into another, so the Library
# shows which empty titles are left to delete and the extension's automatic
# title match stops sending new chapters to it.
MERGED_TAG = "Merged"


def has_custom_tag(drama: dict, tag: str) -> bool:
    """Case-insensitive, so a hand-typed "favorite" counts as Favorite."""
    return tag.lower() in (t.strip().lower() for t in (drama.get("custom_tags") or "").split(","))


def set_custom_tag(drama_id: int, tag: str, present: bool):
    """Adds or removes one tag from a drama's custom_tags, leaving every
    other tag (and every other column) untouched."""
    drama = db.get_drama(drama_id)
    if not drama:
        return
    tags = [t.strip() for t in (drama.get("custom_tags") or "").split(",")
            if t.strip() and t.strip().lower() != tag.lower()]
    if present:
        tags.append(tag)
    db.update_drama(drama_id, custom_tags=", ".join(tags))
