"""Lettering colour: the hand-set colour, the colour or fade sampled from the original lettering, and the automatic fill."""

import numpy as np
from PIL import Image

from scanlate_inpaint import background_is_dark


def chosen_color(box: dict):
    """The region's hand-set text_color ("#rrggbb") as RGB, or None when it
    has none (or a malformed one, which falls back to the automatic fill)."""
    value = str(box.get("text_color") or "")
    if len(value) == 7 and value[0] == "#":
        try:
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return None
    return None


# Below this spread between the strongest and weakest channel the ink is grey: anti-aliased black
# or white lettering reads slightly tinted and must stay plain black or white.
_INK_MIN_CHROMA = 48
_INK_MIN_PIXELS = 30
_INK_MIN_DISTANCE = 90


def sampled_ink_color(page_bgr, box: dict):
    """The typical colour of the region's original lettering as RGB, or None when it is
    plain black or white (or too little ink was found to say). Taken from the page before
    the lettering is erased."""
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.size == 0:
        return None
    # Ink is whatever differs strongly from the balloon's own colour, so a saturated red on a
    # dark balloon counts although its channel average is not bright.
    background = np.median(roi.reshape(-1, 3), axis=0)
    distance = np.abs(roi.astype(int) - background.astype(int)).max(axis=2)
    ink = distance > _INK_MIN_DISTANCE
    if int(ink.sum()) < _INK_MIN_PIXELS:
        return None
    # Anti-aliased edges blend toward the paper, so the median of all ink pixels reads washed
    # out (a deep orange comes back pale); the pixels farthest from the paper are the pen colour.
    core = ink & (distance >= np.median(distance[ink]))
    b, g, r = (int(v) for v in np.median(roi[core], axis=0))
    if max(r, g, b) - min(r, g, b) < _INK_MIN_CHROMA:
        return None
    return (r, g, b)


# A fade needs the lettering's top and bottom halves to differ clearly and both to be a real colour.
_FADE_MIN_DISTANCE = 55
_FADE_MAX_OFF_LINE = 50
_RING_MIN_SPAN = 0.85
_FADE_MIN_END_CHROMA = 30


