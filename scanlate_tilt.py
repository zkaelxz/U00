"""Owns reading how far a balloon's original lettering is tilted and setting the English at that angle."""
import math

import numpy as np
from PIL import Image

import scanlate_tuning
from scanlate_inpaint import _lettering_mask


def estimate_text_angle(page_bgr, box: dict) -> float:
    """Slant of the region's original lettering in degrees, positive when the line runs
    down to the right on screen; 0.0 unless it is a single line that is clearly tilted.
    Read from the page before the lettering is erased. Vertical columns and multi-line
    blocks have no reliable baseline, so they stay at 0 and are lettered as before."""
    import cv2
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.shape[0] < 8 or roi.shape[1] < 8:
        return 0.0
    ink = _lettering_mask(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)) > 0
    # A mark that touches the crop's edge and is long is the balloon outline or the art; one
    # glyph-sized is lettering that the box was cut tight around, which is common on narrow strips.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    longest = max(roi.shape[:2]) * scanlate_tuning.TILT_GLYPH_MAX_OF_BOX
    for i in range(1, count):
        left, top, width, height = stats[i, :4]
        touches = left == 0 or top == 0 or left + width == roi.shape[1] or top + height == roi.shape[0]
        if touches and max(width, height) > longest:
            ink[labels == i] = False
    ys, xs = np.nonzero(ink)
    if len(xs) < scanlate_tuning.TILT_MIN_INK_PX:
        return 0.0
    eigenvalues, vectors = np.linalg.eigh(np.cov(np.vstack([xs, ys])))
    if eigenvalues[0] <= 0 or math.sqrt(eigenvalues[1] / eigenvalues[0]) < scanlate_tuning.TILT_MIN_ELONGATION:
        return 0.0
    angle = math.degrees(math.atan2(vectors[1, 1], vectors[0, 1]))
    angle = (angle + 90) % 180 - 90
    if not scanlate_tuning.TILT_MIN_DEG <= abs(angle) <= scanlate_tuning.TILT_MAX_DEG:
        return 0.0
    # Two or three level lines, staggered, also read as a slanted blob. A single tilted line
    # smears across the rows in one band; separate bands mean it is a block of level lines.
    if _row_bands(ys) > 1:
        return 0.0
    return angle


def _row_bands(ys) -> int:
    """How many separate bands of rows the ink occupies: a row counts when it holds at least
    TILT_BAND_ROW_SHARE of the busiest row's ink, and a band thinner than 3 rows is noise."""
    rows = np.bincount(ys - ys.min())
    busy = rows >= scanlate_tuning.TILT_BAND_ROW_SHARE * rows.max()
    bands, run = 0, 0
    for row in list(busy) + [False]:
        if row:
            run += 1
        else:
            bands += run >= 3
            run = 0
    return bands


def draw_tilted(pil_img, box: dict, mask, angle: float, size: int, draw_text) -> bool:
    """Sets the text at `angle` (see estimate_text_angle) over the page and returns True,
    or returns False with the page untouched when the tilted text would not stay inside
    the balloon, so the caller letters it level instead.
    `draw_text(target, box_in_target, size, mask_in_target)` draws the text level into a copy
    of the box."""
    if mask is not None and _draw_in_text_frame(pil_img, box, mask, angle, size, draw_text):
        return True
    # No shape (or none that fits in the text's own frame): the level layout of the box,
    # turned, and a little smaller when the turn pushes it out of the box.
    w, h = box["w"], box["h"]
    pad = max(w, h) // 2 + 2
    for tried in (size, int(size * 0.9), int(size * 0.8), int(size * 0.7)):
        if tried < scanlate_tuning.TYPESET_SMALLEST_PX:
            break
        room = np.zeros((h + 2 * pad, w + 2 * pad), bool)
        room[pad:pad + h, pad:pad + w] = True if mask is None else mask
        if _place_turned(pil_img, box["x"] - pad, box["y"] - pad, (w + 2 * pad, h + 2 * pad),
                         dict(box, x=pad, y=pad), mask, room, angle, tried, draw_text,
                         (pad + w / 2, pad + h / 2)):
            return True
    return False


def _draw_in_text_frame(pil_img, box: dict, mask, angle: float, size: int, draw_text) -> bool:
    """Lays the text out against the balloon turned level, so a slanted strip gives the text
    its full length as one line instead of a narrow level box, then turns the result back."""
    w, h = box["w"], box["h"]
    level = Image.fromarray(mask.astype(np.uint8) * 255).rotate(angle, resample=Image.NEAREST, expand=True)
    level_mask = np.asarray(level) > 127
    ww, hh = level.size
    # The frame's centre is the box's centre, so the result turns back onto the box.
    room = np.asarray(Image.fromarray(level_mask.astype(np.uint8) * 255).rotate(
        -angle, resample=Image.NEAREST)) > 127
    return _place_turned(pil_img, round(box["x"] + w / 2 - ww / 2), round(box["y"] + h / 2 - hh / 2),
                         (ww, hh), {"x": 0, "y": 0, "w": ww, "h": hh}, level_mask, room, angle, size,
                         draw_text, (ww / 2, hh / 2))


def _place_turned(pil_img, x: int, y: int, frame: tuple, frame_box: dict, mask_in_frame, room, angle: float,
                  size: int, draw_text, centre) -> bool:
    """Draws the text level in a `frame`-sized layer, turns it by `angle` about `centre`, and
    blends it onto the page at (x, y) when it stays inside `room` and is not clipped."""
    # The text is drawn on white and on black, which gives its exact coverage and colour
    # without fringes: over black it is coverage * colour.
    layers = []
    for ground in (255, 0):
        layer = Image.new("RGB", frame, (ground,) * 3)
        draw_text(layer, frame_box, size, mask_in_frame)
        layers.append(np.asarray(layer).astype(np.float32))
    white, black = layers
    coverage = 1.0 - (white - black).mean(axis=2) / 255.0
    turned = [np.asarray(Image.fromarray(a).rotate(-angle, resample=Image.BICUBIC, center=centre))
              for a in (coverage.astype(np.float32), black[:, :, 0], black[:, :, 1], black[:, :, 2])]
    alpha = np.clip(turned[0], 0.0, 1.0)
    ink = alpha > 0.2
    if not ink.any() or ink.sum() < 0.98 * (coverage > 0.2).sum():
        return False
    if (ink & ~room).sum() > scanlate_tuning.TILT_MAX_OUTSIDE_SHARE * ink.sum():
        return False
    _composite(pil_img, x, y, alpha, np.dstack(turned[1:]))
    return True


def _composite(pil_img, x: int, y: int, alpha, premultiplied) -> None:
    """Blends the layer onto the page at (x, y), clipped to the page."""
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(pil_img.width, x + alpha.shape[1]), min(pil_img.height, y + alpha.shape[0])
    if x1 <= x0 or y1 <= y0:
        return
    a = alpha[y0 - y:y1 - y, x0 - x:x1 - x, None]
    over = premultiplied[y0 - y:y1 - y, x0 - x:x1 - x]
    under = np.asarray(pil_img.crop((x0, y0, x1, y1)).convert("RGB")).astype(np.float32)
    blended = np.clip(under * (1.0 - a) + over, 0, 255).astype(np.uint8)
    pil_img.paste(Image.fromarray(blended), (x0, y0))
