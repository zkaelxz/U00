"""
novel_text.py -- the raw source novel as text: chunking it into narration-sized
lines, finding paragraph ends, using it as transcription context, and the
cleanup rules every novel import shares (site chrome lines, ruby readings,
OCR line joins, chapter headings, legacy encodings).
"""

import os
import re


# A line that's a chapter heading of its own: 第十二章 / 第3回 / 第二卷 /
# 第5話 / 제3화 / 12화 / 序章 / プロローグ / 프롤로그 / "Chapter 12: Title" /
# "Prologue". Kept short and without sentence-ending punctuation, so an
# ordinary sentence that merely starts the same way (第一回合他就输了。 /
# "Chapter and verse, ...") isn't one. Markers that also start everyday
# words (第一部电影, 1화를, 프롤로그가) must be followed by a space,
# a separator or the end of the line.
_HEADING_END = r"(?=\s|[:：.\-–—]|$)"
_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第\s*[0-9０-９零〇一二三四五六七八九十百千两兩]+\s*[章回节節卷]|"
    r"(?:第\s*[0-9０-９零〇一二三四五六七八九十百千两兩]+\s*[話话部篇集幕]|"
    r"제\s*[0-9０-９]+\s*[장화권부]|[0-9０-９]+\s*화|"
    r"终章|終章|プロローグ|エピローグ|幕間|프롤로그|에필로그|외전|서장|종장|"
    r"序章|序言|楔子|引子|尾声|尾聲|后记|後記|番外篇?)" + _HEADING_END + "|"
    r"(?P<english>(chapter\s+\S+|prologue|epilogue)\s*(?:[:：.\-–—]|$)))", re.IGNORECASE)
_CHAPTER_HEADING_MAX_CHARS = 60
_SENTENCE_END = tuple("。！？!?…」』”\"")


def is_heading_text(text: str) -> bool:
    text = (text or "").strip()
    if not text or len(text) > _CHAPTER_HEADING_MAX_CHARS or text.endswith(_SENTENCE_END):
        return False
    match = _CHAPTER_HEADING_RE.match(text)
    # Korean prose ends its sentences with "."; an English "Chapter 3." stays a heading.
    return bool(match and (match.group("english") or not text.endswith(".")))


def is_chapter_heading(ln) -> bool:
    return is_heading_text(ln.zh) or is_heading_text(ln.en)


_HEADING_RUN = 2


