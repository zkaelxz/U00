"""
services/novel_attach_service.py -- attach novel text to a drama and OCR
chapter images for a novel-narration drama. Text
lands in `novel_narration_source.txt` (the
file `narration_service` reads).

Only extracted plain text is ever stored: an uploaded EPUB is opened with
the stdlib zip/HTML parsers (no ebooklib, no entity or external-resource
resolution), capped on entry count and total uncompressed size, and
rejected on path traversal, absolute names or symlink entries. Nothing is
extracted to disk and the .epub itself is not kept. Client filenames are
never used or returned; OCR images are staged under generated names in a
private temp folder that is always removed. No path is ever returned.

No FastAPI import: takes binary file-like objects.
"""
import codecs
import io
import logging
import os
import posixpath
import re
import shutil
import stat
import tempfile
import zipfile
import zlib
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import unquote
from xml.etree import ElementTree

try:
    import lzma
except ImportError:  # Python built without lzma: zipfile raises RuntimeError instead
    lzma = None

import background_jobs
from jobs import job_launch
import db
import novel_text
import ocr as ocr_module
import subtitle_parse
from services import cover_art_service, drama_service, media_upload_service, ownership_service, settings_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2_000_000
MAX_EPUB_BYTES = 50 * 1024 * 1024
MAX_EPUB_ENTRIES = 5000
MAX_EPUB_UNCOMPRESSED = 100 * 1024 * 1024
MAX_IMAGES = 200
MAX_IMAGE_BYTES = 20 * 1024 * 1024
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
_HTML_EXTENSIONS = (".xhtml", ".html", ".htm")
_MODES = ("append", "replace")
_BACKENDS = {"zh": ("tesseract", "paddle"), "ja": ("manga_ocr", "tesseract"),
             "ko": ("tesseract",)}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
_SKIP_TAGS = {"script", "style", "head"}
_BAD_ENTRY_ERRORS = (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError,
                     OSError, EOFError)
if lzma is not None:  # a corrupt LZMA (method 14) entry raises LZMAError, not OSError
    _BAD_ENTRY_ERRORS += (lzma.LZMAError,)


def _check_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A job is running for this title. Wait for it to finish or cancel it.")


def _novel_path(drama_id: int, create: bool) -> str:
    base = db.drama_dir(drama_id) if create else os.path.join(db.DRAMAS_DIR, str(drama_id))
    return os.path.join(base, novel_text.NOVEL_SOURCE_FILENAME)


def _read_novel(drama_id: int) -> str:
    path = _novel_path(drama_id, create=False)
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write_novel(drama_id: int, text: str, mode: str) -> int:
    """Atomic write; returns the stored character count."""
    if mode == "append":
        old = _read_novel(drama_id).rstrip()
        text = f"{old}\n\n{text}" if old else text
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidInputError(
            f"The combined novel text is too large (at most {MAX_TEXT_CHARS:,} characters).")
    final = _novel_path(drama_id, create=True)
    fd, tmp = tempfile.mkstemp(prefix=".novel_", suffix=".part", dir=os.path.dirname(final))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, final)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    return len(text)


def _clean(text) -> str:
    # A non-breaking space (EPUB &nbsp;, web copy) is a plain space to every reader of this text.
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").replace("\u00a0", " ").strip()


def _check_mode(mode):
    if mode not in _MODES:
        raise InvalidInputError(f"mode must be one of {', '.join(_MODES)}.")


