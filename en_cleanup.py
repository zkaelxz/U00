"""
en_cleanup.py -- deterministic, no-AI tidy-up of English subtitle text.

Pure functions only (no db, no I/O) so the Review preview/apply and the CLI
share one definition of each rule. Every rule is conservative: when a fix
is ambiguous ("i" as a list marker, an odd number of straight quotes, "that
that") the text is left alone, because a wrong auto-fix is worse than a
missed one.
"""
import hashlib
import re
from typing import Iterable, Optional

# Order matters: CJK leakage runs first so its output (",", "...") is
# normalised by the later rules, and capitalisation runs after spacing so it
# sees the final sentence boundaries.
RULES = ("cjk_punctuation", "ellipsis", "extra_space", "repeated_punctuation",
         "space_before_punctuation", "space_inside_brackets", "space_after_punctuation",
         "quotes", "doubled_word", "pronoun_i", "capitalize_sentence")

RULE_LABELS = {
    "cjk_punctuation": "Full-width / CJK punctuation",
    "ellipsis": "Ellipsis style",
    "extra_space": "Double or stray spaces",
    "repeated_punctuation": "Repeated punctuation",
    "space_before_punctuation": "Space before punctuation",
    "space_inside_brackets": "Space inside quotes and brackets",
    "space_after_punctuation": "Missing space after punctuation",
    "quotes": "Straight vs curly quotes",
    "doubled_word": "Doubled words",
    "pronoun_i": "Lowercase \"i\"",
    "capitalize_sentence": "Lowercase after a sentence end",
}

# A private-use character stands in for text a rule must never see.
_SENTINEL_BASE = 0xE000
_SENTINEL_END = 0xF8FF
_SENTINEL_RE = re.compile("[-]")

_CJK_LETTER_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_TAG_RE = re.compile(r"<[^<>\n]{1,80}>|\{\\[^{}\n]{0,80}\}")
# The local part is bounded: an unbounded one is quadratic on "a.a.a.a...".
_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w.+-]{1,64}@[\w-]+\.[\w.-]+\b", re.I)
# "JOHN:" / "- Mary (O.S.):" at the start of a (physical) line.
_LABEL_RE = re.compile(r"^([ \t]*(?:[-–—][ \t]*)?[^\W\d_][\w .'()\-]{0,24}?:)(?=[ \t])", re.M)

_FULLWIDTH_PUNCT = {
    "，": ",", "、": ",", "。": ".", "！": "!", "？": "?", "：": ":", "；": ";",
    "（": "(", "）": ")", "「": '"', "」": '"', "『": '"', "』": '"', "【": "[", "】": "]",
    "⋯": "…", "　": " ", "——": "—",
}
_FULLWIDTH_ASCII_RE = re.compile("[！-～]")

_ELLIPSIS_RE = re.compile(r"(?:\.{3,}|…+)")

_SENTENCE_END_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "st", "jr", "sr", "vs", "etc", "no", "prof", "inc", "ltd", "co",
    "approx", "fig", "mt", "gen", "col", "capt", "sgt", "lt", "e.g", "i.e", "a.m", "p.m",
}

# Words whose immediate repetition is practically always a machine-translation
# slip. Content words ("had had", "that that", "very very") can be real.
_DOUBLEABLE_FUNCTION_WORDS = frozenset(
    "the a an of to in on at for with from by and or but as if my your his her our their its "
    "was were".split())

# Imported text can be far longer than the API's 2000-char cap; the regexes
# below are only cheap on subtitle-sized input.
MAX_LINE_CHARS = 4000

_OPEN_QUOTES = '“‘'
_CLOSE_QUOTES = '”’'


def has_cjk_letters(text: str) -> bool:
    """True when `text` holds ideographs, kana or hangul -- not English, so
    never cleaned. Full-width punctuation alone does not count: removing
    that is the point of the cjk_punctuation rule."""
    return bool(_CJK_LETTER_RE.search(text or ""))


def too_long(text: str) -> bool:
    return len(text or "") > MAX_LINE_CHARS


def detect_style(texts: Iterable[str]) -> dict:
    """The title's existing majority: {"quotes": "curly"|"straight",
    "ellipsis": "dots"|"char"}. A tie, or a title with neither, is
    straight/dots -- the plainest choice, and a no-op when there is nothing
    to convert."""
    curly = straight = char = dots = 0
    for t in texts:
        t = t or ""
        curly += sum(t.count(c) for c in "“”‘’")
        straight += t.count('"') + len(re.findall(r"(?<=[A-Za-z])'(?=[A-Za-z])", t))
        char += t.count("…")
        dots += len(re.findall(r"\.{3,}", t))
    return {"quotes": "curly" if curly > straight else "straight",
            "ellipsis": "char" if char > dots else "dots"}


