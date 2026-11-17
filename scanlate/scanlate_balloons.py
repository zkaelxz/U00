"""Balloon shapes: sorting text regions into kinds, and the masks of a balloon's inside, outside and erase area."""

import numpy as np

from scanlate import scanlate_tuning
from scanlate.scanlate_page_decode import page_bgr, page_gray


# ---------------------------------------------------------------------------
# Text region classification: bubbles vs signs vs SFX
# ---------------------------------------------------------------------------

TEXT_REGION_KINDS = {
    "bubble": "Dialogue inside a speech balloon",
    "narration": "Narration or caption box (usually rectangular)",
    "sign": "Text that's part of the artwork -- signs, banners, letters, screens",
    "sfx": "Sound effect lettering",
    "thought": "Thought bubble (cloud-shaped or dashed outline)",
}


def classify_text_regions(image_path: str, boxes, image=None):
    """
    Sorts detected text regions into bubbles, narration boxes, signs, and
    sound effects using cheap local geometry -- no model download.

    Why it matters: these want different treatment. Bubbles get clean
    inpaint-and-replace. Signs are part of the art and are usually better
    served by a small overlay or a margin note than by painting over the
    artwork. SFX often shouldn't be touched at all, since the lettering
    IS the art.

    Heuristic and imperfect -- exposed for review rather than applied
    silently. `image`: the page already decoded as BGR (see page_bgr()).
    """
    import cv2

    img = page_bgr(image_path, image)
    if img is None:
        return [dict(b, kind="bubble", kind_confidence=0.0) for b in boxes]
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    out = []
    for b in boxes:
        x, y = max(0, b["x"]), max(0, b["y"])
        bw, bh = min(b["w"], w - x), min(b["h"], h - y)
        if bw <= 0 or bh <= 0:
            out.append(dict(b, kind="bubble", kind_confidence=0.0))
            continue
        roi = gray[y:y + bh, x:x + bw]

        # Bubbles/narration sit on a light, uniform fill; art does not.
        mean_val = float(roi.mean())
        # Sample the border ring -- a bubble's surroundings differ from its interior.
        pad = 6
        oy1, oy2 = max(0, y - pad), min(h, y + bh + pad)
        ox1, ox2 = max(0, x - pad), min(w, x + bw + pad)
        outer = gray[oy1:oy2, ox1:ox2]
        outer_mean = float(outer.mean()) if outer.size else mean_val

        aspect = bw / max(bh, 1)
        fill_uniformity = 1.0 - min(float(roi.std()) / 80.0, 1.0)
        area_frac = (bw * bh) / (h * w)

        # A balloon's background is light however dense its lettering: the
        # share of light pixels says so, where the mean and spread used to
        # call any well-filled balloon on a white panel a sign.
        light_share = float((roi > scanlate_tuning.CLASSIFY_LIGHT_LEVEL).mean())
        if light_share >= scanlate_tuning.CLASSIFY_MIN_LIGHT_SHARE:
            # Light interior -- a balloon or a caption box.
            # Caption boxes tend to be wide, short, and page-edge aligned.
            edge_aligned = x < w * 0.05 or (x + bw) > w * 0.95
            kind = "narration" if (aspect > 3.0 and edge_aligned) else "bubble"
            conf = 0.75
        elif _holds_balloon_interior(roi):
            # A balloon shaded grey inside is no lighter than art, so the light
            # share can't tell it from a sign; its outline can.
            kind = "bubble"
            conf = 0.6
        elif mean_val < 140 and fill_uniformity < 0.4:
            # Dark and busy -- lettering laid over artwork.
            kind = "sfx"
            conf = 0.5
        elif abs(mean_val - outer_mean) < 12:
            # Blends into its surroundings -- part of the art, not a balloon.
            kind = "sign"
            conf = 0.5
        else:
            kind = "bubble"
            conf = 0.35

        if area_frac > 0.30:
            kind, conf = "sign", 0.4  # implausibly large for a balloon

        out.append(dict(b, kind=kind, kind_confidence=conf))
    return out


