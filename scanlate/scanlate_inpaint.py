"""
scanlate_inpaint.py -- erase the original text inside a bubble or mask
(LaMa-manga model, or plain OpenCV inpainting as the fallback).
"""

import math
from typing import Any

import numpy as np

from scanlate import scanlate_tuning
from scanlate.scanlate_lama import _run_ml_inpaint, lama_ml_weights_cached


# Dark-on-light lettering is every pixel under this share of the paper's gray.
_LETTERING_CUTOFF_OF_PAPER = 0.8
# The halo a stroke leaves is only slightly darker than the paper, so it is
# taken from a looser cut-off, and only where it touches lettering already
# masked: elsewhere it would be the art's own shading.
_LETTERING_HALO_OF_PAPER = 0.9
_LETTERING_HALO_GROW_ITERATIONS = 2
_LETTERING_FINAL_GROW_ITERATIONS = 2
# A halo that more than doubles the mask is shading, not lettering.
_LETTERING_HALO_MAX_GROWTH = 2.5


class InpaintModelUnavailable(RuntimeError):
    """The ML inpainting backend couldn't be loaded or run. Carries
    whether plain OpenCV inpainting already ran as a fallback, matching
    BubbleModelUnavailable's shape so callers can handle both the same
    way."""

    # Set only when the OpenCV fallback already produced an image.
    result: Any

    def __init__(self, message, fell_back_to_cv=True):
        super().__init__(message)
        self.fell_back_to_cv = fell_back_to_cv




_DARK_MEDIAN = 100


def background_is_dark(gray_roi) -> bool:
    """True when most of the region is dark, i.e. the lettering is the light part."""
    return float(np.median(gray_roi)) < _DARK_MEDIAN


_LIGHT_LETTERING_GROW_ITERATIONS = 5
_LIGHT_LETTERING_BIG_BOX_PX = 100


def _lettering_mask(gray_roi, wide: bool = False):
    """uint8 mask (255 = erase) of the lettering in a gray crop, grown past
    its anti-aliased edge: a fixed gray cut-off left the pale strokes and the
    halo round each stroke behind as ghost text under the English. The cut-off
    follows the paper's own brightness, so lettering on tinted paper is caught
    too. On paper (not light lettering on a dark balloon) the pale halo round
    each stroke is added too: a 3x3 dilation left it, and on ornate narration
    panels it showed as ghost Korean under the English."""
    import cv2
    if background_is_dark(gray_roi) or _has_light_lettering_on_midtone(gray_roi):
        # Light lettering on a dark balloon: masking the dark pixels would
        # erase the whole balloon and smear the surrounding art across it. `wide`, asked for only
        # inside a found balloon outline, grows the mask in a big dark balloon past the coloured
        # edge its large lettering carries, which a smaller grow left as a ghost. A mid-tone
        # balloon keeps the small grow (a bigger one ate its body), and so does any box with no
        # outline, where nothing bounds the grow and the share tests read the mask's size.
        _, mask = cv2.threshold(gray_roi, 150, 255, cv2.THRESH_BINARY)
    else:
        paper = float(np.median(gray_roi))
        cutoff = int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
        _, mask = cv2.threshold(gray_roi, cutoff, 255, cv2.THRESH_BINARY_INV)
        return _with_halo(gray_roi, paper, cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2))
    big = wide and min(gray_roi.shape[:2]) >= _LIGHT_LETTERING_BIG_BOX_PX
    grow = _LIGHT_LETTERING_GROW_ITERATIONS if big and background_is_dark(gray_roi) else 2
    return cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=grow)


_MIDTONE_MEDIAN_MAX = 130
_LIGHT_LETTERING_GRAY = 220
_LIGHT_LETTERING_MIN_SHARE = 0.08
_LIGHT_LETTERING_MAX_SHARE = 0.27


def _has_light_lettering_on_midtone(gray_roi) -> bool:
    """True for a mid-tone box (a blue-grey gradient balloon) with a clear minority of
    near-white pixels. `_lettering_mask` reads such a box as paper and marks nearly all of it,
    so a sign there was taken for artwork and left unerased under its English."""
    median = float(np.median(gray_roi))
    if not _DARK_MEDIAN <= median < _MIDTONE_MEDIAN_MAX:
        return False
    light = float((gray_roi >= _LIGHT_LETTERING_GRAY).mean())
    return _LIGHT_LETTERING_MIN_SHARE <= light <= _LIGHT_LETTERING_MAX_SHARE


