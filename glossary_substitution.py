"""Hard glossary substitution: replacing a term's known variants in an engine's output with the confirmed translation."""

import difflib
import re
from engine_backends.llm_feature_prompts import term_in_text


# Kana, CJK ideographs and Hangul count as letters to Python's \w, but CJK
# text has no spaces between words, so only a Latin-script edge of a variant
# needs a word boundary.
_CJK_CHARS = "\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"
_NON_CJK_WORD = f"[^\\W{_CJK_CHARS}]"
_CJK_RE = re.compile(f"[{_CJK_CHARS}]")


def _variant_pattern(variant: str) -> str:
    head = ""
    if re.match(r"\w", variant[0]) and not _CJK_RE.match(variant[0]):
        head = f"(?<!{_NON_CJK_WORD})"
    tail = ""
    if re.match(r"\w", variant[-1]) and not _CJK_RE.match(variant[-1]):
        tail = f"(?!{_NON_CJK_WORD})"
    return head + re.escape(variant) + tail


def _is_free_text_note(variant: str, canonical: str) -> bool:
    """`notes` doubles as the variant list and a free-text note ("her
    childhood friend"); a spaced phrase longer than the name is a note,
    unless it is just the name spaced differently ("Shen Qing Yi"). Names
    are capitalised, so only a phrase with a lowercase-initial word counts:
    "Su Shan Shan" and "Ah Yao" are real variants."""
    if not re.search(r"\s", variant) or len(variant) <= len(canonical):
        return False
    if not any(w[0].islower() for w in variant.split()):
        return False
    squash = lambda t: re.sub(r"[\s\-]+", "", t).casefold()
    return squash(variant) != squash(canonical)


def _plausible_variant(variant: str, canonical: str, lone: bool) -> bool:
    """Whether a `notes` entry reads as another spelling of the name rather than
    a remark about it. Names are capitalised, so a lowercase single word ("lead")
    is a remark. A lone entry, with no "|" list around it, must also resemble the
    canonical form (share a word or most of its letters): "Protagonist" on a
    name is a remark, "Qingyi" on "Shen Qingyi" is a spelling. An entry in a
    "|" list is taken as a deliberate variant."""
    if variant[0].islower() and not re.search(r"\s", variant):
        return False
    if not lone:
        return True
    squash = lambda t: re.sub(r"[\s\-]+", "", t).casefold()
    v, c = squash(variant), squash(canonical)
    if v in c or c in v:
        return True
    if set(variant.casefold().split()) & set(canonical.casefold().split()):
        return True
    return difflib.SequenceMatcher(a=v, b=c).ratio() >= 0.6


def build_hard_term_substituter(glossary_terms):
    """
    Hard find-replace for terms marked as non-negotiable (LunaTranslator's
    user-dictionary approach). The LLM glossary asks for consistency; this
    guarantees it for terms where drift is unacceptable -- typically
    character names.

    Returns a `(text, source=None) -> text` function that replaces any variant
    the model produced with the canonical form. A term's variants are replaced
    only on a line whose source holds the term (or one of its aliases): a
    variant is often an ordinary word too ("Sister" for 师姐), and on a line
    that never says the term it is that ordinary word. source=None (no line to
    check against) lets every term apply. The matcher for each set of terms in
    play is built once per run instead of once per line. Idempotent, because it
    runs again over lines it already corrected: a variant matches whole words
    only ("Lin" never inside "Berlin"), and a match that lies inside a canonical
    form already in the text is left alone ("Qingyi" inside "Shen Qingyi").
    Longer variants win where two could match at the same place.
    """
    # enforce_exact terms only, with variants from `notes`. A glossary term's
    # banned_translations are separate and flag-only (auto_qc.build_banned_terms
    # flags the line for review); never rewrite text from them here.
    enforced = sorted((t for t in (glossary_terms or [])
                       if t.get("enforce_exact") and t.get("term_translation")),
                      key=lambda x: len(x["term_translation"]), reverse=True)
    variants_of = [_term_variants(t) for t in enforced]
    if not any(len(vs) > 1 for vs in variants_of):
        return lambda text, source=None: text
    matchers = {}

    def matcher(active):
        # Every term's canonical form stays in the pattern, active or not: it is what keeps
        # an active term's variant from matching inside another name ("Qingyi" inside
        # "Shen Qingyi" when only 清仪 is in the source).
        if active not in matchers:
            matchers[active] = _variant_matcher(
                [p for i, vs in enumerate(variants_of) for p in (vs if i in active else vs[:1])])
        return matchers[active]

    def substitute(text: str, source: str | None = None) -> str:
        if not text:
            return text
        active = tuple(i for i, t in enumerate(enforced)
                       if source is None or term_in_text(source, t))
        return matcher(frozenset(active))(text) if active else text

    return substitute


def _term_variants(term: dict) -> list:
    """(variant, canonical) pairs for one enforced term, its canonical form first: as the
    longest match at its position it keeps a shorter variant inside it ("Qingyi" in
    "Shen Qingyi") from compounding, whatever case the model used."""
    canonical = term["term_translation"]
    pairs = [(canonical, canonical)]
    notes = term.get("notes") or ""
    for v in notes.split("|"):
        v = v.strip()
        if (v and v != canonical and not _is_free_text_note(v, canonical)
                and _plausible_variant(v, canonical, lone="|" not in notes)):
            pairs.append((v, canonical))
    return pairs


def _variant_matcher(pairs):
    """`text -> text` replacing each variant in `pairs` with its canonical form. The first
    pair for a casefolded variant wins, so with pairs in longest-canonical-first order a
    variant two terms share goes to the longer name."""
    canonical_of = {}
    for v, canonical in pairs:
        canonical_of.setdefault(v.casefold(), (v, canonical))
    variants = sorted(canonical_of.values(), key=lambda vc: len(vc[0]), reverse=True)
    # One group per alternative: casefolding the match to find its variant
    # breaks where IGNORECASE and casefold disagree (Turkish dotted I).
    pattern = re.compile("|".join(f"({_variant_pattern(v)})" for v, _ in variants),
                         re.IGNORECASE)

    def replace(text: str) -> str:
        out, pos, scan = [], 0, 0
        while True:
            m = pattern.search(text, scan)
            if m is None:
                break
            if m.lastindex is None:
                raise TypeError("a variant match always has a group")
            out += [text[pos:m.start()], variants[m.lastindex - 1][1]]
            pos = scan = m.end()
        out.append(text[pos:])
        return "".join(out)

    return replace


def apply_hard_term_substitutions(text: str, glossary_terms, source: str | None = None) -> str:
    """One-off form of `build_hard_term_substituter` for a single text."""
    return build_hard_term_substituter(glossary_terms)(text, source)
