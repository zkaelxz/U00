"""Lines of a drama: the id-keyed save (full sync and field-scoped), the
compare-and-set edits, the per-line tables a sync carries along, and the
loaders."""

import contextlib

import db
# core has no db dependency, so importing it here can't cycle.
from core import LINE_FIELDS as _LINE_COLUMNS
from core import LINE_LANGUAGES as _LINE_LANGUAGES
from core import lines_from_rows
from lib.errors import ConflictError


def line_value(ln, f):
    v = getattr(ln, f, None)
    if f in ("speaker_manual", "sfx"):
        return int(bool(v))
    if f == "lang":
        # "" and None both mean "the title's language"; store one of them.
        return v or None
    return "" if (f == "flag_note" and v is None) else v


def update_line_fields_if(drama_id: int, line_id: int, values: dict, expected: dict) -> bool:
    """Compare-and-set for one line: ONE conditional UPDATE that writes
    `values` (column -> new value) only if every `expected` column still
    holds the value the caller saw (start/end within 1e-6, sfx as a bool,
    text/speaker/flag/flag_note with NULL equal to ""). Returns True if the row changed,
    False if it no longer matches (or no longer exists) -- nothing is
    written then. Never inserts or deletes."""
    sql, params = _line_cas_sql(drama_id, line_id, values, expected)
    conn = db.get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_lines_fields_if_many(drama_id: int, items) -> list:
    """Batch compare-and-set: `items` is [(line_id, values, expected), ...],
    each the same conditional UPDATE as update_line_fields_if, all inside
    ONE `BEGIN IMMEDIATE` transaction with one commit. Returns the line ids
    whose expected values no longer matched (or that no longer exist);
    those are skipped, the rest are written. On any error the whole batch
    is rolled back and nothing is written."""
    stmts = [(lid, *_line_cas_sql(drama_id, lid, values, expected))
             for lid, values, expected in items]
    if not stmts:
        return []
    missed = []
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        for lid, sql, params in stmts:
            if conn.execute(sql, params).rowcount == 0:
                missed.append(lid)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return missed


# SET clause dropping a line's stored words when its text really changes (SQLite
# evaluates every SET against the old row, so `zh` here is the text before).
# The fingerprint in the payload already makes stale words unusable; this keeps
# them from lingering in the row.
_CLEAR_STALE_WORDS = "word_timings = CASE WHEN zh IS ? THEN word_timings ELSE NULL END"


def _line_cas_sql(drama_id: int, line_id: int, values: dict, expected: dict):
    """(sql, params) for one line's conditional UPDATE; validates columns."""
    sets, args = [], []
    for col, val in values.items():
        if col not in _LINE_COLUMNS:
            raise ValueError(f"Unknown line column: {col}")
        sets.append(f"{col} = ?")
        args.append(val)
    if "zh" in values:
        sets.append(_CLEAR_STALE_WORDS)
        args.append(values["zh"])
    conds, cargs = ["id = ?", "drama_id = ?"], [line_id, drama_id]
    for col, val in (expected or {}).items():
        if col in ("start", "end"):
            conds.append(f"ABS({col} - ?) < 1e-6")
            cargs.append(float(val))
        elif col == "sfx":
            conds.append("COALESCE(sfx, 0) = ?")
            cargs.append(int(bool(val)))
        elif col == "lang":
            # Stored rows are read leniently (core._stored_line_lang): an
            # imported "KO" reads as "ko" and an unknown code as the title
            # default, so the check must see the same value or the line could
            # never be saved again.
            codes = ", ".join("?" for _ in _LINE_LANGUAGES)
            conds.append(f"CASE WHEN LOWER(lang) IN ({codes}) THEN LOWER(lang) ELSE '' END = ?")
            cargs.extend(_LINE_LANGUAGES)
            cargs.append(val or "")
        elif col in ("zh", "en", "speaker", "flag", "flag_note"):
            conds.append(f"COALESCE({col}, '') = ?")
            cargs.append(val or "")
        else:
            raise ValueError(f"Unknown expected column: {col}")
    return f"UPDATE lines SET {', '.join(sets)} WHERE {' AND '.join(conds)}", args + cargs


