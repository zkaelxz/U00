"""
services/library_service.py -- read-only Library queries, shared by the
Streamlit Library tab and the FastAPI `/api/library` routes.

This is the first piece of the React + FastAPI migration's service layer
(see `docs/migration-react-fastapi.md`). Before it existed, the Library
tab's "All dramas" list did part of its filtering in `db.list_dramas`
(SQL) and the rest inline in the widget code (the Quick-filter pill and
the Custom-tags multiselect), so any second UI would have had to copy
that logic and hope it stayed in sync. Now both UIs call
`list_library_dramas` and get the same answer for the same filters.

Deliberately thin: it composes existing `db.py` functions and keeps
their semantics exactly -- the Quick filter is case-insensitive (via
`db.has_custom_tag`, so a hand-typed "favorite" still counts), the
Custom-tags filter is an exact match on each comma-separated tag, and
every tag picked must be present. No Streamlit import, no HTTP types:
it takes plain values and returns plain dicts, so `cli.py` could call it
too.
"""

import os
import sqlite3

import db
from services.service_errors import ConflictError, InvalidInputError, NotFoundError


def cache_hit_share(usage: dict) -> float:
    """Share of logged input tokens that were prompt-cache reads."""
    total = usage.get("input_tokens") or 0
    return (usage.get("cache_read_tokens") or 0) / total if total else 0.0


def split_custom_tags(drama: dict) -> list:
    """A drama's `custom_tags` column (comma-separated text) as a list of
    trimmed, non-empty tags, in stored order."""
    return [t.strip() for t in (drama.get("custom_tags") or "").split(",") if t.strip()]


def list_library_dramas(search: str = "", studio: str = "", author: str = "",
                        voice_actor: str = "", status: str = "", source_language: str = "",
                        media_type: str = "", quick_filter: str = None, custom_tags=()):
    """Every drama matching all the given filters, newest first -- the
    Library tab's "All dramas" list.

    Empty-string filters mean "any" (same as `db.list_dramas`).
    `quick_filter` must be one of `db.ORGANIZATIONAL_TAGS` or None;
    anything else raises `InvalidInputError` rather than silently
    matching nothing, since those tags are a fixed set the UI offers.
    `custom_tags` is free-form: a drama must carry every one listed."""
    if quick_filter and quick_filter not in db.ORGANIZATIONAL_TAGS:
        raise InvalidInputError(
            f"Unknown quick filter {quick_filter!r}.",
            details={"allowed": list(db.ORGANIZATIONAL_TAGS)})
    dramas = db.list_dramas(search=search, studio=studio, author=author,
                            voice_actor=voice_actor, status=status,
                            source_language=source_language, media_type=media_type)
    if quick_filter:
        dramas = [d for d in dramas if db.has_custom_tag(d, quick_filter)]
    if custom_tags:
        dramas = [d for d in dramas
                  if all(t in [x.strip() for x in (d.get("custom_tags") or "").split(",")]
                         for t in custom_tags)]
    return dramas


def get_library_drama(drama_id: int) -> dict:
    """One drama's full row. Raises `InvalidInputError` for a
    non-positive id and `NotFoundError` if no such drama exists, instead
    of `db.get_drama`'s bare None, so every caller reports the same
    thing the same way."""
    if not isinstance(drama_id, int) or isinstance(drama_id, bool) or drama_id < 1:
        raise InvalidInputError("A drama id is a positive whole number.")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


# ---------------------------------------------------------------------------
# Migration E0: the Library tab's remaining read views plus preset /
# voice-bank rename. Plain dicts; no file paths or clip filenames leave here.
# ---------------------------------------------------------------------------

_MAX_ID = 2**31 - 1