def is_art_mistaken_for_sign(gray_page, box: dict) -> bool:
    """True when `box`, a guessed sign, is artwork rather than lettering: the
    lettering mask would cover nearly all of it, so erasing would paint the art
    out (a leg became a smear). Only a low-confidence sign is judged; a box the
    person or the detector was sure of keeps the normal path, and so does a wide
    strip, which is a caption."""
    if box.get("kind") != "sign" or float(box.get("kind_confidence") or 0.0) > scanlate_tuning.ART_SIGN_MAX_CONFIDENCE:
        return False
    if box["w"] > scanlate_tuning.ART_SIGN_MAX_ASPECT * box["h"]:
        return False
    h, w = gray_page.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = gray_page[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.size == 0:
        return False
    if _has_light_lettering_on_midtone(roi):
        return False
    return float((_lettering_mask(roi) > 0).mean()) > scanlate_tuning.ART_SIGN_MIN_MASK_SHARE


def _with_halo(gray_roi, paper: float, core_mask):
    """`core_mask` plus the connected pixels a little darker than the paper that
    touch it, grown a little; `core_mask` itself when that would more than
    double it."""
    import cv2
    kernel = np.ones((3, 3), np.uint8)
    _, halo = cv2.threshold(gray_roi, int(min(paper * _LETTERING_HALO_OF_PAPER, 245)), 255,
                            cv2.THRESH_BINARY_INV)
    halo = cv2.dilate(halo, kernel, iterations=_LETTERING_HALO_GROW_ITERATIONS)
    _, labels = cv2.connectedComponents((halo > 0).astype(np.uint8), connectivity=8)
    touched = np.unique(labels[core_mask > 0])
    grown = np.isin(labels, touched[touched > 0]) | (core_mask > 0)
    if grown.sum() > _LETTERING_HALO_MAX_GROWTH * max(int((core_mask > 0).sum()), 1):
        return core_mask
    return cv2.dilate(grown.astype(np.uint8) * 255, kernel, iterations=_LETTERING_FINAL_GROW_ITERATIONS)


_ART_MIN_EDGE_MARK_SHARE = 0.08


_RESCUE_MIN_CHROMA = 15
_RESCUE_MAX_COLOUR_GAP = 12
_RESCUE_MAX_DROPPED_SHARE = 0.35


def _drop_marks_running_off_edge(mask, roi=None) -> None:
    """Clears, in place, the mask components that touch the crop's edge and
    cover a sizeable share of it. Small ones stay: a stroke can end at the
    edge of a tight box. With `roi`, pixels of a cleared component that have
    the colour of the lettering that stayed are put back: coloured lettering
    that touches the art is one component with it, and dropping that left the
    letters unerased."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    rh, rw = mask.shape[:2]
    dropped = np.zeros(mask.shape[:2], bool)
    rim = None
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if (x == 0 or y == 0 or x + w == rw or y + h == rh) and area >= _ART_MIN_EDGE_MARK_SHARE * rh * rw:
            dropped |= labels == i
    if roi is not None and dropped.any():
        ab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.int32)[..., 1:] - 128
        chroma = np.hypot(ab[..., 0], ab[..., 1])
        coloured = (mask > 0) & (chroma >= _RESCUE_MIN_CHROMA)
        if coloured.any():
            # Lettering that stayed sets the colour; when it all joined the art, the most
            # common chroma is taken, since art is varied. The share cap stops a plain blob.
            stayed = coloured & ~dropped
            bins = ab[stayed if stayed.any() else coloured] // _RESCUE_MAX_COLOUR_GAP
            keys, counts = np.unique(bins, axis=0, return_counts=True)
            colour = (keys[counts.argmax()] + 0.5) * _RESCUE_MAX_COLOUR_GAP
            gap = np.hypot(ab[..., 0] - colour[0], ab[..., 1] - colour[1])
            rescued = dropped & (chroma >= _RESCUE_MIN_CHROMA) & (gap <= _RESCUE_MAX_COLOUR_GAP)
            if rescued.sum() <= _RESCUE_MAX_DROPPED_SHARE * dropped.sum():
                dropped &= ~rescued
                # The anti-aliased rim is paler than the core and left ghost outlines; only
                # pixels already marked are added, so unmarked art stays.
                rim = (cv2.dilate(rescued.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=2) > 0) & dropped
    mask[dropped] = 0
    if rim is not None:
        mask[rim] = 255


_RIM_BAND_PX = 6
_RIM_RING_SHARE = 0.8


def _drop_rim_rings(mask, keep) -> None:
    """Clears, in place, the mask components that lie almost wholly in a thin band along the
    balloon's rim. The rays or shading at a burst's edge read as one ring of "lettering";
    repainting it ate the rim and left a staircase edge. Lettering sits inside the band
    only where it touches the outline, so it is never mostly in it."""
    import cv2
    depth = cv2.distanceTransform(keep.astype(np.uint8), cv2.DIST_L2, 3)
    band = depth <= _RIM_BAND_PX
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    for i in range(1, count):
        part = labels == i
        if int((band & part).sum()) >= _RIM_RING_SHARE * stats[i, cv2.CC_STAT_AREA]:
            # A stroke joined to the ring stays: only the ring's own part is cleared.
            mask[part & band] = 0


# A mark this large in both directions yet this hollow is a frame or ornament
# round the lettering, not the lettering itself.
_FRAME_MIN_SPAN = 0.35
_FRAME_MAX_FILL = 0.15


def _drop_hollow_frames(mask, gray_roi) -> None:
    """Clears, in place, the mask components that span much of the crop in both
    directions but hold little ink: a caption frame's border and ornaments, and
    the line art they touch. Lettering lines are small in one direction or
    dense. The ink is counted from the gray crop, not the mask, because the
    mask's halo grows a thin frame into something that looks solid. Erasing a
    frame left a pale patch with the lettering's ghost where it had been."""
    import cv2
    paper = float(np.median(gray_roi))
    ink = gray_roi < int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    rh, rw = mask.shape[:2]
    for i in range(1, count):
        x, y, w, h, _area = stats[i]
        if w >= _FRAME_MIN_SPAN * rw and h >= _FRAME_MIN_SPAN * rh:
            if (ink & (labels == i)).sum() <= _FRAME_MAX_FILL * w * h:
                mask[labels == i] = 0
                _restore_glyphs(mask, ink & (labels == i))


_FRAME_GLYPH_MAX_SPAN = 0.12
_FRAME_GLYPH_MIN_SPAN = 8
_FRAME_GLYPH_MIN_FILL = 0.25


def _restore_glyphs(mask, ink) -> None:
    """Puts back, in place, the small ink marks of a dropped frame component:
    lettering that touches the frame or a watermark shares its component, and
    leaving it out left the original letters showing under the English."""
    import cv2
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    limit = _FRAME_GLYPH_MAX_SPAN * min(mask.shape[:2])
    keep = np.zeros(mask.shape, np.uint8)
    for i in range(1, count):
        w, h, area = (stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT],
                      stats[i, cv2.CC_STAT_AREA])
        # A glyph is compact; the art's stray line fragments are thin or sparse.
        if _FRAME_GLYPH_MIN_SPAN <= min(w, h) and max(w, h) <= limit and area >= _FRAME_GLYPH_MIN_FILL * w * h:
            keep[labels == i] = 255
    mask[cv2.dilate(keep, np.ones((3, 3), np.uint8), iterations=3) > 0] = 255


_ART_FILL_CONTEXT_PX = 60
_ART_FILL_FEATHER_PX = 8
# Outlined lettering is white or pale; bright pixels that are strongly coloured
# are artwork (flames, glare) and stay out of the lettering mask, so painting the
# lettering out cannot blank the art round it.
_ART_FILL_SATURATION = 120
# Median saturation from which a box counts as coloured art. Its flat-tone fill
# would show as a smudge on that texture, and its median brightness can read as
# light even though the lettering is pale outlined text, so LaMa gets the mask.
_COLOURED_ART_MEDIAN_SATURATION = 100
# A plain black or near-black ground (a notice banner, a title card) has no art for coloured
# lettering to be mistaken for, so any bright pixel on it is lettering: yellow headlines on a
# black disclaimer page were left under the English when only pale strokes were taken.
_PLAIN_GROUND_MAX_GRAY = 40
_PLAIN_GROUND_MAX_SPREAD = 40


def _on_plain_dark_ground(gray, colour) -> bool:
    """`colour` is a per-pixel measure of how coloured the crop is: HSV saturation for the
    fill choice, channel spread (_channel_spread) for the lettering mask. HSV saturation
    reads a near-black strip with olive stripes as strongly coloured, so for the mask a
    black banner over art never counted as a plain ground and its yellow lettering was kept."""
    return (float(np.median(gray)) <= _PLAIN_GROUND_MAX_GRAY
            and float(np.median(colour)) <= _PLAIN_GROUND_MAX_SPREAD)


_STRIP_MIN_ASPECT = 6
_STRIP_DARK_GRAY = 70
_STRIP_DARK_SHARE = 0.5
_STRIP_GAP_COLUMNS = 6


def widen_to_dark_strip(img, box: dict) -> dict:
    """`box` stretched along the dark banner strip it sits on to the strip's ends, or `box`
    itself when it is not a long, narrow, dark strip. OCR often stops short of the last glyphs
    of a banner line, and the lettering left past the box showed under the English. A strip
    of vertical lettering is stretched up and down the same way."""
    if box["h"] >= _STRIP_MIN_ASPECT * box["w"]:
        across = {**box, "x": box["y"], "y": box["x"], "w": box["h"], "h": box["w"]}
        turned = _widen_wide_strip(np.ascontiguousarray(img.swapaxes(0, 1)), across)
        return {**box, "y": turned["x"], "h": turned["w"]}
    return _widen_wide_strip(img, box)


