"""
services/page_import_limits.py -- the page-upload rules for comic pages
(user decision 2026-09-29), meant to be shared by every path that adds
pages to a drama. Used today by the pasted-URL comic import and its reviewed
import (Sources parity SO06/SO10, services/sources_import_service.py and
services/sources_extraction_service.py) and the Scanlate page upload
(services/scanlate_pages_service.py). Not yet adopted: the S-4 adapter
chapter import (sources/pipeline.py run_import_job's page downloads).
UI-free.

  * Accepted: PNG, JPEG and WebP images (PDF too where a path can receive
    one; the pasted-URL import can't). Anything else is skipped.
  * Per image: MAX_IMAGE_BYTES and MAX_IMAGE_PIXELS, the pixel count read
    from the file header before anything is decoded.
  * Per PDF: MAX_PDF_BYTES and MAX_PDF_PAGES.
  * Per import: MAX_FILES_PER_IMPORT files and MAX_IMPORT_BYTES in all.
  * An image taller than STRIP_SLICE_RATIO times its width is a webtoon
    strip and is cut into pages (scanlate.slice_webtoon_to_files) by
    default.
  * EXIF orientation is applied on import.

An image over a cap is skipped with a plain reason (ImageRejected); it
never fails the whole import. Reasons contain "over the" or "accepted image
type", which sources/adaptive.py treats as hard facts (such an image can't
be classified or marked as a page).
"""

import io
import os
import tempfile

ALLOWED_IMAGE_TYPES = ("PNG", "JPEG", "WEBP")       # Pillow format names
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".pdf")
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
MAX_PDF_BYTES = 300 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_FILES_PER_IMPORT = 300
MAX_IMPORT_BYTES = 1024 * 1024 * 1024
STRIP_SLICE_RATIO = 3.0

_EXT_FOR = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


class ImageRejected(ValueError):
    """An image the page rules skip; str() is the plain reason."""


def check_image(content: bytes) -> tuple:
    """(format, width, height) from the header alone -- nothing is decoded.
    Raises ImageRejected for a type that isn't accepted, a file over
    MAX_IMAGE_BYTES or a picture over MAX_IMAGE_PIXELS."""
    from PIL import Image
    if len(content or b"") > MAX_IMAGE_BYTES:
        raise ImageRejected(f"over the {MAX_IMAGE_BYTES // (1024 * 1024)} MB per-image limit")
    try:
        with Image.open(io.BytesIO(content)) as im:
            fmt, (w, h) = (im.format or "").upper(), im.size
    except Exception:
        raise ImageRejected("not a readable image of an accepted image type") from None
    if fmt not in ALLOWED_IMAGE_TYPES:
        raise ImageRejected(f"{fmt or 'this'} is not an accepted image type (PNG, JPEG or WebP)")
    if w <= 0 or h <= 0:
        raise ImageRejected("not a readable image of an accepted image type")
    if w * h > MAX_IMAGE_PIXELS:
        raise ImageRejected(f"over the {MAX_IMAGE_PIXELS // 1_000_000} megapixel limit "
                            f"({w}x{h})")
    return fmt, w, h


def is_strip(width: int, height: int) -> bool:
    return width > 0 and height > STRIP_SLICE_RATIO * width


def _slices(png: bytes) -> list:
    """A strip cut at whitespace into PNG pages (Scanlate's own slicer), or
    [png] when OpenCV isn't installed or the slicer can't read it."""
    try:
        import scanlate
        with tempfile.TemporaryDirectory() as tmp:
            # A fixed ASCII name: OpenCV can't open non-ASCII paths on Windows.
            path = os.path.join(tmp, "strip.png")
            with open(path, "wb") as f:
                f.write(png)
            out = []
            for p in scanlate.slice_webtoon_to_files(path, tmp):
                with open(p, "rb") as f:
                    out.append(f.read())
            return out or [png]
    except Exception:
        return [png]


def prepare_page(content: bytes, slice_strips: bool = True) -> list:
    """The page files one accepted image becomes: [(bytes, ext)], with EXIF
    orientation applied and, by default, a webtoon strip cut into pages.
    Raises ImageRejected -- the caps are checked before any decode, and a
    file that then fails to decode, rotate or re-encode (truncated, corrupt)
    is rejected too, so one bad image never fails the rest."""
    fmt, _w, _h = check_image(content)
    try:
        return _prepare(content, fmt, slice_strips)
    except ImageRejected:
        raise
    except Exception:
        raise ImageRejected("not a readable image (the file is damaged or incomplete)") from None


def _prepare(content: bytes, fmt: str, slice_strips: bool) -> list:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(content)) as im:
        try:
            rotated = im.getexif().get(0x0112, 1) not in (None, 1)   # EXIF Orientation
        except Exception:
            rotated = False
        if rotated:
            im = ImageOps.exif_transpose(im)
        size = im.size
        if not rotated and not (slice_strips and is_strip(*size)):
            im.load()                     # decodes cleanly, within the pixel cap
            return [(content, _EXT_FOR[fmt])]
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "PNG")
    png = buf.getvalue()
    if slice_strips and is_strip(*size):
        return [(p, ".png") for p in _slices(png)]
    return [(png, ".png")]
