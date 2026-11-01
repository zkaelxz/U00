"""Comic pages and their text regions (speech bubbles, narration, signs,
sound effects) for scanlation and typesetting: page rows, the id-preserving
bubble edits that bump a page's rev, the comic editor's recorded edits,
creates and deletes with their undo, and the legacy full replace. The tables
are created by db.init_db."""

import contextlib
import json

import db


# ---------------------------------------------------------------------------
# Pages & bubbles CRUD (scanlation/typesetting)
# ---------------------------------------------------------------------------

def create_page(drama_id: int, idx: int, filename: str, width: int, height: int) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO pages (drama_id, idx, filename, width, height) VALUES (?, ?, ?, ?, ?)",
            (drama_id, idx, filename, width, height))
        conn.commit()
        new_id = cur.lastrowid
    return new_id


_PAGE_UPDATE_FIELDS = ("idx", "filename", "rendered_filename", "width", "height",
                       "context_summary", "run_notes")


def update_page(page_id: int, **fields):
    """Field-scoped page update. Keys are interpolated into the SQL, so only
    _PAGE_UPDATE_FIELDS are accepted (ValueError otherwise); rev is bumped
    only by the region writes below, never set directly."""
    if not fields:
        return
    bad = [k for k in fields if k not in _PAGE_UPDATE_FIELDS]
    if bad:
        raise ValueError(f"update_page: unknown field(s) {bad}")
    with contextlib.closing(db.get_conn()) as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE pages SET {set_clause} WHERE id = ?", list(fields.values()) + [page_id])
        conn.commit()


