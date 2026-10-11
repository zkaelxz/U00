"""
services/review_lines_service.py -- the Review stage's READ-ONLY line views
(migration slice R1a): filtered/paginated line list, transcript search,
find-and-replace PREVIEW, coverage check, pacing check, "What happened
here?" provenance, and original-transcript-text lookup. Reads the
database by permanent `Line.id`, never a browser session list.

Also read-only, added later: the nearest flagged line across pages and
the page it is on (review parity R08).

Nothing here writes to the database or disk. No FastAPI import:
plain dicts in and out. Identity is always `Line.id`; `idx` is returned for
display only and never accepted as an identifier.
"""
import os
import re

try:  # Python 3.11+ moved the regex parser under `re`; `sre_parse` is the deprecated alias
    from re import _constants as _sre
    from re import _parser as _sre_parse
except ImportError:  # pragma: no cover - Python < 3.11
    import sre_constants as _sre
    import sre_parse as _sre_parse

import core as core_module
import db
import debug_view
import raw_transcript
import review_thresholds
import scanlate
import translate_engines
from services.service_errors import InvalidInputError, NotFoundError

MAX_PAGE_SIZE = 200
MAX_TEXT_CHARS = 500
MAX_SEARCH_LIMIT = 200
MAX_REGEX_MATCH_CHARS = 2000   # ReDoS mitigation: only this much of each line is matched
# Each open-ended repeat in sequence multiplies backtracking by the line
# length (a*a*a*! on 300 chars took ~30 s), so their number bounds the cost.
MAX_OPEN_REPEATS = 2
# A repeat whose count can vary by more than this is treated as open-ended;
# small ranges such as \d{1,3} add little backtracking.
_OPEN_REPEAT_SPAN = 10
_REPEAT_OPS = {_sre.MAX_REPEAT, _sre.MIN_REPEAT} | (
    {_sre.POSSESSIVE_REPEAT} if hasattr(_sre, "POSSESSIVE_REPEAT") else set())
_SLOW_PATTERN_MESSAGE = ("This pattern could take too long to run. Avoid repeating a group "
                         "that itself repeats, has alternatives (|) or refers back to a group, "
                         f"and use at most {MAX_OPEN_REPEATS} open-ended repeats (+, *, {{n,}}).")
_ONLY_VALUES = ("all", "flagged", "untranslated")


def _load_drama_and_lines(drama_id: int):
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    lines = core_module.lines_from_rows(db.load_lines(drama_id))
    return drama, lines


