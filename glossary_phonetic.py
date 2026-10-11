"""
glossary_phonetic.py -- sound-alike keys for names, so the glossary can find the
different ways ASR wrote one person ("Yalo" / "Yaloch" / ヤロー / ヤロック).

Pure and offline. Every script is reduced to one small roman alphabet
(k t p s c h n m l y w + a e i o u) so that kana, pinyin, Hangul and Latin spellings
can be compared with one edit distance. The keys are deliberately lossy: voiced and
unvoiced pairs, long vowels and doubled letters are folded, because those are exactly
the differences between two ASR spellings of one sound. They are NOT a pronunciation
guide, and two names with the same key are only candidates for a human to confirm.

Japanese kanji need pykakasi and Chinese hanzi need pypinyin (both optional, see
diagnostics.OPTIONAL_DEPENDENCIES); kana, Hangul and Latin need nothing. Traditional
and Simplified Chinese need no OpenCC step: pypinyin reads both.

All the numbers live in the constants block below.
"""
import re
import unicodedata
from functools import lru_cache
from typing import List, Optional, Tuple

# --- Tunables (the one place for the numbers) -------------------------------
# Largest normalised edit distance (edits / longer key) still counted as the same
# sound. 0.2 lets one slip in a 5+ letter key through but not in a 4-letter one,
# because Mika/Miki and Yuki/Yuka are different people and short names have no slack.
SIMILARITY_THRESHOLD = {"ja": 0.2, "zh": 0.2, "ko": 0.2, "latin": 0.2}
# A key shorter than this only matches an identical key: one slip in two or three
# letters is a different name, not a spelling variant.
MIN_FUZZY_KEY_LEN = 3
# Extra or missing letters at the END of a name cost this much instead of 1: ASR
# often adds or drops a trailing sound (ヤロ / ヤロック, Yalo / Yaloch).
TAIL_EDIT_COST = 0.5
# No name sounds longer than this; longer keys (a pasted sentence, a 200-letter alias) only
# match an identical key, which also bounds the cost of one comparison.
MAX_KEY_LEN = 40

_INSTALL_HINTS = {
    "ja": ("pykakasi", "Japanese kanji readings need the pykakasi package (pip install pykakasi)."),
    "zh": ("pypinyin", "Chinese pinyin needs the pypinyin package (pip install pypinyin)."),
}


class PhoneticUnavailable(RuntimeError):
    """A reading library this text needs is not installed; the message says which."""

    def __init__(self, language: str):
        self.package, self.hint = _INSTALL_HINTS[language]
        super().__init__(self.hint)


def availability(language: str) -> dict:
    """Whether readings for `language` can be computed here, and the install hint when not."""
    lang = _lang(language)
    if lang in _INSTALL_HINTS:
        package, hint = _INSTALL_HINTS[lang]
        try:
            __import__(package)
        except ImportError:
            return {"available": False, "package": package, "hint": hint}
    return {"available": True, "package": None, "hint": ""}


def _lang(language: Optional[str]) -> str:
    return (language or "").strip().lower()[:2]


# --- Japanese -----------------------------------------------------------------

_JA_ROWS = [
    ("あいうえお", "a i u e o"),
    ("かきくけこ", "ka ki ku ke ko"), ("がぎぐげご", "ka ki ku ke ko"),
    ("さしすせそ", "sa si su se so"), ("ざじずぜぞ", "sa si su se so"),
    ("たちつてと", "ta ci tu te to"), ("だぢづでど", "ta ci tu te to"),
    ("なにぬねの", "na ni nu ne no"),
    ("はひふへほ", "ha hi hu he ho"), ("ばびぶべぼ", "pa pi pu pe po"),
    ("ぱぴぷぺぽ", "pa pi pu pe po"),
    ("まみむめも", "ma mi mu me mo"),
    ("やゆよ", "ya yu yo"), ("らりるれろ", "la li lu le lo"),
    ("わを", "wa o"), ("ん", "n"), ("ゔ", "pu"),
    ("ぁぃぅぇぉ", "a i u e o"), ("ゃゅょゎ", "ya yu yo wa"), ("ゕゖ", "ka ke"),
]
_JA_ROMAN = {k: v for kana, roman in _JA_ROWS for k, v in zip(kana, roman.split())}
_JA_SMALL_VOWEL = dict(zip("ぁぃぅぇぉ", "aiueo"))
_JA_SMALL_Y = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
# After these, ゃゅょ adds no "y" (しゃ is sha, not shya), matching how Latin "sh"/"ch" fold.
_JA_NO_Y_AFTER = ("s", "c")