def compile_terms(terms: Iterable[str]) -> Optional["re.Pattern"]:
    """One alternation for all glossary terms, longest first so a term inside
    a longer term doesn't split it. Build once per plan, not per line."""
    unique = sorted({t.strip() for t in terms if t and t.strip()}, key=len, reverse=True)
    if not unique:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(t) for t in unique) + r")(?![\w])", re.I)


def _protect(text: str, protected_terms, speaker: Optional[str]):
    """(masked text, spans) with markup, URLs, speaker labels and glossary
    terms swapped for sentinels; None if the text already holds private-use
    characters (a sentinel could not be told from the original)."""
    if _SENTINEL_RE.search(text):
        return None
    spans = []

    def hide(m):
        # Expanded now so a span that swallowed an earlier sentinel (a URL
        # running into a masked tag) never nests: _unprotect is one pass.
        spans.append(_unprotect(m.group(0), spans))
        return chr(_SENTINEL_BASE + len(spans) - 1)

    text = _TAG_RE.sub(hide, text)
    text = _URL_RE.sub(hide, text)
    text = _LABEL_RE.sub(hide, text)
    if speaker:
        text = re.sub(rf"^[ \t]*{re.escape(speaker)}[ \t]*:", hide, text, flags=re.M)
    terms_re = protected_terms if isinstance(protected_terms, re.Pattern) else compile_terms(protected_terms)
    if terms_re:
        text = terms_re.sub(hide, text)
    if len(spans) > _SENTINEL_END - _SENTINEL_BASE:
        return None
    return text, spans


def _unprotect(text: str, spans) -> str:
    # An index past the spans is a sentinel no rule should have produced; it is
    # left in place for clean_text's leak check to reject.
    return _SENTINEL_RE.sub(
        lambda m: spans[i] if (i := ord(m.group(0)) - _SENTINEL_BASE) < len(spans) else m.group(0), text)


def _cjk_punctuation(text, style):
    for src in ("——",):
        text = text.replace(src, _FULLWIDTH_PUNCT[src])
    text = "".join(_FULLWIDTH_PUNCT.get(c, c) for c in text)
    # Full-width Latin letters, digits and symbols (Ａ, １, ％) -> ASCII.
    return _FULLWIDTH_ASCII_RE.sub(lambda m: chr(ord(m.group(0)) - 0xFEE0), text)


def _ellipsis(text, style):
    target = "…" if style["ellipsis"] == "char" else "..."
    return _ELLIPSIS_RE.sub(target, text)


def _extra_space(text, style):
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip(" \t")


def _repeated_punctuation(text, style):
    # "!!" and "?!" are emphasis; a doubled comma, semicolon or colon is
    # never intended.
    return re.sub(r"([,;:])\1+", r"\1", text)


def _space_before_punctuation(text, style):
    # A period only when no word follows, so "wait .com" style fragments and
    # decimals are untouched; a colon only when no digit follows (times).
    text = re.sub(r"[ \t]+([,;!?])", r"\1", text)
    text = re.sub(r"[ \t]+(:)(?!\d)", r"\1", text)
    return re.sub(r"(?<=[\w”\"')\]])[ \t]+(\.)(?![\w.])", r"\1", text)


def _space_inside_brackets(text, style):
    text = re.sub(r"([(\[{])[ \t]+", r"\1", text)
    text = re.sub(r"[ \t]+([)\]}])", r"\1", text)
    text = re.sub(r"([“‘])[ \t]+", r"\1", text)
    text = re.sub(r"[ \t]+([”’])", r"\1", text)
    if text.count('"') % 2 == 0:
        # Straight quotes carry no direction: pair them in reading order, so
        # the odd-numbered segments are the quoted text.
        parts = text.split('"')
        parts[1::2] = [p.strip(" \t") for p in parts[1::2]]
        text = '"'.join(parts)
    return text


def _space_after_punctuation(text, style):
    text = re.sub(r"(?<=\D),(?=[A-Za-z])", ", ", text)
    text = re.sub(r";(?=[A-Za-z])", "; ", text)
    text = re.sub(r"([!?])(?=[A-Za-z])", r"\1 ", text)
    # A period only between a real word and a capitalised word, so
    # "U.S.A", "e.g." and "Mr.Smith" are left alone.
    return re.sub(r"(?<=[a-z]{3})\.(?=[A-Z][a-z])", ". ", text)


_QUOTE_OPENER_PRECEDERS = frozenset(" \t\n\r\f\v([{-–—…")