def chapter_starts(text: str) -> list:
    """[(char offset, title)] of the lines that start a chapter in a whole-book
    text (an uploaded .txt or an EPUB's text), or [] when fewer than two do, so
    a text without headings stays one block. A heading only starts a chapter
    when prose follows it before the next heading: a contents page lists every
    title in a row, and those lines must not each become an empty chapter."""
    starts, run, offset = [], [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            if is_heading_text(stripped):
                run.append((offset, stripped))
            elif run:
                # A volume heading over a chapter heading (or a chapter with no
                # text) starts where the run starts; a longer run is a contents page.
                first = run[0] if len(run) <= _HEADING_RUN else run[-1]
                starts.append((first[0], run[-1][1]))
                run = []
        offset += len(line)
    return starts if len(starts) >= 2 else []


def chapter_count(text: str) -> int:
    """How many chapters a whole-book text lists: one per heading, plus the front
    matter before the first heading (it gets its own entry in the saved list), and
    one for a non-empty text without headings."""
    if not text.strip():
        return 0
    starts = chapter_starts(text)
    if not starts:
        return 1
    return len(starts) + (1 if text[:starts[0][0]].strip() else 0)


def chunk_novel_text(raw_text: str, max_chars: int = 200):
    """Splits novel prose into narration-sized chunks: paragraph-aware,
    falling back to sentence splits for long paragraphs. Used for the
    'novel narration' content mode where there's no source audio to
    align to -- these chunks become the Lines directly."""
    raw_text = raw_text.strip()
    paragraphs = [p.strip() for p in re.split(r"\n+", raw_text) if p.strip()]
    chunks = []
    for para in paragraphs:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            # ASCII . ! ? only end a sentence before whitespace, so "3.5" and "e.g." stay whole.
            sentences = re.split(r"(?<=[。！？…～])|(?<=[.!?])(?=\s)", para)
            buf = ""
            for s in sentences:
                if len(buf) + len(s) > max_chars and buf:
                    chunks.append(buf)
                    buf = s
                else:
                    buf += s
                # No sentence end to split on: wrap at the limit rather than emit an oversized line.
                while len(buf) > max_chars:
                    chunks.append(buf[:max_chars])
                    buf = buf[max_chars:]
            if buf:
                chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


def novel_paragraph_ends(lines, source_text: str):
    """Set of line idx whose line ends a paragraph of `source_text` -- the
    novel text the lines were chunked from (chunk_novel_text only ever
    splits a paragraph into pieces that join back into it). None when the
    lines no longer match the source (edited, merged or re-split since),
    rather than a guess that would put breaks in the wrong places."""
    paragraphs = [re.sub(r"\s+", "", p) for p in re.split(r"\n+", source_text or "") if p.strip()]
    ends, buf, p = set(), "", 0
    for ln in lines:
        piece = re.sub(r"\s+", "", ln.zh or "")
        if not piece:
            continue
        if p >= len(paragraphs):
            return None
        buf += piece
        if buf == paragraphs[p]:
            ends.add(ln.idx)
            buf, p = "", p + 1
        elif not paragraphs[p].startswith(buf):
            return None
    return ends if p == len(paragraphs) and not buf else None


# ---------------------------------------------------------------------------
# Using the raw source novel as transcription context
# ---------------------------------------------------------------------------

def extract_novel_excerpt_for_prompt(novel_text: str, max_chars: int = 800) -> str:
    """
    Pulls a usable excerpt from the raw novel for Whisper's initial_prompt.

    Whisper's initial_prompt has a real, hard limit -- only roughly the
    last ~224 tokens actually influence decoding, and anything before that
    is silently wasted. So this deliberately does NOT hand over a whole
    novel; it takes a bounded excerpt from early in the text, which is
    normally where the drama's episode 1 audio corresponds to.

    Kept separate from build_initial_prompt() (which lists proper nouns)
    so a caller can combine both: names first as the highest-value part,
    then a slice of real prose for phrasing and rhythm, trimmed to fit.
    """
    text = re.sub(r"\s+", " ", (novel_text or "").strip())
    if not text:
        return ""
    return text[:max_chars]


def combine_initial_prompt(name_prompt: str, novel_excerpt: str, max_chars: int = 900) -> str:
    """
    Merges the name-list prompt with a novel excerpt, names first since
    they're the highest-value part and must not get truncated away.
    """
    parts = [p for p in (name_prompt.strip(), novel_excerpt.strip()) if p]
    combined = "".join(parts)
    return combined[:max_chars]


_NOVEL_FALLBACK_ENCODINGS = ("utf-8-sig", "gb18030", "cp949", "big5", "cp932")
_KANA = re.compile(r"[\u3040-\u30ff]")


def decode_novel_bytes(data: bytes) -> str:
    """Novel text from a .txt/.md file in the encoding it was saved in. Chinese web
    novels are often GBK and Japanese ones Shift-JIS; decoding those as UTF-8 with
    errors ignored leaves a mostly empty text with no error."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    # GB18030 accepts most Shift-JIS byte pairs and returns CJK noise, so text
    # that reads as Japanese kana under Shift-JIS is taken as Shift-JIS.
    try:
        japanese = data.decode("cp932")
    except UnicodeDecodeError:
        japanese = ""
    if japanese and len(_KANA.findall(japanese)) >= 0.1 * len(japanese):
        return japanese
    text, _encoding = best_decoding(data, _NOVEL_FALLBACK_ENCODINGS[1:])
    if text is not None:
        return text
    raise ValueError("This text file is not in a text encoding Baihe can read "
                     "(tried UTF-8, GBK, Shift-JIS, Korean and Big5). Save it as UTF-8 and try again.")


def load_novel_text_for_context(file_bytes: bytes, filename: str) -> str:
    """
    Extracts plain text from an uploaded novel file for use as
    transcription/alignment context. Supports .txt/.md directly and
    .epub via epub_io (falls back to a clear error rather than silently
    returning nothing if ebooklib isn't installed).
    """
    name = (filename or "").lower()
    if name.endswith(".epub"):
        import tempfile
        import os as _os
        try:
            import epub_io
        except ImportError as exc:
            raise ImportError(
                "Reading .epub needs an extra package:\n    pip install ebooklib"
            ) from exc
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            return epub_io.import_epub_text(tmp_path)
        finally:
            _os.unlink(tmp_path)
    return decode_novel_bytes(file_bytes)


def best_decoding(data: bytes, encodings) -> tuple:
    """(text, encoding) for the first of `encodings` that decodes `data`, unless a
    later one that also decodes reads clearly more like real text. GB18030 decodes
    most Big5 bytes without an error into kana, Greek and box symbols, so "first
    that decodes" turned a traditional-Chinese novel into noise. (None, None) when
    none decode."""
    first = best = None
    for encoding in encodings:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        score = _plausible_share(text)
        if first is None:
            first = best = (text, encoding, score)
        elif score >= best[2] + _CLEARLY_BETTER:
            best = (text, encoding, score)
    return (best[0], best[1]) if best else (None, None)


_CLEARLY_BETTER = 0.05
_PLAUSIBLE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\u3041-\u3096\u30a1-\u30fa\u30fc\uac00-\ud7a3"
                        r"\uff01-\uff5e\u2010-\u205e\u2460-\u24ff\u2500-\u257f\u25a0-\u25ff\u2600-\u26ff"
                        r"\u00b7\x21-\x7e]")


def _plausible_share(text: str) -> float:
    """Share of the visible characters that are common CJK, kana, Hangul, ASCII or
    everyday punctuation: near 1 for correctly decoded prose."""
    sample = re.sub(r"\s+", "", text[:200_000])
    if not sample:
        return 0.0
    return len(_PLAUSIBLE.findall(sample)) / len(sample)


# ---------------------------------------------------------------------------
# Cleaning imported chapter text
# ---------------------------------------------------------------------------
# One table for every way a chapter comes in (site adapters, the extractor for
# sites with no adapter, browser-extension captures), so a site's leftovers are
# added here once instead of in each adapter. A rule only ever drops a whole,
# short line: prose that mentions a website or "the next chapter" is kept.

RUBY_READING_TAGS = ("rt", "rp")   # furigana: keep the base text only

_NAV_TOKENS = re.compile(
    r"上一[章页頁话話节節]|下一[章页頁话話节節]|(?:返回)?(?:章[节節])?目[录錄]|返回(?:书页|書頁|首页|首頁)|"
    r"加入[书書]签|加入[书書]籤|投推[荐薦]票|回到[顶頂]部|[书書]架|前の話|次の話|前へ|次へ|目次|이전화|다음화|목록|"
    r"previous chapter|next chapter|table of contents|\bprev(?:ious)?\b|\bnext\b|\bcontents\b|"
    r"\bindex\b|\btoc\b", re.IGNORECASE)
# These also stand alone in prose ("Next", "目录"), so alone on a line they are
# kept; next to another nav word or a separator they are a nav row.
_AMBIGUOUS_NAV = re.compile(r"(?:目[录錄]|[书書]架|前へ|次へ|目次|목록|prev(?:ious)?|next|contents|index|toc)",
                            re.IGNORECASE)
_NAV_ARROWS = re.compile(r"[|｜/／·•←→«»<>‹›]")
_NAV_SEPARATORS = re.compile(r"[\s|｜]+")
_NAV_FILLER = re.compile(r"[\s|｜/／·•←→«»<>‹›【】\[\]()（）:：\-–—>]+")
_DOMAIN = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)+(?:com|net|org|cc|cn|info|top|xyz|me|tw|la|vip|io|co|"
                     r"biz|pw|tv)\b", re.IGNORECASE)
_SITE_WORDS = re.compile(r"本站|本[书書]首[发發]|首[发發]域名|手[机機]版|[记記]住.{0,12}(?:地址|域名|[网網]址)|"
                         r"收藏本|[书書]友|最新章[节節]|更新最快|latest (?:chapters?|updates)",
                         re.IGNORECASE)
_PAGING = re.compile(r"本章未完|[点點]击下一[页頁]|[请請][点點]击下一|章[节節]错误|章[节節]錯誤|"
                     r"[点點]此[举舉][报報]")
_MAX_JUNK_LINE = 120


def is_site_chrome_line(line: str) -> bool:
    """True for a line that is the website, not the novel: a row of chapter
    navigation, a "remember our address" line, or a "continue on the next page"
    notice."""
    text = (line or "").strip()
    if not text or len(text) > _MAX_JUNK_LINE:
        return False
    if _NAV_TOKENS.search(text) and not _NAV_FILLER.sub("", _NAV_TOKENS.sub("", text)):
        tokens = _NAV_TOKENS.findall(text)
        if len(tokens) > 1 or _NAV_ARROWS.search(text) or not _AMBIGUOUS_NAV.fullmatch(tokens[0]):
            return True
    if _NAV_SEPARATORS.fullmatch(text):    # what is left of a nav row split into links
        return True
    if _DOMAIN.search(text) and (_SITE_WORDS.search(text) or not _DOMAIN.sub("", text).strip(" :：。.")):
        return True
    return len(text) <= 60 and bool(_PAGING.search(text))


def drop_site_chrome(text: str) -> str:
    return "\n".join(line for line in (text or "").split("\n") if not is_site_chrome_line(line))


# ---------------------------------------------------------------------------
# Chapter OCR output
# ---------------------------------------------------------------------------

_CJK = r"\u2014\u2026\u3000-\u303f\u3041-\u3096\u30a1-\u30fa\u30fc\u4e00-\u9fff\uff01-\uff5e“”‘’"
# Tesseract puts a space between Chinese and Japanese characters; Korean keeps its
# word spaces, so Hangul is not in the class.
_CJK_GAP = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
_CJK_EDGE = re.compile(rf"[{_CJK}]")
_LINE_END = tuple("。！？!?…」』”\"：:）)—～~♪")
_PAGE_NUMBER = re.compile(r"[-—\s]*\d{1,4}[-—\s]*")


def tidy_ocr_text(text: str) -> str:
    """OCR of a chapter's page images, as paragraphs: the spaces OCR puts between
    CJK characters removed, and each screen line joined to the next until a line
    ends a sentence (so a paragraph that wraps over several lines, or over a page
    break, is one paragraph again). Headings stay lines of their own."""
    lines = [_CJK_GAP.sub("", ln.strip()) for ln in (text or "").splitlines()]
    paragraphs, buf = [], ""
    for line in lines:
        if not line or _PAGE_NUMBER.fullmatch(line):
            continue
        if is_heading_text(line):
            if buf:
                paragraphs.append(buf)
            paragraphs.append(line)
            buf = ""
            continue
        if buf:
            # Screen lines in CJK prose wrap anywhere, even inside a Latin word.
            glue = "" if len(_CJK_EDGE.findall(buf)) * 2 >= len(buf) else " "
            buf += glue + line
        else:
            buf = line
        if buf.endswith(_LINE_END):
            paragraphs.append(buf)
            buf = ""
    if buf:
        paragraphs.append(buf)
    return "\n".join(paragraphs)


# Where the novel's source text is saved beside its title: narration and the
# translate pipeline both read it, so it lives here and not with the dub code.
NOVEL_SOURCE_FILENAME = "novel_narration_source.txt"


def narration_paragraph_ends(lines, drama_dir: str):
    """Set of line idx that end a paragraph of the novel's saved source
    text (see novel_paragraph_ends), or None if there's no source
    text or the lines no longer match it."""
    path = os.path.join(drama_dir, NOVEL_SOURCE_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return novel_paragraph_ends(lines, f.read())
