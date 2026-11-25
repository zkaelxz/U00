"""Owns the per-series fix list for comics: hand fixes of a box's English, kept and shown to later pages.

A fix is stored in the series translation memory the subtitle Review already fills
(db.translation_memory), so it needs no upkeep and a series has one list of approved wording.
"""
import logging

import db
import translation_guide
import translation_memory

log = logging.getLogger(__name__)

# A comic page carries many short balloons; more than a handful of fixes still fits the prompt,
# and the most used entries are searched so a long series keeps the matching cheap.
MAX_FIXES_PER_PAGE = 8
_SEARCHED_ENTRIES = 3000


def _series_id(drama) -> int | None:
    return (drama or {}).get("series_id") or None


def record_fix(drama: dict, before_text: str | None, written_text: str, region_now: dict | None) -> None:
    """Keeps a saved edit of a region's English (`before_text` -> `written_text`) as the series'
    fix for the region's source text. Only when the English changed, the region has source text
    and it still holds what this edit wrote: a job rewriting the region at once must not be
    learned as the person's wording."""
    series_id = _series_id(drama)
    if not series_id or region_now is None:
        return
    after = (written_text or "").strip()
    source = (region_now.get("source_text") or "").strip()
    if (not source or not after or after == (before_text or "").strip()
            or (region_now.get("translated_text") or "").strip() != after):
        return
    try:
        db.record_translation_memory(series_id, source, after)
    except Exception as exc:  # noqa: BLE001 - the edit itself is saved; losing the fix is not fatal
        log.warning("Comic fix not kept for series %s: %s", series_id, type(exc).__name__)


def forget_fix(drama: dict, source_text: str, undone_text: str) -> None:
    """The person undid a fix: it stops being offered, unless the series has since approved
    different wording for that text."""
    series_id = _series_id(drama)
    if not series_id:
        return
    try:
        db.forget_translation_memory(series_id, source_text, undone_text)
    except Exception as exc:  # noqa: BLE001 - the undo itself is done
        log.warning("Comic fix not dropped for series %s: %s", series_id, type(exc).__name__)


def fixes_block(drama: dict, regions, fresh_attempt: bool = False) -> str:
    """The series' fixes whose source matches (or nearly matches) a region being translated, as a
    prompt block; "" when there are none. A page run keeps a fixed region's fix even when the
    region already holds it, so translating a page again keeps the person's wording.
    `fresh_attempt` (Redo of one box) leaves out a fix equal to a region's current English: the
    person asked for other words, not the same ones back."""
    series_id = _series_id(drama)
    regions = list(regions or [])
    sources = [r.get("source_text") or "" for r in regions if (r.get("source_text") or "").strip()]
    if not series_id or not sources:
        return ""
    found = translation_memory.example_matches(
        sources, db.list_translation_memory(series_id, limit=_SEARCHED_ENTRIES), (),
        limit=MAX_FIXES_PER_PAGE,
        exclude_translations=[r.get("translated_text") or "" for r in regions] if fresh_attempt else ())
    if not found:
        return ""
    return translation_guide.build_examples_block(found)


def with_fixes(character_notes: str, drama: dict, regions, fresh_attempt: bool = False) -> str:
    """`character_notes` followed by the page's matching fixes. A failure only costs the fixes,
    never the translation."""
    try:
        block = fixes_block(drama, regions, fresh_attempt)
    except Exception as exc:  # noqa: BLE001 - best effort by design
        log.warning("Comic fixes not loaded: %s", type(exc).__name__)
        block = ""
    return "\n\n".join(part for part in (character_notes, block) if part)
