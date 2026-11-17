"""Tokenizers that compare two transcriptions of the same audio, shared by
the chunk overlap dedup."""
import re

# One token per CJK/kana character (no spaces to split on), one per run
# of other letters/digits (space-separated languages, Korean included).
# Punctuation and whitespace aren't tokens at all, so "北京。" and
# "北京，" compare equal -- Whisper's punctuation of the same audio often
# differs between two transcriptions, its words much less so.
CJK_CHARS = "぀-ヿ㐀-䶿一-鿿豈-﫿"
TOKEN_RE = re.compile(rf"[{CJK_CHARS}]|[^\W_{CJK_CHARS}]+")
LEADING_NON_WORD_RE = re.compile(r"^[\W_]+")


def tokens(text: str):
    return [m.group(0).casefold() for m in TOKEN_RE.finditer(text or "")]