def _widen_wide_strip(img, box: dict) -> dict:
    import cv2
    h, w = img.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(w, box["x"] + box["w"]), min(h, box["y"] + box["h"])
    if x1 <= x0 or y1 <= y0 or (x1 - x0) < _STRIP_MIN_ASPECT * (y1 - y0):
        return box
    gray = cv2.cvtColor(img[y0:y1], cv2.COLOR_BGR2GRAY)
    if float(np.median(gray[:, x0:x1])) > _PLAIN_GROUND_MAX_GRAY:
        return box
    dark = (gray < _STRIP_DARK_GRAY).mean(axis=0) >= _STRIP_DARK_SHARE

    def reach(columns) -> int:
        end = gaps = 0
        for step, is_dark in enumerate(columns, 1):
            gaps = 0 if is_dark else gaps + 1
            if gaps >= _STRIP_GAP_COLUMNS:
                break
            end = step if is_dark else end
        return end

    right, left = reach(dark[x1:]), reach(dark[:x0][::-1])
    return {**box, "x": x0 - left, "w": (x1 - x0) + left + right}


_STRIP_LETTER_GRAY = 70


def _dark_strip_inpaint(roi):
    """`roi` (BGR) with the lettering of a dark banner strip painted out from the strip's own
    dark ground, or None when the crop is not a long, narrow, dark strip (either way up). LaMa
    painted the surrounding art over a strip like this, so the English lost its dark ground."""
    import cv2
    h, w = roi.shape[:2]
    if h >= _STRIP_MIN_ASPECT * w:
        turned = _dark_strip_inpaint(np.ascontiguousarray(roi.swapaxes(0, 1)))
        return None if turned is None else np.ascontiguousarray(turned.swapaxes(0, 1))
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    if w < _STRIP_MIN_ASPECT * h or float(np.median(gray)) > _PLAIN_GROUND_MAX_GRAY:
        return None
    rows = (gray < _STRIP_DARK_GRAY).mean(axis=1) >= _STRIP_DARK_SHARE
    mask = ((gray >= _STRIP_LETTER_GRAY) & rows[:, None]).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    mask[~rows] = 0
    return cv2.inpaint(roi, mask, 3, cv2.INPAINT_TELEA)


# Black lettering this close to its own darkest level is the letters' ink; the art's lines
# round it vary more and are thinner than a heavy stroke.
_HEAVY_INK_SPREAD = 12
_HEAVY_STROKE_PX = 7
_HEAVY_GROW_PX = 11
_GROUND_RING_PX = 16


def _heavy_dark_lettering(img, x: int, y: int, bw: int, bh: int):
    """uint8 mask (255 = erase) of black lettering so heavy that it is most of its box, set on
    lighter art, or None. The box then reads as a dark ground, and the pale art between the
    letters was painted out instead of the letters, which stayed as black blocks. A black
    strip or banner is a ground of its own (near-black through), so it is not this."""
    import cv2
    gray = cv2.cvtColor(img[y:y + bh, x:x + bw], cv2.COLOR_BGR2GRAY)
    if not background_is_dark(gray) or float(np.median(gray)) <= _PLAIN_GROUND_MAX_GRAY:
        return None
    ih, iw = img.shape[:2]
    r = _GROUND_RING_PX
    x0, y0, x1, y1 = max(0, x - r), max(0, y - r), min(iw, x + bw + r), min(ih, y + bh + r)
    around = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    outside = np.ones(around.shape, bool)
    outside[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = False
    if not outside.any() or background_is_dark(around[outside]):
        return None
    level = float(np.percentile(gray, 10))
    ink = (gray <= level + _HEAVY_INK_SPREAD).astype(np.uint8) * 255
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((_HEAVY_STROKE_PX, _HEAVY_STROKE_PX), np.uint8))
    if not ink.any():
        return None
    return cv2.dilate(ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_HEAVY_GROW_PX, _HEAVY_GROW_PX)))


def _fill_enclosed_holes(mask):
    """`mask` with every 0-region that does not reach its edge set to 255: the
    inside of a letter whose outline is the only part that was masked. The
    border is padded first so the flood starts on background even when the
    corner pixel is itself masked."""
    import cv2
    h, w = mask.shape[:2]
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    cv2.floodFill(padded, np.zeros((h + 4, w + 4), np.uint8), (0, 0), 255)
    return cv2.bitwise_or(mask, cv2.bitwise_not(padded[1:-1, 1:-1]))


def _pale_pixels(crop):
    """Bool array: bright, weakly coloured pixels of a BGR crop, i.e. the pale
    outline and glow of lettering rather than the artwork."""
    import cv2
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    if _on_plain_dark_ground(gray, _channel_spread(crop)):
        return gray > 150
    return (gray > 150) & (saturation <= _ART_FILL_SATURATION)


def is_coloured_art(crop) -> bool:
    import cv2
    return float(np.median(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1])) > _COLOURED_ART_MEDIAN_SATURATION


_GLOW_REACH_OF_SHORT_SIDE = 0.4


def _dark_art_lettering_mask(crop):
    """uint8 mask (255 = erase) of outlined lettering over dark art. The bright
    outline is the only part a brightness cut-off finds, so the fill inside
    each letter is recovered by flooding from the corner and everything within
    a few pixels is taken too, which removes the glow round the strokes."""
    import cv2
    core = _pale_pixels(crop).astype(np.uint8) * 255
    core = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=2)
    core = _fill_enclosed_holes(core)
    core = cv2.morphologyEx(core, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    # The glow reach shrinks with the box so a thin strip keeps some of its own dark ground:
    # the full reach covered a banner strip entirely and LaMa painted the art over it.
    reach = min(33, max(9, int(_GLOW_REACH_OF_SHORT_SIDE * min(crop.shape[:2]))) | 1)
    return cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))


def _tone_from_clear_pixels(area, clear):
    """The art's tone at every pixel, averaged from the `clear` (1.0) pixels.
    Widens the blur where a lettered block is too large for the narrow one to
    reach clear pixels: dividing by a clamped, vanishing weight faded the fill to
    black in the middle of a big sign."""
    import cv2
    def scale(sigma):
        weight = cv2.GaussianBlur(clear, (0, 0), sigma)
        tone = cv2.GaussianBlur(area * clear[..., None], (0, 0), sigma) \
            / np.maximum(weight[..., None], 1e-6)
        return tone, np.minimum(weight * 20, 1.0)

    tone, reach = scale(15)
    for sigma in (45, 135):
        wider, wider_reach = scale(sigma)
        # Pixels with little narrow-scale support take the wider estimate.
        tone = tone * reach[..., None] + wider * (1 - reach[..., None])
        reach = np.maximum(reach, wider_reach)
    return tone


def _box_edge_ramp(shape, ox: int, oy: int, bw: int, bh: int, ih: int, iw: int, x: int, y: int):
    """Weight 0 on the box's own edge rising to 1 a few pixels in, so the fill
    meets the untouched art outside the box without a straight seam. Sides on
    the page border have nothing outside to meet and are left alone."""
    ramp = np.ones(shape, np.float32)
    steps = (np.arange(_ART_FILL_FEATHER_PX, dtype=np.float32) / _ART_FILL_FEATHER_PX)
    for side, on_page_edge in (("l", x == 0), ("r", x + bw >= iw), ("t", y == 0), ("b", y + bh >= ih)):
        if on_page_edge:
            continue
        for i, v in enumerate(steps):
            if side == "l":
                ramp[:, ox + i] = np.minimum(ramp[:, ox + i], v)
            elif side == "r":
                ramp[:, ox + bw - 1 - i] = np.minimum(ramp[:, ox + bw - 1 - i], v)
            elif side == "t":
                ramp[oy + i, :] = np.minimum(ramp[oy + i, :], v)
            else:
                ramp[oy + bh - 1 - i, :] = np.minimum(ramp[oy + bh - 1 - i, :], v)
    return ramp


