"""
segment.py -- word segmentation + pronunciation annotation, per source
language. This is what powers the Reader's ruby-text (pinyin/furigana)
display and click-to-define word boundaries.

  zh: jieba (segmentation) + pypinyin (pinyin) -- `pip install jieba pypinyin`
  ja: sudachipy (segmentation, pure-Python, bundles its own dictionary,
      no external MeCab binary needed) + pykakasi (kanji -> hiragana
      reading, for furigana) -- `pip install sudachipy sudachidict_core pykakasi`
  ko: kiwipiepy (segmentation, pure-Python, no external Java dependency
      unlike konlpy) -- `pip install kiwipiepy`. Hangul is already
      phonetic, so no reading annotation is generated for Korean by
      default (nothing to add above the text itself).

Each function returns a list of (word, reading_or_none) tuples in
reading order, ready to render as ruby text.
"""

_jieba = None
_sudachi_tokenizer = None
_kakasi = None
_kiwi = None


def segment_zh(text: str):
    global _jieba
    if _jieba is None:
        import jieba
        _jieba = jieba
    from pypinyin import pinyin, Style
    words = list(_jieba.cut(text))
    out = []
    for w in words:
        if not w.strip():
            out.append((w, None))
            continue
        py = " ".join(p[0] for p in pinyin(w, style=Style.TONE))
        out.append((w, py))
    return out


def segment_ja(text: str):
    global _sudachi_tokenizer, _kakasi
    if _sudachi_tokenizer is None:
        from sudachipy import tokenizer, dictionary
        _sudachi_tokenizer = dictionary.Dictionary().create()
        globals()["_sudachi_mode"] = tokenizer.Tokenizer.SplitMode.B
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


def segment_and_annotate(text: str, language: str):
    if language == "ja":
        return segment_ja(text)
    if language == "ko":
        return segment_ko(text)
    return segment_zh(text)
