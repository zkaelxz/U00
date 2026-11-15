"""Owns reading how far a balloon's original lettering is tilted and setting the English at that angle."""
import logging
import math

import numpy as np
from PIL import Image

import scanlate_tuning

log = logging.getLogger(__name__)


def estimate_text_angle(page_bgr, box: dict) -> float:
    """Slant of the region's original lettering in degrees, positive when it runs down to the
    right on screen; 0.0 unless the lettering is clearly tilted. Read from the page before the
    lettering is erased. Found as the angle at which the ink lines up into the sharpest rows
    (skew detection), so one tilted line, several parallel tilted lines and a lone strip all
    give their slant, while level text of any number of lines scores best at 0 and vertical
    columns are left to their own layout."""
    import cv2
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.shape[0] < 8 or roi.shape[1] < 8:
        return 0.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    paper = float(np.median(gray))
    if paper < 100:
        ink = gray > 150
    else:
        ink = gray < min(max(150, paper * 0.8), 240)
    # Undilated, so glyphs stay apart from each other and from the balloon's outline. A long
    # mark touching the crop's edge is that outline or the art; a glyph-sized one is lettering
    # the box was cut tight around, which is common on narrow strips. A long thin straight
    # mark anywhere (a panel edge or speed line across the balloon) is no lettering either:
    # it would otherwise outvote the glyphs and drag the angle onto its own.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    longest = max(roi.shape[:2]) * scanlate_tuning.TILT_GLYPH_MAX_OF_BOX
    for i in range(1, count):
        left, top, width, height, pixels = stats[i]
        length = math.hypot(width, height)
        touches = left == 0 or top == 0 or left + width == roi.shape[1] or top + height == roi.shape[0]
        if (touches and max(width, height) > longest) or (length > longest and _is_straight(labels == i)):
            ink[labels == i] = False
    ys, xs = np.nonzero(ink)
    if len(xs) < scanlate_tuning.TILT_MIN_INK_PX:
        return 0.0
    points = np.vstack([xs, ys]).astype(np.float32)
    points -= points.mean(axis=1, keepdims=True)
    angles = np.arange(-scanlate_tuning.TILT_MAX_DEG, scanlate_tuning.TILT_MAX_DEG + 1.0)
    scores = np.array([_row_sharpness(points, a) for a in angles])
    best = int(scores.argmax())
    level = _row_sharpness(points, 0.0)
    # Columns of vertical lettering line up best across, not along, the page.
    if _row_sharpness(points, 90.0) >= scores[best]:
        return 0.0
    angle = float(angles[best])
    if abs(angle) < scanlate_tuning.TILT_MIN_DEG or scores[best] < scanlate_tuning.TILT_MIN_GAIN * level:
        return 0.0
    return angle


def _is_straight(mark) -> bool:
    """True when a mark's pixels lie along one line, far longer than thick."""
    ys, xs = np.nonzero(mark)
    eigenvalues = np.linalg.eigvalsh(np.cov(np.vstack([xs, ys])))
    return eigenvalues[0] <= 0 or math.sqrt(eigenvalues[1] / eigenvalues[0]) >= scanlate_tuning.TILT_LINE_MIN_ELONGATION


def _row_sharpness(points, angle: float) -> float:
    """How sharply the ink piles into rows when the page is read at `angle` degrees: the sum of
    squared row counts, largest when text lines run exactly along the angle."""
    radians = math.radians(angle)
    across = -points[0] * math.sin(radians) + points[1] * math.cos(radians)
    rows = np.bincount(np.round(across - across.min()).astype(np.int64))
    return float((rows.astype(np.float64) ** 2).sum())


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
        log.debug("tilt %.1f at size %d: clipped by the frame", angle, size)
        return False
    outside = (ink & ~room).sum() / ink.sum()
    if outside > scanlate_tuning.TILT_MAX_OUTSIDE_SHARE:
        log.debug("tilt %.1f at size %d: %.1f%% of the ink outside the balloon", angle, size, 100 * outside)
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
