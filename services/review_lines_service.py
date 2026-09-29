"""
services/review_lines_service.py -- the Review stage's READ-ONLY line views
(migration slice R1a): filtered/paginated line list, transcript search,
find-and-replace PREVIEW, coverage check, pacing check, "What happened
here?" provenance, and original-transcript-text lookup. Mirrors the
matching blocks of `with tab_review:` in `tabs/workspace_tab.py`, but reads
the database by permanent `Line.id` instead of the browser session list.

Explicitly OUT OF SCOPE for this slice (each its own later slice): every
write (applying a replace, editing, flagging, restoring original text), the
media player / burned preview / pronunciation, translation-memory
suggestions, LLM tools, bulk modes, and history/versions/notes reads.

Nothing here writes to the database or disk. No Streamlit/FastAPI import:
plain dicts in and out. Identity is always `Line.id`; `idx` is returned for
display only and never accepted as an identifier.
"""
import os
import re

import core as core_module
import db
import debug_view
import raw_transcript
import scanlate
import translate_engines
from services.service_errors import InvalidInputError, NotFoundError

MAX_PAGE_SIZE = 200
MAX_TEXT_CHARS = 500
MAX_SEARCH_LIMIT = 200
MAX_REGEX_MATCH_CHARS = 2000   # ReDoS mitigation: only this much of each line is matched
# a group that contains an unbounded quantifier and is itself repeated
# unboundedly, e.g. (a+)+  (x*)*  (a|b+){2,}  -- the classic catastrophic shape
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*(?:[+*]|\{\d+,\d*\})")
_ONLY_VALUES = ("all", "flagged", "untranslated")


def _load_drama_and_lines(drama_id: int):
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    lines = core_module.lines_from_rows(db.load_lines(drama_id))
    return drama, lines


def _line_dict(ln) -> dict:
    return {
        "id": ln.id, "idx": ln.idx, "start": ln.start, "end": ln.end,
        "zh": ln.zh, "en": ln.en, "speaker": ln.speaker,
        "speaker_manual": bool(ln.speaker_manual), "sfx": bool(ln.sfx),
        "flag": ln.flag, "flag_note": ln.flag_note,
        # bare filename only, never the relative folder layout (D2)
        "dub_filename": os.path.basename(ln.dub_filename) if ln.dub_filename else None,
    }


def _is_flagged(ln) -> bool:
    return bool(ln.flag)


def _is_untranslated(ln) -> bool:
    return bool(ln.zh.strip() and not ln.en.strip())


def _find_line(lines, line_id):
    for ln in lines:
        if ln.id == line_id:
            return ln
    raise NotFoundError(f"No line with id {line_id} in this drama.")


def _check_len(name: str, value: str):
    if len(value) > MAX_TEXT_CHARS:
        raise InvalidInputError(f"{name} is too long (max {MAX_TEXT_CHARS} characters).")


def list_review_lines(drama_id: int, page: int = 1, page_size: int = 40,
                      only: str = "all") -> dict:
    """One page of lines, optionally only flagged or only untranslated
    (same definitions and totals as the Review tab). The two counts are
    always over the whole drama, not the filtered view. An out-of-range
    page returns an empty `lines` list rather than an error."""
    if only not in _ONLY_VALUES:
        raise InvalidInputError(f"Unknown filter {only!r}; use one of {_ONLY_VALUES}.")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise InvalidInputError("page must be an integer >= 1.")
    if (not isinstance(page_size, int) or isinstance(page_size, bool)
            or not 1 <= page_size <= MAX_PAGE_SIZE):
        raise InvalidInputError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")

    _, lines = _load_drama_and_lines(drama_id)
    flagged = sum(1 for ln in lines if _is_flagged(ln))
    untranslated = sum(1 for ln in lines if _is_untranslated(ln))
    if only == "flagged":
        visible = [ln for ln in lines if _is_flagged(ln)]
    elif only == "untranslated":
        visible = [ln for ln in lines if _is_untranslated(ln)]
    else:
        visible = lines
    start = (page - 1) * page_size
    return {
        "lines": [_line_dict(ln) for ln in visible[start:start + page_size]],
        "page": page, "page_size": page_size, "total": len(visible),
        "flagged_count": flagged, "untranslated_count": untranslated,
    }


