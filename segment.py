"""
segment.py -- word segmentation + pronunciation annotation, per source
language. This is what powers the Reader's ruby-text (pinyin/furigana)
display and click-to-define word boundaries.

  zh: jieba (segmentation) + pypinyin (pinyin) -- `pip install jieba pypinyin`.
      jieba's dictionary is built for Simplified Chinese and segments
      Traditional text poorly (many words go unrecognized). For
      chinese_script="traditional", OpenCC (`pip install
      opencc-python-reimplemented`) converts to Simplified just to find
      word BOUNDARIES, then the original Traditional text is sliced using
      those same lengths -- pypinyin's own dictionary covers Traditional
      characters natively, so only the segmentation step needs this detour.
  ja: sudachipy (segmentation, pure-Python, bundles its own dictionary,
      no external MeCab binary needed) + pykakasi (kanji -> hiragana
      reading, for furigana) -- `pip install sudachipy sudachidict_core pykakasi`
  ko: kiwipiepy (segmentation, pure-Python, no external Java dependency
      unlike konlpy) -- `pip install kiwipiepy`. Hangul is already
      phonetic, so no reading annotation is generated for Korean by
      default (nothing to add above the text itself).

Each function returns a list of (word, reading_or_none) tuples in
reading order, ready to render as ruby text. segment_words() is the same
segmentation without the readings (word boundaries only).
"""
import re

_jieba = None
_sudachi_tokenizer = None
_kakasi = None
_kiwi = None
_opencc_t2s = None

# jieba's cut time grows with the square of an unbroken run (40k chars took
# 6 s, 500k never finished) and sudachi refuses input over ~49 KB. Cutting
# at sentence ends keeps words whole; the hard cut only applies to text with
# no punctuation at all, where a word split at the seam is the lesser evil.
MAX_SEGMENT_CHARS = 2000
_SENTENCE_END = "。！？!?；;\n.，,、 "


def split_long_text(text: str, limit: int = MAX_SEGMENT_CHARS):
    chunks = []
    while len(text) > limit:
        cut = max(text.rfind(c, limit // 2, limit) for c in _SENTENCE_END) + 1 or limit
        chunks.append(text[:cut])
        text = text[cut:]
    if text:
        chunks.append(text)
    return chunks


def _per_chunk(fn):
    def run(text, *args, **kwargs):
        if len(text) <= MAX_SEGMENT_CHARS:
            return fn(text, *args, **kwargs)
        out = []
        for chunk in split_long_text(text):
            out.extend(fn(chunk, *args, **kwargs))
        return out
    run.__name__, run.__doc__ = fn.__name__, fn.__doc__
    return run


def _cut_traditional(text: str):
    """Word-boundary lengths come from segmenting the Simplified conversion
    (jieba's own dictionary); the actual words returned are sliced from the
    ORIGINAL Traditional text, not the converted one -- so the reading
    order's characters are always exactly what was in the source."""
    global _opencc_t2s
    if _opencc_t2s is None:
        import opencc
        _opencc_t2s = opencc.OpenCC("t2s")
    simplified = _opencc_t2s.convert(text)
    if len(simplified) != len(text):
        # OpenCC's t2s is character-count-preserving for the overwhelming
        # majority of real text; on the rare input where it isn't, slicing
        # the original by the converted text's word lengths would misalign
        # -- fall back to segmenting the original directly rather than risk
        # a bad slice. Still degraded (jieba doesn't know these words), but
        # not actively wrong.
        return list(_jieba.cut(text))
    lengths = [len(w) for w in _jieba.cut(simplified)]
    words, pos = [], 0
    for length in lengths:
        words.append(text[pos:pos + length])
        pos += length
    return words


def _load_jieba():
    global _jieba
    if _jieba is None:
        import jieba
        _jieba = jieba


def _load_sudachi():
    global _sudachi_tokenizer
    if _sudachi_tokenizer is None:
        from sudachipy import tokenizer, dictionary
        _sudachi_tokenizer = dictionary.Dictionary().create()
        globals()["_sudachi_mode"] = tokenizer.Tokenizer.SplitMode.B


@_per_chunk
def segment_words(text: str, language: str, chinese_script: str = "simplified"):
    """Just the words, in order, with no reading annotation -- for callers
    that only need where words start and end (resegment.py), so they don't
    also need pypinyin/pykakasi installed. The words always reassemble into
    exactly `text` for zh/ja. Korean is space-delimited already, so its
    words are simply the space-separated runs (spaces kept as their own
    items) -- no kiwipiepy needed for that."""
    if language == "ko":
        return [w for w in re.split(r"(\s+)", text) if w]
    if language == "ja":
        _load_sudachi()
        return [m.surface() for m in _sudachi_tokenizer.tokenize(text, globals()["_sudachi_mode"])]
    _load_jieba()
    return _cut_traditional(text) if chinese_script == "traditional" else list(_jieba.cut(text))


@_per_chunk
def segment_zh(text: str, chinese_script: str = "simplified"):
    _load_jieba()
    from pypinyin import pinyin, Style
    words = _cut_traditional(text) if chinese_script == "traditional" else list(_jieba.cut(text))
    out = []
    for w in words:
        if not w.strip():
            out.append((w, None))
            continue
        py = " ".join(p[0] for p in pinyin(w, style=Style.TONE))
        out.append((w, py))
    return out


@_per_chunk
def segment_ja(text: str):
    global _kakasi
    _load_sudachi()
    if _kakasi is None:
        import pykakasi
        _kakasi = pykakasi.kakasi()

    out = []
    for m in _sudachi_tokenizer.tokenize(text, globals()["_sudachi_mode"]):
        surface = m.surface()
        if not surface.strip():
            out.append((surface, None))
            continue
        conv = _kakasi.convert(surface)
        reading = "".join(c["hira"] for c in conv)
        # Only show a reading if it differs from the surface text
        # (skip furigana on words that are already kana-only).
        out.append((surface, reading if reading != surface else None))
    return out


@_per_chunk
def segment_ko(text: str):
    global _kiwi
    if _kiwi is None:
        from kiwipiepy import Kiwi
        _kiwi = Kiwi()
    tokens = _kiwi.tokenize(text)
    out = []
    last_end = 0
    for tok in tokens:
        if tok.start > last_end:
            out.append((text[last_end:tok.start], None))
        out.append((tok.form, None))  # Hangul is phonetic; no reading needed
        last_end = tok.end
    if last_end < len(text):
        out.append((text[last_end:], None))
    return out


def segment_and_annotate(text: str, language: str, chinese_script: str = "simplified"):
    if language == "ja":
        return segment_ja(text)
    if language == "ko":
        return segment_ko(text)
    return segment_zh(text, chinese_script=chinese_script)