def attach_text(drama_id: int, text: str, mode: str = "replace") -> dict:
    ownership_service.require_drama(drama_id)
    _check_mode(mode)
    text = _clean(text)
    if not text:
        raise InvalidInputError("The novel text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidInputError(f"The novel text is too large (at most {MAX_TEXT_CHARS:,} characters).")
    with media_upload_service.folder_write(drama_id):
        _check_idle(drama_id)
        count = _write_novel(drama_id, text, mode)
    _save_as_raw_when_none(drama_id)
    return {"char_count": count}


def _save_as_raw_when_none(drama_id: int, job_id: Optional[str] = None, book=None) -> None:
    """The title page lists the saved raw chapters, so a title with none shows
    "No chapters saved" even after text is pasted. The pasted text becomes them
    only when the title has no raw novel, so scraped chapters are never replaced."""
    from services import novel_files_service  # imports this module at load time
    # The pasted text is already stored, so nothing here may fail the attach:
    # in append mode a retry would add the same text twice.
    try:
        if novel_files_service.get_raw_novel_status(drama_id)["present"]:
            return
        # job_id: the running job that is attaching, which the idle check must not count.
        text = _read_novel(drama_id)
        # An EPUB's own chapter boundaries, unless an append put other text in front of them.
        starts = book[1] if book and book[0] == text else None
        novel_files_service.save_raw_novel_text(drama_id, text, job_id=job_id, starts=starts)
    except (ConflictError, InvalidInputError, NotFoundError, OSError, ValueError):
        log.warning("Could not save the pasted text as chapters for drama %s", drama_id, exc_info=True)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0
        self._reading = False
        self._link = 0
        self.link_chars = 0
        self.links = 0

    def handle_starttag(self, tag, attrs):
        # HTML lets </rt> and </rp> be left out, so a reading is not counted like
        # a skipped tag: it ends at the next reading, the ruby's end or a block.
        self._reading = tag in novel_text.RUBY_READING_TAGS or (
            self._reading and tag not in _BLOCK_TAGS)
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "a":
            self._link += 1
            if dict(attrs).get("href"):
                self.links += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in novel_text.RUBY_READING_TAGS or tag == "ruby" or tag in _BLOCK_TAGS:
            self._reading = False
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "a":
            self._link = max(0, self._link - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip and not self._reading:
            self.parts.append(data)
            if self._link:
                self.link_chars += len(data.strip())

    def is_contents_page(self) -> bool:
        """A contents page: nearly all of its text is links to the chapters. In
        the reading order it would count as a chapter of its own."""
        text = sum(len(p.strip()) for p in self.parts)
        return self.links >= 3 and text > 0 and self.link_chars >= 0.8 * text


_DECLARED_CHARSET = re.compile(
    rb"""<\?xml[^>]*?encoding\s*=\s*["']([A-Za-z0-9._:-]{1,40})["']"""
    rb"""|<meta[^>]*?charset\s*=\s*["']?([A-Za-z0-9._:-]{1,40})""", re.IGNORECASE)
# Older Chinese, Japanese and Korean EPUBs declare the narrow legacy names;
# the supersets read every file those do, plus the characters they lack.
_CHARSET_SUPERSETS = {"gbk": "gb18030", "gb2312": "gb18030", "cp936": "gb18030",
                      "x-gbk": "gb18030", "shift_jis": "cp932", "shift-jis": "cp932",
                      "sjis": "cp932", "euc-kr": "cp949", "ks_c_5601-1987": "cp949",
                      "big5": "big5hkscs"}


def _decode_html(raw: bytes) -> str:
    """A chapter's text in the encoding it was saved in. EPUB asks for UTF-8,
    but older CJK books are often GBK, Big5 or Shift-JIS (declared in the
    XML prolog or a meta tag) or UTF-16, which read as UTF-8 come out as
    replacement characters with no error. Valid UTF-8 wins over a
    declaration, which converters often leave stale."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    declared = _DECLARED_CHARSET.search(raw[:2048])
    if declared:
        name = (declared.group(1) or declared.group(2)).decode("ascii").lower()
        try:
            return raw.decode(codecs.lookup(_CHARSET_SUPERSETS.get(name, name)).name)
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return subtitle_parse.decode_subtitle_bytes(raw)[0]
    except subtitle_parse.SubtitleParseError:
        return raw.decode("utf-8", errors="replace")


def _html_to_text(raw: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(_decode_html(raw))
    if parser.is_contents_page():
        return ""
    lines = [ln.strip() for ln in "".join(parser.parts).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _safe_zip_entry(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return False
    return ".." not in name.split("/")


def _spine_order(zf, names) -> tuple:
    """(reading order from the OPF spine, spine entries that pointed at no
    readable file); falls back to sorted names with 0 dropped."""
    try:
        container = zf.read("META-INF/container.xml")
        if b"<!ENTITY" in container:
            raise ValueError
        opf_name = ElementTree.fromstring(container).find(".//{*}rootfile").get("full-path")
        if not _safe_zip_entry(opf_name) or opf_name not in names:
            raise ValueError
        opf = zf.read(opf_name)
        if b"<!ENTITY" in opf:
            raise ValueError
        root = ElementTree.fromstring(opf)
        manifest = {i.get("id"): i.get("href") for i in root.iterfind(".//{*}manifest/{*}item")}
        base = posixpath.dirname(opf_name)
        ordered, dropped = [], 0
        for ref in root.iterfind(".//{*}spine/{*}itemref"):
            href = manifest.get(ref.get("idref"))
            full = _manifest_entry(base, href, names) if href else None
            if full is None:
                dropped += 1
            elif full not in ordered:
                ordered.append(full)
        if ordered:
            return ordered, dropped
    except Exception:
        pass
    return sorted((n for n in names if n.lower().endswith(_HTML_EXTENSIONS)), key=_natural_key), 0


def _manifest_entry(base: str, href: str, names) -> Optional[str]:
    """The zip entry a manifest href names, or None. Hrefs are URLs, so a
    space or a CJK filename is percent-encoded in the OPF but not in the zip."""
    path = href.split("#")[0]
    for candidate in (unquote(path), path):
        full = posixpath.normpath(posixpath.join(base, candidate))
        if full in names:
            return full
    return None


def _natural_key(name: str) -> list:
    # chapter2 before chapter10, as a reader expects.
    return [(0, int(part), "") if part.isdigit() else (1, 0, part.lower())
            for part in re.split(r"(\d+)", name)]


def extract_epub_text(fileobj) -> str:
    """Safe plain-text extraction; raises InvalidInputError for anything
    that is not a well-formed, in-bounds EPUB."""
    return "\n\n".join(extract_epub_chapters(fileobj))


def extract_epub_chapters(fileobj) -> list:
    return extract_epub_chapters_counting_drops(fileobj)[0]


def extract_epub_chapters_counting_drops(fileobj) -> tuple:
    """(chapters, dropped): the EPUB's text as one entry per reading-order
    document that has any text (the chapters a range picks from), and how
    many spine entries named a file that isn't in the EPUB. Same checks as
    extract_epub_text."""
    book = read_epub(fileobj)
    return [text for _title, text in book["chapters"]], book["dropped"]


def read_epub(fileobj) -> dict:
    """{chapters: [(title, text)], dropped, meta: {title, author, cover}}: one
    chapter per reading-order document with text, titled from the EPUB's table
    of contents (a first line stands in when it names none). Same safety checks
    as extract_epub_text."""
    try:
        zf = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError):
        raise InvalidInputError("That file is not a valid EPUB.")
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_EPUB_ENTRIES:
            raise InvalidInputError("That EPUB has too many entries.")
        if sum(i.file_size for i in infos) > MAX_EPUB_UNCOMPRESSED:
            raise InvalidInputError("That EPUB is too large once unpacked.")
        for i in infos:
            if not _safe_zip_entry(i.filename) or stat.S_ISLNK(i.external_attr >> 16):
                raise InvalidInputError("That EPUB contains an unsafe entry.")
        names = {i.filename for i in infos if not i.is_dir()}
        docs = []
        order, dropped = _spine_order(zf, names)
        titles, meta = _toc_and_metadata(zf, names)
        for name in order:
            if not name.lower().endswith(_HTML_EXTENSIONS):
                continue
            try:
                with zf.open(name) as f:
                    raw = f.read(MAX_EPUB_UNCOMPRESSED + 1)  # bounded even if the header lied
            except _BAD_ENTRY_ERRORS:
                # corrupt/encrypted/unsupported entry: a client error, not a 500
                raise InvalidInputError("That file is not a valid EPUB.") from None
            if len(raw) > MAX_EPUB_UNCOMPRESSED:
                raise InvalidInputError("That EPUB is too large once unpacked.")
            text = _html_to_text(raw)
            if text:
                docs.append((titles.get(name), text))
    return {"chapters": _group_documents(docs), "dropped": dropped, "meta": meta}


def _group_documents(docs) -> list:
    """[(title, text)] chapters from the reading-order documents [(toc title or
    None, text)]. A document starts a chapter when the table of contents names it
    or its first line is a chapter heading; one that does neither continues the
    chapter before it (converters split a long chapter over several files). A book
    that gives neither signal anywhere is one chapter per document."""
    def first_line(text):
        return text.split("\n", 1)[0].strip()

    def starts(toc_title, text):
        return bool(toc_title) or novel_text.is_heading_text(first_line(text))

    if not any(starts(t, x) for t, x in docs):
        return [(first_line(x)[:_MAX_CHAPTER_TITLE], x) for _t, x in docs]
    chapters = []
    for toc_title, text in docs:
        if chapters and not starts(toc_title, text):
            title, body = chapters[-1]
            chapters[-1] = (title, f"{body}\n\n{text}")
        else:
            chapters.append(((toc_title or first_line(text))[:_MAX_CHAPTER_TITLE], text))
    return chapters


_MAX_CHAPTER_TITLE = 80
_MAX_COVER_READ = 10 * 1024 * 1024


class _NavLinks(HTMLParser):
    """The (href, text) links of an EPUB 3 navigation document's toc list."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self._in_toc = 0
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav" and (not self._in_toc) and "toc" in (a.get("epub:type") or a.get("type") or ""):
            self._in_toc = 1
        elif tag == "a" and self._in_toc and a.get("href"):
            self._href, self._text = a["href"], []

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None
        elif tag == "nav":
            self._in_toc = 0

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)


def _toc_and_metadata(zf, names) -> tuple:
    """({zip entry: title}, {title, author, cover}) from the OPF, the EPUB 3
    navigation document or the EPUB 2 NCX. Anything unreadable gives less, never an error:
    a book without a usable table of contents still imports."""
    titles, meta = {}, {"title": "", "author": "", "cover": None}
    try:
        container = zf.read("META-INF/container.xml")
        if b"<!ENTITY" in container:
            return titles, meta
        opf_name = ElementTree.fromstring(container).find(".//{*}rootfile").get("full-path")
        if not _safe_zip_entry(opf_name) or opf_name not in names:
            return titles, meta
        opf = zf.read(opf_name)
        if b"<!ENTITY" in opf:
            return titles, meta
        root = ElementTree.fromstring(opf)
    except Exception:
        return titles, meta
    base = posixpath.dirname(opf_name)
    items = {i.get("id"): i for i in root.iterfind(".//{*}manifest/{*}item")}
    try:
        meta["title"] = " ".join((root.findtext(".//{*}metadata/{*}title") or "").split())
        meta["author"] = " ".join((root.findtext(".//{*}metadata/{*}creator") or "").split())
    except Exception:
        pass

    def entry(item):
        href = item.get("href") if item is not None else None
        return _manifest_entry(base, href, names) if href else None

    cover_item = next((i for i in items.values() if "cover-image" in (i.get("properties") or "").split()), None)
    if cover_item is None:
        wanted = next((m.get("content") for m in root.iterfind(".//{*}metadata/{*}meta")
                       if m.get("name") == "cover"), None)
        cover_item = items.get(wanted)
    cover_name = entry(cover_item)
    if cover_name:
        try:
            with zf.open(cover_name) as f:
                data = f.read(_MAX_COVER_READ + 1)
            if len(data) <= _MAX_COVER_READ:
                meta["cover"] = data
        except _BAD_ENTRY_ERRORS:
            pass

    def add(toc_dir, href, label):
        path = unquote(href.split("#")[0])
        full = posixpath.normpath(posixpath.join(toc_dir, path)) if path else None
        if full in names and label and full not in titles:
            titles[full] = label[:_MAX_CHAPTER_TITLE]

    try:
        nav = next((i for i in items.values() if "nav" in (i.get("properties") or "").split()), None)
        nav_name = entry(nav)
        if nav_name:
            parser = _NavLinks()
            parser.feed(_decode_html(zf.read(nav_name)))
            for href, label in parser.links:
                add(posixpath.dirname(nav_name), href, label)
        if not titles:
            spine = root.find(".//{*}spine")
            ncx = items.get(spine.get("toc")) if spine is not None else None
            ncx_name = entry(ncx) or next((n for n in names if n.lower().endswith(".ncx")), None)
            if ncx_name and ncx_name in names:
                data = zf.read(ncx_name)
                if b"<!ENTITY" not in data:
                    for point in ElementTree.fromstring(data).iterfind(".//{*}navPoint"):
                        src = point.find("{*}content")
                        label_el = point.find("{*}navLabel")
                        label = " ".join("".join(label_el.itertext()).split()) if label_el is not None else ""
                        if src is not None and src.get("src"):
                            add(posixpath.dirname(ncx_name), src.get("src"), label)
    except Exception:
        pass
    return titles, meta


def _check_range(chapter_from, chapter_to, total: int):
    """1-based, inclusive. Either end may be omitted (first / last chapter)."""
    for v in (chapter_from, chapter_to):
        if v is not None and (isinstance(v, bool) or not isinstance(v, int)):
            raise InvalidInputError("Chapter numbers are whole numbers.")
    start = 1 if chapter_from is None else chapter_from
    end = total if chapter_to is None else chapter_to
    if not 1 <= start <= end <= total:
        raise InvalidInputError(
            f"That EPUB has {total} chapter{'s' if total != 1 else ''}; pick a range from 1 to {total}.",
            details={"chapters": total})
    return start, end


def attach_epub(drama_id: int, fileobj, mode: str = "replace",
                chapter_from: Optional[int] = None, chapter_to: Optional[int] = None) -> dict:
    """Attaches the EPUB's text, or only chapters chapter_from..chapter_to
    (1-based, inclusive). Returns {char_count, epub_chapters,
    chapter_from, chapter_to}."""
    ownership_service.require_drama(drama_id)
    _check_mode(mode)
    with media_upload_service.folder_write(drama_id):
        _check_idle(drama_id)
        result, book = _attach_epub_data(drama_id, fileobj, mode, chapter_from, chapter_to)
    _save_as_raw_when_none(drama_id, book=book)
    _fill_from_epub_metadata(drama_id, book[2])
    return result


def attach_epub_from_job(drama_id: int, fileobj, mode: str = "replace",
                         job_id: Optional[str] = None) -> dict:
    """attach_epub for a job that already holds this drama (the
    lightnovel-crawler import, services/lncrawl_service.py): the same EPUB
    limits, without the "a job is running" refusal its own job would hit."""
    ownership_service.require_drama(drama_id)
    _check_mode(mode)
    result, book = _attach_epub_data(drama_id, fileobj, mode, None, None)
    _save_as_raw_when_none(drama_id, job_id=job_id, book=book)
    _fill_from_epub_metadata(drama_id, book[2])
    return result


def epub_text_and_starts(chapters) -> tuple:
    """(text, [(character offset, title)]) of EPUB chapters joined the way they are stored."""
    picked = [(title, _clean(body)) for title, body in chapters]
    picked = [(title, body) for title, body in picked if body]
    starts, offset = [], 0
    for title, body in picked:
        starts.append((offset, title))
        offset += len(body) + 2
    # One document is not a split: the heading scan may still find several chapters in it.
    return "\n\n".join(body for _title, body in picked), (starts if len(starts) >= 2 else [])


def _attach_epub_data(drama_id, fileobj, mode, chapter_from, chapter_to) -> tuple:
    """(result, book): book is (joined text, [(offset, title)], metadata) for the
    chapters attached, to save as the title's chapters and fill its blanks."""
    data = fileobj.read(MAX_EPUB_BYTES + 1)
    if len(data) > MAX_EPUB_BYTES:
        raise InvalidInputError("The uploaded EPUB is too large.")
    if not data:
        raise InvalidInputError("The uploaded file is empty.")
    parsed = read_epub(io.BytesIO(data))
    chapters = parsed["chapters"]
    if not chapters:
        raise InvalidInputError("No readable text was found in that EPUB.")
    start, end = _check_range(chapter_from, chapter_to, len(chapters))
    text, starts = epub_text_and_starts(chapters[start - 1:end])
    if not text:
        raise InvalidInputError("No readable text was found in that EPUB.")
    result = {"char_count": _write_novel(drama_id, text, mode), "epub_chapters": len(chapters),
              "chapter_from": start, "chapter_to": end}
    dropped = parsed["dropped"]
    if dropped:
        result["warning"] = (f"{dropped} chapter{'s' if dropped != 1 else ''} listed in the EPUB "
                             "couldn't be found inside it and were left out.")
    return result, (text, starts, parsed["meta"])


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def _fill_from_epub_metadata(drama_id: int, meta: dict) -> None:
    """The book's own title, author and cover, only where the title has none
    (a name the owner typed is never replaced). Best effort: the text is already attached."""
    try:
        drama = db.get_drama(drama_id) or {}
        fields = {}
        title = meta.get("title") or ""
        if title and not (drama.get("title_en") or "").strip() and not (drama.get("title_zh") or "").strip():
            fields["title_zh" if _CJK.search(title) else "title_en"] = title[:200]
        if meta.get("author") and not (drama.get("author") or "").strip():
            fields["author"] = meta["author"][:200]
        if fields:
            db.update_drama(drama_id, **fields)
        if meta.get("cover") and not drama.get("cover_art_filename"):
            cover_art_service.save_cover(drama_id, io.BytesIO(meta["cover"]))
    except Exception:
        log.warning("Could not take the title or cover from the EPUB for drama %s", drama_id, exc_info=True)


RAW_NOVEL_FILENAME = "raw_novel_context.txt"  # sources/pipeline.save_novel_text


def attach_from_sources(drama_id: int, mode: str = "replace") -> dict:
    """Uses the chapters imported in Sources (or saved as the original novel),
    `raw_novel_context.txt`, as the narration text. 404
    when there is none."""
    ownership_service.require_drama(drama_id)
    _check_mode(mode)
    path = os.path.join(db.DRAMAS_DIR, str(drama_id), RAW_NOVEL_FILENAME)
    if not os.path.isfile(path):
        raise NotFoundError("No chapters have been imported for this title.")
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read(MAX_TEXT_CHARS + 1)
    if len(raw) > MAX_TEXT_CHARS:     # checked before cleaning, so it never truncates
        raise InvalidInputError("The imported chapters are too large.")
    text = _clean(raw)
    if not text:
        raise InvalidInputError("The imported chapters are empty.")
    with media_upload_service.folder_write(drama_id):
        _check_idle(drama_id)
        return {"char_count": _write_novel(drama_id, text, mode)}


def _job_id(drama_id: int) -> str:
    return f"ocrchapter_{drama_id}"


def _chapter_count(text: str) -> int:
    return novel_text.chapter_count(text)


def get_status(drama_id: int) -> dict:
    ownership_service.require_drama(drama_id)
    text = _read_novel(drama_id)
    job = background_jobs.get_status(_job_id(drama_id))
    return {"drama_id": drama_id, "has_novel_text": bool(text.strip()),
            "char_count": len(text),
            "chapters": _chapter_count(text),
            "ocr_running": bool(job and job["status"] in ("running", "queued"))}


def start_ocr_chapter(drama_id: int, images, backend: str = "tesseract",
                      mode: str = "append",
                      tesseract_cmd: Optional[str] = None) -> dict:
    """images: list of (client_filename, fileobj). Stages them under
    generated names, then starts the job that OCRs them in order and writes
    the text. Returns {"job_id"}. tesseract_cmd None: the Settings
    Tesseract path."""
    drama = ownership_service.require_drama(drama_id)
    _check_mode(mode)
    language = drama.get("source_language") or "zh"
    tesseract_cmd = tesseract_cmd or settings_service.get("tesseract_cmd") or None
    if backend not in _BACKENDS.get(language, ("tesseract",)):
        raise InvalidInputError(f"OCR backend {backend!r} is not available for {language}.")
    if not images:
        raise InvalidInputError("Upload at least one chapter image.")
    if len(images) > MAX_IMAGES:
        raise InvalidInputError("Too many images.")
    exts = []
    for name, _f in images:
        clean = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
        ext = os.path.splitext(clean)[1].lower()
        if ext not in _IMAGE_EXTENSIONS:
            raise InvalidInputError("Unsupported image type. Upload PNG or JPG images.")
        exts.append(ext)
    ocr_module.require_backend(backend, tesseract_cmd)
    with media_upload_service.folder_write(drama_id):
        _check_idle(drama_id)

        stage = tempfile.mkdtemp(prefix=".ocr_", dir=db.drama_dir(drama_id))
        try:
            paths = []
            for n, ((_name, f), ext) in enumerate(zip(images, exts), 1):
                data = f.read(MAX_IMAGE_BYTES + 1)
                if len(data) > MAX_IMAGE_BYTES:
                    raise InvalidInputError("An uploaded image is too large.")
                if not data:
                    raise InvalidInputError("An uploaded image is empty.")
                path = os.path.join(stage, f"{n:04d}{ext}")
                with open(path, "wb") as out:
                    out.write(data)
                paths.append(path)
            job_id = _job_id(drama_id)
            started = job_launch.start_job(
                job_id, _run_ocr_job, job_id, drama_id, stage, paths, backend, mode, language,
                drama.get("chinese_script") or "simplified", tesseract_cmd or None,
                gpu_touching=True,
                description=f"Chapter OCR (title {drama_id})", line_fields=())
            if not started:
                raise ConflictError("A chapter OCR run is already active for this title.")
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return {"job_id": job_id}


def _raise_if_cancelled(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def _image_readable(path: str) -> bool:
    """Whether the page decodes as an image. Only the extension was checked at
    upload; a renamed, truncated or oversized file would otherwise fail the
    whole chapter with an error that names its staging path."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.load()
    except Exception:
        return False
    return True


def _run_ocr_job(job_id, drama_id, stage, paths, backend, mode, language, script,
                 tesseract_cmd=None):
    skipped = []
    try:
        background_jobs.update_progress(job_id, 0.1, "Running OCR...")
        text = _clean(novel_text.tidy_ocr_text(ocr_module.extract_text_from_images(
            paths, backend=backend, source_language=language, chinese_script=script,
            tesseract_cmd=tesseract_cmd, before_page=lambda: _raise_if_cancelled(job_id),
            on_skip=skipped.append, page_readable=_image_readable)))
        if not text:
            result = {"failed_reason": "empty"}
            if skipped:   # every page unreadable reads differently from pages with no text
                result["skipped_pages"] = len(skipped)
            background_jobs.set_result(job_id, result)
            return
        background_jobs.update_progress(job_id, 0.9, "Saving text...")
        _raise_if_cancelled(job_id)
        count = _write_novel(drama_id, text, mode)
        _save_as_raw_when_none(drama_id, job_id=job_id)
        result = {"char_count": count, "image_count": len(paths)}
        if skipped:
            result["skipped_pages"] = len(skipped)
        background_jobs.set_result(job_id, result)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
