"""
services/name_fix_service.py -- rewrite the ASR's spellings of a glossary name to the
term's own text, using the spellings saved as aliases of that term.

The series glossary is the memory: a creator's titles share one series, so a spelling
saved once is fixed on every later transcription of that creator's titles. Only those
exact spellings are rewritten.

A transcription rewrites lines, so the fixes run on the new lines in the same save path
and never raise into it. No FastAPI import.
"""
import logging
import re
from typing import Dict, List

import db
from services import glossary_service, novel_line_chapters_service

log = logging.getLogger(__name__)

MIN_SPELLING_LEN = 2        # a single character is a word in CJK, not a spelling of a name


def _replacement_map(terms: List[dict]) -> Dict[str, str]:
    """spelling -> text it becomes. Every term maps to itself so a longer term or alias
    that contains a shorter alias wins ("ヤソー" is not turned into "ヤソーー")."""
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
    # A Latin spelling must not match inside a longer word ("Yaso" in "Yasodeen").
    parts = [(r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])") if s.isascii() else re.escape(s)
             for s in ordered]
    return re.compile("|".join(parts))


def _rewrite(drama_id: int, mapping: Dict[str, str]) -> dict:
    changed_lines, replacements, counts = [], 0, {}
    if not any(k != v for k, v in mapping.items()):
        return {"lines_changed": 0, "replacements": 0}
    pattern = _compile(mapping)
    lines = db.load_line_objects(drama_id)
    # A novel's chapters are found by their title line's exact text, so rewriting a
    # name inside a title would drop that chapter from the chapter list.
    title_positions = {pos for _n, _t, pos in novel_line_chapters_service.starts_from_manifest(
        drama_id, [novel_line_chapters_service.norm(ln.zh) for ln in lines])}
    for pos, ln in enumerate(lines):
        text = ln.zh or ""
        if not text or ln.sfx or pos in title_positions:
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
            counts[ln.id] = count
    if changed_lines:
        # Only the source text is written, by id, so edits to other fields survive.
        # Compared with what was read above, so a line edited in Review meanwhile is kept.
        stale = set(db.save_lines(drama_id, changed_lines, fields=("zh",),
                                  only_if_unchanged=True) or ())
        replacements -= sum(counts[ln.id] for ln in changed_lines if ln.id in stale)
        changed_lines = [ln for ln in changed_lines if ln.id not in stale]
    return {"lines_changed": len(changed_lines), "replacements": replacements}


def apply_known_aliases(drama_id: int) -> dict:
    """Rewrite spellings saved as aliases. Offline and free. Nothing goes in the edit
    log: the lines are new transcription output, not corrections."""
    terms = glossary_service.effective_glossary_terms(drama_id)
    return _rewrite(drama_id, _replacement_map(terms))


def after_transcription(drama_id: int) -> dict:
    """The transcription's hook: a failure here must never lose the new lines."""
    try:
        return apply_known_aliases(drama_id)
    except Exception:
        log.exception("Name fixes after transcription failed for drama %s", drama_id)
        return {"lines_changed": 0, "replacements": 0}