def save_lines(drama_id: int, lines, fields=None, only_if_unchanged=False, guard_fields=(),
               guard_neighbours=None, expect_unchanged=None, known_note_ids=None):
    """Saves a drama's lines by their permanent id (Line.id).

    Full sync (fields=None) -- the list IS the drama's lines now:
      - a line whose id exists for this drama is updated in place;
      - a line with no id (or an id that isn't this drama's) is inserted,
        and its new id is written back onto the object;
      - an existing row whose id isn't in the list is deleted (its notes
        and emotions with it), except that a line's `merged_ids` are
        re-pointed onto that line first, so a note on a merged-away line
        stays with the line it was merged into.

    Field-scoped (fields=("en",) etc.) -- for background jobs: only those
    columns are updated, only on rows that still exist. Never inserts or
    deletes, so a job can't resurrect a line the user merged away or
    delete one the user just added.

    Either way, a line loaded through core.line_from_row carries `orig`,
    and a field whose value still equals `orig` isn't written -- it wasn't
    changed by this caller, so whatever is in the database now (another
    writer's newer value) is kept. After saving, `orig` is updated.

    only_if_unchanged (field-scoped only): a field is written only while
    the database still holds this caller's `orig` value, so an edit saved
    by someone else since the lines were loaded is kept, not overwritten.
    A line without `orig` is then not written at all.

    guard_fields (with only_if_unchanged): columns not written but whose
    database value must still equal this Line's own value -- the text the
    written fields were computed from (a flag from `en` and its timing).

    guard_neighbours (with guard_fields): {line id: [Line, ...]} -- other
    lines a written line's value was computed from (an overlap flag reads
    the next line's start). Each must still exist at its `idx` and hold its
    guard_fields values, or the line is left unwritten.

    expect_unchanged (full sync only): the Lines this write was computed
    from, as read. The drama's lines must still be exactly these (same ids,
    every field equal), checked inside this transaction, or ConflictError
    is raised and nothing is written. Line edits don't take the
    structural writers' drama lock, so only this makes their check and
    save one step.

    known_note_ids (full sync only): translation note ids the caller has
    seen. A line this sync deletes that carries any other note raises
    ConflictError, so a note added since the caller looked isn't lost.

    Word timings (Line.word_timings) are written only by a full sync, and
    only when the Line carries a value that differs from `orig`; None leaves
    the column alone. Any save that changes a line's `zh` without new words
    clears them, since they described the old text.

    Returns the ids only_if_unchanged left unwritten (an edit was kept);
    empty otherwise.

    One transaction: on any error nothing is written."""
    if only_if_unchanged and fields is None:
        raise ValueError("only_if_unchanged needs field-scoped saving")
    if guard_fields and not only_if_unchanged:
        raise ValueError("guard_fields needs only_if_unchanged")
    if guard_neighbours and not guard_fields:
        raise ValueError("guard_neighbours needs guard_fields")
    if fields is not None and (expect_unchanged is not None or known_note_ids is not None):
        raise ValueError("expect_unchanged and known_note_ids need a full sync")
    guard_cols = tuple(f for f in _LINE_COLUMNS if f in guard_fields)
    cols = _LINE_COLUMNS if fields is None else tuple(f for f in _LINE_COLUMNS if f in fields)
    conn = db.get_conn()
    try:
        # IMMEDIATE: a deferred BEGIN reads then upgrades to a write,
        # and in WAL mode a commit from another connection in between fails
        # that upgrade at once with "database is locked" (no busy wait).
        conn.execute("BEGIN IMMEDIATE")
        existing = {r["id"] for r in conn.execute(
            "SELECT id FROM lines WHERE drama_id = ?", (drama_id,)).fetchall()}
        if expect_unchanged is not None:
            _check_unchanged(conn, drama_id, expect_unchanged)
        kept, unwritten, words_written, words_cleared = set(), set(), set(), set()
        for ln in lines:
            lid = getattr(ln, "id", None)
            orig = getattr(ln, "orig", None)
            if lid in existing and lid not in kept:
                changed = [f for f in cols
                           if orig is None or db.line_value(ln, f) != orig.get(f)]
                sets = [f + " = ?" for f in changed]
                values = [db.line_value(ln, f) for f in changed]
                words = getattr(ln, "word_timings", None)
                if fields is None and words is not None and (
                        orig is None or words != orig.get("word_timings")):
                    sets.append("word_timings = ?")
                    values.append(words)
                    words_written.add(id(ln))
                elif "zh" in changed:
                    sets.append(_CLEAR_STALE_WORDS)
                    values.append(db.line_value(ln, "zh"))
                    words_cleared.add(id(ln))
                if changed and only_if_unchanged:
                    if orig is None:
                        unwritten.add(lid)
                        words_cleared.discard(id(ln))
                    else:
                        expected = [(f, orig.get(f)) for f in changed]
                        expected += _guard_values(ln, guard_cols)
                        where, args = _guard_sql(expected), [v for _f, v in expected]
                        for other in (guard_neighbours or {}).get(lid, ()):
                            theirs = [("idx", other.idx)] + _guard_values(other, guard_cols)
                            where += (" AND EXISTS (SELECT 1 FROM lines WHERE id = ? "
                                      f"AND drama_id = ? AND {_guard_sql(theirs)})")
                            args += [other.id, drama_id] + [v for _f, v in theirs]
                        cur = conn.execute(
                            f"UPDATE lines SET {', '.join(sets)} "
                            f"WHERE id = ? AND drama_id = ? AND {where}",
                            values + [lid, drama_id] + args)
                        if cur.rowcount == 0:
                            unwritten.add(lid)
                            words_cleared.discard(id(ln))
                elif sets:
                    conn.execute(
                        f"UPDATE lines SET {', '.join(sets)} WHERE id = ? AND drama_id = ?",
                        values + [lid, drama_id])
                kept.add(lid)
            elif fields is None:
                cur = conn.execute(
                    f"INSERT INTO lines (drama_id, {', '.join(_LINE_COLUMNS)}, word_timings) "
                    f"VALUES (?, {', '.join('?' for _ in _LINE_COLUMNS)}, ?)",
                    [drama_id] + [db.line_value(ln, f) for f in _LINE_COLUMNS]
                    + [getattr(ln, "word_timings", None)])
                ln.id = cur.lastrowid
                kept.add(ln.id)
                words_written.add(id(ln))
            else:
                # A field-scoped save never inserts: a job's stale copy of a
                # line the user deleted or merged away must not resurrect it.
                continue
        if fields is None:
            for ln in lines:
                for mid in getattr(ln, "merged_ids", None) or []:
                    if mid in existing and mid not in kept:
                        _repoint_line_refs(conn, drama_id, mid, ln.id)
            removed = existing - kept
            if known_note_ids is not None:
                _check_no_new_notes(conn, drama_id, removed, known_note_ids)
            for lid in removed:
                _delete_line_refs(conn, lid)
            conn.executemany("DELETE FROM lines WHERE id = ?", [(lid,) for lid in removed])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    for ln in lines:
        if getattr(ln, "id", None) is None or not hasattr(ln, "orig"):
            continue
        # What this caller last wrote/saw is now the baseline, so its next
        # save only writes what changes after this point.
        ln.orig = {**(ln.orig or {}), **{f: db.line_value(ln, f) for f in cols}}
        if id(ln) in words_written:
            ln.orig["word_timings"] = ln.word_timings
        elif id(ln) in words_cleared:
            # The row's words were for the old text; a later save mustn't put them back.
            ln.word_timings = None
            ln.orig.pop("word_timings", None)
        if fields is None:
            ln.merged_ids = []
    return unwritten


