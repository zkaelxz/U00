"""Checks a translated batch against the series glossary, once, with the same engine.

A prompt can only ask a model to use the glossary; a small local model often
doesn't, and nothing looked at the result. Terms are matched on the source
text exactly (the term or a recorded alias), the required English is looked
for case-insensitively, and only the lines that miss go back for one more
request. What is still wrong afterwards is flagged for Review."""

import re

from .llm_tasks import matching_glossary_terms

GLOSSARY_FLAG = "glossary_missing"
FLAG_NOTE_PREFIX = "Glossary: "
MAX_TERMS_IN_NOTE = 3


def required_terms(zh: str, terms) -> list:
    """(source, required English) for each glossary term the source line holds."""
    return [(t["term_original"], t["term_translation"].strip())
            for t in matching_glossary_terms(zh or "", [t for t in (terms or [])
                                                        if (t.get("term_translation") or "").strip()])]


def missing_terms(zh: str, en: str, terms) -> list:
    """The required terms of this line that its English does not contain."""
    folded = (en or "").casefold()
    return [(src, tr) for src, tr in required_terms(zh, terms) if tr.casefold() not in folded]


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


def enforce(lines, terms, ask, stop) -> int:
    """Re-asks for the lines missing a required term and flags those still wrong.

    ask(lines, pairs) returns one reply per line, in order, or None when the
    request was abandoned or failed; the caller has already put the lines' ids
    in the request, which is what pairs each reply with its line. stop() is
    true once a cap, cancel or failure rule says no more requests. A reply
    that does not contain the term is dropped, so a retry can't make a line
    worse. Returns how many lines were re-asked."""
    misses = {id(ln): missing_terms(ln.zh, ln.en, terms) for ln in lines if (ln.en or "").strip()}
    asked = [ln for ln in lines if misses.get(id(ln))]
    if asked and not stop():
        pairs = [p for ln in asked for p in misses[id(ln)]]
        replies = ask(asked, pairs)
        if replies is not None and len(replies) == len(asked):
            for ln, reply in zip(asked, replies):
                if (reply or "").strip() and not missing_terms(ln.zh, reply, terms):
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
