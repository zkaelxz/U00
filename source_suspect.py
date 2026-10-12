"""Deterministic "source text looks off" checks on transcribed lines.

Bad transcription makes odd translations, so Review flags source lines that
look mis-transcribed or mis-split. Text-only rules on data already stored:
the ASR backends keep no per-line confidence, so none is used. Pure functions;
services/source_suspect_service.py writes the flags.
"""
import re
import unicodedata

SUSPECT_FLAG = "source_suspect"

# Beyond this, a gap means the short line is a separate utterance, not the tail
# of the previous one.
MAX_SPLIT_GAP_S = 1.0
SHORT_NEIGHBOUR_CHARS = 2
MIN_HEAD_CHARS = 3
SAME_CHAR_RUN = 7
PHRASE_REPEATS = 4
# Below this share of punctuated lines the transcript simply has no
# punctuation, so "doesn't end the sentence" says nothing about a split.
MIN_PUNCTUATED_SHARE = 0.15
MIN_LINES_FOR_SPLIT_RULE = 5

# Punctuation a transcriber writes where a phrase really ends or pauses; a line
# ending in one was cut on purpose.
_PHRASE_END = set("。！？…!?.,，、；;：:」』）)\"”’~〜♪-—")
_INTERJECTIONS = {"嗯", "啊", "哦", "喔", "哈", "呃", "唉", "哎", "哇", "喂", "诶", "欸", "咦",
                  "あ", "う", "ん", "え", "お", "は", "うん", "はい", "ええ", "응", "네", "아", "어", "예", "음"}
_MARKS = set("ー〜~…-—.")
_REPEAT_EXEMPT = _MARKS | set(" \t　,，、。!！?？")

# Scripts a line in each source language may not hold. Latin is allowed
# everywhere (names, acronyms); Chinese gets its own share rule below.
_FOREIGN = {"zh": {"HANGUL", "KANA"}, "ja": {"HANGUL"}, "ko": {"KANA"}}
_STRAY_SCRIPTS = {"CYRILLIC", "ARABIC", "HEBREW", "THAI", "GREEK", "DEVANAGARI"}
_LATIN_MIN_LETTERS = 10
_LATIN_MIN_SHARE = 0.5


def _script(ch: str) -> str:
    code = ord(ch)
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF or 0xFF66 <= code <= 0xFF9F:
        return "KANA"
    if 0xAC00 <= code <= 0xD7AF or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F:
        return "HANGUL"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF:
        return "HAN"
    if not ch.isalpha():
        return "OTHER"
    first = unicodedata.name(ch, "").split(" ")[0]
    return "LATIN" if first == "LATIN" else first


def _core(text: str) -> str:
    """The text without spaces and punctuation, for length and run checks."""
    return "".join(c for c in text if c.isalnum())


def repeated_text(text: str):
    """Note for a run of one character or of a short phrase repeated back to
    back (an ASR loop), else None."""
    run, prev = 0, ""
    for ch in text:
        if ch in _REPEAT_EXEMPT:
            run, prev = 0, ""
            continue
        run = run + 1 if ch == prev else 1
        prev = ch
        if run >= SAME_CHAR_RUN:
            return f"“{ch}” repeats {run}+ times in a row"
    for size in (2, 3, 4):
        match = re.search(r"(.{%d})\1{%d,}" % (size, PHRASE_REPEATS - 1), text)
        if match and len(set(match.group(1))) > 1 and not set(match.group(1)) & _REPEAT_EXEMPT:
            return f"“{match.group(1)}” repeats {PHRASE_REPEATS}+ times in a row"
    return None


def script_mix(text: str, language: str):
    """Note when the line holds a script the source language doesn't use, else None."""
    counts = {}
    for ch in text:
        if ch.isalpha():
            script = _script(ch)
            counts[script] = counts.get(script, 0) + 1
    foreign = _FOREIGN.get(language, set()) | _STRAY_SCRIPTS
    odd = sorted(s for s in counts if s in foreign)
    if odd:
        return "has " + "/".join(s.title() for s in odd) + " characters"
    latin, letters = counts.get("LATIN", 0), sum(counts.values())
    if language == "zh" and latin >= _LATIN_MIN_LETTERS and latin / letters >= _LATIN_MIN_SHARE:
        return "is mostly Latin letters"
    return None


def _ends_mid_phrase(text: str) -> bool:
    text = text.rstrip()
    return bool(text) and text[-1] not in _PHRASE_END and text[-1].isalnum()


def _split_note(line, nxt, language: str):
    """Note when `line` stops mid-phrase right before a tiny `nxt` that carries the rest."""
    tail = _core(nxt.zh)
    if not (1 <= len(tail) <= SHORT_NEIGHBOUR_CHARS) or tail in _INTERJECTIONS:
        return None
    if len(_core(line.zh)) < MIN_HEAD_CHARS or not _ends_mid_phrase(line.zh):
        return None
    if nxt.start - line.end > MAX_SPLIT_GAP_S:
        return None
    if line.speaker and nxt.speaker and line.speaker != nxt.speaker:
        return None
    if (nxt.lang or language) != (line.lang or language):
        return None
    return f"ends mid-phrase; the next line is only “{nxt.zh.strip()}”"


def find_suspects(lines, source_language: str) -> dict:
    """{line id: note} for every line whose source text looks off. Lines are
    in timeline order; one without an id is skipped."""
    language = source_language if source_language in _FOREIGN else "zh"
    found = {}
    ended = [bool(ln.zh.strip()) and ln.zh.strip()[-1] in _PHRASE_END for ln in lines]
    split_rule = (len(lines) >= MIN_LINES_FOR_SPLIT_RULE
                  and sum(ended) / len(lines) >= MIN_PUNCTUATED_SHARE)
    for pos, ln in enumerate(lines):
        if ln.id is None or not ln.zh.strip() or ln.sfx:
            continue
        line_language = ln.lang or language
        notes = [repeated_text(ln.zh)]
        if line_language in _FOREIGN:
            notes.append(script_mix(ln.zh, line_language))
        if split_rule and pos + 1 < len(lines):
            notes.append(_split_note(ln, lines[pos + 1], language))
        notes = [n for n in notes if n]
        if notes:
            found[ln.id] = "; ".join(notes)
    return found
