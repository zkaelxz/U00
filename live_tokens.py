"""Tokenizers that compare two transcriptions of the same audio, shared by
the chunk overlap dedup and the streaming agreement rule."""
import re
import unicodedata

# One token per CJK/kana character (no spaces to split on), one per run
# of other letters/digits (space-separated languages, Korean included).
# Punctuation and whitespace aren't tokens at all, so "北京。" and
# "北京，" compare equal -- Whisper's punctuation of the same audio often
# differs between two transcriptions, its words much less so.
CJK_CHARS = "぀-ヿ㐀-䶿一-鿿豈-﫿"
TOKEN_RE = re.compile(rf"[{CJK_CHARS}]|[^\W_{CJK_CHARS}]+")
LEADING_NON_WORD_RE = re.compile(r"^[\W_]+")

# Streaming only: a partial transcription re-spaces Korean between two
# passes ("안녕 하세요" / "안녕하세요"), so Hangul is compared by syllable.
HANGUL_CHARS = "ᄀ-ᇿ㄰-㆏가-힯"
_AGREEMENT_TOKEN_RE = re.compile(rf"[{CJK_CHARS}{HANGUL_CHARS}]|[^\W_{CJK_CHARS}{HANGUL_CHARS}]+")


def tokens(text: str):
    return [m.group(0).casefold() for m in TOKEN_RE.finditer(text or "")]


def agreement_tokens(text: str):
    # NFKC first so half-width kana and full-width Latin match their normal forms.
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    return _AGREEMENT_TOKEN_RE.findall(folded)
