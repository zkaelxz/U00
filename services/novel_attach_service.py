"""
services/novel_attach_service.py -- attach novel text to a drama and OCR
chapter images for a novel-narration drama. Text
lands in `novel_narration_source.txt` (the
file `narration_service` and `cli.cmd_dub` read).

Only extracted plain text is ever stored: an uploaded EPUB is opened with
the stdlib zip/HTML parsers (no ebooklib, no entity or external-resource
resolution), capped on entry count and total uncompressed size, and
rejected on path traversal, absolute names or symlink entries. Nothing is
extracted to disk and the .epub itself is not kept. Client filenames are
never used or returned; OCR images are staged under generated names in a
private temp folder that is always removed. No path is ever returned.

No FastAPI import: takes binary file-like objects.
"""
import importlib.util
import io
import os
import posixpath
import shutil
import stat
import tempfile
import zipfile
import zlib
from html.parser import HTMLParser
from typing import Optional
from xml.etree import ElementTree

try:
    import lzma
except ImportError:  # Python built without lzma: zipfile raises RuntimeError instead
    lzma = None

import background_jobs
import db
import dub_narration
import ocr as ocr_module
from services import drama_service, settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)

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
_BACKEND_MODULES = {"tesseract": "pytesseract", "paddle": "paddleocr", "manga_ocr": "manga_ocr"}
_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
_SKIP_TAGS = {"script", "style", "head"}
_BAD_ENTRY_ERRORS = (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError,
                     OSError, EOFError)
if lzma is not None:  # a corrupt LZMA (method 14) entry raises LZMAError, not OSError
    _BAD_ENTRY_ERRORS += (lzma.LZMAError,)


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return drama


def _check_idle(drama_id: int):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError("A job is running for this title. Wait for it to finish or cancel it.")


def _novel_path(drama_id: int, create: bool) -> str:
    base = db.drama_dir(drama_id) if create else os.path.join(db.DRAMAS_DIR, str(drama_id))
    return os.path.join(base, dub_narration.NOVEL_SOURCE_FILENAME)


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
        raise InvalidInputError("The combined novel text is too large.")
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
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()


def _check_mode(mode):
    if mode not in _MODES:
        raise InvalidInputError(f"mode must be one of {', '.join(_MODES)}.")


