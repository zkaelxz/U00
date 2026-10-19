"""
translation_memory.py -- reuse a translation the translator
already approved for an exact or near-identical source line.

Distinct from adaptive_style.py, which learns aggregate preferences
(shorter, more literal, preferred terms) and folds a summary into the
prompt. This recognizes that THIS source phrase was translated and
approved before, and offers that specific translation back.

Matches are only ever suggestions: nothing here writes to a line. The
Workspace shows each one next to its line with an Accept button, the same
guardrail as the voice-match Accept/Reject and the glossary
suggestions -- a near-match can be wrong (one changed character can flip
a line's meaning), so a person decides.

Similarity is plain difflib, the same tool benchmark.py and
core.align_transcript_to_timing already use -- no embedding dependency.
"""
import difflib

MIN_SIMILARITY = 0.85
MAX_EXAMPLES = 5
# Close enough to a tested line to be that line with a changed mark or word. Below the match floor an
# example could never be offered for a line under test, so it sits just above it.
LEAK_SIMILARITY = 0.9


def _normalize(text: str) -> str:
    return "".join((text or "").split())


def find_match(source_text: str, entries: list, min_similarity: float = MIN_SIMILARITY):
    """Best stored entry for `source_text`, or None.

    entries: rows from db.list_translation_memory(). An exact match
    (ignoring whitespace) always wins; otherwise the closest entry at or
    above `min_similarity`. Returns {"entry", "similarity", "exact"}."""
    key = _normalize(source_text)
    if not key or not entries:
        return None
    by_key = {}
    for e in entries:
        by_key.setdefault(_normalize(e["source_text"]), e)
    if key in by_key:
        return {"entry": by_key[key], "similarity": 1.0, "exact": True}
    best, best_ratio = None, 0.0
    for k, e in by_key.items():
        sm = difflib.SequenceMatcher(a=key, b=k, autojunk=False)
        floor = max(min_similarity, best_ratio)
        if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
            continue
        ratio = sm.ratio()
        if ratio >= floor and ratio > best_ratio:
            best, best_ratio = e, ratio
    if best is None:
        return None
    return {"entry": best, "similarity": best_ratio, "exact": False}


def _near(a: str, b: str, min_similarity: float) -> bool:
    if not a or not b:
        return False
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return (sm.real_quick_ratio() >= min_similarity and sm.quick_ratio() >= min_similarity
            and sm.ratio() >= min_similarity)


def example_matches(source_texts, entries: list, exclude_sources, limit: int = MAX_EXAMPLES,
                    min_similarity: float = MIN_SIMILARITY, exclude_translations=(),
                    leak_similarity: float = LEAK_SIMILARITY) -> list:
    """The closest stored entries for a set of source lines, best first, one per entry, as few-shot
    examples. An entry is never offered when its source is `exclude_sources`' or as close to one as
    LEAK_SIMILARITY (one changed punctuation mark is still the same line), or when its translation
    is one of `exclude_translations`: a line under test must not be shown its own answer."""
    banned = {_normalize(t) for t in exclude_sources} - {""}
    answers = {_normalize(t) for t in exclude_translations} - {""}
    pool = [e for e in entries
            if _normalize(e["source_text"]) not in banned and _normalize(e["translation"]) not in answers
            and not any(_near(_normalize(e["source_text"]), t, leak_similarity) for t in banned)]
    found = {}
    for text in source_texts:
        match = find_match(text, pool, min_similarity)
        if match and (match["entry"]["id"] not in found or match["similarity"] > found[match["entry"]["id"]][0]):
            found[match["entry"]["id"]] = (match["similarity"], match["entry"])
    ranked = sorted(found.values(), key=lambda pair: -pair[0])
    return [entry for _, entry in ranked[:limit]]


def suggest_for_lines(lines, entries: list, min_similarity: float = MIN_SIMILARITY) -> dict:
    """{line idx: match} for every line with a stored match whose translation
    differs from what the line already has -- a line already using the
    remembered translation needs no suggestion."""
    suggestions = {}
    for ln in lines:
        match = find_match(ln.zh, entries, min_similarity)
        if match and match["entry"]["translation"].strip() != (ln.en or "").strip():
            suggestions[ln.idx] = match
    return suggestions
