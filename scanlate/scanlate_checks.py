"""Measures a rendered comic page against the original: fill leaking past a balloon, text off-centre, tiny or colliding with a neighbour's, source lettering left behind."""
import numpy as np

# A balloon's own outline is dark and sits just inside the mask's edge; changes within this many
# pixels of the interior are the antialiased edge of the fill, not a leak.
EDGE_SLACK_PX = 3
# Share of a box's area that may change outside its balloon before it counts as a leak.
MAX_LEAK_SHARE = 0.015
# Text may sit this far (share of the balloon's width or height) from the balloon's centre.
MAX_OFF_CENTRE = 0.15
# A letter this short is unreadable on a phone at 900 px page width.
MIN_LETTER_PX = 9
# Dark pixels left in the interior of an erase-only render, as a share of the interior.
MAX_LEFTOVER_SHARE = 0.004
# Two balloons' lettering collides when the area they share is this share of the smaller one.
MAX_TEXT_OVERLAP = 0.1
_DARK = 110
_CHANGED = 40


def _crop(arr, box):
    x, y, w, h = (int(box[k]) for k in ("x", "y", "w", "h"))
    return arr[max(0, y):y + h, max(0, x):x + w]


def leak_share(original, rendered, box: dict, interior) -> float:
    """Share of the box's pixels that changed between `original` and `rendered` outside the
    balloon interior (grown by EDGE_SLACK_PX). Text that overflows the outline and a fill that
    follows the box instead of the balloon both show up here."""
    import cv2

    a, b = _crop(original, box), _crop(rendered, box)
    if a.size == 0 or interior is None:
        return 0.0
    mask = (np.asarray(interior)[:a.shape[0], :a.shape[1]] > 0).astype(np.uint8)
    grown = cv2.dilate(mask, np.ones((2 * EDGE_SLACK_PX + 1,) * 2, np.uint8))
    changed = cv2.absdiff(a, b) > _CHANGED
    return float((changed & (grown == 0)).sum()) / max(a.size, 1)


def text_offset(rendered, box: dict, interior):
    """(dx, dy) of the dark text's bounding box centre from the interior's, as shares of the
    interior's width and height, or None when the interior holds no text."""
    import cv2

    b = _crop(rendered, box)
    mask = (np.asarray(interior)[:b.shape[0], :b.shape[1]] > 0).astype(np.uint8)
    inner = cv2.erode(mask, np.ones((2 * EDGE_SLACK_PX + 5,) * 2, np.uint8))
    ink = (b < _DARK) & (inner > 0)
    if not ink.any() or not mask.any():
        return None
    ys, xs = np.nonzero(ink)
    my, mx = np.nonzero(mask)
    mw, mh = mx.max() - mx.min() + 1, my.max() - my.min() + 1
    dx = ((xs.min() + xs.max()) / 2 - (mx.min() + mx.max()) / 2) / mw
    dy = ((ys.min() + ys.max()) / 2 - (my.min() + my.max()) / 2) / mh
    return float(dx), float(dy)


def median_letter_height(rendered, box: dict, interior):
    """Median height in pixels of the dark marks inside the interior, or None without text."""
    import cv2

    b = _crop(rendered, box)
    mask = (np.asarray(interior)[:b.shape[0], :b.shape[1]] > 0).astype(np.uint8)
    inner = cv2.erode(mask, np.ones((2 * EDGE_SLACK_PX + 5,) * 2, np.uint8))
    ink = ((b < _DARK) & (inner > 0)).astype(np.uint8)
    n, _labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    heights = [int(s[cv2.CC_STAT_HEIGHT]) for s in stats[1:] if s[cv2.CC_STAT_AREA] >= 4]
    return float(np.median(heights)) if heights else None


def leftover_share(erased_only, box: dict, interior) -> float:
    """Share of the interior still dark in a render that drew no new text: source lettering the
    erase missed."""
    import cv2

    b = _crop(erased_only, box)
    mask = (np.asarray(interior)[:b.shape[0], :b.shape[1]] > 0).astype(np.uint8)
    inner = cv2.erode(mask, np.ones((2 * EDGE_SLACK_PX + 5,) * 2, np.uint8))
    if not inner.any():
        return 0.0
    return float(((b < _DARK) & (inner > 0)).sum()) / float(inner.sum())


def region_problems(original, rendered, erased_only, box: dict, interior) -> list:
    """The names of the checks this region fails, for the report and the tests."""
    problems = []
    if leak_share(original, rendered, box, interior) > MAX_LEAK_SHARE:
        problems.append("fill-outside-balloon")
    offset = text_offset(rendered, box, interior)
    if offset is not None and (abs(offset[0]) > MAX_OFF_CENTRE or abs(offset[1]) > MAX_OFF_CENTRE):
        problems.append("off-centre")
    height = median_letter_height(rendered, box, interior)
    if height is not None and height < MIN_LETTER_PX:
        problems.append("tiny-text")
    if erased_only is not None and leftover_share(erased_only, box, interior) > MAX_LEFTOVER_SHARE:
        problems.append("source-left")
    return problems


def text_extent(rendered, erased_only, box: dict):
    """(x0, y0, x1, y1) in page pixels of the English drawn in `box`: dark pixels that the render
    added to the erase-only page, or all dark ones when there is none. None when nothing was drawn."""
    b = _crop(rendered, box)
    ink = b < _DARK
    if erased_only is not None:
        ink &= _crop(erased_only, box)[:b.shape[0], :b.shape[1]] >= _DARK
    if not ink.any():
        return None
    ys, xs = np.nonzero(ink)
    x0, y0 = max(0, int(box["x"])), max(0, int(box["y"]))
    return x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1


def colliding_texts(extents: list) -> set:
    """Indexes of the extents (None skipped) that share an area with another's of at least
    MAX_TEXT_OVERLAP of the smaller one: two balloons whose English runs into each other."""
    hit = set()
    for i, a in enumerate(extents):
        for j in range(i + 1, len(extents)):
            b = extents[j]
            if a is None or b is None:
                continue
            w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
            smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
            if w > 0 and h > 0 and w * h >= MAX_TEXT_OVERLAP * smaller:
                hit.update((i, j))
    return hit