def _dark_art_fill(img, x: int, y: int, bw: int, bh: int):
    """The box's crop with its lettering replaced by the colours of the art
    round it, or None when it holds no pale lettering.
    TELEA and LaMa both pull the stroke glow into the fill and leave a grey
    smudge on dark art; averaging only pixels well clear of the lettering
    gives the art's own tone."""
    import cv2
    crop = img[y:y + bh, x:x + bw]
    if not _pale_pixels(crop).any():
        return None
    mask_box = _dark_art_lettering_mask(crop)
    ih, iw = img.shape[:2]
    x0, y0 = max(0, x - _ART_FILL_CONTEXT_PX), max(0, y - _ART_FILL_CONTEXT_PX)
    x1, y1 = min(iw, x + bw + _ART_FILL_CONTEXT_PX), min(ih, y + bh + _ART_FILL_CONTEXT_PX)
    area = img[y0:y1, x0:x1].astype(np.float32)
    mask = np.zeros(area.shape[:2], np.uint8)
    mask[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = mask_box
    mask = _fill_enclosed_holes(mask)
    clear = (cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41))) == 0)
    clear = clear.astype(np.float32)
    tone = _tone_from_clear_pixels(area, clear)
    weight = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 4)
    weight = (weight * _box_edge_ramp(area.shape[:2], x - x0, y - y0, bw, bh, ih, iw, x, y))[..., None]
    blended = (tone * weight + area * (1 - weight)).clip(0, 255).astype(np.uint8)
    return blended[y - y0:y - y0 + bh, x - x0:x - x0 + bw]


# Lettering is near-white and barely coloured; pale art such as a tan coat is
# brighter than the dark ground but fails one of these, so it stays unmasked.
_STROKE_MIN_GRAY = 200
_STROKE_MAX_SATURATION = 60


def _dark_art_stroke_mask(crop):
    """uint8 mask (255 = erase) of just the white letter strokes and their fill
    plus a thin margin for the glow, so LaMa repaints the lettering and leaves
    the art between and around the letters alone."""
    import cv2
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    saturation = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    core = ((gray >= _STROKE_MIN_GRAY) & (saturation <= _STROKE_MAX_SATURATION)).astype(np.uint8) * 255
    core = cv2.dilate(core, np.ones((3, 3), np.uint8), iterations=2)
    core = _fill_enclosed_holes(core)
    return cv2.dilate(core, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))


def _dark_art_inpaint(img, x: int, y: int, bw: int, bh: int, backend: str, hf_token):
    """The crop for lettering on dark art. LaMa keeps the art's edges and
    texture where the flat tone fill smears them, so it goes first when the
    model is usable; the tone fill is the fallback and the answer without it."""
    import cv2
    crop = img[y:y + bh, x:x + bw]
    # A plain ground has no edges or texture for LaMa to keep, and LaMa left faint grey blocks
    # where the lettering was on a black notice page; the tone fill gives the ground itself.
    if (_resolve_inpaint_backend(backend) != "ml" or not _pale_pixels(crop).any()
            or _on_plain_dark_ground(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY),
                                     cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1])):
        return _dark_art_fill(img, x, y, bw, bh)
    mask = _dark_art_stroke_mask(crop)
    if not mask.any():
        return _dark_art_fill(img, x, y, bw, bh)
    lama, _ = _try_ml_inpaint(crop, mask, hf_token, backend)
    if lama is None:
        return _dark_art_fill(img, x, y, bw, bh)
    ih, iw = img.shape[:2]
    weight = cv2.GaussianBlur(mask.astype(np.float32) / 255, (0, 0), 4)
    weight *= _box_edge_ramp(weight.shape, 0, 0, bw, bh, ih, iw, x, y)
    blended = lama * weight[..., None] + crop * (1 - weight[..., None])
    return blended.clip(0, 255).astype(np.uint8)


def _resolve_inpaint_backend(backend: str) -> str:
    if backend == "auto":
        return "ml" if lama_ml_weights_cached() else "cv"
    return backend


# Lettering on the art is erased from a crop of its own box; a mask that fills most of a
# narrow crop leaves the model no clear pixels to copy, and it painted a smear. A margin of
# untouched art round the box gives it the picture to continue.
_ML_CONTEXT_PX = 32


def _try_ml_inpaint_with_context(img, window, mask, hf_token, backend, context: int):
    """_try_ml_inpaint over the window (x, y, w, h) of `img` widened by `context` px each
    side, returning only the window's part. The glow of lettering that spreads past the
    window is repainted too, in `img` itself: left outside, the model copied its colour
    back into the window as a smudge."""
    x, y, bw, bh = window
    if context <= 0:
        return _try_ml_inpaint(img[y:y + bh, x:x + bw], mask, hf_token, backend)
    h, w = img.shape[:2]
    x0, y0 = max(0, x - context), max(0, y - context)
    x1, y1 = min(w, x + bw + context), min(h, y + bh + context)
    wide_mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
    wide_mask[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = mask
    wide_roi = img[y0:y1, x0:x1]
    spill = _glow_beyond(wide_roi, wide_mask)
    wide_mask |= spill
    wide, error = _try_ml_inpaint(wide_roi, wide_mask, hf_token, backend)
    if wide is None:
        return None, error
    margin = (spill > 0)
    margin[y - y0:y - y0 + bh, x - x0:x - x0 + bw] = False
    wide_roi[margin] = wide[margin]
    return wide[y - y0:y - y0 + bh, x - x0:x - x0 + bw], error


def _glow_beyond(roi, mask):
    """uint8 mask of the coloured glow round the masked lettering of `roi`, within reach of
    it, or zeros when the art is coloured throughout or there is no mask."""
    import cv2
    if not mask.any():
        return np.zeros_like(mask)
    coloured = _channel_spread(roi) > _GLOW_SPREAD
    if coloured.mean() > _GLOW_MAX_SHARE:
        return np.zeros_like(mask)
    reach = cv2.dilate(mask, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_GLOW_REACH_PX, _GLOW_REACH_PX))) > 0
    return (coloured & reach).astype(np.uint8) * 255


def _try_ml_inpaint(roi, mask, hf_token, backend):
    """(inpainted crop, None) from LaMa, or (None, InpaintModelUnavailable) when
    it can't run, so the caller finishes with OpenCV and still raises the error."""
    try:
        return _run_ml_inpaint(roi, mask, hf_token=hf_token, allow_download=backend == "ml"), None
    except ImportError as exc:
        error = InpaintModelUnavailable(
            "ML inpainting needs extra packages:\n"
            "    pip install torch safetensors huggingface_hub\n\n"
            "Falling back to plain OpenCV inpainting for this bubble."
        )
        error.__cause__ = exc
    except Exception as exc:
        error = InpaintModelUnavailable(
            f"LaMa-manga inpainting failed: {type(exc).__name__}: {exc}\n\n"
            "Falling back to plain OpenCV inpainting for this bubble."
        )
        error.__cause__ = exc
    return None, error


def _paint_paper(inpainted, fill):
    if fill is None:
        return inpainted
    take, colour = fill
    out = inpainted.copy()
    out[take] = colour.astype(inpainted.dtype)
    return out