def _holds_balloon_interior(roi) -> bool:
    """True when an outlined balloon interior fills most of `roi` (gray)."""
    found = _balloon_interior(roi)
    return found is not None and float(found[0].mean()) >= scanlate_tuning.CLASSIFY_BALLOON_FILL


def _balloon_interior(roi):
    """(filled interior, light-outside mask) of the balloon under the centre
    of `roi` (gray), or None. The interior is the light region, labelled with
    its outline intact, whose outline (holes filled) encloses the centre;
    the centre usually falls on a letter, so the region itself rarely covers
    it. When the box is wider than the balloon, the paper around it is a
    ring that also "encloses" the centre, so a region touching the box edge
    loses to a nested one that doesn't, if that one is big enough to be the
    balloon. The outside mask is the light paper that touches the box
    edge and isn't the interior.

    A balloon shaded to grey (a gradient inside) has no pixel as light as
    CV_LIGHT_FLOOR, so when that finds nothing the search repeats at the grey
    level, accepting only a region ringed by dark pixels (its outline, or dark
    art against it)."""
    import cv2

    otsu_val, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    found = _interior_above(roi, max(otsu_val, scanlate_tuning.CV_LIGHT_FLOOR))
    if found is not None:
        return found
    found = _interior_above(roi, scanlate_tuning.CV_GREY_PAPER_FLOOR)
    if found is not None and _ringed_by_dark(roi, found[0]):
        return found
    return None


def _ringed_by_dark(roi, filled) -> bool:
    """True when the dark pixels hug `filled` where it is not cut by the box edge:
    an outline, as opposed to a mid-grey patch of art fading into its surroundings."""
    import cv2

    rim = cv2.dilate(filled, np.ones((2 * 3 + 1, 2 * 3 + 1), np.uint8)) & (1 - filled)
    rim[0, :] = rim[-1, :] = 0
    rim[:, 0] = rim[:, -1] = 0
    if not rim.any():
        return False
    return float((roi[rim > 0] < scanlate_tuning.CV_GREY_RIM_LEVEL).mean()) >= scanlate_tuning.CV_GREY_RIM_DARK_SHARE


def _interior_above(roi, level):
    """_balloon_interior()'s search with the light paper being gray >= `level`."""
    import cv2

    _, light = cv2.threshold(roi, level - 1, 255, cv2.THRESH_BINARY)
    seal = int(scanlate_tuning.CV_SEAL_PX)
    kernel = np.ones((2 * seal + 1, 2 * seal + 1), np.uint8)
    if seal > 0:
        light = cv2.erode(light, kernel)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(light, connectivity=4)
    rh, rw = labels.shape
    cy, cx = rh // 2, rw // 2

    def touches_edge(i):
        x, y, w, h = stats[i, :4]
        return x == 0 or y == 0 or x + w == rw or y + h == rh

    chosen = None
    for i in sorted(range(1, n), key=lambda k: -stats[k, cv2.CC_STAT_AREA]):
        x, y, w, h = stats[i, :4]
        # Only a region whose bounding box holds the centre can enclose it;
        # checking that first keeps tone and art (thousands of tiny regions) cheap.
        if not (x <= cx < x + w and y <= cy < y + h):
            continue
        if chosen is not None and stats[i, cv2.CC_STAT_AREA] < 0.3 * rh * rw:
            break
        region = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidate = np.zeros_like(region)
        cv2.drawContours(candidate, contours, -1, 1, thickness=-1)
        if not candidate[cy - y, cx - x]:
            continue
        filled = np.zeros((rh, rw), np.uint8)
        filled[y:y + h, x:x + w] = candidate
        if chosen is not None:
            chosen = (filled, i)
            break
        chosen = (filled, i)
        if not touches_edge(i):
            break
    if chosen is None:
        return None
    filled, label = chosen
    on_edge = set(np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])))
    outside = np.isin(labels, [k for k in on_edge if k not in (0, label)]).astype(np.uint8)
    if seal > 0:
        filled = cv2.dilate(filled, kernel)
        outside = cv2.dilate(outside, kernel)
    return filled, outside


