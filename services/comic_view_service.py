"""
services/comic_view_service.py -- read-only comic viewer data for one
drama's page images (Migration comic viewer slice; the read subset of
Scanlate S1): the page list, one page's image file, one page's text
regions, and the page-based resume point.

Security:
- A client never supplies a path. A page is looked up by id inside
  `db.list_pages(drama_id)`, so another drama's page id is a 404.
- The stored filename is joined to the drama folder and must resolve
  (realpath) to exactly the same path: no `..` escape, no symlink at any
  level below the drama folder. The file itself must be a regular file,
  png/jpg/jpeg by extension, at most MAX_IMAGE_BYTES, and start with a
  PNG or JPEG signature; the content type comes from those bytes.
- The image is never decoded (no PIL), and width/height come from the DB.
- Every image refusal is the same generic NotFoundError with no path.
Returns plain dicts; no FastAPI import.
"""
import os
import stat

import comic_chapters
import db
from services.service_errors import InvalidInputError, NotFoundError

MAX_IMAGE_BYTES = 50 * 1024 * 1024
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
VARIANTS = ("original", "rendered")
_VARIANT_FIELD = {"original": "filename", "rendered": "rendered_filename"}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
_PAGED_MEDIA_TYPES = ("manga",)
_IMAGE_MISSING = "Page image not found."


def _require_drama(drama_id: int) -> dict:
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    return drama


def _find_page(drama_id: int, page_id: int, message: str = "No such page in this title."):
    """(ordinal, page row, page count). The page must belong to this drama."""
    pages = db.list_pages(drama_id)
    for i, page in enumerate(pages):
        if page["id"] == page_id:
            return i + 1, page, len(pages)
    raise NotFoundError(message)


def _visible(bubble: dict) -> bool:
    """Same rule as scanlate.region_excluded_from_auto, plus skipped
    bubbles (false positives). Copied to avoid importing scanlate's
    image stack here -- keep in sync by hand."""
    if bubble.get("skip"):
        return False
    return not (bubble.get("kind") == "sfx" and not bubble.get("include_sfx"))


def safe_file(drama_id: int, name):
    """(real path, os.stat_result) for a stored page file, or None."""
    if not name or not isinstance(name, str) or "\x00" in name:
        return None
    if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
        return None
    base = os.path.realpath(db.drama_dir(drama_id))
    if os.path.isabs(name):
        return None
    joined = os.path.normpath(os.path.join(base, name))
    real = os.path.realpath(joined)
    try:
        inside = os.path.commonpath([base, real]) == base
    except ValueError:   # different Windows drives, or mixed absolute/relative
        inside = False
    # real != joined means a symlink somewhere below the drama folder.
    if not inside or real == base or real != joined:
        return None
    try:
        st = os.lstat(real)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    if st.st_size <= 0 or st.st_size > MAX_IMAGE_BYTES:
        return None
    return real, st


def _sniff(path: str):
    """Content type from the file's first bytes, or None."""
    try:
        with open(path, "rb") as f:
            head = f.read(len(_PNG_MAGIC))
    except OSError:
        return None
    if head.startswith(_PNG_MAGIC):
        return "image/png"
    if head.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return None