def _plain_paper_fill(roi, mask, inside):
    """(pixels to set, paper colour) that flatten the inside of a plain white balloon
    to its paper colour. The lettering mask misses the faint halo a JPEG leaves round
    each stroke, and inpainting only repaints the mask, so that halo showed as a ghost
    under the English. None for a balloon whose paper varies (tone, a gradient) or is
    too small to measure. The outline's own edge is kept out by eroding the inside first."""
    import cv2
    core = cv2.erode(inside.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    paper = core & (mask == 0)
    if int(paper.sum()) < 50:
        return None
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    values = gray[paper]
    # The halo is darker than the paper, so the typical value is the median;
    # the spread is taken round it over the lighter half to ignore that halo.
    median = float(np.median(values))
    light = values[values >= median]
    if float(light.std()) > scanlate_tuning.INPAINT_FLAT_PAPER_STD:
        return None
    if _shaded_away_from_lettering(gray, mask, paper, median):
        return None
    colour = np.median(roi[paper], axis=0)
    # Art the interior mask spilled over (a pink wall between a burst's lobes, the
    # soft rays of its edge) differs from the paper and is far from the lettering:
    # flattening it cut a white block out of the picture with a staircase edge.
    near = cv2.dilate((mask > 0).astype(np.uint8), cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_HALO_REACH_PX, _HALO_REACH_PX))).astype(bool)
    like_paper = np.abs(roi.astype(np.float32) - colour).max(axis=2) <= _PAPER_LIKE_DISTANCE
    # Eroded, so the soft rays or shading at the balloon's edge keep their own gradient
    # instead of ending in a staircase where the flat paper begins.
    inner_paper = cv2.erode(like_paper.astype(np.uint8), np.ones((_EDGE_KEEP_PX, _EDGE_KEEP_PX), np.uint8)).astype(bool)
    return core & (inner_paper | near), colour


_HALO_REACH_PX = 13
# A pixel this close to the balloon's paper colour (largest channel difference) is paper
# or its JPEG halo; anything further that is not beside the lettering is the art.
_PAPER_LIKE_DISTANCE = 22
_EDGE_KEEP_PX = 9


def _shaded_away_from_lettering(gray, mask, paper, median: float) -> bool:
    """True when the paper beyond the halo of the lettering has patches clearly
    darker than its median: a gradient, cloud or tone that flattening would turn
    into a white block. The halo itself is darker by design, so it is left out."""
    import cv2
    near = cv2.dilate((mask > 0).astype(np.uint8), cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_HALO_REACH_PX, _HALO_REACH_PX))).astype(bool)
    far = gray[paper & ~near]
    if far.size < 50:
        return False
    shaded = float((far < median - scanlate_tuning.INPAINT_FLAT_PAPER_SHADE_DEPTH).mean())
    return shaded > scanlate_tuning.INPAINT_FLAT_PAPER_MAX_SHADED


_AROUND_LETTERING_GROW_PX = 15
# Pixels this much darker than the paper or lighter are the halo; darker ones are art.
_AROUND_LETTERING_HALO_DEPTH = 40


_AROUND_LETTERING_MIN_INK_FILL = 0.15


def _keep_dense_marks(mask, gray):
    """`mask` without its sparse components: a line of lettering is dense with
    ink, a few strokes of the art round it are not, and whitening those would
    leave a pale patch in the picture."""
    import cv2
    ink = gray < int(min(max(150, float(np.median(gray)) * _LETTERING_CUTOFF_OF_PAPER), 240))
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
    out = np.zeros_like(mask)
    for i in range(1, count):
        w, h = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if (ink & (labels == i)).sum() >= _AROUND_LETTERING_MIN_INK_FILL * w * h:
            out[labels == i] = mask[labels == i]
    return out


def _flatten_around_lettering(roi, inpainted, mask):
    """`inpainted` with the pale halo round masked lettering set to the paper
    colour, for lettering on flat paper that has no balloon outline to measure
    (a caption frame on a sign region). The mask misses the faint halo a JPEG
    leaves round each stroke, and it showed as a smudge of ghost text under the
    English. The lettering itself is set to the paper colour too, because the
    model repaints a flat row of lettering with a faint copy of it. Paper that
    varies, or too little of it to measure, is returned as it is, and pixels
    darker than the halo and outside the mask (the frame, the art) are kept."""
    import cv2
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask = _keep_dense_marks(mask, gray)
    near = cv2.dilate((mask > 0).astype(np.uint8), cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_AROUND_LETTERING_GROW_PX, _AROUND_LETTERING_GROW_PX))).astype(bool)
    ring = near & (mask == 0)
    if int(ring.sum()) < 50:
        return inpainted
    median = float(np.median(gray[ring]))
    light = gray[ring][gray[ring] >= median]
    if float(light.std()) > scanlate_tuning.INPAINT_FLAT_PAPER_STD:
        return inpainted
    out = inpainted.copy()
    halo = near & ((gray >= median - _AROUND_LETTERING_HALO_DEPTH) | (mask > 0))
    out[halo] = np.median(roi[ring & (gray >= median - 5)], axis=0).astype(inpainted.dtype)
    return out


def _grown_erase_mask(mask, grow: float, keep):
    """`mask` widened by about 2px per 0.1 of `grow` above 1.0, kept inside `keep` (the
    balloon's outline) when there is one."""
    import cv2
    if grow <= 1.0 or not mask.any():
        return mask
    reach = max(1, round((grow - 1.0) * 20))
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1)))
    if keep is not None:
        mask[~keep] = 0
    return mask


# Lettering is the darkest ink in a balloon: a pixel counts as lettering when it is within this
# share of the way from the darkest strokes to the paper. Hatching and tone drawn in a lighter
# line fall outside it.
_HATCHING_STROKE_DEPTH = 0.35
# A mark this far from any stroke is not an edge or halo of it.
_HATCHING_REACH_PX = 6

def _without_hatching(gray_roi, keep):
    """`gray_roi` with the pale marks that sit away from the lettering set to the paper's gray,
    so the erase mask follows the lettering and leaves the hatching or screentone drawn inside
    a balloon alone (erasing it left a hard-edged white patch). Unchanged unless such marks
    outweigh the lettering, so an ordinary balloon, or one with a little furigana, is
    erased exactly as before. `keep`: where the balloon's interior is."""
    import cv2
    if background_is_dark(gray_roi):
        return gray_roi
    paper = float(np.median(gray_roi))
    cutoff = int(min(max(150, paper * _LETTERING_CUTOFF_OF_PAPER), 240))
    ink = gray_roi < cutoff
    # The outline, and any hatching joined to it, is darker than the lettering can be, so the
    # darkest strokes are read from the marks that are not part of such a frame.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    rh, rw = gray_roi.shape
    lettering = ink.copy()
    for i in range(1, count):
        w, h, area = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], stats[i, cv2.CC_STAT_AREA]
        if w >= _FRAME_MIN_SPAN * rw and h >= _FRAME_MIN_SPAN * rh and area <= _FRAME_MAX_FILL * w * h:
            lettering[labels == i] = False
    sample = gray_roi[lettering & keep]
    if sample.size < 30:
        return gray_roi
    darkest = float(np.percentile(sample, 10))
    strokes = ink & (gray_roi <= darkest + _HATCHING_STROKE_DEPTH * (paper - darkest))
    reach = 2 * _HATCHING_REACH_PX + 1
    near = cv2.dilate(strokes.astype(np.uint8),
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach))).astype(bool)
    if int((ink & ~near).sum()) <= int(strokes.sum()):
        return gray_roi
    cleared = gray_roi.copy()
    cleared[(gray_roi < int(min(paper * _LETTERING_HALO_OF_PAPER, 245))) & ~near] = int(paper)
    return cleared


