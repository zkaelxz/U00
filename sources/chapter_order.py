"""
sources/chapter_order.py -- sorting chapter lists the way a reader
expects (Step 23 item 12).

"Chapter 1, 2, 10" -- not "1, 10, 2". Understands CJK chapter markers
(第1章 / 第十章 / 第001話 / 제3화), full-width digits, fractional
chapters (10.5), and the common non-numeric parts (Prologue, Interlude,
Side Story, Epilogue...). The original title is never changed; sorting
only adds a normalized key alongside it.
"""

import re
import unicodedata

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000, "万": 10000, "萬": 10000}
_CN_CHARS = "".join(_CN_DIGITS) + "".join(_CN_UNITS)

# Sections that come before chapter 1 / after the last chapter, and
# extras ("Side Story", "Interlude", "番外"), which sort after the main run.
_BEFORE = re.compile(r"prologue|序章|序幕|楔子|序|프롤로그|プロローグ|前日谭|前日談", re.I)
_AFTER_MAIN = re.compile(r"epilogue|终章|終章|尾声|尾聲|完结|完結|에필로그|エピローグ", re.I)
_EXTRA = re.compile(r"side ?story|extra|special|bonus|interlude|omake|番外|特别篇|特別篇|外传|外傳|"
                    r"외전|특별편|おまけ|幕間|间章|間章", re.I)

_MARKER_NUM = re.compile(
    r"(?:第\s*([0-9]+(?:\.[0-9]+)?|[" + _CN_CHARS + r"]+)\s*[章话話回集卷部篇节節幕])"
    r"|(?:제?\s*([0-9]+(?:\.[0-9]+)?)\s*[화장권])"
    r"|(?:(?:chapter|chap|ch|episode|ep|vol|volume)\.?\s*([0-9]+(?:\.[0-9]+)?))",
    re.I)
_BARE_NUM = re.compile(r"([0-9]+(?:\.[0-9]+)?)")
_VOLUME = re.compile(r"(?:第\s*([0-9]+|[" + _CN_CHARS + r"]+)\s*[卷部])|(?:vol(?:ume)?\.?\s*([0-9]+))", re.I)


def cn_to_int(s: str):
    """Chinese numerals to int: 十 -> 10, 二十三 -> 23, 一百零五 -> 105,
    also plain digit runs like 〇〇七 -> 7. None if it isn't one."""
    if not s:
        return None
    if all(ch in _CN_DIGITS for ch in s):
        return int("".join(str(_CN_DIGITS[ch]) for ch in s))
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            if unit == 10000:
                section = (section + number) * unit
                total += section
                section = 0
            else:
                section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


def _num(tok: str):
    if tok is None:
        return None
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", tok):
        return float(tok)
    v = cn_to_int(tok)
    return float(v) if v is not None else None


def chapter_number(title: str):
    """The chapter's number, or None if the title has none."""
    t = unicodedata.normalize("NFKC", title or "")
    # A volume marker's number isn't the chapter number ("第2卷 第5话" is chapter 5).
    stripped = _VOLUME.sub(" ", t)
    for m in _MARKER_NUM.finditer(stripped):
        n = _num(next(g for g in m.groups() if g is not None))
        if n is not None:
            return n
    m = _BARE_NUM.search(stripped)
    return float(m.group(1)) if m else None


def volume_number(title: str):
    t = unicodedata.normalize("NFKC", title or "")
    m = _VOLUME.search(t)
    if not m:
        return None
    return _num(next(g for g in m.groups() if g is not None))


def sort_key(title: str, position: int = 0) -> tuple:
    """(bucket, volume, number, position). Buckets: 0 prologue, 1 the
    numbered run, 2 epilogue, 3 extras/side stories (by their own number
    if they have one, else where the source listed them). `position` is
    the entry's original index, which keeps ties and unnumbered entries
    stable."""
    t = unicodedata.normalize("NFKC", title or "")
    num = chapter_number(t)
    vol = volume_number(t)
    vol = vol if vol is not None else 0.0
    if num is None and _BEFORE.search(t):
        return (0, vol, 0.0, position)
    if _EXTRA.search(t):
        return (3, vol, float("inf") if num is None else num, position)
    if num is None and _AFTER_MAIN.search(t):
        return (2, vol, 0.0, position)
    return (1, vol, float("inf") if num is None else num, position)


def sort_chapters(chapters, key=lambda c: c.title):
    """Returns a new list sorted by sort_key, and fills each item's
    `sort_key` attribute when it has one. The titles are untouched."""
    items = list(chapters)
    keyed = []
    for i, c in enumerate(items):
        k = sort_key(key(c), i)
        if hasattr(c, "sort_key"):
            try:
                c.sort_key = k
            except AttributeError:
                pass
        keyed.append((k, c))
    keyed.sort(key=lambda kc: kc[0])
    return [c for _, c in keyed]


def sort_chapters_grouped(chapters):
    """Like sort_chapters, but keeps a source's own sections (单话 /
    单行本 / 番外篇...) together in the order the source lists them, and
    sorts naturally within each -- so volume 1 doesn't get interleaved
    with chapter 1."""
    groups = {}
    for c in chapters:
        groups.setdefault(getattr(c, "group", ""), []).append(c)
    out = []
    for items in groups.values():
        out.extend(sort_chapters(items))
    return out
