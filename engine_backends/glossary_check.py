"""Checks a translated batch against the series glossary, once, with the same engine.

A prompt can only ask a model to use the glossary; a small local model often
doesn't, and nothing looked at the result. Terms are matched on the source
text exactly (the term or a recorded alias), the required English is looked
for case-insensitively, and only the lines that miss go back for one more
request. What is still wrong afterwards is flagged for Review."""

import re

from glossary_io import split_prompt_terms
from .translation_guard import line_problems, unusable_reply

_LATIN_EDGE = "A-Za-z0-9"


def _form_pattern(form: str):
    head = f"(?<![{_LATIN_EDGE}])" if re.match(f"[{_LATIN_EDGE}]", form[0]) else ""
    tail = f"(?![{_LATIN_EDGE}])" if re.match(f"[{_LATIN_EDGE}]", form[-1]) else ""
    return re.compile(head + re.escape(form) + tail, re.IGNORECASE)


def matching_glossary_terms(zh: str, glossary_terms) -> list:
    """Glossary entries whose source term (or a recorded alias) is in zh, as a
    whole term: the longest form claims its characters first, so 王 does not
    match inside 王明 when 王明 is itself a term, and a Latin-script form
    never matches inside a longer word. This is the "in play for this line"
    heuristic line_tools.explain_translation already used, factored out so the
    "what happened here?" view shows the same real, non-fabricated
    match set instead of re-deriving it differently."""
    def _term_forms(t):
        aliases = [a.strip() for a in re.split(r"[|,，、]", t.get("aliases") or "") if a.strip()]
        return [f for f in [t.get("term_original", ""), *aliases] if f]
    forms = sorted(((f, i) for i, t in enumerate(glossary_terms or []) for f in _term_forms(t)),
                   key=lambda fi: -len(fi[0]))
    left, hit = zh or "", set()
    for form, i in forms:
        pattern = _form_pattern(form)
        if pattern.search(left):
            hit.add(i)
            # NUL cannot be in a term, so the claimed span matches nothing else.
            left = pattern.sub(lambda m: "\0" * len(m.group()), left)
    return [t for i, t in enumerate(glossary_terms or []) if i in hit]


GLOSSARY_FLAG = "glossary_missing"
FLAG_NOTE_PREFIX = "Glossary: "
MAX_TERMS_IN_NOTE = 3


# A source term of one character ("道" in "知道") is too ambiguous to demand
# unless the user marked it exact, and a "contextual" term is meant to be
# rendered differently from line to line.
_CJK_CHAR = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")


def _must_appear(t) -> bool:
    if t.get("enforce_exact"):
        return True
    if t.get("policy") == "contextual":
        return False
    original = (t.get("term_original") or "").strip()
    return not (len(original) == 1 and _CJK_CHAR.match(original))


def required_terms(zh: str, terms) -> list:
    """(source, required English) for each glossary term the source line holds."""
    usable = [t for t in (terms or []) if (t.get("term_translation") or "").strip() and _must_appear(t)]
    return [(t["term_original"], t["term_translation"].strip())
            for t in matching_glossary_terms(zh or "", usable)]


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


def with_overflow(context: dict) -> dict:
    """context with the names of glossary terms left out of the stable prompt
    (see split_prompt_terms) that this batch's source lines hold."""
    _, overflow = split_prompt_terms(context.get("glossary_terms"))
    if not overflow:
        return context
    pairs = [p for zh in context.get("batch_source_lines") or [] for p in required_terms(zh, overflow)]
    return dict(context, glossary_required=[*(context.get("glossary_required") or []), *pairs]) if pairs else context
