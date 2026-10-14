"""Comic pages and their text regions (speech bubbles, narration, signs,
sound effects) for scanlation and typesetting: page rows, the id-preserving
bubble edits that bump a page's rev, and the legacy full replace. The tables
are created by db.init_db."""

import contextlib

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


def next_page_idx(drama_id: int) -> int:
    """MAX(idx)+1 for a drama's pages (0 for none). Unlike len(list_pages),
    never reuses an idx after a gap. Callers adding pages hold their own
    per-drama lock around this and the inserts."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT MAX(idx) FROM pages WHERE drama_id = ?", (drama_id,)).fetchone()
    return 0 if row is None or row[0] is None else int(row[0]) + 1


# Id-preserving region writes. Unlike save_bubbles (a full delete-and-reinsert
# that gives every bubble a new id), each of these keeps every other bubble's
# id and bumps pages.rev once.
BUBBLE_EDIT_FIELDS = ("x", "y", "w", "h", "source_text", "translated_text", "font_size",
                      "skip", "font_category", "kind", "kind_confidence", "confidence",
                      "language", "orientation", "panel_id", "include_sfx")
_BUBBLE_TEXT_FIELDS = ("source_text", "translated_text", "font_category", "kind",
                       "language", "orientation")
_BUBBLE_BOOL_FIELDS = ("skip", "include_sfx")


def _bubble_row_values(b: dict) -> tuple:
    return (b["x"], b["y"], b["w"], b["h"], b.get("source_text", "") or "",
            b.get("translated_text", "") or "", b.get("font_size", 18),
            int(bool(b.get("skip", False))), b.get("font_category") or "regular",
            b.get("kind") or "bubble", b.get("kind_confidence"), b.get("confidence"),
            b.get("language"), b.get("orientation"), b.get("panel_id"),
            int(bool(b.get("include_sfx"))))


_BUBBLE_INSERT_SQL = (
    "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, "
    "font_size, skip, font_category, kind, kind_confidence, confidence, language, "
    "orientation, panel_id, include_sfx) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


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


def insert_bubble(page_id: int, bubble: dict, position: int = None) -> int:
    """Adds one bubble to a page without touching the others' ids. position
    None appends in reading order; otherwise it is inserted at that list
    position (clamped) and later bubbles' idx shift by one. Returns the new id."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ? ORDER BY idx, id", (page_id,)).fetchall()]
        pos = len(ids) if position is None else max(0, min(int(position), len(ids)))
        cur = conn.execute(_BUBBLE_INSERT_SQL, (page_id, pos) + _bubble_row_values(bubble))
        new_id = cur.lastrowid
        ids.insert(pos, new_id)
        conn.executemany("UPDATE bubbles SET idx = ? WHERE id = ?",
                         [(i, bid) for i, bid in enumerate(ids)])
        _bump_page_rev(conn, page_id)
        conn.commit()
        return new_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_bubble(page_id: int, bubble_id: int) -> bool:
    """Deletes one bubble of that page; the others keep their ids (idx is
    renumbered to stay contiguous). False if it isn't on that page."""
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute("DELETE FROM bubbles WHERE id = ? AND page_id = ?", (bubble_id, page_id))
        if cur.rowcount == 0:
            conn.rollback()
            return False
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ? ORDER BY idx, id", (page_id,)).fetchall()]
        conn.executemany("UPDATE bubbles SET idx = ? WHERE id = ?",
                         [(i, bid) for i, bid in enumerate(ids)])
        _bump_page_rev(conn, page_id)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reorder_bubbles(page_id: int, ordered_ids) -> bool:
    """Sets reading order to `ordered_ids`, which must be exactly the page's
    current bubble ids (no more, no fewer, no repeats); False, and nothing
    written, otherwise."""
    ordered_ids = [int(i) for i in ordered_ids]
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        current = {r[0] for r in conn.execute(
            "SELECT id FROM bubbles WHERE page_id = ?", (page_id,)).fetchall()}
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != current:
            conn.rollback()
            return False
        conn.executemany("UPDATE bubbles SET idx = ? WHERE id = ?",
                         [(i, bid) for i, bid in enumerate(ordered_ids)])
        _bump_page_rev(conn, page_id)
        conn.commit()
        return True
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


def save_bubbles(page_id: int, bubbles):
    """bubbles: list of dicts with x,y,w,h,source_text,translated_text,font_size,skip,
    font_category (one of scanlate.FONT_CATEGORIES -- "regular" if unset), plus the
    region fields kind ("bubble" if unset), kind_confidence, confidence,
    language, orientation, panel_id, include_sfx. List order is reading order (idx).
    Replaces all bubbles for this page -- so a caller rebuilding the list must carry
    every one of these fields through, or they're wiped."""
    conn = db.get_conn()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM bubbles WHERE page_id = ?", (page_id,))
        conn.executemany(
            "INSERT INTO bubbles (page_id, idx, x, y, w, h, source_text, translated_text, "
            "font_size, skip, font_category, kind, kind_confidence, confidence, language, "
            "orientation, panel_id, include_sfx) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(page_id, i, b["x"], b["y"], b["w"], b["h"], b.get("source_text", ""),
              b.get("translated_text", ""), b.get("font_size", 18), int(b.get("skip", False)),
              b.get("font_category") or "regular", b.get("kind") or "bubble",
              b.get("kind_confidence"), b.get("confidence"), b.get("language"),
              b.get("orientation"), b.get("panel_id"), int(bool(b.get("include_sfx"))))
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


def update_bubble_text(bubble_id: int, translated_text: str):
    """Updates just one bubble's translated_text, nothing else -- unlike
    save_bubbles() (which deletes and re-inserts every bubble on a
    page), this is the safe, minimal-field write bulk find-and-replace
    needs: touching only the field the operation is
    actually about, so x/y/w/h/source_text/skip/font_category on every
    other bubble -- and every OTHER bubble on the same page -- are never
    at risk of being silently clobbered."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE bubbles SET translated_text = ? WHERE id = ?",
                     (translated_text, bubble_id))
        conn.commit()


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