def _guard_values(ln, cols) -> list:
    # A guard with no value is empty text, as NULL is to a Line.
    return [(f, "" if db.line_value(ln, f) is None else db.line_value(ln, f)) for f in cols]


def _guard_sql(expected) -> str:
    # NULL and "" are the same empty text to a Line, so a text field compares
    # through COALESCE.
    return " AND ".join(f"COALESCE({f}, '') = ?" if isinstance(v, str) else f"{f} IS ?"
                        for f, v in expected)


_CHANGED_WHILE_SAVING = ("This title's lines changed while saving -- nothing was changed; "
                         "reload and try again.")


def _check_unchanged(conn, drama_id, expected):
    now = lines_from_rows(dict(r) for r in conn.execute(
        f"SELECT {_LOAD_COLUMNS} FROM lines WHERE drama_id = ? ORDER BY idx, id", (drama_id,)))
    if [ln.id for ln in now] != [ln.id for ln in expected] or now != list(expected):
        raise ConflictError(_CHANGED_WHILE_SAVING)


def _check_no_new_notes(conn, drama_id, removed, known_note_ids):
    if any(r["line_id"] in removed and r["id"] not in known_note_ids for r in conn.execute(
            "SELECT id, line_id FROM translation_notes WHERE drama_id = ?", (drama_id,))):
        raise ConflictError(_CHANGED_WHILE_SAVING)