def _inside_in_crop(inside, box: dict, x: int, y: int, bw: int, bh: int):
    """The balloon's `inside` array (laid over `box`) as a bool array over the padded crop
    that starts at (x, y), False outside the box."""
    keep = np.zeros((bh, bw), dtype=bool)
    oy, ox = box["y"] - y, box["x"] - x
    part = inside[max(0, -oy):, max(0, -ox):]
    part = part[:bh - max(oy, 0), :bw - max(ox, 0)]
    keep[max(oy, 0):max(oy, 0) + part.shape[0], max(ox, 0):max(ox, 0) + part.shape[1]] = part
    return keep


def _lettering_glyphs(gray_roi):
    """Label image, stats and centres of the dark marks of `gray_roi`, and the ids of those
    that look like a glyph: of letter size and sitting on pale paper (a ring round them
    that is mostly light), as lettering on a white band does and art's own line work
    mostly does not."""
    import cv2
    h, w = gray_roi.shape
    count, labels, stats, centres = cv2.connectedComponentsWithStats(
        (gray_roi < scanlate_tuning.LINE_INK_LEVEL).astype(np.uint8), connectivity=8)
    ring_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    glyphs = []
    for i in range(1, count):
        x, y, cw, ch, area = stats[i]
        if area < scanlate_tuning.LINE_GLYPH_MIN_AREA or not (
                scanlate_tuning.LINE_GLYPH_MIN_SPAN <= max(cw, ch) <= scanlate_tuning.LINE_GLYPH_MAX_SPAN * h):
            continue
        x0, y0, x1, y1 = max(0, x - 5), max(0, y - 5), min(w, x + cw + 5), min(h, y + ch + 5)
        mark = (labels[y0:y1, x0:x1] == i).astype(np.uint8)
        ring = (cv2.dilate(mark, ring_kernel) > 0) & (mark == 0)
        if (gray_roi[y0:y1, x0:x1][ring] >= scanlate_tuning.LINE_PAPER_LEVEL).mean() >= scanlate_tuning.LINE_RING_PALE_SHARE:
            glyphs.append(i)
    return labels, stats, centres, glyphs


def _strokes_on_a_line(gray_roi):
    """(mask, slant in degrees, typical glyph size) of one line of dark lettering set on a pale
    band over busy art, or None when the crop holds no such line; the mask is uint8, 255 =
    erase. The paper-relative mask reads the dark
    art round such a band as lettering too, covers nearly the whole crop, and the
    repaint whitened the art out. The letters are found as glyph-sized dark marks on pale
    paper that lie along one straight (possibly tilted) line; marks off that line (windows,
    hatching) are left alone."""
    import cv2
    labels, stats, centres, glyphs = _lettering_glyphs(gray_roi)
    anchors = [i for i in glyphs if stats[i, cv2.CC_STAT_AREA] >= scanlate_tuning.LINE_ANCHOR_AREA
               and min(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= scanlate_tuning.LINE_ANCHOR_THICKNESS]
    if len(anchors) < scanlate_tuning.LINE_MIN_GLYPHS:
        return None
    typical = float(np.median([max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) for i in anchors]))
    # Refitted on the marks that lie along the first fit, so a few stray marks (hatching, windows)
    # cannot hold the angle off the real line.
    for _ in range(2):
        points = np.array([centres[i] for i in anchors], np.float32)
        vx, vy, px, py = cv2.fitLine(points, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        near = [i for i in anchors if abs((centres[i][0] - px) * vy - (centres[i][1] - py) * vx)
                <= scanlate_tuning.LINE_REACH * max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], typical)]
        if len(near) < scanlate_tuning.LINE_MIN_GLYPHS:
            return None
        anchors = near
    points = np.array([centres[i] for i in anchors], np.float32)
    along = (points[:, 0] - px) * vx + (points[:, 1] - py) * vy
    if float(along.max() - along.min()) < scanlate_tuning.LINE_MIN_LENGTH_IN_GLYPHS * typical:
        return None
    mask = np.zeros(gray_roi.shape, np.uint8)
    for i in glyphs:
        reach = scanlate_tuning.LINE_REACH * max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT], typical)
        if abs((centres[i][0] - px) * vy - (centres[i][1] - py) * vx) <= reach:
            mask[labels == i] = 255
    # Specks of the old lettering (a dot, a stroke end) are too small or too close to a letter
    # to pass as glyphs, yet sit in the same strip; left behind they read as dirt beside the English.
    # One lookup over the label image: a crop of busy art holds tens of thousands of marks, and a
    # scan of the whole crop per mark took seconds.
    lo, hi = float(along.min()) - typical, float(along.max()) + typical
    spans = np.maximum(stats[:, cv2.CC_STAT_WIDTH], stats[:, cv2.CC_STAT_HEIGHT])
    across = (centres[:, 0] - px) * vy - (centres[:, 1] - py) * vx
    lengthwise = (centres[:, 0] - px) * vx + (centres[:, 1] - py) * vy
    speck = ((spans <= scanlate_tuning.LINE_SPECK_MAX_OF_GLYPH * typical)
             & (np.abs(across) <= scanlate_tuning.LINE_SPECK_REACH * typical)
             & (lengthwise >= lo) & (lengthwise <= hi))
    speck[0] = False
    mask[speck[labels]] = 255
    grown = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    return grown, math.degrees(math.atan2(vy, vx)) * (1 if vx >= 0 else -1), typical


# Lettering that is brighter than its surroundings by this much, measured over a window
# wider than a stroke, is a stroke; a flat pale area of the art is not.
_BRIGHT_STROKE_WINDOW_PX = 21
_BRIGHT_STROKE_LIFT = 70
_BRIGHT_STROKE_GROW_PX = 5
_BRIGHT_STROKE_MIN_SHARE = 0.04
_BRIGHT_STROKE_MAX_MEDIAN = 200
# A glow is a coloured halo within this reach of the strokes; left behind it showed as a
# dark-red smudge under the English.
_GLOW_SPREAD = 35
_GLOW_REACH_PX = 45
_GLOW_MAX_SHARE = 0.6
# A sign whose paper-relative mask covers more of its box than this is taking the art for
# lettering, and repainting that much erased a blurry rectangle over the picture.
BRIGHT_STROKE_MASK_SHARE = 0.5


def _bright_strokes(gray_roi, roi=None):
    """uint8 mask (255 = erase) of the pale strokes of lettering set on mid-tone art with a
    glow round it, found as pixels much brighter than the window round them, or None when
    there are none. With the colour crop `roi`, a coloured glow round the strokes is taken
    too, unless the art is coloured everywhere (then the glow can't be told from it)."""
    import cv2
    lift = cv2.morphologyEx(gray_roi, cv2.MORPH_TOPHAT, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_BRIGHT_STROKE_WINDOW_PX, _BRIGHT_STROKE_WINDOW_PX)))
    # Pale paper with dark lettering on it is the ordinary case, not this one: the paper
    # between two lines of lettering would count as a pale stroke.
    if float(np.median(gray_roi)) >= _BRIGHT_STROKE_MAX_MEDIAN:
        return None
    mask = (lift > _BRIGHT_STROKE_LIFT).astype(np.uint8) * 255
    # The corners of a dark mark on paper also lift a little; real lettering is far more.
    if (mask > 0).mean() < _BRIGHT_STROKE_MIN_SHARE:
        return None
    strokes = cv2.dilate(mask, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_BRIGHT_STROKE_GROW_PX, _BRIGHT_STROKE_GROW_PX)))
    if roi is None:
        return strokes
    saturated = _channel_spread(roi) > _GLOW_SPREAD
    if saturated.mean() > _GLOW_MAX_SHARE:
        return strokes
    reach = cv2.dilate(strokes, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (_GLOW_REACH_PX, _GLOW_REACH_PX))) > 0
    return strokes | ((saturated & reach).astype(np.uint8) * 255)