_KAKASI = None


def _katakana_to_hiragana(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        out.append(chr(code - 0x60) if 0x30A1 <= code <= 0x30F6 else ch)
    return "".join(out)


@lru_cache(maxsize=8192)
def _kanji_to_hira(ch: str) -> str:
    global _KAKASI
    if _KAKASI is None:
        try:
            import pykakasi
        except ImportError:
            raise PhoneticUnavailable("ja") from None
        _KAKASI = pykakasi.kakasi()
    return "".join(item["hira"] for item in _KAKASI.convert(ch))


def _hira_to_roman(hira: str) -> str:
    out = []
    i = 0
    while i < len(hira):
        ch = hira[i]
        nxt = hira[i + 1] if i + 1 < len(hira) else ""
        base = _JA_ROMAN.get(ch)
        if base is None:
            i += 1  # っ and ー only lengthen a sound, which the doubled-letter fold erases
            continue
        if nxt in _JA_SMALL_Y and ch not in "ぁぃぅぇぉゃゅょゎ":
            cons = base[:-1]
            out.append(cons if cons in _JA_NO_Y_AFTER else cons + "y")
            out.append(_JA_SMALL_Y[nxt])
            i += 2
        elif nxt in _JA_SMALL_VOWEL and ch not in "ぁぃぅぇぉゃゅょゎ":
            cons = {"u": "w"}.get(base, base[:-1]) if len(base) == 1 else base[:-1]
            out.append(cons + _JA_SMALL_VOWEL[nxt])
            i += 2
        else:
            out.append(base)
            i += 1
    return "".join(out)


# --- Chinese ---------------------------------------------------------------------

_PINYIN_INITIAL_FOLD = (("zh", "z"), ("ch", "c"), ("sh", "s"), ("b", "p"), ("d", "t"),
                        ("g", "k"), ("j", "q"))


@lru_cache(maxsize=8192)
def _hanzi_pinyin(ch: str) -> str:
    try:
        from pypinyin import Style, pinyin
    except ImportError:
        raise PhoneticUnavailable("zh") from None
    syllable = pinyin(ch, style=Style.NORMAL, errors=lambda c: [""])[0][0]
    syllable = unicodedata.normalize("NFKD", syllable.replace("ü", "u"))
    syllable = "".join(c for c in syllable if "a" <= c <= "z")
    for src, dst in _PINYIN_INITIAL_FOLD:
        if syllable.startswith(src):
            syllable = dst + syllable[len(src):]
            break
    # -n / -ng are routinely confused by ASR and accents.
    return syllable[:-2] + "n" if syllable.endswith("ng") else syllable


# --- Korean ------------------------------------------------------------------------

_KO_INITIAL = ["k", "k", "n", "t", "t", "l", "m", "p", "p", "s", "s", "", "c", "c", "c", "k", "t",
               "p", "h"]
# ㅡ is written "u" because Korean names are often romanised that way by ASR
_KO_VOWEL = ["a", "e", "ya", "ye", "eo", "e", "yeo", "ye", "o", "wa", "we", "we", "yo", "u", "wo",
             "we", "wi", "yu", "u", "ui", "i"]
# Final consonants are pronounced as one of seven sounds whatever the spelling.
_KO_FINAL = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "p", "l", "l", "p", "l", "m",
             "p", "p", "t", "t", "n", "t", "t", "k", "t", "p", "t"]


