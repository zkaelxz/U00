"""services/comic_panels_service.py -- a stored comic page's panels in reading order, for the reader's panel-by-panel view."""
import threading
from collections import OrderedDict

import db
from lib.errors import NotFoundError
from services import comic_view_service, ownership_service

# Panel finding decodes the page (comic_view_service never does), so it is skipped where
# its answer would go unused or the decode is large: a strip is read by scrolling, and
# no printed page comes near this many pixels.
_MAX_PIXELS = 40_000_000
_STRIP_RATIO = 2
_CACHE_SIZE = 256

_cache: "OrderedDict[tuple, tuple | None]" = OrderedDict()
_cache_lock = threading.Lock()


def _stored_file(drama_id: int, page: dict):
    for field in ("filename", "rendered_filename"):
        found = comic_view_service.safe_file(drama_id, page.get(field))
        if found is not None:
            return found
    return None


def _skipped(page: dict) -> bool:
    w, h = int(page.get("width") or 0), int(page.get("height") or 0)
    return w > 0 and h > 0 and (h >= w * _STRIP_RATIO or w * h > _MAX_PIXELS)


def _find_panels(path: str):
    """(page width, page height, panel boxes in pixels), or None when the page can't be read."""
    try:
        from PIL import Image
        from scanlate.scanlate_page_decode import decode_page
        from scanlate.scanlate_panels import detect_panels
    except ImportError:
        return None
    try:
        image = decode_page(path)
        if image.shape[0] * image.shape[1] > _MAX_PIXELS:
            return None
        boxes = detect_panels(path, image=image)
    # cv2 is imported inside detect_panels, so a missing install is an ImportError here too.
    except (ImportError, ValueError, OSError, Image.DecompressionBombError):
        return None
    return image.shape[1], image.shape[0], boxes


def _cached_panels(path: str, st):
    key = (path, st.st_mtime_ns, st.st_size)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    found = _find_panels(path)
    with _cache_lock:
        _cache[key] = found
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return found


def page_panels(drama_id: int, page_id: int, right_to_left=None) -> dict:
    """{page_id, panels}: fewer than two panels is an empty list, so the reader shows the
    whole page. right_to_left None follows the title's language (manga reads from the right)."""
    drama = ownership_service.require_drama(drama_id)
    page = next((p for p in db.list_pages(drama_id) if p["id"] == page_id), None)
    if page is None:
        raise NotFoundError("No such page in this title.")
    stored = None if _skipped(page) else _stored_file(drama_id, page)
    found = _cached_panels(*stored) if stored else None
    if found is None or len(found[2]) < 2:
        return {"page_id": page_id, "panels": []}
    w, h, boxes = found
    from scanlate import scanlate_tuning
    from scanlate.scanlate_regions import reading_sequence
    if right_to_left is None:
        right_to_left = scanlate_tuning.reads_right_to_left(drama.get("source_language") or "")
    return {"page_id": page_id,
            "panels": [{"x": boxes[i]["x"] / w, "y": boxes[i]["y"] / h,
                        "w": boxes[i]["w"] / w, "h": boxes[i]["h"] / h}
                       for i in reading_sequence(boxes, right_to_left)]}