def sampled_ink_fade(page_bgr, box: dict):
    """(top, bottom) RGB colours of the region's original lettering when it fades from one real
    colour to another down its height, else None. Read from the page before the erase, like
    sampled_ink_color; a single colour, black or white lettering, or too little ink gives None."""
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.size == 0:
        return None
    import cv2
    background = np.median(roi.reshape(-1, 3), axis=0)
    distance = np.abs(roi.astype(int) - background.astype(int)).max(axis=2)
    # Art, a leaf or an outline around the lettering reaches the box edge; lettering strokes
    # sit inside it, so any stroke component touching the edge is not counted.
    ink = _without_border_components(distance > _INK_MIN_DISTANCE)
    if int(ink.sum()) < 2 * _INK_MIN_PIXELS:
        return None
    rows = np.where(ink.any(axis=1))[0]
    # The outer thirds, not the halves: each half of a fade already averages over half the change.
    third = max(1, (rows.max() - rows.min() + 1) // 3)
    top_end, bottom_end = rows.min() + third, rows.max() + 1 - third
    bands = [slice(0, top_end), slice(top_end, bottom_end), slice(bottom_end, roi.shape[0])]
    colours = []
    for part in bands:
        d, px = distance[part], roi[part]
        # Stroke interiors only: thin marks (a balloon outline, a glow) have none, and an
        # outline of another colour hugs the stroke's edge.
        m = cv2.erode(ink[part].astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        if int(m.sum()) < _INK_MIN_PIXELS:
            # A two-line balloon has no ink in the middle third; that is a step, not a gap to reject.
            colours.append(None)
            continue
        core = m & (d >= np.median(d[m]))
        b, g, r = (int(v) for v in np.median(px[core], axis=0))
        colours.append((r, g, b))
    top, middle, bottom = colours
    if top is None or bottom is None:
        return None
    chromas = [max(c) - min(c) for c in (top, bottom)]
    # A dark end of a real fade is duller than the light end (dark purple to lavender), but a
    # grey end is plain black or white lettering.
    if max(chromas) < _INK_MIN_CHROMA or min(chromas) < _FADE_MIN_END_CHROMA:
        return None
    span = np.array(bottom, float) - np.array(top, float)
    if np.abs(span).max() < _FADE_MIN_DISTANCE:
        return None
    # A real fade passes smoothly through the middle; two unrelated colours (a red rose over a
    # green leaf) do not land between each other.
    if middle is not None:
        along = float(np.dot(np.array(middle, float) - top, span) / np.dot(span, span))
        off = np.abs(np.array(middle, float) - (np.array(top, float) + along * span)).max()
        if not 0.1 <= along <= 0.9 or off > _FADE_MAX_OFF_LINE:
            return None
    return top, bottom


def _without_border_components(ink):
    """`ink` (bool array) minus every connected component that touches the array's edge, and
    minus specks (stray ornament fragments) far smaller than the largest stroke component."""
    import cv2
    _, labels = cv2.connectedComponents(ink.astype(np.uint8), connectivity=8)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    inside = ink & ~np.isin(labels, edge[edge > 0])
    # A balloon outline is one component reaching nearly all the way round the box; lettering
    # is many small ones, so such a ring is not lettering however far it sits from the edge.
    for label in np.unique(labels[inside]):
        ys, xs = np.where(labels == label)
        if (ys.max() - ys.min() + 1) >= _RING_MIN_SPAN * ink.shape[0] and \
                (xs.max() - xs.min() + 1) >= _RING_MIN_SPAN * ink.shape[1]:
            inside &= labels != label
    sizes = np.bincount(labels[inside])
    if sizes.size > 1:
        sizes[0] = 0
        specks = np.where(sizes < 0.08 * sizes.max())[0]
        inside &= ~np.isin(labels, specks)
    return inside


def _fade_lettering(before, after, top, bottom) -> None:
    """Recolours, in place, the lettering drawn between `before` and `after` (the same region
    of the page, PIL images) from the solid `top` colour it was drawn in to a vertical fade
    from `top` to `bottom`. The lettering's own coverage is kept, so its anti-aliased edges
    stay soft."""
    b = np.asarray(before.convert("RGB")).astype(float)
    a = np.asarray(after.convert("RGB")).astype(float)
    away = np.array(top, float) - b
    weight = (a - b) * away
    share = np.clip(weight.sum(axis=2) / np.maximum((away * away).sum(axis=2), 1.0), 0.0, 1.0)
    rows = np.where((share > 0.1).any(axis=1))[0]
    if rows.size < 2:
        return
    t = np.clip((np.arange(b.shape[0]) - rows.min()) / max(rows.max() - rows.min(), 1), 0.0, 1.0)
    colour = np.array(top, float)[None, :] * (1 - t[:, None]) + np.array(bottom, float)[None, :] * t[:, None]
    out = b + share[:, :, None] * (colour[:, None, :] - b)
    after.paste(Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)))


def _sampled_inks(page, regions: list, match_color: bool):
    """Per region, the colour its original lettering was drawn in and the (top, bottom) pair
    when it faded; both None unless `match_color`. A fade starts from its top colour."""
    inks = [sampled_ink_color(page, b) if match_color else None for b in regions]
    fades = [sampled_ink_fade(page, b) if match_color and chosen_color(b) is None else None
             for b in regions]
    return [fade[0] if fade else ink for ink, fade in zip(inks, fades)], fades


def _text_fill(pil_img, box: dict, sampled=None):
    """The hand-set colour if any, then the colour sampled from the original lettering
    (when the person turned that on); else white lettering on a dark balloon,
    black otherwise."""
    chosen = chosen_color(box)
    if chosen is not None:
        return chosen
    if sampled is not None:
        return sampled
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = np.asarray(pil_img.crop((x, y, x + box["w"], y + box["h"])).convert("L"))
    if roi.size and background_is_dark(roi):
        return (255, 255, 255)
    return (0, 0, 0)