def _hangul_roman(ch: str) -> str:
    code = ord(ch) - 0xAC00
    return _KO_INITIAL[code // 588] + _KO_VOWEL[(code % 588) // 28] + _KO_FINAL[code % 28]


# --- Latin ---------------------------------------------------------------------------

_LATIN_DIGRAPHS = (("tch", "c"), ("ch", "c"), ("sh", "s"), ("zh", "s"), ("th", "t"), ("ph", "h"),
                   ("kh", "k"), ("ts", "t"), ("ng", "n"))
_LATIN_LETTERS = str.maketrans({"c": "k", "q": "k", "g": "k", "d": "t", "b": "p", "v": "p",
                                "f": "h", "j": "s", "z": "s", "r": "l"})


def _fold_latin(word: str) -> str:
    word = unicodedata.normalize("NFKD", word.lower())
    word = "".join(c for c in word if "a" <= c <= "z")
    word = word.replace("x", "ks")
    for src, dst in _LATIN_DIGRAPHS:
        word = word.replace(src, dst.upper())
    return word.translate(_LATIN_LETTERS).lower()


# --- Units and keys --------------------------------------------------------------------

Unit = Tuple[str, str]  # ("hira" | "key", text)


def char_script(ch: str) -> str:
    code = ord(ch)
    if 0x3041 <= code <= 0x30FF or code == 0x30FC or 0xFF66 <= code <= 0xFF9F:
        return "kana"
    if 0xAC00 <= code <= 0xD7A3:
        return "hangul"
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF:
        return "han"
    if ch.isalpha() and code < 0x250:
        return "latin"
    return "other"


def phonetic_units(text: str, language: str) -> List[Unit]:
    """One unit per character of `text`, so a window of characters maps to a window of
    units. Characters with no sound (spaces, punctuation) give an empty unit.
    `language` decides how kanji/hanzi are read: "zh" as pinyin, anything else as
    Japanese."""
    lang = _lang(language)
    units: List[Unit] = []
    for raw in text:
        # Per character, not per string: a ligature that expands must not shift the windows.
        ch = unicodedata.normalize("NFKC", raw)[:1]
        kind = char_script(ch)
        if kind == "kana":
            units.append(("hira", _katakana_to_hiragana(ch)))
        elif kind == "han":
            units.append(("key", _hanzi_pinyin(ch)) if lang == "zh"
                         else ("hira", _katakana_to_hiragana(_kanji_to_hira(ch))))
        elif kind == "hangul":
            units.append(("key", _hangul_roman(ch)))
        elif kind == "latin":
            units.append(("latin", ch))
        else:
            units.append(("key", ""))
    return units


def key_from_units(units: List[Unit]) -> str:
    """Join a run of units into a key, collapsing long vowels and doubled letters."""
    parts: List[str] = []
    pending: List[str] = []
    latin: List[str] = []

    def flush():
        if pending:
            parts.append(_hira_to_roman("".join(pending)))
            pending.clear()
        if latin:
            parts.append(_fold_latin("".join(latin)))
            latin.clear()

    for kind, text in units:
        if kind == "hira":
            if latin:
                flush()
            pending.append(text)
        elif kind == "latin":
            if pending:
                flush()
            latin.append(text)
        else:
            flush()
            parts.append(text)
    flush()
    key = "".join(parts).replace("ou", "o")
    return re.sub(r"(.)\1+", r"\1", key)


def phonetic_key(text: str, language: str) -> str:
    """The sound-alike key of `text` ("" when it has no sound). Raises
    PhoneticUnavailable when kanji/hanzi need a library that isn't installed."""
    return key_from_units(phonetic_units(text or "", language))


# --- Comparison -------------------------------------------------------------------------

def key_distance(a: str, b: str) -> float:
    """Edit distance between two keys divided by the longer key, with cheap edits at the
    end (TAIL_EDIT_COST). 0 is identical, 1 is nothing in common."""
    if a == b:
        return 0.0
    if not a or not b:
        return 1.0
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    # lev(a[:i], b) for every i, so a cheap tail on `a` is as easy to price as one on `b`.
    prefix_vs_b = [prev[n]]
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prefix_vs_b.append(cur[n])
        prev = cur
    # `prev` is now lev(a, b[:j]) for every j.
    best = min(min(prev[j] + TAIL_EDIT_COST * (n - j) for j in range(n + 1)),
               min(prefix_vs_b[i] + TAIL_EDIT_COST * (m - i) for i in range(m + 1)))
    return min(1.0, best / max(m, n))


def similar(a_key: str, b_key: str, language: str = "latin") -> bool:
    """True when two keys sound alike enough to offer as one name. Keys shorter than
    MIN_FUZZY_KEY_LEN must be identical."""
    if not a_key or not b_key:
        return False
    if a_key == b_key:
        return True
    if min(len(a_key), len(b_key)) < MIN_FUZZY_KEY_LEN or max(len(a_key), len(b_key)) > MAX_KEY_LEN:
        return False
    threshold = SIMILARITY_THRESHOLD.get(_lang(language), SIMILARITY_THRESHOLD["latin"])
    # The length gap alone is a lower bound on the distance, so most pairs never reach the DP.
    gap = abs(len(a_key) - len(b_key))
    if gap * TAIL_EDIT_COST / max(len(a_key), len(b_key)) > threshold:
        return False
    return key_distance(a_key, b_key) <= threshold