def list_pages(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM pages WHERE drama_id = ? ORDER BY idx", (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def get_page(page_id: int, drama_id: int = None):
    """One page row, or None. With drama_id, None unless the page belongs to
    that drama (ids from a request must be scoped to their drama)."""
    with contextlib.closing(db.get_conn()) as conn:
        if drama_id is None:
            row = conn.execute("SELECT * FROM pages WHERE id = ?", (page_id,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM pages WHERE id = ? AND drama_id = ?",
                               (page_id, drama_id)).fetchone()
    return dict(row) if row else None


# Id-preserving region writes. Unlike save_bubbles (a full delete-and-reinsert
# that gives every bubble a new id), each of these keeps every other bubble's
# id and bumps pages.rev once.
BUBBLE_EDIT_FIELDS = ("x", "y", "w", "h", "source_text", "translated_text", "font_size",
                      "skip", "font_category", "kind", "kind_confidence", "confidence",
                      "language", "orientation", "panel_id", "include_sfx", "text_color")
_BUBBLE_TEXT_FIELDS = ("source_text", "translated_text", "font_category", "kind",
                       "language", "orientation", "text_color")
_BUBBLE_BOOL_FIELDS = ("skip", "include_sfx")


def _bubble_row_values(b: dict) -> tuple:
    return (b["x"], b["y"], b["w"], b["h"], b.get("source_text", "") or "",
            b.get("translated_text", "") or "", b.get("font_size", 18),
            int(bool(b.get("skip", False))), b.get("font_category") or "regular",
            b.get("kind") or "bubble", b.get("kind_confidence"), b.get("confidence"),
            b.get("language"), b.get("orientation"), b.get("panel_id"),
            int(bool(b.get("include_sfx"))), b.get("text_color"))


_BUBBLE_INSERT_SQL = (
    "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, "
    "font_size, skip, font_category, kind, kind_confidence, confidence, language, "
    "orientation, panel_id, include_sfx, text_color) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def _bump_page_rev(conn, page_id: int):
    conn.execute("UPDATE pages SET rev = COALESCE(rev, 0) + 1 WHERE id = ?", (page_id,))


def _bubble_value(field, value):
    if field in _BUBBLE_BOOL_FIELDS:
        return int(bool(value))
    return value


def update_bubble_fields(bubble_id: int, fields: dict, expected: dict = None,
                         page_id: int = None) -> bool:
    """Compare-and-set for one bubble: ONE conditional UPDATE that writes
    `fields` only if every `expected` column still holds the value the
    caller saw (text columns: NULL equals ""; skip/include_sfx as bools).
    With page_id, the bubble must also be on that page. Returns True if the
    row changed (and bumps its page's rev), False if it no longer matches
    or no longer exists -- nothing is written then. Unknown columns raise
    ValueError (they are interpolated into the SQL)."""
    expected = expected or {}
    bad = [k for k in list(fields) + list(expected) if k not in db.BUBBLE_EDIT_FIELDS]
    if bad or not fields:
        raise ValueError(f"update_bubble_fields: bad field(s) {bad or 'none given'}")
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = [_bubble_value(k, v) for k, v in fields.items()]
    where = ["id = ?"]
    params.append(bubble_id)
    if page_id is not None:
        where.append("page_id = ?")
        params.append(page_id)
    for k, v in expected.items():
        if k in _BUBBLE_TEXT_FIELDS:
            where.append(f"COALESCE({k}, '') = ?")
            params.append("" if v is None else v)
        elif v is None:
            where.append(f"{k} IS NULL")
        else:
            where.append(f"{k} = ?")
            params.append(_bubble_value(k, v))
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(f"UPDATE bubbles SET {sets} WHERE {' AND '.join(where)}", params)
        changed = cur.rowcount > 0
        if changed:
            pid = conn.execute("SELECT page_id FROM bubbles WHERE id = ?", (bubble_id,)).fetchone()[0]
            _bump_page_rev(conn, pid)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def replace_bubbles_if_unchanged(page_id: int, expected_ids, bubbles, expected_rev: int = None):
    """Conditional full replace for a background job: in ONE transaction,
    replaces the page's bubbles with `bubbles` (reading order = list order)
    only if the page's current bubble-id set is exactly `expected_ids` and,
    when given, its rev is still `expected_rev` (an id-preserving edit bumps
    rev; a legacy save_bubbles changes the ids) -- so a page the user (or
    another writer) changed since the job read it is left alone. Returns
    the new ids in order, or None if nothing was written."""
    expected = {int(i) for i in expected_ids}
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT COALESCE(rev, 0) FROM pages WHERE id = ?", (page_id,)).fetchone()
        if row is None or (expected_rev is not None and int(row[0]) != int(expected_rev)):
            conn.rollback()
            return None
        current = {r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ?", (page_id,)).fetchall()}
        if current != expected:
            conn.rollback()
            return None
        conn.execute("DELETE FROM bubbles WHERE page_id = ?", (page_id,))
        new_ids = [conn.execute(_BUBBLE_INSERT_SQL, (page_id, i) + _bubble_row_values(b)).lastrowid
                   for i, b in enumerate(bubbles)]
        _bump_page_rev(conn, page_id)
        conn.commit()
        return new_ids
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# The comic editor's per-page undo list (pages.edit_history), oldest first.
# One entry per editor write: {"op": "edit" | "create" | "delete",
# "region_id", "rev_before", "rev_after"} plus, for an edit, the changed
# fields' "before"/"after"; for a create, the new row as "after"; for a
# delete, the removed row as "before". Entries chain: each one's rev_before
# is the previous one's rev_after, and the latest one also keeps the page's
# region ids as it left them ("ids_after"), because the legacy save_bubbles
# reissues every id without bumping rev. Any other region write breaks the
# chain, so an undo can never write over a change it didn't make.
EDIT_HISTORY_LIMIT = 50
# Bounds what one page's editor rows, undo entries and render can grow to,
# whatever a client sends.
MAX_REGIONS_PER_PAGE = 200
_BUBBLE_INSERT_WITH_ID_SQL = (_BUBBLE_INSERT_SQL.replace("(page_id,", "(id, page_id,", 1)
                              .replace("VALUES (", "VALUES (?, ", 1))


def _region_row_fields() -> tuple:
    """What a delete keeps so its undo can put the row back exactly as it was."""
    return ("idx",) + db.BUBBLE_EDIT_FIELDS


def _same_value(field, stored, wanted) -> bool:
    if field in _BUBBLE_TEXT_FIELDS:
        return (stored or "") == (wanted or "")
    if field in _BUBBLE_BOOL_FIELDS:
        return bool(stored) == bool(wanted)
    return stored == wanted


def _well_formed(entry) -> bool:
    """create entries carry only `after` and delete entries only `before`, so
    each side may be absent; a present side must be a dict."""
    return (isinstance(entry, dict) and isinstance(entry.get("op", "edit"), str)
            and all(entry.get(side) is None or isinstance(entry[side], dict)
                    for side in ("before", "after")))


def _load_history(raw) -> list:
    # Any Exception, not just ValueError: a deeply nested value raises RecursionError.
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    return [e for e in data if _well_formed(e)] if isinstance(data, list) else []


def _save_history(conn, page_id: int, history: list):
    conn.execute("UPDATE pages SET edit_history = ? WHERE id = ?",
                 (json.dumps(history[-db.EDIT_HISTORY_LIMIT:], ensure_ascii=False) if history
                  else None, page_id))


def edit_history_depth(page: dict, region_ids=None) -> int:
    """How many edits of this page row can be undone one after another: 0
    once another writer has moved the page on past the latest entry (its
    rev, or with `region_ids` the page's current ids, no longer match)."""
    history = _load_history(page.get("edit_history"))
    if not history or history[-1].get("rev_after") != int(page.get("rev") or 0):
        return 0
    ids_after = history[-1].get("ids_after")
    if region_ids is not None and ids_after is not None and ids_after != sorted(region_ids):
        return 0
    return len(history)


def _page_rev_and_history(conn, page_id: int):
    row = conn.execute("SELECT COALESCE(rev, 0), edit_history FROM pages WHERE id = ?",
                       (page_id,)).fetchone()
    return (None, []) if row is None else (int(row[0]), _load_history(row[1]))


def _page_region_ids(conn, page_id: int) -> list:
    return [r[0] for r in conn.execute("SELECT id FROM bubbles WHERE page_id = ? ORDER BY id",
                                       (page_id,)).fetchall()]


def _chain_holds(conn, page_id: int, history: list, rev: int) -> bool:
    """True when nothing but the recorded entries has written the page since
    the latest one."""
    if not history or history[-1].get("rev_after") != rev:
        return False
    ids_after = history[-1].get("ids_after")
    return ids_after is None or ids_after == _page_region_ids(conn, page_id)


def _live_history(conn, page_id: int, history: list, rev: int) -> list:
    """The history a new entry joins, read before the write: emptied when
    another writer broke the chain, since its entries no longer describe
    the page."""
    return history if _chain_holds(conn, page_id, history, rev) else []


def _record(conn, page_id: int, history: list, rev: int, entry: dict):
    """After the write and the rev bump: appends `entry` and saves."""
    for older in history:
        older.pop("ids_after", None)              # only the latest one is checked
    entry.update(rev_before=rev, rev_after=rev + 1, ids_after=_page_region_ids(conn, page_id))
    history.append(entry)
    _save_history(conn, page_id, history)


def _region_row(conn, page_id: int, region_id):
    row = conn.execute("SELECT * FROM bubbles WHERE id = ? AND page_id = ?",
                       (region_id, page_id)).fetchone()
    return dict(row) if row is not None else None


def _page_write(page_id: int, expected_rev: int, step) -> tuple:
    """Runs `step(conn, rev, history)` in ONE IMMEDIATE transaction once the
    page exists and is still at `expected_rev`; `step` returns (commit,
    result). Returns ("no_page", None), ("stale", rev) or the step's result."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        rev, history = _page_rev_and_history(conn, page_id)
        if rev is None:
            commit, result = False, ("no_page", None)
        elif rev != int(expected_rev):
            commit, result = False, ("stale", rev)
        else:
            commit, result = step(conn, rev, history)
        if commit:
            conn.commit()
        else:
            conn.rollback()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _check_columns(name: str, keys):
    # The keys are interpolated into the SQL.
    bad = [k for k in keys if k not in db.BUBBLE_EDIT_FIELDS]
    if bad:
        raise ValueError(f"{name}: bad field(s) {bad}")


def edit_region(page_id: int, bubble_id: int, fields: dict, expected_rev: int,
                expected: dict = None) -> tuple:
    """The comic editor's write: in ONE transaction, sets `fields` on one
    bubble of this page only if the page's rev is still `expected_rev` and
    every `expected` column still holds the value the caller saw, then bumps
    rev and records the edit for undo. Returns (status, rev): "saved" with
    the new rev, "unchanged" (every field already held its value; nothing
    written), "stale" (rev or an expected value moved on), "missing" (no
    such bubble on the page) or "no_page", each with the current rev.
    Unknown columns raise ValueError (they are interpolated into the SQL)."""
    expected = expected or {}
    if not fields:
        raise ValueError("edit_region: no fields given")
    _check_columns("edit_region", list(fields) + list(expected))

    def step(conn, rev, history):
        row = _region_row(conn, page_id, bubble_id)
        if row is None:
            return False, ("missing", rev)
        if not all(_same_value(k, row[k], v) for k, v in expected.items()):
            return False, ("stale", rev)
        changes = {k: _bubble_value(k, v) for k, v in fields.items()
                   if not _same_value(k, row[k], v)}
        if not changes:
            return False, ("unchanged", rev)
        history = _live_history(conn, page_id, history, rev)
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE bubbles SET {sets} WHERE id = ?",
                     list(changes.values()) + [bubble_id])
        _bump_page_rev(conn, page_id)
        _record(conn, page_id, history, rev, {
            "op": "edit", "region_id": bubble_id, "before": {k: row[k] for k in changes},
            "after": changes})
        return True, ("saved", rev + 1)
    return _page_write(page_id, expected_rev, step)


def create_region(page_id: int, values: dict, expected_rev: int) -> tuple:
    """Adds one region (x, y, w, h required; any other BUBBLE_EDIT_FIELDS
    optional) at the end of the page's reading order, under the same rev
    check, and records it for undo. Returns (status, rev, region_id):
    "saved" with the new rev and id, "full" (MAX_REGIONS_PER_PAGE reached),
    "stale" or "no_page" with no id. Unknown columns raise ValueError."""
    _check_columns("create_region", values)
    if any(values.get(k) is None for k in ("x", "y", "w", "h")):
        raise ValueError("create_region: x, y, w and h are required")

    def step(conn, rev, history):
        count = conn.execute("SELECT COUNT(*) FROM bubbles WHERE page_id = ?",
                             (page_id,)).fetchone()[0]
        if count >= db.MAX_REGIONS_PER_PAGE:
            return False, ("full", rev, None)
        history = _live_history(conn, page_id, history, rev)
        idx = conn.execute("SELECT COALESCE(MAX(idx), -1) + 1 FROM bubbles WHERE page_id = ?",
                           (page_id,)).fetchone()[0]
        new_id = conn.execute(_BUBBLE_INSERT_SQL,
                              (page_id, idx) + _bubble_row_values(values)).lastrowid
        _bump_page_rev(conn, page_id)
        row = _region_row(conn, page_id, new_id)
        _record(conn, page_id, history, rev, {
            "op": "create", "region_id": new_id,
            "after": {k: row[k] for k in _region_row_fields()}})
        return True, ("saved", rev + 1, new_id)
    result = _page_write(page_id, expected_rev, step)
    return result if len(result) == 3 else result + (None,)


def delete_region(page_id: int, bubble_id: int, expected_rev: int) -> tuple:
    """Removes one region of this page under the same rev check, keeping
    the whole row in the undo list. Other regions keep their ids and idx.
    Returns (status, rev): "deleted" with the new rev, "missing" (no such
    region on the page), "stale" or "no_page"."""
    def step(conn, rev, history):
        row = _region_row(conn, page_id, bubble_id)
        if row is None:
            return False, ("missing", rev)
        history = _live_history(conn, page_id, history, rev)
        conn.execute("DELETE FROM bubbles WHERE id = ?", (bubble_id,))
        _bump_page_rev(conn, page_id)
        _record(conn, page_id, history, rev, {
            "op": "delete", "region_id": bubble_id,
            "before": {k: row[k] for k in _region_row_fields()}})
        return True, ("deleted", rev + 1)
    return _page_write(page_id, expected_rev, step)


# Each undo step first checks the region is still as its entry left it and
# returns False, having written nothing, when it is not.

def _undo_edit(conn, page_id: int, entry: dict, older: list) -> bool:
    before, after = entry.get("before") or {}, entry.get("after") or {}
    row = _region_row(conn, page_id, entry.get("region_id"))
    if not (row is not None and before and set(before) == set(after)
            and set(before) <= set(db.BUBBLE_EDIT_FIELDS)
            and all(_same_value(k, row[k], v) for k, v in after.items())):
        return False
    sets = ", ".join(f"{k} = ?" for k in before)
    conn.execute(f"UPDATE bubbles SET {sets} WHERE id = ?",
                 [_bubble_value(k, v) for k, v in before.items()] + [row["id"]])
    return True


def _undo_create(conn, page_id: int, entry: dict, older: list) -> bool:
    after = entry.get("after") or {}
    row = _region_row(conn, page_id, entry.get("region_id"))
    if not (row is not None and after and set(after) <= set(_region_row_fields())
            and all(_same_value(k, row[k], v) for k, v in after.items())):
        return False
    conn.execute("DELETE FROM bubbles WHERE id = ?", (row["id"],))
    return True


def _undo_delete(conn, page_id: int, entry: dict, older: list) -> bool:
    before, old_id = entry.get("before") or {}, entry.get("region_id")
    if not (isinstance(old_id, int) and set(before) <= set(_region_row_fields())
            and all(before.get(k) is not None for k in ("x", "y", "w", "h"))):
        return False
    owner = conn.execute("SELECT page_id FROM bubbles WHERE id = ?", (old_id,)).fetchone()
    if owner is not None and owner[0] == page_id:
        return False
    values = (page_id, before.get("idx")) + _bubble_row_values(before)
    if owner is None:
        conn.execute(_BUBBLE_INSERT_WITH_ID_SQL, (old_id,) + values)
        return True
    # bubbles.id is AUTOINCREMENT, so the old id is normally still free. If a
    # row elsewhere holds it, the region comes back under a new id and every
    # older entry naming it follows; one page's uses of an id never overlap
    # in time, so they all mean this region.
    new_id = conn.execute(_BUBBLE_INSERT_SQL, values).lastrowid
    for e in older:
        if e.get("region_id") == old_id:
            e["region_id"] = new_id
    return True


_UNDO_STEPS = {"edit": _undo_edit, "create": _undo_create, "delete": _undo_delete}


def _undone(undo, conn, page_id: int, history: list) -> bool:
    """A stored entry the step cannot make sense of counts as a broken
    history, not a server error."""
    try:
        return undo(conn, page_id, history[-1], history[:-1])
    except Exception:
        return False


def undo_region_edit(page_id: int, expected_rev: int) -> tuple:
    """Reverses the page's latest recorded edit, create or delete, in ONE
    transaction, only if the page's rev is still `expected_rev` and nothing
    has written the page since that entry (rev, region ids and the region's
    values are as it left them). Returns (status, rev): "undone" with the
    new rev, "stale" (the caller's rev is old), "nothing" (no edit to undo),
    "broken" (another writer changed the page after the entry; the history
    is dropped, since every older entry builds on that one) or "no_page"."""
    def step(conn, rev, history):
        if not history:
            return False, ("nothing", rev)
        # Entries written before ops were recorded are all edits.
        undo = _UNDO_STEPS.get(history[-1].get("op", "edit"))
        if not (_chain_holds(conn, page_id, history, rev) and undo
                and _undone(undo, conn, page_id, history)):
            _save_history(conn, page_id, [])
            return True, ("broken", rev)
        _bump_page_rev(conn, page_id)
        history.pop()
        if history:
            # The page is now as the previous entry left it.
            history[-1].update(rev_after=rev + 1, ids_after=_page_region_ids(conn, page_id))
        _save_history(conn, page_id, history)
        return True, ("undone", rev + 1)
    return _page_write(page_id, expected_rev, step)


def save_bubbles(page_id: int, bubbles):
    """bubbles: list of dicts with x,y,w,h,source_text,translated_text,font_size,skip,
    font_category (one of scanlate.FONT_CATEGORIES -- "regular" if unset), plus the
    region fields kind ("bubble" if unset), kind_confidence, confidence,
    language, orientation, panel_id, include_sfx, text_color. List order is reading order (idx).
    Replaces all bubbles for this page -- so a caller rebuilding the list must carry
    every one of these fields through, or they're wiped."""
    conn = db.get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM bubbles WHERE page_id = ?", (page_id,))
        conn.executemany(
            "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, "
            "font_size, skip, font_category, kind, kind_confidence, confidence, language, "
            "orientation, panel_id, include_sfx, text_color) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(page_id, i, b["x"], b["y"], b["w"], b["h"], b.get("source_text", ""),
              b.get("translated_text", ""), b.get("font_size", 18), int(b.get("skip", False)),
              b.get("font_category") or "regular", b.get("kind") or "bubble",
              b.get("kind_confidence"), b.get("confidence"), b.get("language"),
              b.get("orientation"), b.get("panel_id"), int(bool(b.get("include_sfx"))),
              b.get("text_color"))
             for i, b in enumerate(bubbles)]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_bubbles(page_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM bubbles WHERE page_id = ? ORDER BY idx", (page_id,)).fetchall()
    return [dict(r) for r in rows]


def list_bubbles_for_drama(drama_id: int):
    """Every bubble across every saved page of a drama, each carrying its
    page's idx as page_idx -- used by Scanlate's bulk find-and-replace,
    which needs to preview/apply across a whole drama's saved pages at once, not just whichever page is currently
    open in the tab."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT bubbles.*, pages.idx AS page_idx FROM bubbles "
            "JOIN pages ON pages.id = bubbles.page_id "
            "WHERE pages.drama_id = ? ORDER BY pages.idx, bubbles.idx", (drama_id,)).fetchall()
    return [dict(r) for r in rows]


def move_pages(source_id: int, target_id: int, moves) -> bool:
    """Re-points pages from one drama to another in ONE transaction: each of
    `moves` is {page_id, filename (as the caller saw it), idx, new_filename,
    rendered_filename}. Bubbles follow on their own (they hang off the page
    id). False, and nothing written, if any page is no longer on the source
    drama under the filename the caller copied."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        for m in moves:
            cur = conn.execute(
                "UPDATE pages SET drama_id = ?, idx = ?, filename = ?, rendered_filename = ? "
                "WHERE id = ? AND drama_id = ? AND filename IS ?",
                (target_id, m["idx"], m["new_filename"], m["rendered_filename"],
                 m["page_id"], source_id, m["filename"]))
            if cur.rowcount != 1:
                conn.rollback()
                return False
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