def _with_page_cut_lettering(filled, outside, area, page_shape):
    """(filled, outside) of _balloon_interior() for a balloon the page's edge cuts. The balloon
    runs on past that edge, so paper meeting the box's edge only there, between the places
    where the balloon itself meets it, is its own, not outside it: the gaps in a line of
    lettering the page cuts off, which as outside paper kept that line from being erased.
    Paper reaching the page's edge beside the balloon stays out."""
    import cv2

    x0, y0, x1, y1 = area
    ph, pw = page_shape[:2]
    on_page_edge = (y0 == 0, y1 == ph, x0 == 0, x1 == pw)
    if not any(on_page_edge):
        return filled, outside

    def sides(a):
        return a[0], a[-1], a[:, 0], a[:, -1]

    spans = []
    for line in sides(filled):
        cut = np.flatnonzero(line)
        spans.append((cut[0], cut[-1]) if cut.size else (1, 0))

    def held(labels: np.ndarray) -> np.ndarray:
        out = np.zeros(labels.max() + 1, bool)
        for side, page_edge, (lo, hi) in zip(sides(labels), on_page_edge, spans):
            if page_edge:
                beside = np.ones(side.size, bool)
                beside[lo + 1:hi] = False
                out[np.unique(side[beside])] = True
            else:
                out[np.unique(side)] = True
        return out

    paper = np.asarray(cv2.connectedComponents(np.asarray(outside, np.uint8), connectivity=8)[1], np.int32)
    page_cut = (paper > 0) & ~held(paper)[paper]
    return ((filled > 0) | page_cut).astype(np.uint8), ((np.asarray(outside) > 0) & ~page_cut).astype(np.uint8)


def _roi(gray, box):
    ph, pw = gray.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(pw, box["x"] + box["w"]), min(ph, box["y"] + box["h"])
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _to_box_mask(box, area, part):
    x0, y0, x1, y1 = area
    mask = np.zeros((box["h"], box["w"]), dtype=bool)
    mask[y0 - box["y"]:y1 - box["y"], x0 - box["x"]:x1 - box["x"]] = part.astype(bool)
    return mask


def bubble_shape_mask(image_path: str, box: dict, margin: int = 4, image=None):
    """The bubble's real (often oval/irregular) interior inside `box`, as
    a boolean (h, w) array for render_text_in_box(mask=...) -- found the way
    detect_bubbles_cv() finds bubbles (see _balloon_interior()), then eroded
    by `margin` px so text keeps off the outline. None when there's no
    usable light region there (the box sits on artwork, or the region covers
    under 30% of the box) -- callers then keep the plain rectangle layout.
    `image`: the page already decoded, gray or BGR (see page_gray())."""
    import cv2

    gray = page_gray(image_path, image)
    if gray is None or box["w"] <= 2 * margin or box["h"] <= 2 * margin:
        return None
    area = _roi(gray, box)
    if area is None:
        return None
    x0, y0, x1, y1 = area
    found = _balloon_interior(gray[y0:y1, x0:x1])
    if found is None:
        return None
    filled = found[0]
    if margin > 0:
        filled = cv2.erode(filled, np.ones((2 * margin + 1, 2 * margin + 1), np.uint8))
    mask = _to_box_mask(box, area, filled)
    if mask.sum() < 0.3 * box["w"] * box["h"]:
        return None
    return mask


def balloon_outside_mask(image_path: str, box: dict, image=None):
    """Boolean (h, w) array of `box`: True on the light paper that reaches the box's edge
    outside the balloon under its centre (see _balloon_interior()). None when no balloon
    is found there."""
    gray = page_gray(image_path, image)
    if gray is None:
        return None
    area = _roi(gray, box)
    if area is None:
        return None
    x0, y0, x1, y1 = area
    found = _balloon_interior(gray[y0:y1, x0:x1])
    if found is None:
        return None
    return _to_box_mask(box, area, found[1])


