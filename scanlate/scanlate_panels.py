"""Comic panels and slicing a long webtoon strip into pages."""

from scanlate import scanlate_tuning
from scanlate.scanlate_page_decode import page_bgr


def detect_panels(image_path: str, min_panel_frac: float = 0.02, image=None):
    """
    Finds comic panels by looking for the gutters (whitespace channels)
    between them. Returns boxes in reading order.

    Useful for two things: translating only the panels you're actually
    looking at, and giving the translator panel-level context instead of
    a flat list of bubbles with no sense of which are in the same beat.
    `image`: the page already decoded as BGR (see page_bgr()).
    """
    import cv2
    import numpy as np

    img = page_bgr(image_path, image)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Panels are bounded by dark borders on a light page (or vice versa);
    # inverting and closing turns panel interiors into solid blobs. Gutters
    # are the paper's own shade, which on a scan or a yellowed page sits well
    # under pure white: a fixed cut at 240 made the whole page one panel.
    edge = max(2, min(h, w) // 50)
    paper = float(np.median(np.concatenate([gray[:edge].ravel(), gray[-edge:].ravel(),
                                            gray[:, :edge].ravel(), gray[:, -edge:].ravel()])))
    cut = min(240.0, paper - scanlate_tuning.PANEL_PAPER_MARGIN)
    # A median blur first: scan noise in a gutter would otherwise close it up.
    _, thresh = cv2.threshold(cv2.medianBlur(gray, 5), cut, 255, cv2.THRESH_BINARY_INV)
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    total_area = h * w
    panels = []
    for c in contours:
        x, y, pw, ph = cv2.boundingRect(c)
        if (pw * ph) < total_area * min_panel_frac:
            continue
        if pw < w * 0.1 or ph < h * 0.03:
            continue
        panels.append({"x": x, "y": y, "w": pw, "h": ph})

    # Manga reading order: top-to-bottom, then right-to-left within a row.
    panels.sort(key=lambda p: (p["y"] // max(int(h * 0.08), 1), -p["x"]))
    return panels


_MAX_WEBTOON_SLICES = 200
_MIN_TAIL_MARGIN_PX = 60


class StripTooLong(ValueError):
    """A strip that would need more than _MAX_WEBTOON_SLICES slices."""


def split_webtoon_strip(image_path: str, target_height: int = 1600, overlap: int = 100):
    """
    Webtoons are single continuous vertical strips, often 10,000+ pixels
    tall. Treating one as a "page" breaks bubble detection (everything
    is tiny relative to the canvas) and blows past image size limits.

    This slices the strip at whitespace gaps near the target height, so
    cuts land between panels rather than through artwork. `overlap`
    keeps a small band shared between adjacent slices so a bubble
    straddling a cut isn't lost.

    Returns a list of {"y_start", "y_end", "index"} describing the
    slices, without writing files -- the caller decides what to save.
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    if h <= target_height:
        return [{"y_start": 0, "y_end": h, "index": 0}]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # A row is "empty" if it's nearly uniform -- that's a gutter.
    row_std = gray.std(axis=1)
    empty_rows = row_std < 6

    slices = []
    y = 0
    idx = 0
    while y < h:
        ideal_end = min(y + target_height, h)
        if ideal_end >= h:
            slices.append({"y_start": y, "y_end": h, "index": idx})
            break
        # Search a window around the ideal cut for the emptiest row.
        search_lo = max(y + int(target_height * 0.6), 0)
        search_hi = min(y + int(target_height * 1.25), h - 1)
        window = empty_rows[search_lo:search_hi]
        cut = ideal_end
        at_gutter = bool(window.any())
        if window.any():
            empty_idxs = np.flatnonzero(window)
            # prefer the empty row closest to the ideal cut point
            cut = search_lo + int(empty_idxs[np.argmin(np.abs(
                (empty_idxs + search_lo) - ideal_end))])
        if h - cut <= overlap + min(_MIN_TAIL_MARGIN_PX, target_height // 4):
            # A tail this short would be a page of just the overlap band.
            slices.append({"y_start": y, "y_end": h, "index": idx})
            break
        slices.append({"y_start": y, "y_end": cut, "index": idx})
        # No lettering crosses a gutter row, so nothing is split there; sharing a
        # band would only show the bubble above the cut twice, the second time
        # clipped, and OCR reads that clipped copy as junk. The band is kept for
        # a cut that had to be made through artwork.
        y = cut if (overlap == 0 or at_gutter) else max(cut - overlap, y + 1)
        idx += 1
        if idx >= _MAX_WEBTOON_SLICES:
            # Stopping here would silently drop the rest of the strip.
            raise StripTooLong(f"This strip needs more than {_MAX_WEBTOON_SLICES} slices; "
                             "split it into shorter images before importing it.")
    return slices
