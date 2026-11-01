"""Checks a translated batch against the series glossary, once, with the same engine.

A prompt can only ask a model to use the glossary; a small local model often
doesn't, and nothing looked at the result. Terms are matched on the source
text exactly (the term or a recorded alias), the required English is looked
for case-insensitively, and only the lines that miss go back for one more
request. What is still wrong afterwards is flagged for Review."""

import re

from .llm_feature_prompts import glossary_term_forms
from .llm_tasks import matching_glossary_terms
from .translation_guard import line_problems, unusable_reply

NAME_CATEGORIES = ("person_name", "courtesy_name", "clan_sect")
GLOSSARY_FLAG = "glossary_missing"
FLAG_NOTE_PREFIX = "Glossary: "
MAX_TERMS_IN_NOTE = 3


def _has_required_word(en: str, translation: str) -> bool:
    """Whether `en` holds the required English. A name made of letters must stand
    alone ("Li" is not in "Lily"); anything else is looked for as written."""
    folded, wanted = en.casefold(), translation.casefold()
    if not (wanted[0].isascii() and wanted[0].isalnum()
            and wanted[-1].isascii() and wanted[-1].isalnum()):
        return wanted in folded
    return re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", folded) is not None


def _is_ambiguous_source(term: dict) -> bool:
    """A term whose English the model is meant to choose per occurrence, or a
    single character that is not a name (道 or 王 sit inside many other words),
    is not demanded of a line unless the term is marked enforce_exact."""
    if term.get("enforce_exact"):
        return False
    if term.get("policy") == "contextual":
        return True
    single = len((term.get("term_original") or "").strip()) < 2
    return single and term.get("category") not in NAME_CATEGORIES


def required_terms(zh: str, terms) -> list:
    """(source, required English) for each glossary term the source line holds.
    A term whose every occurrence lies inside a longer matched term's (王 inside
    王明) is not demanded."""
    zh = zh or ""
    matched = matching_glossary_terms(zh, [t for t in (terms or [])
                                           if (t.get("term_translation") or "").strip()
                                           and not _is_ambiguous_source(t)])
    spans_of = {id(t): [(m.start(), m.end()) for f in glossary_term_forms(t) if f
                        for m in re.finditer(re.escape(f), zh)] for t in matched}
    longest_first = sorted(matched, key=lambda t: -max(len(f) for f in glossary_term_forms(t)))
    covered, kept = [], set()
    for t in longest_first:
        spans = spans_of[id(t)]
        if not all(any(a <= s0 and e0 <= b for a, b in covered) for s0, e0 in spans):
            kept.add(id(t))
        covered += spans
    return [(t["term_original"], t["term_translation"].strip()) for t in matched if id(t) in kept]


def missing_terms(zh: str, en: str, terms) -> list:
    """The required terms of this line that its English does not contain."""
    return [(src, tr) for src, tr in required_terms(zh, terms) if not _has_required_word(en or "", tr)]


def flag_note(missing) -> str:
    shown = [f"{src} -> {tr}" for src, tr in missing[:MAX_TERMS_IN_NOTE]]
    more = len(missing) - len(shown)
    return f"{FLAG_NOTE_PREFIX}{'; '.join(shown)} missing" + (f" (+{more} more)" if more > 0 else "")


def required_note(context: dict) -> str:
    """The prompt block that lists the names this batch must use. Opens the
    user message, next to the lines, because a small model drops what sits
    far up in a long system prompt. glossary_correction marks the second try."""
    pairs = context.get("glossary_required") or []
    if not pairs:
        return ""
    rows = "\n".join(f"- {src} -> {tr}" for src, tr in dict.fromkeys(pairs))
    if context.get("glossary_correction"):
        head = ("GLOSSARY CORRECTION: your earlier translation of the lines below did not use "
                "these exact English names. Redo them so each of these appears exactly as "
                "written:\n")
    else:
        head = "REQUIRED NAMES: use these exact English names wherever the source has them:\n"
    return head + rows + "\n\n"


def tag_batch(context: dict, lines, terms) -> dict:
    """context with the batch's required names set, for a local model's first try."""
    pairs = [p for ln in lines for p in required_terms(ln.zh, terms)]
    return dict(context, glossary_required=pairs) if pairs else context


_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _only_names(zh: str, reply: str, terms) -> bool:
    """True for a reply that is just the required names while the source says
    more than them: a small model answering "Shirakami Fubuki" for a whole line."""
    rest_zh = zh
    rest_en = reply.casefold()
    for src, tr in required_terms(zh, terms):
        rest_zh = rest_zh.replace(src, "")
        rest_en = rest_en.replace(tr.casefold(), "")
    return (len(_NON_WORD.sub("", rest_zh)) >= 2) and not _NON_WORD.sub("", rest_en)


def _acceptable(ln, reply, terms, allowed_runs) -> bool:
    """A re-ask reply replaces the first try only if it has the names, is more
    than the names, and trips no more of the merged/shifted/length checks than
    the first try did."""
    if not (reply or "").strip() or missing_terms(ln.zh, reply, terms):
        return False
    if _only_names(ln.zh, reply, terms) or unusable_reply(ln.zh, reply, allowed_runs):
        return False
    return len(line_problems(ln.zh, reply, allowed_runs)) <= len(line_problems(ln.zh, ln.en, allowed_runs))


def enforce(lines, terms, ask, stop, allowed_runs=()) -> int:
    """Re-asks for the lines missing a required term and flags those still wrong.

    ask(lines, pairs) returns one reply per line, in order, or None when the
    request was abandoned or failed; the caller has already put the lines' ids
    in the request, which is what pairs each reply with its line. stop() is
    true once a cap, cancel or failure rule says no more requests. A reply
    that fails _acceptable is dropped, so a retry can't make a line worse. Returns how many lines were re-asked."""
    misses = {id(ln): missing_terms(ln.zh, ln.en, terms) for ln in lines if (ln.en or "").strip()}
    asked = [ln for ln in lines if misses.get(id(ln))]
    if asked and not stop():
        pairs = [p for ln in asked for p in misses[id(ln)]]
        replies = ask(asked, pairs)
        if replies is not None and len(replies) == len(asked):
            for ln, reply in zip(asked, replies):
                if _acceptable(ln, reply, terms, allowed_runs):
                    ln.en = reply
                    misses[id(ln)] = []
    for ln in lines:
        left = misses.get(id(ln))
        if left:
            if not ln.flag or ln.flag == GLOSSARY_FLAG:
                ln.flag, ln.flag_note = GLOSSARY_FLAG, flag_note(left)
        elif ln.flag == GLOSSARY_FLAG:
            ln.flag, ln.flag_note = None, ""
    return len(asked)
