"""services/comic_reader_copy_service.py -- the smaller JPEG copy of a comic page the viewer loads.

Kept apart from comic_view_service, which never decodes an image: only this
module opens page files, and only to make the copy. No FastAPI import.
"""

import os
import tempfile
import threading

import db

_FOLDER = "reader_cache"
_QUALITY = 85
# Low-data copy for a phone on mobile data: about a fifth of the bytes of the reader copy. The
# aspect ratio is kept, so region overlays (percentages of the page's stored size) still line up.
_LOW_QUALITY = 60
_LOW_MAX_WIDTH = 720
# A tall webtoon page decodes to tens of MB; several readers opening a chapter
# at once would otherwise decode that many pages together on the PC.
_DECODE_SLOTS = threading.BoundedSemaphore(2)


def reader_copy(drama_id: int, found: dict, low: bool = False) -> dict:
    """`found` (from comic_view_service.resolve_page_image) with the path swapped
    for a JPEG copy of the page, made once and kept in the drama's folder. A
    webtoon page is a 1-3 MB PNG, and over a household or remote link the viewer
    showed it as a slow top-down paint. The picture keeps its size, so region
    overlays still line up. `found` itself is returned when the copy can't be
    made or isn't smaller. A page rewritten in place changes its mtime and size,
    which names a new copy, and the older copies of that page are removed.
    `low` makes the smaller, narrower low-data copy, kept beside the reader copy."""
    path, st = found["path"], found["stat"]
    folder = os.path.join(db.drama_dir(drama_id), _FOLDER)
    stem = os.path.splitext(os.path.basename(path))[0]
    stamp = f"{stem}.{st.st_mtime_ns}.{st.st_size}"
    copy = os.path.join(folder, f"{stamp}{'.low' if low else ''}.jpg")
    try:
        if not os.path.isfile(copy):
            with _DECODE_SLOTS:
                # Another request may have made it while this one waited.
                if not os.path.isfile(copy):
                    _write_copy(path, folder, stamp, copy, low)
        copy_stat = os.stat(copy)
    except Exception:
        # A page Pillow can't decode (or refuses as a decompression bomb) is still
        # shown, as the original; the viewer must never fail on the copy.
        return found
    if copy_stat.st_size >= st.st_size:
        return found
    return {**found, "path": copy, "stat": copy_stat, "content_type": "image/jpeg"}


def _write_copy(path: str, folder: str, stamp: str, copy: str, low: bool) -> None:
    from PIL import Image
    os.makedirs(folder, exist_ok=True)
    with Image.open(path) as img:
        img.load()
        if img.mode != "RGB":
            rgba = img.convert("RGBA")
            img = Image.new("RGB", rgba.size, "white")
            img.paste(rgba, mask=rgba.getchannel("A"))
        if low and img.width > _LOW_MAX_WIDTH:
            img = img.resize((_LOW_MAX_WIDTH, max(1, round(img.height * _LOW_MAX_WIDTH / img.width))), Image.Resampling.LANCZOS)
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                img.save(fh, "JPEG", quality=_LOW_QUALITY if low else _QUALITY, optimize=True, progressive=True)
            os.replace(tmp, copy)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    stem = stamp.rsplit(".", 2)[0]
    for name in os.listdir(folder):
        old = os.path.join(folder, name)
        # Only copies of an older version of the page: the other size of this one stays.
        if name.startswith(stem + ".") and name.endswith(".jpg") and not name.startswith(stamp + "."):
            try:
                os.remove(old)
            except OSError:
                pass
