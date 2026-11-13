"""The glossary a title keeps of its own: the link to its private series and the terms a run uses once the title joins another series."""

import contextlib

import db


def link_title_glossary(drama_id: int, series_id: int) -> None:
    """Records which series holds a title's own terms. First link wins, so a second
    private series is never made for a title that already has one."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("INSERT OR IGNORE INTO title_glossary_series (drama_id, series_id) VALUES (?, ?)",
                     (drama_id, series_id))
        conn.commit()


def get_title_glossary_series(drama_id: int) -> int | None:
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT series_id FROM title_glossary_series WHERE drama_id = ?",
                           (drama_id,)).fetchone()
    return row["series_id"] if row else None


def title_only_terms(drama: dict) -> list:
    """The title's own terms while the title uses a different series' glossary (or none).
    Each carries origin "title"; the id is negated so it is never taken for a term of the
    series being read, as with shared-group rows."""
    own = db.get_title_glossary_series(drama["id"]) if drama.get("id") else None
    if own is None or own == drama.get("series_id"):
        return []
    return [{**t, "id": -t["id"], "series_id": own, "origin": "title", "origin_name": None}
            for t in db.list_glossary_terms(own)]


def list_run_glossary_terms(drama: dict) -> list:
    """What a run translates with: the series' effective glossary overlaid by the title's own
    terms, the title's winning on the same source text. A series with no title terms gives
    exactly db.list_effective_glossary_terms."""
    series_id = drama.get("series_id")
    base = db.list_effective_glossary_terms(series_id) if series_id else []
    mine = db.title_only_terms(drama)
    if not mine:
        return base
    taken = {db.shared_glossary.term_key(t["term_original"]) for t in mine}
    kept = [t for t in base if db.shared_glossary.term_key(t["term_original"]) not in taken]
    return sorted(kept + mine, key=lambda t: t["term_original"] or "")


def link_existing_private_series(conn) -> None:
    """Links titles that already sit in the private series made for them (named by
    their id), so their terms survive a later move into another series. Idempotent."""
    conn.execute(
        "INSERT OR IGNORE INTO title_glossary_series (drama_id, series_id) "
        "SELECT d.id, s.id FROM dramas d JOIN series s ON s.id = d.series_id "
        "WHERE s.name = 'Title ' || d.id OR s.name LIKE '% (title ' || d.id || ')'")