def list_pages(drama_id: int) -> dict:
    """C1: the page list. No filenames or paths; `image_version` is the
    newest file mtime (ms) and busts caches when typeset output is
    overwritten in place."""
    drama = _require_drama(drama_id)
    visible_counts = {}
    for b in db.list_bubbles_for_drama(drama_id):
        if _visible(b):
            visible_counts[b["page_id"]] = visible_counts.get(b["page_id"], 0) + 1
    pages = []
    rows = db.list_pages(drama_id)
    manifest = comic_chapters.load(drama_id)
    groups = comic_chapters.group_pages(rows, manifest)
    owner = {name: (g["id"], g["first_page"]) for g in groups for name in g["filenames"]}
    hidden = set(manifest["hidden"])
    for i, page in enumerate(rows):
        gid, first = owner.get(page.get("filename"), (None, 1))
        original = safe_file(drama_id, page.get("filename"))
        rendered = safe_file(drama_id, page.get("rendered_filename"))
        mtimes = [int(f[1].st_mtime * 1000) for f in (original, rendered) if f]
        pages.append({
            "id": page["id"], "ordinal": i + 1,
            "width": int(page.get("width") or 0), "height": int(page.get("height") or 0),
            "has_rendered": rendered is not None,
            "has_regions": visible_counts.get(page["id"], 0) > 0,
            "image_version": max(mtimes) if mtimes else 0,
            "chapter_id": gid, "chapter_page": i + 2 - first if gid else i + 1,
            "hidden": page.get("filename") in hidden,
        })
    media_type = drama.get("media_type") or "other"
    return {"drama_id": drama_id, "media_type": media_type,
            "reading_mode_default": "paged" if media_type in _PAGED_MEDIA_TYPES else "vertical",
            "page_count": len(pages), "pages": pages,
            "hidden_count": sum(1 for p in pages if p["hidden"]),
            "chapters": [{k: v for k, v in g.items() if k not in ("filenames", "chapter_id")}
                         for g in groups]}


def resolve_page_image(drama_id: int, page_id: int, variant: str = "original") -> dict:
    """C2: {path, content_type, ordinal, stat} for the router's
    FileResponse, or a generic NotFoundError. The path never reaches a
    response body."""
    if variant not in VARIANTS:
        raise InvalidInputError("variant must be 'original' or 'rendered'.")
    if db.get_drama(drama_id) is None:
        raise NotFoundError(_IMAGE_MISSING)
    ordinal, page, _count = _find_page(drama_id, page_id, _IMAGE_MISSING)
    found = safe_file(drama_id, page.get(_VARIANT_FIELD[variant]))
    if found is None:
        raise NotFoundError(_IMAGE_MISSING)
    path, st = found
    ctype = _sniff(path)
    if ctype is None:
        raise NotFoundError(_IMAGE_MISSING)
    return {"path": path, "content_type": ctype, "ordinal": ordinal, "stat": st}


def get_page_regions(drama_id: int, page_id: int) -> dict:
    """C3: the page's visible text regions in reading order. Keyed by
    reading-order idx, not bubble id (ids change on every save)."""
    _require_drama(drama_id)
    _ordinal, page, _count = _find_page(drama_id, page_id)
    regions = [{"idx": int(b.get("idx") or 0),
                "x": int(b.get("x") or 0), "y": int(b.get("y") or 0),
                "w": int(b.get("w") or 0), "h": int(b.get("h") or 0),
                "translated_text": b.get("translated_text") or "",
                "source_text": b.get("source_text") or "",
                "kind": b.get("kind") or "bubble"}
               for b in db.load_bubbles(page["id"]) if _visible(b)]
    return {"page_id": page["id"], "width": int(page.get("width") or 0),
            "height": int(page.get("height") or 0), "regions": regions}


def _progress_view(drama_id: int) -> dict:
    row = db.get_progress(drama_id) or {}
    return {"last_page": int(row.get("last_page") or 1),
            "percent_complete": float(row.get("percent_complete") or 0.0)}


def get_progress(drama_id: int) -> dict:
    """C4: the page resume point (default household profile, shared by
    every API user, like the Reader)."""
    _require_drama(drama_id)
    return _progress_view(drama_id)


def save_progress(drama_id: int, page: int) -> dict:
    """C5: writes only last_page and percent_complete (never
    last_line_idx or the audio position)."""
    _require_drama(drama_id)
    count = len(db.list_pages(drama_id))
    if not isinstance(page, int) or page < 1 or page > count:
        raise InvalidInputError("page is past the last page." if count else
                                "This title has no pages.")
    db.save_progress(drama_id, last_page=page,
                     percent_complete=round(page / count * 100, 2))
    return _progress_view(drama_id)