def _tone_from_edge(roi, filled):
    """uint8 mask of `roi`'s flat grey areas (lighter than an outline, darker than
    paper, at least ERASE_FLAT_TONE_PX across) outside `filled` that connect to
    the box edge."""
    import cv2

    t = scanlate_tuning
    tone = ((roi >= t.CV_GREY_RIM_LEVEL) & (roi < t.CV_LIGHT_FLOOR) & (filled == 0)).astype(np.uint8)
    tone = cv2.morphologyEx(tone, cv2.MORPH_OPEN, np.ones((t.ERASE_FLAT_TONE_PX,) * 2, np.uint8))
    _, labels = cv2.connectedComponents(tone, connectivity=4)
    on_edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    return np.isin(labels, on_edge[on_edge > 0]).astype(np.uint8)


def _art_from_edge(roi, filled, outside):
    """bool mask of `roi`'s solid areas of ink or tone (at least ERASE_SOLID_ART_PX
    across) outside `filled` that connect to the box edge, with their one-pixel
    rim. The outline and lettering are thinner, so they stay out."""
    import cv2

    t = scanlate_tuning
    ink = ((filled == 0) & (outside == 0) & (roi < t.CV_LIGHT_FLOOR)).astype(np.uint8)
    solid = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((t.ERASE_SOLID_ART_PX,) * 2, np.uint8))
    _, labels = cv2.connectedComponents(solid, connectivity=4)
    on_edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    solid = np.isin(labels, on_edge[on_edge > 0]).astype(np.uint8)
    return (cv2.dilate(solid, np.ones((3, 3), np.uint8)) & ink) > 0


def bubble_erase_mask(image_path: str, box: dict, image=None):
    """Where cleanup may erase inside `box`: the balloon's interior plus any
    lettering joined to its outline (a stroke touching the outline bends the
    light region's edge around it, so the interior alone would keep that
    stroke). Those strokes are the marks inside the interior's convex hull
    that sit deeper than the outline is thick, measured from the paper or
    tone outside; art beyond the outline is nearer that than this, or
    outside the hull. None as bubble_shape_mask() returns it."""
    import cv2

    gray = page_gray(image_path, image)
    if gray is None:
        return None
    area = _roi(gray, box)
    if area is None:
        return None
    x0, y0, x1, y1 = area
    roi = gray[y0:y1, x0:x1]
    found = _balloon_interior(roi)
    if found is None:
        return None
    filled, outside = found
    if filled.sum() < 0.3 * box["w"] * box["h"]:
        return None
    filled, outside = _with_page_cut_lettering(filled, outside, area, gray.shape)
    if outside.any():
        depth = cv2.distanceTransform((1 - outside).astype(np.uint8), cv2.DIST_L2, 3)
        edge = filled - cv2.erode(filled, np.ones((3, 3), np.uint8))
        thickness = float(np.median(depth[edge > 0])) if edge.any() else 0.0
        # A mark must also be that deep from the non-ink tone outside: grey art
        # beside the outline is as far from the paper as lettering, so where a
        # tail makes the hull bridge a dent, that art and the outline along it
        # were erased to a straight white edge. The thickness stays measured
        # from the paper: measured from the tone it shrinks, and dark art
        # against the outline (hair, burst spikes) would then pass as lettering.
        clear = np.logical_or(np.asarray(outside) > 0, _tone_from_edge(roi, filled) > 0)
        depth = cv2.distanceTransform((~clear).astype(np.uint8), cv2.DIST_L2, 3)
        contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = np.zeros_like(filled)
        cv2.fillPoly(hull, [cv2.convexHull(np.vstack(contours))], 1)
        # Dark art in a dent the hull bridges (a jacket or brick wall between the
        # bumps of a burst) is as far from the paper as lettering, and was erased
        # to a straight white edge; lettering joined to the outline is thinner.
        joined = (hull > 0) & (depth > thickness + 1) & ~_art_from_edge(roi, filled, outside)
        filled = filled | joined.astype(np.uint8)
    elif filled.mean() >= scanlate_tuning.BALLOON_FILLS_BOX:
        # No paper outside the balloon in the box: the box lies within it.
        filled = np.ones_like(filled)
    # Otherwise the balloon sits on dark art or tone, so no paper surrounds
    # it, yet part of the box is not balloon: only its interior is cleared.
    # Clearing the whole box wiped the outline and the art along it into a
    # white block.
    return _to_box_mask(box, area, filled)
