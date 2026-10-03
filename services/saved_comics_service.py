"""
services/saved_comics_service.py -- reading the chapters saved as CBZ files
(services/sources_save_service.py) inside the app.

The save folder is laid out `<source>/<series>/<chapter>.cbz`. A series is
named by its source and series folder names, a chapter by its file name
without `.cbz`; those names are all the API takes or returns, never a path.
Each name must be one plain path component that exists under the save
folder, and the resolved file must still be inside it (a link pointing
elsewhere is refused), so a request can't reach any other file.

Pages are a CBZ's image members (jpg/png/webp/gif/avif), in natural name
order, skipping folders such as `__MACOSX`. A member over MAX_PAGE_BYTES, or
a CBZ with more than MAX_PAGES images, is refused rather than read. Page
sizes come from the image headers and are cached per file and mtime.
"""

import os
import re
import threading
import zipfile

from services import sources_save_service as saves
from services.service_errors import InvalidInputError, NotFoundError

MAX_NAME_LEN = 255
MAX_PAGES = 2000
MAX_PAGE_BYTES = 64 * 1024 * 1024
_MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}
_CHAPTER_NAME = re.compile(r"(\d{1,6}) (.+)")
_CACHE_LOCK = threading.Lock()
_SIZES = {}      # (path, mtime_ns) -> [(width, height)]
_MAX_CACHED = 64


def _natural(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _component(value, what: str) -> str:
    text = value if isinstance(value, str) else ""
    if (not text or len(text) > MAX_NAME_LEN or text in (".", "..") or text.startswith(".")
            or any(c in text for c in "/\\\0") or any(ord(c) < 32 for c in text)):
        raise InvalidInputError(f"{what} is not a saved {what}.")
    return text


def _inside(root: str, path: str) -> bool:
    real_root = os.path.realpath(root)
    return os.path.commonpath([real_root, os.path.realpath(path)]) == real_root


def _series_dir(root: str, source: str, series: str) -> str:
    path = os.path.join(root, _component(source, "source"), _component(series, "series"))
    if not os.path.isdir(path) or not _inside(root, path):
        raise NotFoundError("No such saved series.")
    return path


def _chapter_file(root: str, source: str, series: str, chapter: str) -> str:
    path = os.path.join(_series_dir(root, source, series), _component(chapter, "chapter") + ".cbz")
    if not os.path.isfile(path) or not _inside(root, path):
        raise NotFoundError("No such saved chapter.")
    return path


def _dirs(path: str) -> list:
    try:
        names = os.listdir(path)
    except OSError:
        return []
    return sorted((n for n in names if not n.startswith(".")
                   and os.path.isdir(os.path.join(path, n))), key=_natural)


def _chapters_in(path: str) -> list:
    try:
        names = os.listdir(path)
    except OSError:
        return []
    return sorted((n[:-4] for n in names if n.lower().endswith(".cbz") and not n.startswith(".")
                   and os.path.isfile(os.path.join(path, n))), key=_natural)


def _chapter_view(stem: str) -> dict:
    m = _CHAPTER_NAME.fullmatch(stem)
    return {"chapter": stem, "title": m.group(2) if m else stem,
            "number": int(m.group(1)) if m else None}


def list_series() -> list:
    """Every saved series: source and series names, chapter count and when
    a chapter was last added."""
    root = saves.save_root()
    out = []
    for source in _dirs(root):
        for series in _dirs(os.path.join(root, source)):
            folder = os.path.join(root, source, series)
            chapters = _chapters_in(folder)
            if not chapters:
                continue
            try:
                updated = max(os.path.getmtime(os.path.join(folder, c + ".cbz")) for c in chapters)
            except OSError:
                updated = None
            out.append({"source": source, "series": series, "chapter_count": len(chapters),
                        "updated_at": updated})
    return out


def list_chapters(source, series) -> dict:
    root = saves.save_root()
    folder = _series_dir(root, source, series)
    return {"source": source, "series": series,
            "chapters": [_chapter_view(c) for c in _chapters_in(folder)]}


def _image_members(zf: zipfile.ZipFile) -> list:
    members = [i for i in zf.infolist()
               if not i.is_dir() and os.path.splitext(i.filename)[1].lower() in _MEDIA_TYPES
               and not any(part.startswith(("__MACOSX", ".")) for part in i.filename.split("/"))]
    if len(members) > MAX_PAGES:
        raise InvalidInputError(f"This chapter has more than {MAX_PAGES} pages.")
    return sorted(members, key=lambda i: _natural(i.filename))


def _open(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise NotFoundError("This saved chapter can't be read (the file is damaged).") from None


def _sizes(path: str, zf: zipfile.ZipFile, members: list) -> list:
    key = (path, os.stat(path).st_mtime_ns)
    with _CACHE_LOCK:
        if key in _SIZES:
            return _SIZES[key]
    from PIL import Image
    sizes = []
    for info in members:
        try:
            with zf.open(info) as fh, Image.open(fh) as img:
                sizes.append(img.size)
        except Exception:
            sizes.append((None, None))
    with _CACHE_LOCK:
        if len(_SIZES) >= _MAX_CACHED:
            _SIZES.pop(next(iter(_SIZES)))
        _SIZES[key] = sizes
    return sizes


def chapter_pages(source, series, chapter) -> dict:
    """The chapter's pages (1-based, with sizes when readable) and the
    chapters before and after it in the series, for paging between them."""
    root = saves.save_root()
    path = _chapter_file(root, source, series, chapter)
    with _open(path) as zf:
        members = _image_members(zf)
        sizes = _sizes(path, zf, members)
    stems = _chapters_in(os.path.dirname(path))
    i = stems.index(chapter) if chapter in stems else -1
    return {**_chapter_view(chapter), "source": source, "series": series,
            "pages": [{"page": n, "width": w, "height": h}
                      for n, (w, h) in enumerate(sizes, start=1)],
            "prev_chapter": stems[i - 1] if i > 0 else None,
            "next_chapter": stems[i + 1] if 0 <= i < len(stems) - 1 else None}


def page_image(source, series, chapter, page: int):
    """(bytes, media type, mtime) of one page, 1-based."""
    root = saves.save_root()
    path = _chapter_file(root, source, series, chapter)
    with _open(path) as zf:
        members = _image_members(zf)
        if not isinstance(page, int) or not 1 <= page <= len(members):
            raise NotFoundError("No such page.")
        info = members[page - 1]
        if info.file_size > MAX_PAGE_BYTES:
            raise InvalidInputError("This page's image is too large to show.")
        data = zf.read(info)
    return data, _MEDIA_TYPES[os.path.splitext(info.filename)[1].lower()], os.path.getmtime(path)