def attach_text(drama_id: int, text: str, mode: str = "replace") -> dict:
    _require_drama(drama_id)
    _check_mode(mode)
    text = _clean(text)
    if not text:
        raise InvalidInputError("The novel text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidInputError("The novel text is too large.")
    _check_idle(drama_id)
    return {"char_count": _write_novel(drama_id, text, mode)}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _html_to_text(raw: bytes) -> str:
    parser = _TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    lines = [ln.strip() for ln in "".join(parser.parts).split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _safe_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        return False
    return ".." not in name.split("/")


def _spine_order(zf, names) -> list:
    """Reading order from the OPF spine; falls back to sorted names."""
    try:
        container = zf.read("META-INF/container.xml")
        if b"<!ENTITY" in container:
            raise ValueError
        opf_name = ElementTree.fromstring(container).find(".//{*}rootfile").get("full-path")
        if not _safe_name(opf_name) or opf_name not in names:
            raise ValueError
        opf = zf.read(opf_name)
        if b"<!ENTITY" in opf:
            raise ValueError
        root = ElementTree.fromstring(opf)
        manifest = {i.get("id"): i.get("href") for i in root.iterfind(".//{*}manifest/{*}item")}
        base = posixpath.dirname(opf_name)
        ordered = []
        for ref in root.iterfind(".//{*}spine/{*}itemref"):
            href = manifest.get(ref.get("idref"))
            if href:
                full = posixpath.normpath(posixpath.join(base, href.split("#")[0]))
                if full in names and full not in ordered:
                    ordered.append(full)
        if ordered:
            return ordered
    except Exception:
        pass
    return sorted(n for n in names if n.lower().endswith(_HTML_EXTENSIONS))


def extract_epub_text(fileobj) -> str:
    """Safe plain-text extraction; raises InvalidInputError for anything
    that is not a well-formed, in-bounds EPUB."""
    return "\n\n".join(extract_epub_chapters(fileobj))


def extract_epub_chapters(fileobj) -> list:
    """The EPUB's text as one entry per reading-order document that has any
    text (the chapters a range picks from). Same checks as extract_epub_text."""
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
            if not _safe_name(i.filename) or stat.S_ISLNK(i.external_attr >> 16):
                raise InvalidInputError("That EPUB contains an unsafe entry.")
        names = {i.filename for i in infos if not i.is_dir()}
        chunks = []
        for name in _spine_order(zf, names):
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
                chunks.append(text)
    return chunks


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
    (1-based, inclusive; inventory S13). Returns {char_count, epub_chapters,
    chapter_from, chapter_to}."""
    _require_drama(drama_id)
    _check_mode(mode)
    _check_idle(drama_id)
    return _attach_epub_data(drama_id, fileobj, mode, chapter_from, chapter_to)


def attach_epub_from_job(drama_id: int, fileobj, mode: str = "replace") -> dict:
    """attach_epub for a job that already holds this drama (the
    lightnovel-crawler import, services/lncrawl_service.py): the same EPUB
    limits, without the "a job is running" refusal its own job would hit."""
    _require_drama(drama_id)
    _check_mode(mode)
    return _attach_epub_data(drama_id, fileobj, mode, None, None)


def _attach_epub_data(drama_id, fileobj, mode, chapter_from, chapter_to) -> dict:
    data = fileobj.read(MAX_EPUB_BYTES + 1)
    if len(data) > MAX_EPUB_BYTES:
        raise InvalidInputError("The uploaded EPUB is too large.")
    if not data:
        raise InvalidInputError("The uploaded file is empty.")
    chapters = extract_epub_chapters(io.BytesIO(data))
    if not chapters:
        raise InvalidInputError("No readable text was found in that EPUB.")
    start, end = _check_range(chapter_from, chapter_to, len(chapters))
    text = _clean("\n\n".join(chapters[start - 1:end]))
    if not text:
        raise InvalidInputError("No readable text was found in that EPUB.")
    return {"char_count": _write_novel(drama_id, text, mode), "epub_chapters": len(chapters),
            "chapter_from": start, "chapter_to": end}


RAW_NOVEL_FILENAME = "raw_novel_context.txt"  # sources/pipeline.save_novel_text


def attach_from_sources(drama_id: int, mode: str = "replace") -> dict:
    """Uses the chapters imported in Sources (or saved as the original novel),
    `raw_novel_context.txt`, as the narration text (inventory S12). 404
    when there is none."""
    _require_drama(drama_id)
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
    _check_idle(drama_id)
    return {"char_count": _write_novel(drama_id, text, mode)}


def _job_id(drama_id: int) -> str:
    return f"ocrchapter_{drama_id}"


def get_status(drama_id: int) -> dict:
    _require_drama(drama_id)
    text = _read_novel(drama_id)
    job = background_jobs.get_status(_job_id(drama_id))
    return {"drama_id": drama_id, "has_novel_text": bool(text.strip()),
            "char_count": len(text),
            "chapters": len([p for p in text.split("\n\n") if p.strip()]),
            "ocr_running": bool(job and job["status"] in ("running", "queued"))}


def start_ocr_chapter(drama_id: int, images, backend: str = "tesseract",
                      mode: str = "append",
                      tesseract_cmd: Optional[str] = None) -> dict:
    """images: list of (client_filename, fileobj). Stages them under
    generated names, then starts the job that OCRs them in order and writes
    the text. Returns {"job_id"}. tesseract_cmd None: the Settings
    Tesseract path."""
    drama = _require_drama(drama_id)
    _check_mode(mode)
    language = drama.get("source_language") or "zh"
    tesseract_cmd = tesseract_cmd or settings_service.get_tesseract_cmd()
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
    if importlib.util.find_spec(_BACKEND_MODULES[backend]) is None:
        raise DependencyUnavailableError(f"The {backend} OCR backend is not installed.")
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
        started = background_jobs.start_job(
            job_id, _run_ocr_job, job_id, drama_id, stage, paths, backend, mode, language,
            drama.get("chinese_script") or "simplified", tesseract_cmd or None,
            gpu_touching=True,
            description=f"Chapter OCR (title {drama_id})")
        if not started:
            raise ConflictError("A chapter OCR run is already active for this title.")
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {"job_id": job_id}


def _raise_if_cancelled(job_id):
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)


def _run_ocr_job(job_id, drama_id, stage, paths, backend, mode, language, script,
                 tesseract_cmd=None):
    skipped = []
    try:
        background_jobs.update_progress(job_id, 0.1, "Running OCR...")
        text = _clean(ocr_module.extract_text_from_images(
            paths, backend=backend, source_language=language, chinese_script=script,
            tesseract_cmd=tesseract_cmd, before_page=lambda: _raise_if_cancelled(job_id),
            on_skip=skipped.append))
        if not text:
            background_jobs.set_result(job_id, {"failed_reason": "empty"})
            return
        background_jobs.update_progress(job_id, 0.9, "Saving text...")
        _raise_if_cancelled(job_id)
        count = _write_novel(drama_id, text, mode)
        result = {"char_count": count, "image_count": len(paths)}
        if skipped:
            result["skipped_pages"] = len(skipped)
        background_jobs.set_result(job_id, result)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