def sign_text_line(page_bgr, box: dict):
    """(corridor, angle, size) of the single line of lettering on a pale band over busy art, or
    None. `corridor` is a bool array the size of `box`, True along the line's own strip, `angle`
    its slant in degrees (positive when it runs down to the right) and `size` the font size that
    fits the strip's height, so the English can be set along the band instead of in the level
    box that spills over the art. Read from the page
    before the erase, and only where the erase itself takes the line-only path."""
    import cv2
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    gray = cv2.cvtColor(page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])], cv2.COLOR_BGR2GRAY)
    if gray.size == 0 or (_lettering_mask(gray) > 0).mean() <= scanlate_tuning.LINE_ONLY_MASK_SHARE:
        return None
    found = _strokes_on_a_line(gray)
    if found is None:
        return None
    strokes, angle, typical = found
    reach = max(3, round(scanlate_tuning.LINE_CORRIDOR_CLOSE * typical)) | 1
    corridor = cv2.morphologyEx(strokes, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
    margin = max(3, round(scanlate_tuning.LINE_CORRIDOR_MARGIN * typical)) | 1
    corridor = cv2.dilate(corridor, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (margin, margin)))
    return corridor > 0, angle, round(scanlate_tuning.LINE_TEXT_SIZE_OF_GLYPH * typical)


_RULE_MAX_THICKNESS_PX = 6
_RULE_MIN_LENGTH_PX = 40
_RULE_LETTERING_GAP_PX = 8
_RULE_LETTERING_MIN_AREA_PX = 30
_RULE_LETTERING_MIN_SPAN_PX = 12
_RULE_SPREAD = 100


def _channel_spread(bgr):
    """Per pixel largest minus smallest colour channel: how coloured it is. HSV saturation
    is high for near-black ink, which is neutral."""
    bgr = bgr.astype(np.int16)
    return bgr.max(axis=2) - bgr.min(axis=2)


def _beside_coloured_lettering(stats, i, glyphs) -> bool:
    """True when a coloured glyph (`glyphs`, rows of x, y, w, h) sits within a few pixels of
    horizontal rule `i` on its line: glow lettering whose dash is as coloured
    as its letters. A credit rule has plain dark text beside it; a small end cap on it is
    smaller than a glyph."""
    x, y, w, h = (int(v) for v in stats[i, :4])
    if h > w:
        return False
    middle = y + h / 2
    gap = _RULE_LETTERING_GAP_PX
    return bool(np.any((glyphs[:, 0] < x + w + gap) & (x - gap < glyphs[:, 0] + glyphs[:, 2])
                       & (glyphs[:, 1] - gap <= middle) & (middle <= glyphs[:, 1] + glyphs[:, 3] + gap)))


def drop_colour_rules(mask, roi) -> None:
    """Clears, in place, the part of the mask that lies on a thin coloured line, such as the
    red rules a credit line sits between. They are decoration, not lettering, and erasing
    one left the layout missing half its frame. The line is found in the colour crop, not
    the mask: the mask's halo joins it to the lettering beside it. Lettering is not this
    thin, long and coloured (a dash is dark ink), so it is never taken for one."""
    import cv2
    coloured = (_channel_spread(roi) > _RULE_SPREAD).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(coloured, connectivity=8)
    rules = np.zeros(mask.shape, np.uint8)
    thin = {i for i in range(1, count)
            if min(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) <= _RULE_MAX_THICKNESS_PX
            and max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= _RULE_MIN_LENGTH_PX}
    glyphs = np.array([stats[j, :4] for j in range(1, count) if j not in thin
                       and min(stats[j, cv2.CC_STAT_WIDTH], stats[j, cv2.CC_STAT_HEIGHT]) >= _RULE_LETTERING_MIN_SPAN_PX
                       and stats[j, cv2.CC_STAT_AREA] >= _RULE_LETTERING_MIN_AREA_PX], dtype=float).reshape(-1, 4)
    for i in thin:
        if not _beside_coloured_lettering(stats, i, glyphs):
            rules[labels == i] = 1
    if rules.any():
        mask[cv2.dilate(rules, np.ones((5, 5), np.uint8)) > 0] = 0


def _erase_mask(gray_roi, keep, spare_art: bool, on_art: bool, roi=None):
    """(uint8 mask of the lettering to erase in a crop, whether it is a single line's strokes).
    `keep` is the balloon's inside, or None for a box with no outline."""
    if keep is not None:
        mask = _lettering_mask(_without_hatching(gray_roi, keep), wide=True)
        mask[~keep] = 0
        _drop_rim_rings(mask, keep)
        return mask, False
    mask = _lettering_mask(gray_roi)
    share = (mask > 0).mean()
    line = _strokes_on_a_line(gray_roi) if on_art and share > scanlate_tuning.LINE_ONLY_MASK_SHARE else None
    if line is not None:
        return line[0], True
    strokes = (_bright_strokes(gray_roi, roi)
               if on_art and not background_is_dark(gray_roi) and share > BRIGHT_STROKE_MASK_SHARE else None)
    if strokes is not None:
        return strokes, False
    if spare_art:
        _drop_marks_running_off_edge(mask, roi)
    elif on_art:
        _drop_hollow_frames(mask, gray_roi)
    return mask, False