def line_dict(ln) -> dict:
    return {
        "id": ln.id, "idx": ln.idx, "start": ln.start, "end": ln.end,
        "zh": ln.zh, "en": ln.en, "speaker": ln.speaker,
        "speaker_manual": bool(ln.speaker_manual), "sfx": bool(ln.sfx),
        "flag": ln.flag, "flag_note": ln.flag_note, "lang": ln.lang,
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
    """One page of lines, optionally only flagged or only untranslated.
    The two counts are
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
    visible = _visible(lines, only)
    start = (page - 1) * page_size
    return {
        "lines": [line_dict(ln) for ln in visible[start:start + page_size]],
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
    return [line_dict(ln) for ln in hits[:limit]]


def _is_slow_pattern(pattern: str) -> bool:
    """True when a regex has a shape Python's backtracking `re` can take
    exponential or high-polynomial time on, judged from the parsed pattern
    rather than its text so that wrapping in extra groups can't hide it."""
    try:
        tree = _sre_parse.parse(pattern)
    except re.error:
        return False  # compiling reports the syntax error to the user
    open_repeats = 0

    def walk(items, in_repeat: bool) -> bool:
        nonlocal open_repeats
        for op, av in items:
            if op in _REPEAT_OPS:
                low, high, body = av
                if in_repeat:
                    return True
                if high - low > _OPEN_REPEAT_SPAN:
                    open_repeats += 1
                if walk(body, high > 1):
                    return True
            elif op is _sre.BRANCH:
                if in_repeat or any(walk(alt, in_repeat) for alt in av[1]):
                    return True
            elif op is _sre.GROUPREF_EXISTS:
                if in_repeat or any(walk(alt, in_repeat) for alt in av[1:] if alt):
                    return True
            elif op is _sre.GROUPREF:
                if in_repeat:
                    return True
            elif op is _sre.SUBPATTERN:
                if walk(av[-1], in_repeat):
                    return True
            elif op in (_sre.ASSERT, _sre.ASSERT_NOT):
                if walk(av[1], in_repeat):
                    return True
            elif op is getattr(_sre, "ATOMIC_GROUP", None):
                if walk(av, in_repeat):
                    return True
        return False

    return walk(tree, False) or open_repeats > MAX_OPEN_REPEATS


def preview_find_replace(drama_id: int, find: str, replace: str,
                         case_sensitive: bool = False, use_regex: bool = False) -> list:
    """Previews a find-and-replace over the translated (`en`) text. Applies
    nothing. Returns [{id, idx, old_text, new_text}] for lines that would
    change.

    ReDoS note: with use_regex a caller supplies a pattern that runs on the
    server. Python's `re` has no timeout, so this is a MITIGATION, not a
    guarantee: patterns are capped at MAX_TEXT_CHARS, patterns whose repeat
    contains another repeat, an alternation or a backreference (`(a+)+`,
    `(a|aa)+`, `(\\w+)\\1+`) or that have more than MAX_OPEN_REPEATS
    open-ended repeats are rejected, and only the first
    MAX_REGEX_MATCH_CHARS characters of each line are matched. The API
    layer should still stay authenticated/local-only."""
    if not find:
        raise InvalidInputError("Enter text to find.")
    replace = replace or ""
    _check_len("Find text", find)
    _check_len("Replacement", replace)
    if use_regex and _is_slow_pattern(find):
        raise InvalidInputError(_SLOW_PATTERN_MESSAGE)
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
    drama, lines = _load_drama_and_lines(drama_id)
    ids = {ln.idx: ln.id for ln in lines}
    report = core_module.diagnose_line_coverage(
        lines, **review_thresholds.thresholds_for(drama).coverage_kwargs())
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
    {"flags": [{id, idx, issue, severity, detail}], "count": n}, worst
    first, at the title's thresholds (review_thresholds)."""
    drama, lines = _load_drama_and_lines(drama_id)
    ids = {ln.idx: ln.id for ln in lines}
    found = translate_engines.smart_segment_lines(
        lines, **review_thresholds.thresholds_for(drama).pacing_kwargs())
    flags = sorted((dict(f, id=ids.get(f["idx"])) for f in found),
                   key=lambda f: -f["severity"])
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


def _visible(lines, only: str) -> list:
    if only == "flagged":
        return [ln for ln in lines if _is_flagged(ln)]
    if only == "untranslated":
        return [ln for ln in lines if _is_untranslated(ln)]
    return lines


def _check_view(page_size, only):
    if only not in _ONLY_VALUES:
        raise InvalidInputError(f"Unknown filter {only!r}; use one of {_ONLY_VALUES}.")
    if (not isinstance(page_size, int) or isinstance(page_size, bool)
            or not 1 <= page_size <= MAX_PAGE_SIZE):
        raise InvalidInputError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")


def _position(lines, ln, page_size: int, only: str) -> dict:
    """Where `ln` sits: its page in the `only` view (None when the filter
    hides it) and its page with no filter."""
    view_ids = [x.id for x in _visible(lines, only)]
    all_ids = [x.id for x in lines]
    page = view_ids.index(ln.id) // page_size + 1 if ln.id in view_ids else None
    return {"line_id": ln.id, "idx": ln.idx, "page": page,
            "page_all": all_ids.index(ln.id) // page_size + 1}


def adjacent_flagged(drama_id: int, forward: bool, from_line_id: int = None,
                     page_size: int = 40, only: str = "all") -> dict:
    """The nearest flagged line after (forward) or before `from_line_id`,
    across every page (review parity R08). With no `from_line_id`: the
    first (forward) or last flagged line. Returns {line_id, idx, page,
    page_all}: `page` in the `only` view (None when the filter hides the
    line), `page_all` with no filter; every field None when there's no
    such line."""
    _check_view(page_size, only)
    _, lines = _load_drama_and_lines(drama_id)
    if from_line_id is None:
        ref = -1 if forward else float("inf")
    else:
        ref = _find_line(lines, from_line_id).idx
    idx = adjacent_flagged_idx(lines, ref, forward)
    if idx is None:
        return {"line_id": None, "idx": None, "page": None, "page_all": None}
    return _position(lines, next(ln for ln in lines if ln.idx == idx), page_size, only)


def adjacent_flagged_idx(all_lines, ref_idx, forward):
    """The nearest flagged line's idx strictly after (forward=True) or
    before (forward=False) ref_idx, or None if there isn't one. The
    next/previous-flagged navigation -- there was previously no way to
    step through flagged lines one at a time, only the "Show flagged
    lines only" filter."""
    if forward:
        return next((ln.idx for ln in all_lines if ln.flag and ln.idx > ref_idx), None)
    return next((ln.idx for ln in reversed(all_lines) if ln.flag and ln.idx < ref_idx), None)