# Every per-line table a full sync touches, and what it does to a removed
# line's rows there: notes and emotions are deleted with the line; a reading
# position only loses its line_id (its line_idx still shows where it was).
# _repoint_line_refs and _delete_line_refs both read this, so a new per-line
# table is handled by both or neither.
_LINE_REF_TABLES = {"translation_notes": "delete", "line_emotions": "delete",
                    "reading_history": "unlink"}


def _repoint_line_refs(conn, drama_id, from_id, to_id):
    """Moves notes/emotions/reading history from a merged-away line onto
    the line it was merged into. Where the target already has an emotion
    (one per line) or a note on the same term, the target's own wins."""
    for table in _LINE_REF_TABLES:
        conn.execute(f"UPDATE OR IGNORE {table} SET line_id = ? WHERE line_id = ? AND drama_id = ?",
                     (to_id, from_id, drama_id))


def line_ids_with_refs(drama_id: int, line_ids) -> set:
    """Which of `line_ids` have a row that a full sync removing them would
    delete (a note or emotion tag; a reading position is only unlinked)."""
    ids = set(line_ids)
    if not ids:
        return set()
    # Read per drama and intersect here: a restore can remove thousands of
    # lines, past SQLite's bound-parameter limit for an IN list.
    tables = [t for t, how in _LINE_REF_TABLES.items() if how == "delete"]
    query = " UNION ".join(f"SELECT line_id FROM {t} WHERE drama_id = ?" for t in tables)
    with contextlib.closing(db.get_conn()) as conn:
        return {r[0] for r in conn.execute(query, [drama_id] * len(tables))} & ids


def _delete_line_refs(conn, line_id):
    for table, how in _LINE_REF_TABLES.items():
        if how == "delete":
            conn.execute(f"DELETE FROM {table} WHERE line_id = ?", (line_id,))
        else:
            conn.execute(f"UPDATE {table} SET line_id = NULL WHERE line_id = ?", (line_id,))


_LOAD_COLUMNS = ("id, idx, start, end, zh, en, speaker, dub_filename, flag, flag_note, "
                 "speaker_manual, sfx, lang")


def load_lines(drama_id: int, with_words: bool = False):
    """with_words adds word_timings, which only the re-split paths need."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            f"SELECT {_LOAD_COLUMNS}{', word_timings' if with_words else ''} FROM lines "
            "WHERE drama_id = ? ORDER BY idx, id",
            (drama_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def load_line_objects(drama_id: int, with_words: bool = False):
    """db.load_lines as core.Line objects (id and `orig` set) -- the shared
    loader every caller that edits and re-saves lines should use."""
    return lines_from_rows(db.load_lines(drama_id, with_words))


def load_line_ids(drama_id: int) -> set:
    """Just the current set of line ids for this drama -- a cheap "does it
    have lines" check. A full sync computed from an earlier read checks
    staleness with save_lines(expect_unchanged=...) instead."""
    with contextlib.closing(db.get_conn()) as conn:
        ids = {r["id"] for r in conn.execute(
            "SELECT id FROM lines WHERE drama_id = ?", (drama_id,)).fetchall()}
    return ids


def line_ids_exist(drama_id: int, line_ids) -> int:
    """How many of line_ids are still lines of this drama."""
    ids = [i for i in line_ids if i is not None]
    if not ids:
        return 0
    with contextlib.closing(db.get_conn()) as conn:
        n = conn.execute(
            f"SELECT COUNT(*) FROM lines WHERE drama_id = ? AND id IN ({', '.join('?' for _ in ids)})",
            [drama_id] + ids).fetchone()[0]
    return n