def _to_curly(text):
    parts = text.split('"')
    if len(parts) > 1 and len(parts) % 2 == 1:
        # An opener must start the text or follow a space, bracket or dash;
        # anything else means the reading-order pairing is a guess, so the
        # double quotes are left straight.
        out, ok = parts[0], True
        for i, part in enumerate(parts[1:]):
            if i % 2 == 0 and out and out[-1] not in _QUOTE_OPENER_PRECEDERS:
                ok = False
                break
            out += ("“" if i % 2 == 0 else "”") + part
        if ok:
            text = out
    return re.sub(r"(?<=[A-Za-z])'(?=[A-Za-z])", "’", text)


def _quotes(text, style):
    if style["quotes"] == "curly":
        return _to_curly(text)
    return text.translate({ord("“"): '"', ord("”"): '"', ord("‘"): "'", ord("’"): "'"})


def _doubled_word(text, style):
    def fix(m):
        a, b = m.group(1), m.group(2)
        if a.lower() != b.lower() or a.lower() not in _DOUBLEABLE_FUNCTION_WORDS:
            return m.group(0)
        if a == b or (a[0].isupper() and b == b.lower()):
            return a
        return m.group(0)
    return re.sub(r"(?<![\w'’-])([A-Za-z]+)[ ]+([A-Za-z]+)(?![\w'’-])", fix, text)


_PRONOUN_I_RE = re.compile(
    r"(?<![\w.'’\-/(\[])i(?=(?:['’](?:m|ll|ve|d))?(?![\w'’\-/)\]]|\.\w))")


def _pronoun_i(text, style):
    return _PRONOUN_I_RE.sub("I", text)


_SENTENCE_START_RE = re.compile(r"(?<=\w)([.!?][\"”’)\]]*)([ \n]+)([a-z]\w*)")


_PREV_WORD_RE = re.compile(r"([A-Za-z.]+)\Z")


def _capitalize_sentence(text, style):
    def fix(m):
        end, gap, word = m.group(1), m.group(2), m.group(3)
        # Abbreviations are short; a bounded tail keeps this linear per match.
        prev = _PREV_WORD_RE.search(text[max(0, m.start(1) - 40):m.start(1)])
        prev_word = prev.group(1).lower().rstrip(".") if prev else ""
        # Abbreviations, initials ("J. smith"), and brand-style words
        # ("iPhone") are not sentence starts.
        if end[0] == "." and (prev_word in _SENTENCE_END_ABBREVIATIONS or len(prev_word) < 2
                              or "." in prev_word):
            return m.group(0)
        if re.search(r"[A-Z]", word[1:]) or any(ch.isdigit() for ch in word):
            return m.group(0)
        return end + gap + word[0].upper() + word[1:]
    return _SENTENCE_START_RE.sub(fix, text)


_RULE_FUNCS = {
    "cjk_punctuation": _cjk_punctuation, "ellipsis": _ellipsis, "extra_space": _extra_space,
    "repeated_punctuation": _repeated_punctuation,
    "space_before_punctuation": _space_before_punctuation,
    "space_inside_brackets": _space_inside_brackets,
    "space_after_punctuation": _space_after_punctuation, "quotes": _quotes,
    "doubled_word": _doubled_word, "pronoun_i": _pronoun_i,
    "capitalize_sentence": _capitalize_sentence,
}


def clean_text(text: str, style: Optional[dict] = None, protected_terms: Iterable[str] = (),
               speaker: Optional[str] = None, rules: Iterable[str] = RULES) -> tuple:
    """(cleaned text, rules that changed it). Text with CJK letters, empty
    text, over-long text, or text holding private-use characters comes back
    unchanged. `protected_terms` may be a `compile_terms` result."""
    if not text or too_long(text) or has_cjk_letters(text):
        return text, []
    style = style or {"quotes": "straight", "ellipsis": "dots"}
    protected = _protect(text, protected_terms, speaker)
    if protected is None:
        return text, []
    masked, spans = protected
    wanted = set(rules)
    applied = []
    for name in RULES:
        if name not in wanted:
            continue
        new = _RULE_FUNCS[name](masked, style)
        if new != masked:
            applied.append(name)
            masked = new
    result = _unprotect(masked, spans)
    # Safety net: a leaked sentinel would save invisible junk, so drop the line.
    if _SENTINEL_RE.search(result):
        return text, []
    return (result, applied) if result != text else (text, [])


def plan_hash(changes: Iterable[tuple]) -> str:
    """Fingerprint of a cleanup plan, [(line id, new text), ...], so an
    apply can refuse when the lines moved on since the preview."""
    h = hashlib.sha256()
    for line_id, new_text in changes:
        h.update(f"{line_id}\x00{new_text}\x01".encode("utf-8"))
    return h.hexdigest()