def _check_id(value, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _MAX_ID:
        raise InvalidInputError(f"A {what} id is a positive whole number.")
    return value


def _clean_name(name) -> str:
    name = name.strip() if isinstance(name, str) else ""
    if not name or len(name) > 100:
        raise InvalidInputError("A name is 1-100 characters.")
    return name


def get_library_dashboard() -> dict:
    """The Dashboard's counts and spend (whole library)."""
    stats = db.get_library_stats()
    usage = db.get_usage_summary()
    return {**stats, "usage": {k: usage[k] for k in (
        "input_tokens", "output_tokens", "cache_read_tokens",
        "estimated_cost_usd", "call_count")}}


def list_recently_active(limit: int = 8) -> list:
    limit = max(1, min(int(limit), 50))
    return [{k: d.get(k) for k in ("id", "title_en", "title_zh", "status", "updated_at")}
            for d in db.list_dramas_recently_active(limit)]


def list_cost_by_drama() -> list:
    """Dramas with at least one logged call (free-engine runs included)."""
    return [{k: d.get(k) for k in (
        "id", "title_en", "title_zh", "translation_engine", "input_tokens",
        "output_tokens", "cache_read_tokens", "estimated_cost_usd", "call_count")}
        for d in db.get_usage_by_drama() if d["call_count"] > 0]


def list_series_with_dramas() -> list:
    """Series holding two or more dramas (what the Series expander shows)."""
    out = []
    for s in db.list_series():
        dramas = db.list_dramas_by_series(s["id"])
        if len(dramas) < 2:
            continue
        out.append({
            "id": s["id"], "name": s["name"],
            "character_count": len(db.list_series_characters(s["id"])),
            "glossary_term_count": len(db.list_glossary_terms(s["id"])),
            "dramas": [{k: d.get(k) for k in ("id", "title_en", "title_zh", "status", "media_type")}
                       for d in dramas]})
    return out


def search_lines(query: str, limit: int = 50) -> dict:
    """Global line search across every drama (Chinese or English text)."""
    query = query.strip() if isinstance(query, str) else ""
    if not query or len(query) > 200:
        raise InvalidInputError("A search is 1-200 characters.")
    limit = max(1, min(int(limit), 100))
    rows = db.search_lines_globally(query, limit=limit)
    return {"count": len(rows), "items": [
        {k: r.get(k) for k in ("drama_id", "idx", "zh", "en", "title_en", "title_zh")}
        for r in rows]}


def list_history(limit: int = 25) -> list:
    """Reading history for the default profile (the API has no profile
    selector yet)."""
    limit = max(1, min(int(limit), 100))
    return [{k: h.get(k) for k in ("drama_id", "line_idx", "percent_complete",
                                   "accessed_at", "title_en", "title_zh")}
            for h in db.list_reading_history(limit=limit)]


_PRESET_FIELDS = ("id", "name", "translation_engine", "engine_model", "style_preset",
                  "locale", "default_female_pronouns", "include_genre_notes")


def list_presets() -> list:
    return [{k: p.get(k) for k in _PRESET_FIELDS} for p in db.list_presets()]


def rename_preset(preset_id: int, name: str) -> dict:
    """Changes only the name; a duplicate name is a ConflictError."""
    preset_id = _check_id(preset_id, "preset")
    name = _clean_name(name)
    if not any(p["id"] == preset_id for p in db.list_presets()):
        raise NotFoundError(f"No preset with id {preset_id}.")
    try:
        db.rename_preset(preset_id, name)
    except sqlite3.IntegrityError:
        raise ConflictError(f"A preset named {name!r} already exists.")
    return next(p for p in list_presets() if p["id"] == preset_id)


_VOICE_FIELDS = ("id", "name", "language", "clone_engine", "source_drama", "source_speaker")


def list_voice_bank() -> list:
    """Entries without the clip filename/path; `clip_available` says
    whether the clip file still exists."""
    return [{**{k: e.get(k) for k in _VOICE_FIELDS},
             "clip_available": os.path.exists(os.path.join(db.VOICE_BANK_DIR, e["clip_filename"]))}
            for e in db.list_voice_bank_entries()]


def rename_voice_bank_entry(entry_id: int, name: str) -> dict:
    entry_id = _check_id(entry_id, "voice bank entry")
    name = _clean_name(name)
    if db.get_voice_bank_entry(entry_id) is None:
        raise NotFoundError(f"No voice bank entry with id {entry_id}.")
    db.rename_voice_bank_entry(entry_id, name)
    return next(e for e in list_voice_bank() if e["id"] == entry_id)