def inpaint_region(image_path: str, box: dict, out_path: str | None = None, padding: int = 4,
                    backend: str = "auto", hf_token: str | None = None, image=None, inside=None,
                    spare_art: bool = False, on_art: bool = False, grow: float = 1.0):
    """Removes text within `box` so the translated text has a clean
    background. Returns the path to the (possibly newly-created) cleaned
    image; if out_path is None, overwrites nothing and returns an image
    array instead.

    backend='cv' (free, plain OpenCV inpainting), 'ml' (LaMa-manga,
    shape-aware, not just a rectangular inset), or
    'auto' (default) -- use LaMa-manga if its weights
    are already cached locally, OpenCV otherwise. If the ML backend is
    picked (explicitly or via auto) but can't actually run, this falls
    back to OpenCV inpainting and raises InpaintModelUnavailable with
    the cleaned image still attached, same fallback shape as
    detect_bubbles().

    image: the page already decoded as a BGR array. It is erased in place
    (image_path isn't read), so a render can clean every bubble on one
    decoded page and encode the result once.

    inside: optional boolean (box h, box w) array, True inside the balloon's
    outline (scanlate_balloons.bubble_erase_mask()). Only marks inside
    it are erased: the box's corners hold the balloon's outline and the art
    around it, which erasing every dark pixel in the rectangle used to wipe
    into a white block.

    spare_art: for a balloon whose outline wasn't found (`inside` is None),
    leave dark marks that run off the box edge. Lettering sits inside the
    balloon, so a large mark touching the edge is the art the balloon is
    drawn over, and erasing it smears white across the box.

    on_art: the region is a sign, lettering on the artwork rather than in a
    balloon or box: pale lettering on dark or coloured art is painted out with
    the art's own tone or a full lettering mask, since an outline-only mask
    left the letters' fill and glow behind as ghost text. A dark box that is a
    balloon or narration panel must not take the colours round it, so only
    signs opt in.

    grow: above 1.0 widens the erased area around the lettering (a stronger erase for ghosts
    that survive the default), by about 2px per 0.1 step, still only inside `inside`."""
    import cv2
    img = image if image is not None else cv2.imread(image_path)
    h, w = img.shape[:2]
    x = max(0, box["x"] - padding)
    y = max(0, box["y"] - padding)
    bw = min(w - x, box["w"] + 2 * padding)
    bh = min(h - y, box["h"] + 2 * padding)

    roi = img[y:y + bh, x:x + bw]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    keep = _inside_in_crop(inside, box, x, y, bw, bh) if inside is not None else None
    mask, line_only = _erase_mask(gray_roi, keep, spare_art, on_art, roi)
    drop_colour_rules(mask, roi)
    mask = _grown_erase_mask(mask, grow, keep if inside is not None else None)

    inpainted_roi = _dark_strip_inpaint(roi) if inside is None and on_art else None
    if inpainted_roi is not None:
        pass
    elif inside is None and on_art and is_coloured_art(roi) and _pale_pixels(roi).any():
        # An outline-only mask left the letters' fill and glow behind as ghosts.
        mask = _dark_art_lettering_mask(roi)
    elif inside is None and on_art and (heavy := _heavy_dark_lettering(img, x, y, bw, bh)) is not None:
        mask = heavy
    elif inside is None and (spare_art or on_art) and background_is_dark(gray_roi):
        inpainted_roi = _dark_art_inpaint(img, x, y, bw, bh, backend, hf_token)
    fallback_error = None
    paper_fill = (_plain_paper_fill(roi, mask, keep)
                  if inside is not None and not background_is_dark(gray_roi) else None)
    if paper_fill is not None and not (mask.astype(bool) & ~paper_fill[0]).any():
        # LaMa and OpenCV repaint only mask pixels, and the paper fill then overwrites
        # every one of them, so running either changes no pixel and only costs time.
        inpainted_roi = roi.copy()
    resolved = "cv" if inpainted_roi is not None else _resolve_inpaint_backend(backend)
    if resolved == "ml" and inpainted_roi is None:
        context = _ML_CONTEXT_PX if inside is None and on_art else 0
        inpainted_roi, fallback_error = _try_ml_inpaint_with_context(
            img, (x, y, bw, bh), mask, hf_token, backend, context)

    if inpainted_roi is None:
        inpainted_roi = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    if inside is not None and not background_is_dark(gray_roi):
        inpainted_roi = _paint_paper(inpainted_roi, paper_fill)
    elif inside is None and on_art and not line_only and not background_is_dark(gray_roi) and mask.any():
        inpainted_roi = _flatten_around_lettering(roi, inpainted_roi, mask)
    img[y:y + bh, x:x + bw] = inpainted_roi

    if out_path:
        cv2.imwrite(out_path, img)
    result = out_path or img

    if fallback_error is not None:
        fallback_error.fell_back_to_cv = True
        # Attach the already-produced result so a caller that wants it
        # doesn't have to redo the OpenCV pass itself.
        fallback_error.result = result
        raise fallback_error
    return result


_ERASE_CONTEXT_PX = 24


def erase_box(img, box: dict) -> None:
    """Paints out every pixel of `box` on `img` (BGR, in place), for a logo or
    stamp. OpenCV, from a margin of surrounding art: the LaMa-manga model is
    trained on balloons and fills a large patch of artwork with flat pale
    grey, and OpenCV given only the box has no pixels to draw from."""
    import cv2
    h, w = img.shape[:2]
    x0, y0 = max(0, box["x"] - _ERASE_CONTEXT_PX), max(0, box["y"] - _ERASE_CONTEXT_PX)
    x1 = min(w, box["x"] + box["w"] + _ERASE_CONTEXT_PX)
    y1 = min(h, box["y"] + box["h"] + _ERASE_CONTEXT_PX)
    roi = img[y0:y1, x0:x1]
    mask = np.zeros(roi.shape[:2], dtype=np.uint8)
    mask[box["y"] - y0:box["y"] - y0 + box["h"], box["x"] - x0:box["x"] - x0 + box["w"]] = 255
    roi[:] = cv2.inpaint(roi, mask, 7, cv2.INPAINT_TELEA)


def inpaint_mask_region(image_path: str, mask, out_path: str | None = None, padding: int = 4,
                         backend: str = "auto", hf_token: str | None = None):
    """Manual erase/heal brush: inpaints exactly the
    pixels the person painted, independent of any detected bubble box --
    a sound effect, background text, or a stray detection artifact the
    auto/manual bubble tools never touch.

    mask: a 2D array the same height/width as the source image; any
    nonzero pixel is erased (this is the raw brush-stroke mask -- unlike
    inpaint_region(), there's no "text is dark pixels inside a light
    bubble" heuristic here, because the person is manually choosing what
    to remove, not detecting text). backend/hf_token: same as
    inpaint_region(). Raises InpaintModelUnavailable the same way, with
    the OpenCV-inpainted result still attached, if the ML backend can't
    run."""
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    mask = np.asarray(mask)
    if mask.shape[:2] != (h, w):
        raise ValueError(
            f"mask shape {mask.shape[:2]} doesn't match the image {(h, w)}")

    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        # Nothing painted -- return the image unchanged.
        if out_path:
            cv2.imwrite(out_path, img)
            return out_path
        return img

    x0, x1 = max(0, int(xs.min()) - padding), min(w, int(xs.max()) + 1 + padding)
    y0, y1 = max(0, int(ys.min()) - padding), min(h, int(ys.max()) + 1 + padding)
    roi = img[y0:y1, x0:x1]
    roi_mask = (mask[y0:y1, x0:x1] > 0).astype("uint8") * 255
    roi_mask = cv2.dilate(roi_mask, np.ones((3, 3), np.uint8), iterations=1)

    resolved = _resolve_inpaint_backend(backend)
    inpainted_roi = None
    fallback_error = None
    if resolved == "ml":
        try:
            inpainted_roi = _run_ml_inpaint(roi, roi_mask, hf_token=hf_token,
                                            allow_download=backend == "ml")
        except ImportError as exc:
            fallback_error = InpaintModelUnavailable(
                "ML inpainting needs extra packages:\n"
                "    pip install torch safetensors huggingface_hub\n\n"
                "Falling back to plain OpenCV inpainting for this brush stroke."
            )
            fallback_error.__cause__ = exc
        except Exception as exc:
            fallback_error = InpaintModelUnavailable(
                f"LaMa-manga inpainting failed: {type(exc).__name__}: {exc}\n\n"
                "Falling back to plain OpenCV inpainting for this brush stroke."
            )
            fallback_error.__cause__ = exc

    if inpainted_roi is None:
        inpainted_roi = cv2.inpaint(roi, roi_mask, 5, cv2.INPAINT_TELEA)
    img[y0:y1, x0:x1] = inpainted_roi

    if out_path:
        cv2.imwrite(out_path, img)
    result = out_path or img

    if fallback_error is not None:
        fallback_error.fell_back_to_cv = True
        fallback_error.result = result
        raise fallback_error
    return result
