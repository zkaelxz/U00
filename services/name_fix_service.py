"""
services/name_fix_service.py -- rewrite the ASR's spellings of a glossary name to the
term's own text, and remember each confirmed spelling as an alias of that term.

The series glossary is the memory: a creator's titles share one series, so a spelling
confirmed once is fixed on every later transcription of that creator's titles. Only exact
spellings the owner already confirmed are rewritten without asking. Sound-alike candidates
(glossary_variants_service) are only suggested; applying one is an explicit request.

A transcription rewrites lines, so the fixes run on the new lines in the same save path
and never raise into it. Title visibility is checked by the route's `{drama_id}` path
guard (api/auth.py). No FastAPI import.
"""
import logging
import re
from typing import Dict, List

import db
from services import glossary_service, glossary_variants_service as gv
from services.glossary_common import MAX_TERM_LEN, _drama
from lib.errors import InvalidInputError, NotFoundError

log = logging.getLogger(__name__)

MIN_SPELLING_LEN = 2        # a single character is a word in CJK, not a spelling of a name
MAX_FIXES_PER_REQUEST = 50
MAX_PREVIEW_TERMS = 40      # each term costs one phonetic scan of the title
MAX_PREVIEW_ROWS = 100


def _replacement_map(terms: List[dict]) -> Dict[str, str]:
    """spelling -> text it becomes. Every term maps to itself so a longer term or alias
    that contains a shorter alias wins ("ヤロー" is not turned into "ヤローー")."""
    mapping = {t["term_original"]: t["term_original"] for t in terms}
    claimed: Dict[str, str] = {}
    for t in terms:
        for alias in t["aliases"]:
            alias = alias.strip()
            if len(alias) < MIN_SPELLING_LEN or alias in mapping:
                continue
            # Two terms claiming one spelling makes it ambiguous: change neither.
            if claimed.setdefault(alias, t["term_original"]) != t["term_original"]:
                claimed[alias] = ""
    mapping.update({a: c for a, c in claimed.items() if c})
    return mapping


def _compile(mapping: Dict[str, str]):
    ordered = sorted(mapping, key=len, reverse=True)
    # A Latin spelling must not match inside a longer word ("Yalo" in "Yalodeen").
    parts = [(r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])") if s.isascii() else re.escape(s)
             for s in ordered]
    return re.compile("|".join(parts))


def _rewrite(drama_id: int, mapping: Dict[str, str]) -> dict:
    changed_lines, replacements = [], 0
    if not any(k != v for k, v in mapping.items()):
        return {"lines_changed": 0, "replacements": 0}
    pattern = _compile(mapping)
    lines = db.load_line_objects(drama_id)
    for ln in lines:
        text = ln.zh or ""
        if not text or ln.sfx:
            continue
        count = 0

        def sub(m):
            nonlocal count
            new = mapping[m.group(0)]
            count += new != m.group(0)
            return new

        new_text = pattern.sub(sub, text)
        if new_text != text:
            ln.zh = new_text
            changed_lines.append(ln)
            replacements += count
    if changed_lines:
        # Only the source text is written, by id, so edits to other fields survive.
        db.save_lines(drama_id, changed_lines, fields=("zh",))
    return {"lines_changed": len(changed_lines), "replacements": replacements}


def apply_known_aliases(drama_id: int) -> dict:
    """Rewrite spellings the owner already confirmed as aliases. Offline and free."""
    terms = glossary_service.list_glossary_terms(drama_id)
    return _rewrite(drama_id, _replacement_map(terms))


def after_transcription(drama_id: int) -> dict:
    """The transcription's hook: a failure here must never lose the new lines."""
    try:
        return apply_known_aliases(drama_id)
    except Exception:
        log.exception("Name fixes after transcription failed for drama %s", drama_id)
        return {"lines_changed": 0, "replacements": 0}


def preview(drama_id: int) -> dict:
    """Sound-alike spellings of each glossary term that are not yet aliases. Read-only."""
    _drama(drama_id)
    terms = glossary_service.list_glossary_terms(drama_id)
    rows, available, reason = [], True, ""
    for t in terms[:MAX_PREVIEW_TERMS]:
        out = gv.find_variants(drama_id, t["id"])
        if not out["available"]:
            available, reason = False, out["reason"]
            break
        for c in out["candidates"]:
            if c["is_alias"] or c["is_term"] or len(c["text"]) < MIN_SPELLING_LEN:
                continue
            rows.append({"term_id": t["id"], "term": t["term_original"], "spelling": c["text"],
                         "count": c["count"], "score": c["score"], "examples": c["examples"]})
    rows.sort(key=lambda r: (-r["score"], -r["count"], r["spelling"]))
    return {"available": available, "reason": reason, "truncated": len(terms) > MAX_PREVIEW_TERMS,
            "fixes": rows[:MAX_PREVIEW_ROWS]}


def apply_fixes(drama_id: int, fixes) -> dict:
    """Confirm spellings: save each as an alias of its term, then rewrite the title's
    lines. Nothing is written when any entry is invalid."""
    _drama(drama_id)
    if not isinstance(fixes, list) or not fixes:
        raise InvalidInputError("fixes must be a non-empty list.")
    if len(fixes) > MAX_FIXES_PER_REQUEST:
        raise InvalidInputError(f"At most {MAX_FIXES_PER_REQUEST} fixes at a time.")
    terms = {t["id"]: t for t in glossary_service.list_glossary_terms(drama_id)}
    all_originals = {t["term_original"] for t in terms.values()}
    additions: Dict[int, List[str]] = {}
    for f in fixes:
        if not isinstance(f, dict) or isinstance(f.get("term_id"), bool) or not isinstance(f.get("term_id"), int):
            raise InvalidInputError("Each fix needs a term_id and a spelling.")
        term = terms.get(f["term_id"])
        if term is None:
            raise NotFoundError("Glossary term not found.")
        spelling = f.get("spelling")
        if not isinstance(spelling, str) or not (MIN_SPELLING_LEN <= len(spelling.strip()) <= MAX_TERM_LEN):
            raise InvalidInputError("A spelling must be 2 to %d characters." % MAX_TERM_LEN)
        spelling = spelling.strip()
        if spelling in all_originals:
            raise InvalidInputError("That spelling is another glossary term.")
        if spelling not in term["aliases"] and spelling not in additions.setdefault(term["id"], []):
            additions[term["id"]].append(spelling)
    for term_id, new in additions.items():
        if new:
            glossary_service.upsert_glossary_term(
                drama_id, {"id": term_id, "aliases": terms[term_id]["aliases"] + new})
    result = apply_known_aliases(drama_id)
    return {**result, "aliases_added": sum(len(v) for v in additions.values())}
