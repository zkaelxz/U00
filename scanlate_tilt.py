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
    # Marks touching the crop's edge are the balloon outline or the art, not lettering.
    count, labels = cv2.connectedComponents(ink.astype(np.uint8), connectivity=8)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    ink &= ~np.isin(labels, edge[edge > 0])
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
    return angle


def draw_tilted(pil_img, box: dict, mask, angle: float, size: int, draw_text) -> bool:
    """Sets the text at `angle` (see estimate_text_angle) over the page and returns True,
    or returns False with the page untouched when the tilted text would not stay inside
    the balloon even a little smaller, so the caller letters it level instead.
    `draw_text(target, box_in_target, size)` draws the text level into a copy of the box."""
    w, h = box["w"], box["h"]
    pad = max(w, h) // 2 + 2
    for tried in (size, int(size * 0.9), int(size * 0.8)):
        if tried < scanlate_tuning.TYPESET_SMALLEST_PX:
            break
        # The text is drawn on white and on black, which gives its exact coverage and colour
        # without fringes: over black it is coverage * colour.
        layers = []
        for ground in (255, 0):
            layer = Image.new("RGB", (w + 2 * pad, h + 2 * pad), (ground,) * 3)
            draw_text(layer, dict(box, x=pad, y=pad), tried)
            layers.append(np.asarray(layer).astype(np.float32))
        white, black = layers
        coverage = 1.0 - (white - black).mean(axis=2) / 255.0
        turned = [np.asarray(Image.fromarray(a).rotate(-angle, resample=Image.BICUBIC,
                                                       center=(pad + w / 2, pad + h / 2)))
                  for a in (coverage.astype(np.float32), black[:, :, 0], black[:, :, 1], black[:, :, 2])]
        alpha = np.clip(turned[0], 0.0, 1.0)
        ink = alpha > 0.2
        if ink.sum() < 0.98 * (coverage > 0.2).sum():
            continue
        room = np.zeros(alpha.shape, bool)
        room[pad:pad + h, pad:pad + w] = True if mask is None else mask
        if (ink & ~room).sum() > scanlate_tuning.TILT_MAX_OUTSIDE_SHARE * ink.sum():
            continue
        _composite(pil_img, box["x"] - pad, box["y"] - pad, alpha, np.dstack(turned[1:]))
        return True
    return False


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