def search_lines(drama_id: int, term: str, limit: int = 50) -> list:
    """Lines whose source or translated text contains `term`
    (case-insensitive, stripped), in line order, at most `limit`. A blank
    term returns []."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_SEARCH_LIMIT:
        raise InvalidInputError(f"limit must be between 1 and {MAX_SEARCH_LIMIT}.")
    term = (term or "")
    _check_len("Search term", term)
    _, lines = _load_drama_and_lines(drama_id)
    term = term.strip().lower()
    if not term:
        return []
    hits = [ln for ln in lines
            if term in (ln.zh or "").lower() or term in (ln.en or "").lower()]
    return [_line_dict(ln) for ln in hits[:limit]]


def _has_nested_quantifier(pattern: str) -> bool:
    return bool(_NESTED_QUANTIFIER.search(pattern))


def preview_find_replace(drama_id: int, find: str, replace: str,
                         case_sensitive: bool = False, use_regex: bool = False) -> list:
    """Previews a find-and-replace over the translated (`en`) text. Applies
    nothing. Returns [{id, idx, old_text, new_text}] for lines that would
    change.

    ReDoS note: with use_regex a caller supplies a pattern that runs on the
    server. Python's `re` has no timeout, so this is a MITIGATION, not a
    guarantee: patterns are capped at MAX_TEXT_CHARS, patterns with a
    repeated group that itself contains an unbounded quantifier (`(a+)+`)
    are rejected, and only the first MAX_REGEX_MATCH_CHARS characters of
    each line are matched. The API layer should still stay authenticated/
    local-only."""
    if not find:
        raise InvalidInputError("Enter text to find.")
    replace = replace or ""
    _check_len("Find text", find)
    _check_len("Replacement", replace)
    if use_regex and _has_nested_quantifier(find):
        raise InvalidInputError("This pattern has nested repetition and could be too slow; "
                                "simplify it.")
    _, lines = _load_drama_and_lines(drama_id)
    cap = MAX_REGEX_MATCH_CHARS if use_regex else None
    items = [{"id": ln.id, "idx": ln.idx, "en": (ln.en or "")[:cap]} for ln in lines]
    try:
        matches = scanlate.bulk_find_replace_preview(
            items, find, replace, text_field="en",
            case_sensitive=case_sensitive, use_regex=use_regex)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    except (re.error, IndexError) as exc:
        # compiled.sub() raises re.error for a bad group reference in `replace`.
        raise InvalidInputError(f"Invalid replacement: {exc}") from exc
    return [{"id": m["id"], "idx": m["idx"], "old_text": m["old_text"],
             "new_text": m["new_text"]} for m in matches]


def get_coverage_report(drama_id: int) -> dict:
    """core.diagnose_line_coverage's result (long_lines, large_gaps,
    blank_zh, blank_en) with each entry's `id` (or `after_id`/`before_id`
    for gaps) added next to its idx."""
    _, lines = _load_drama_and_lines(drama_id)
    ids = {ln.idx: ln.id for ln in lines}
    report = core_module.diagnose_line_coverage(lines)
    out = {}
    for key, entries in report.items():
        rows = []
        for e in entries:
            e = dict(e)
            if "idx" in e:
                e["id"] = ids.get(e["idx"])
            if "after_idx" in e:
                e["after_id"] = ids.get(e["after_idx"])
                e["before_id"] = ids.get(e["before_idx"])
            rows.append(e)
        out[key] = rows
    return out


def get_pacing_flags(drama_id: int) -> dict:
    """translate_engines.smart_segment_lines' flags as
    {"flags": [{id, idx, issue, detail}], "count": n}."""
    _, lines = _load_drama_and_lines(drama_id)
    ids = {ln.idx: ln.id for ln in lines}
    flags = [dict(f, id=ids.get(f["idx"])) for f in translate_engines.smart_segment_lines(lines)]
    return {"flags": flags, "count": len(flags)}


def get_line_provenance(drama_id: int, line_id: int) -> dict:
    """The read-only "What happened here?" view (debug_view.explain_line);
    never saves a bug bundle."""
    _, lines = _load_drama_and_lines(drama_id)
    ln = _find_line(lines, line_id)
    return debug_view.explain_line(drama_id, ln, lines)


def get_original_text(drama_id: int, line_id: int) -> dict:
    """What the latest raw transcription originally said for this line.
    `original_text` is None if no raw transcript exists or nothing matches.
    No file path is returned."""
    _, lines = _load_drama_and_lines(drama_id)
    ln = _find_line(lines, line_id)
    try:
        raw = raw_transcript.load_latest(os.path.join(db.DRAMAS_DIR, str(drama_id)))
        original = raw_transcript.original_text_for_line(raw, ln) if raw else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # corrupt/short raw transcript: report it as unavailable, never 500
        raw, original = None, None
    return {
        "line_id": ln.id, "idx": ln.idx, "current_zh": ln.zh,
        "has_raw_transcript": raw is not None,
        "original_text": original,
        "differs": original is not None and original != ln.zh,
    }


def adjacent_flagged_idx(all_lines, ref_idx, forward):
    """The nearest flagged line's idx strictly after (forward=True) or
    before (forward=False) ref_idx, or None if there isn't one. Step 20's
    next/previous-flagged navigation -- there was previously no way to
    step through flagged lines one at a time, only the "Show flagged
    lines only" filter."""
    if forward:
        return next((ln.idx for ln in all_lines if ln.flag and ln.idx > ref_idx), None)
    return next((ln.idx for ln in reversed(all_lines) if ln.flag and ln.idx < ref_idx), None)


def unsaved_line_count(drama_id, lines):
    """Step 21: how many of Review & edit's lines differ from what's
    actually in the database -- not from st.session_state.lines, which the
    page's splice-back updates on every rerun whether or not Save was
    clicked. Timing is compared at the 2 decimals the start/end boxes
    show, so a stored 1.2345 doesn't read as an edit of the box's 1.23.
    A line with no id yet, or a saved line missing from `lines`, counts
    as unsaved too."""
    saved = {r["id"]: r for r in db.load_lines(drama_id)}
    n = 0
    seen = set()
    for ln in lines:
        row = saved.get(ln.id)
        if row is None:
            n += 1
            continue
        seen.add(ln.id)
        if (round(ln.start, 2) != round(row["start"], 2)
                or round(ln.end, 2) != round(row["end"], 2)
                or (ln.zh or "") != (row["zh"] or "")
                or (ln.en or "") != (row["en"] or "")
                or (ln.speaker or "") != (row["speaker"] or "")
                or bool(ln.sfx) != bool(row.get("sfx"))):
            n += 1
    return n + len(saved.keys() - seen)
