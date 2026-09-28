"""
services/reader_service.py -- Migration Slice 4: the Reader's page HTML,
shared by the FastAPI `/api/reader` route and (in a later slice) the
Streamlit Reader tab.

Scope decision, confirmed by the user (2026-09-28), the real blocker
that held this slice back: the Streamlit Reader tab's own page-load path
does two things an HTTP GET must never silently inherit -- a **paid LLM
call** (`dictionary.build_word_definitions`, run from a button click) and
a **DB write as a side effect of loading a page** (`db.save_vocab_lookup`
for every word looked up). `get_reader_page_html` below does neither: it
only serves whatever's already in `vocab_lookups` (populated by that
existing Streamlit button, or by a future explicit "look up definitions"
action added to this same service) -- never a live dictionary call, never
a write. A fresh/paid lookup stays a separate, explicit action, not a
side effect of viewing a page.

No Streamlit import, no HTTP types: takes plain values, returns an HTML
string, so `cli.py` or a script could call it too.
"""

import core as core_module
import db
import reader
from services.service_errors import InvalidInputError, NotFoundError

DEFAULT_CHAPTER_SIZE = 40


def _cached_definitions(drama_id: int, source_language: str) -> dict:
    """{word: {"reading": ..., "definitions": [...]}} from whatever's
    already been looked up and saved for this drama -- never a live call.
    Matches `source_language` the same way the Streamlit tab's own lookup
    does (one language per drama), so a stale entry from a drama whose
    source language changed doesn't leak in."""
    out = {}
    for row in db.list_vocab_lookups(drama_id=drama_id):
        if row.get("language") != source_language:
            continue
        out[row["word"]] = {"reading": row.get("reading"), "definitions": row.get("definitions") or []}
    return out


def get_reader_page(drama_id: int, page: int = 1, chapter_size: int = DEFAULT_CHAPTER_SIZE,
                    theme: str = "light", font_size: int = 22, line_height: float = 2.4,
                    max_width: int = 1200, font: str = "system") -> dict:
    """{html, page, page_count, total_lines} for one page of a drama's
    reader view, definitions sourced only from what's already cached
    (see module docstring) -- never a live dictionary lookup. Raises
    InvalidInputError for a bad drama id/page/chapter_size and
    NotFoundError for an unknown drama or one with no lines yet, the
    same vocabulary every other service in this app's migration uses."""
    if not isinstance(drama_id, int) or isinstance(drama_id, bool) or drama_id < 1:
        raise InvalidInputError("A drama id is a positive whole number.")
    if not isinstance(chapter_size, int) or isinstance(chapter_size, bool) or not (10 <= chapter_size <= 200):
        raise InvalidInputError("chapter_size must be a whole number from 10 to 200.")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InvalidInputError("page must be a positive whole number.")

    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")

    rows = db.load_lines(drama_id)
    lines = core_module.lines_from_rows(rows)
    if not lines:
        raise NotFoundError(f"Drama {drama_id} has no lines yet.")

    page_count = max(1, (len(lines) + chapter_size - 1) // chapter_size)
    if page > page_count:
        raise InvalidInputError(f"page {page} is past the last page ({page_count}).",
                                details={"page_count": page_count})

    page_lines = lines[(page - 1) * chapter_size: page * chapter_size]
    source_language = drama.get("source_language") or "zh"
    definitions = _cached_definitions(drama_id, source_language)

    html_str = reader.build_reader_html(
        page_lines, source_language, definitions, audio_data_uri=None, theme=theme,
        font_size=font_size, line_height=line_height, max_width=max_width, font=font)

    return {"html": html_str, "page": page, "page_count": page_count, "total_lines": len(lines)}
