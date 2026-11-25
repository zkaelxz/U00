"""
scanlate_art_text.py -- lettering drawn straight onto the artwork (titles,
narration over a splash page): find it, paint it out and set English over it.

The bubble detector never sees this text, so the page's other steps leave it
Chinese. Finding it needs the OCR detector (a brightness rule also matches
lightning and highlights); painting it out needs the LaMa-manga model, because
plain OpenCV inpainting leaves ghosts of the letters on busy art.
"""

import numpy as np

from scanlate.scanlate_inpaint import background_is_dark
from scanlate.scanlate_watermark import looks_like_site_mark

# Paddle's detector runs out of memory on a full-height webtoon page, so the
# page is shrunk to this on its longest side for detection only.
_DETECT_LIMIT_SIDE = 1280

_MIN_LINE_HEIGHT = 28          # px; smaller marks are watermarks, not captions
_MIN_CONFIDENCE = 0.6
_NEUTRAL_INK_SATURATION = 50   # below this channel spread, ink is black or grey
_NEUTRAL_INK_SHARE = 0.4       # the share of neutral ink from which the lettering counts as neutral
_TAG_SATURATION = 100          # decoration this colourful is kept when the lettering is neutral
_MASK_GROW = 27                # covers the glow around the letters
# A ground whose median gray is below this but not dark (a dim wall, an overcast sky) can carry
# lettering darker and lighter than itself in one title; only the dark part was erased, and
# white or pale-grey words stayed under the English.
_MID_GROUND_MAX_GRAY = 200
_MID_GROUND_LIGHT_INK_LIFT = 50
_HAIRLINE_PX = 5               # strokes thinner than this are rays and rules, not lettering
# Stroke thickness over line height from which lettering counts as bold. CJK
# print strokes run about 0.07-0.08 of the glyph height, heavy weights 0.12+.
_BOLD_STROKE_RATIO = 0.10
# A caption cut by a page break ends this close to the edge; OCR boxes rarely
# reach the last pixel row.
_EDGE_PX = 24
# Fewer than this many letters or characters is a speck the detector read as a
# glyph ("0", "4", one stray kanji), never a caption worth painting over.
_MIN_CAPTION_CHARS = 3


def _overlap(a: dict, b: dict) -> float:
    """Share of box `a` that lies inside box `b`."""
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy / float(a["w"] * a["h"])


def group_lines(lines: list) -> list:
    """Joins text lines that belong to one caption (stacked closely, of one size,
    sharing horizontal extent, not vertical columns) into one box with the
    lines' texts in reading order. lines: [{"x","y","w","h","text"}, ...]."""
    ordered = sorted(lines, key=lambda l: (l["y"], l["x"]))
    groups = []
    for line in ordered:
        for g in groups:
            last = g["lines"][-1]
            gap = line["y"] - (last["y"] + last["h"])
            overlap_x = min(line["x"] + line["w"], g["x"] + g["w"]) - max(line["x"], g["x"])
            same_size = 0.7 < line["h"] / float(last["h"]) < 1.4
            horizontal = line["w"] >= line["h"] and last["w"] >= last["h"]
            if (same_size and horizontal and gap < 0.8 * max(line["h"], last["h"])
                    and overlap_x > 0.3 * min(line["w"], g["w"])):
                g["lines"].append(line)
                x0, y0 = min(g["x"], line["x"]), min(g["y"], line["y"])
                x1 = max(g["x"] + g["w"], line["x"] + line["w"])
                y1 = max(g["y"] + g["h"], line["y"] + line["h"])
                g.update(x=x0, y=y0, w=x1 - x0, h=y1 - y0)
                break
        else:
            groups.append({"x": line["x"], "y": line["y"], "w": line["w"], "h": line["h"],
                           "lines": [line]})
    return [{"x": g["x"], "y": g["y"], "w": g["w"], "h": g["h"],
             "source_text": "".join(l["text"] for l in g["lines"]),
             # The weakest line decides: one misread line makes the caption worth a second look.
             "ocr_confidence": min((l["conf"] for l in g["lines"] if "conf" in l), default=None)}
            for g in groups]


def continues_across_pages(upper: dict, lower: dict, upper_page_h: int) -> bool:
    """True when `lower` (a caption box on the next page, top first) carries on
    `upper` (a caption box on the previous page): the upper one ends at the
    page's bottom edge, the lower one starts at the next page's top edge and
    they share horizontal extent. Vertical columns are left alone."""
    if upper["h"] > 1.4 * upper["w"] or lower["h"] > 1.4 * lower["w"]:
        return False
    if upper["y"] + upper["h"] < upper_page_h - _EDGE_PX or lower["y"] > _EDGE_PX:
        return False
    overlap_x = min(upper["x"] + upper["w"], lower["x"] + lower["w"]) - max(upper["x"], lower["x"])
    return overlap_x > 0.5 * min(upper["w"], lower["w"])


def split_by_source_share(text: str, upper_src: str, lower_src: str):
    """Cuts `text` at a word boundary so the two halves follow the share of the
    source each page's fragment held. Returns None when either half would be
    empty: the caller then keeps the per-page translations."""
    words = text.split()
    total = len(upper_src) + len(lower_src)
    if len(words) < 2 or not total:
        return None
    cut = min(max(1, round(len(words) * len(upper_src) / total)), len(words) - 1)
    return " ".join(words[:cut]), " ".join(words[cut:])


def _is_caption_text(text: str) -> bool:
    """False for a speck ("0", "4") and for Latin-only text, which is already
    English (a jacket's printed words) and is only damaged by being painted over."""
    significant = [c for c in text if c.isalpha() or "\u3040" <= c <= "\u9fff"]
    if len(significant) < _MIN_CAPTION_CHARS:
        return False
    return not all(c.isascii() for c in significant)


def _lines_with_stamps(read_lines, page, lang: str, scale: float, backend: str | None) -> list:
    """The OCR lines of `read_lines`, plus the site-stamp lines PaddleOCR finds
    when RapidOCR is the reader. RapidOCR's detector misses a site's faint
    overlay stamp on art that Paddle's finds; Paddle is not needed otherwise, so
    a missing install leaves the RapidOCR lines alone."""
    lines = list(read_lines(page, lang, scale))
    if backend != "rapidocr":
        return lines
    import ocr
    from memory_headroom import HeadroomError
    try:
        extra = ocr.paddle_lines(page, lang, scale)
    except HeadroomError:
        raise
    except Exception:
        return lines
    known = [{"x": x, "y": y, "w": w, "h": h} for _, _, (x, y, w, h) in lines]
    for text, conf, (x, y, w, h) in extra:
        box = {"x": x, "y": y, "w": w, "h": h}
        if looks_like_site_mark(text) and not any(_overlap(box, k) > 0.5 for k in known):
            lines.append((text, conf, (x, y, w, h)))
    return lines


def find_art_captions(image_path: str, lang: str, existing_boxes: list, image=None,
                      backend: str | None = None) -> list:
    """Caption boxes on the artwork that no existing region covers, as
    {"x","y","w","h","source_text"} in full-page pixels. Lines are read with
    RapidOCR when that is the page OCR backend, else with PaddleOCR."""
    import ocr
    from scanlate.scanlate_page_decode import decode_page
    page = image if image is not None else decode_page(image_path)
    h, w = page.shape[:2]
    scale = min(1.0, _DETECT_LIMIT_SIDE / float(max(h, w)))
    lines = []
    read_lines = ocr.rapidocr_lines if backend == "rapidocr" else ocr.paddle_lines
    for text, conf, (x, y, bw, bh) in _lines_with_stamps(read_lines, page, lang, scale, backend):
        # A site's overlay stamp is small by design, which is why small marks are
        # ignored; but it has to be found to be skipped and painted out.
        small = bh < _MIN_LINE_HEIGHT and not looks_like_site_mark(text)
        if conf < _MIN_CONFIDENCE or not text.strip() or small:
            continue
        box = {"x": x, "y": y, "w": bw, "h": bh}
        if any(_overlap(box, e) > 0.5 for e in existing_boxes):
            continue
        lines.append({**box, "text": text.strip(), "conf": conf})
    return [g for g in group_lines(lines) if _is_caption_text(g["source_text"])]


def caption_mask(page, box: dict):
    """Boolean (h, w) mask of the lettering pixels in and just around `box`:
    the bright (or, on a light background, dark) pixels, grown to take the glow."""
    import cv2
    ph, pw = page.shape[:2]
    pad = _MASK_GROW
    x0, y0 = max(0, box["x"] - pad), max(0, box["y"] - pad)
    x1, y1 = min(pw, box["x"] + box["w"] + pad), min(ph, box["y"] + box["h"] + pad)
    gray = cv2.cvtColor(page[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    if background_is_dark(gray):
        ink = gray > 180
    else:
        ink = gray < 110
        colour = page[y0:y1, x0:x1].astype(np.int16)
        # Channel spread, not HSV saturation: near-black ink on a blue sky has a high
        # saturation although it is neutral.
        saturation = colour.max(axis=2) - colour.min(axis=2)
        # Neutral lettering over a box that also holds a red tag or rule: the dark red
        # counted as ink and was painted out with the title.
        if ink.any() and float((saturation[ink] < _NEUTRAL_INK_SATURATION).mean()) >= _NEUTRAL_INK_SHARE:
            ink &= saturation <= _TAG_SATURATION
        median = float(np.median(gray[_inside(gray.shape, box, x0, y0)]))
        if median < _MID_GROUND_MAX_GRAY:
            ink |= gray > median + _MID_GROUND_LIGHT_INK_LIFT
            ink = _without_marks_leaving(ink, (x0 > 0, y0 > 0, x1 < pw, y1 < ph))
    mask = np.zeros((ph, pw), np.uint8)
    inner = (ink & _inside(gray.shape, box, x0, y0)).astype(np.uint8) * 255
    mask[y0:y1, x0:x1] = cv2.dilate(inner, np.ones((_MASK_GROW, _MASK_GROW), np.uint8))
    for bx0, by0, bx1, by1 in decoration_blocks(page, box):
        mask[by0:by1, bx0:bx1] = 0
    return mask


def _without_marks_leaving(ink, open_sides):
    """`ink` without the marks that reach the edge of the crop on a side where the page goes
    on (`open_sides`: left, top, right, bottom). The crop is the caption's box plus a margin,
    so lettering stays inside it, while a window rail or the paper of a narration box beside
    the title runs on out of it and is art to keep."""
    import cv2
    h, w = ink.shape
    raw = ink.astype(np.uint8)
    # Marks are told apart without long straight runs (a rail drawn through the lettering)
    # or hairlines (a light ray crossing it): either joins the letters it touches into one
    # mark that leaves the crop, and the whole title stayed.
    runs = (cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((1, max(3, w // 2)), np.uint8))
            | cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((max(3, h // 2), 1), np.uint8)))
    solid = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((_HAIRLINE_PX, _HAIRLINE_PX), np.uint8))
    solid[cv2.dilate(runs, np.ones((5, 5), np.uint8)) > 0] = 0
    count, labels = cv2.connectedComponents(solid, connectivity=8)
    left, top, right, bottom = open_sides
    leaving = set()
    for edge, is_open in ((labels[:, 0], left), (labels[0, :], top),
                          (labels[:, -1], right), (labels[-1, :], bottom)):
        if is_open:
            leaving.update(np.unique(edge).tolist())
    leaving.discard(0)
    keep_out = runs
    if leaving:
        keep_out = keep_out | np.isin(labels, list(leaving)).astype(np.uint8)
    return ink & (cv2.dilate(keep_out, np.ones((5, 5), np.uint8)) == 0)


def _inside(shape, box, x0, y0):
    inside = np.zeros(shape, bool)
    inside[max(0, box["y"] - y0):box["y"] + box["h"] - y0,
           max(0, box["x"] - x0):box["x"] + box["w"] - x0] = True
    return inside


def _paint_out_over_dark_art(page, box: dict) -> bool:
    """Paints `box`'s outlined lettering out of `page` with the art's own tone,
    and says whether it did. The brightness mask in caption_mask() finds only the
    white outline, so both LaMa and OpenCV drew the glow and the letters' fill
    back as a grey smudge on dark art."""
    from scanlate.scanlate_inpaint import _dark_art_fill, background_is_dark
    import cv2
    ph, pw = page.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    w, h = min(pw - x, box["w"]), min(ph - y, box["h"])
    if w < 1 or h < 1 or not background_is_dark(cv2.cvtColor(page[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)):
        return False
    filled = _dark_art_fill(page, x, y, w, h)
    if filled is None:
        return False
    page[y:y + h, x:x + w] = filled
    return True


def paint_out_captions(page, boxes: list, backend: str = "auto"):
    """Erases each caption's lettering from `page` (a BGR array, changed in
    place) with the LaMa-manga model. Returns True when the model painted every
    caption, False when OpenCV did for any (its result is ghostier): the model
    isn't downloaded or can't load, or a caption was too large for it. Nothing
    is raised: the caller shows the OpenCV result with a note."""
    import cv2
    from scanlate.scanlate_inpaint import _resolve_inpaint_backend
    from scanlate.scanlate_lama import _run_ml_inpaint
    model_usable = _resolve_inpaint_backend(backend) == "ml"
    all_by_model = model_usable
    for box in boxes:
        mask = caption_mask(page, box)
        ys, xs = np.nonzero(mask)
        if not len(xs):
            continue
        if _paint_out_over_dark_art(page, box):
            continue
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        roi, roi_mask = page[y0:y1, x0:x1], mask[y0:y1, x0:x1]
        if model_usable:
            try:
                page[y0:y1, x0:x1] = _run_ml_inpaint(roi, roi_mask, allow_download=False)
                continue
            except ValueError:
                pass            # too large for the model: OpenCV for this caption only
            except Exception:
                model_usable = False
        all_by_model = False
        page[y0:y1, x0:x1] = cv2.inpaint(roi, roi_mask, 5, cv2.INPAINT_TELEA)
    return all_by_model


def _line_layout(ink, box: dict) -> tuple:
    """(line height, "left" or "center") of the original lettering, read from its
    ink pixels: the height of one line of glyphs (a column's width for vertical
    text), and whether every line starts at the same left edge."""
    if box["h"] > 1.4 * box["w"]:
        return box["w"], "center"
    profile = ink.sum(axis=1).astype(float)
    inked = profile > 0.12 * profile.max()
    edges = np.flatnonzero(np.diff(np.concatenate([[0], inked.view(np.int8), [0]])))
    lines = [(int(s), int(e)) for s, e in zip(edges[::2], edges[1::2]) if e - s >= 12]
    if not lines:
        return box["h"], "center"
    lefts = [int(np.flatnonzero(ink[s:e].any(axis=0))[0]) for s, e in lines]
    left_aligned = len(lines) > 1 and max(lefts) - min(lefts) < 0.05 * box["w"]
    return int(np.median([e - s for s, e in lines])), "left" if left_aligned else "center"


def _stroke_weight(ink, line_h: int) -> str:
    """"bold" or "regular" from the median stroke thickness of the ink pixels
    against the line height. Thickness is twice the distance to the nearest
    background pixel, read along each stroke's centre ridge."""
    import cv2
    dist = cv2.distanceTransform(ink.astype(np.uint8), cv2.DIST_L2, 3)
    ridge = (dist > 0) & (dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8)))
    if not ridge.any() or line_h <= 0:
        return "bold"
    return "bold" if 2 * float(np.median(dist[ridge])) / line_h >= _BOLD_STROKE_RATIO else "regular"


def caption_style(page, box: dict) -> dict:
    """The original lettering's look, sampled from the page before it is
    painted out: the fill is the typical bright (or dark) ink pixel, the glow the
    colour of the halo just outside the strokes, plus the line height, the
    alignment of the lines and whether the strokes are bold or regular."""
    import cv2
    mask = caption_mask(page, box)
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return {"fill": (255, 255, 255), "glow": None, "line_h": box["h"], "align": "center"}
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    region = page[y0:y1, x0:x1]
    reg_gray, reg_mask = gray[y0:y1, x0:x1], mask[y0:y1, x0:x1] > 0
    dark_bg = background_is_dark(reg_gray)
    ink = reg_mask & ((reg_gray > 180) if dark_bg else (reg_gray < 110))
    if not ink.any():
        return {"fill": (255, 255, 255) if dark_bg else (0, 0, 0), "glow": None,
                "line_h": box["h"], "align": "center"}
    line_h, align = _line_layout(ink, box)
    fill = tuple(int(v) for v in np.median(region[ink][:, ::-1], axis=0))
    ring = cv2.dilate(ink.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    ring &= ~cv2.erode(ink.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    ring &= ~ink
    glow = None
    if ring.any():
        pix = region[ring][:, ::-1].astype(float)
        brightest = pix[np.argsort(pix.sum(axis=1))[-max(1, len(pix) // 5):]]
        glow = tuple(int(v) for v in brightest.mean(axis=0)) if dark_bg else None
    return {"fill": fill, "glow": glow, "line_h": line_h, "align": align,
            "weight": _stroke_weight(ink, line_h)}


# The title's box also takes in the blank margin and its neighbours' padding, so English that
# filled the whole height ran into the credit line under it.
_CAPTION_MAX_HEIGHT_SHARE = 0.88
_DARK_FILL_LUMA = 110
_DECOR_MIN_AREA = 800
_DECOR_MIN_FILL = 0.5
_DECOR_GAP_PX = 8
_RULE_MAX_THICKNESS = 6
_RULE_MIN_LENGTH = 40
_RULE_SAME_COLOUR = 40


def decoration_blocks(page, box: dict) -> list:
    """Bounding rectangles (x0, y0, x1, y1, page pixels) of the solid coloured blocks inside
    `box`, such as the red chapter tag beside a logo title. The title's box spans the whole
    logo, so the tag was painted out with it and the new title was set over it."""
    import cv2
    ph, pw = page.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(pw, box["x"] + box["w"]), min(ph, box["y"] + box["h"])
    if x1 <= x0 or y1 <= y0:
        return []
    crop = page[y0:y1, x0:x1].astype(np.int16)
    coloured = ((crop.max(axis=2) - crop.min(axis=2)) > _TAG_SATURATION).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(coloured, connectivity=8)
    blocks = []
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area >= _DECOR_MIN_AREA and area >= _DECOR_MIN_FILL * w * h and w <= 0.5 * (x1 - x0):
            blocks.append((x0 + x, y0 + y, x0 + x + w, y0 + y + h))
    return blocks


def _stands_alone(crop, coloured, x: int, y: int, w: int, h: int) -> bool:
    """True when the strip (a thin coloured component of `crop`) has little of its own colour
    just above and below: a rule has plain ground either side, while a band of a colour fade
    borders rows of nearly the same colour."""
    inside = crop[y:y + h, x:x + w][coloured[y:y + h, x:x + w] > 0]
    colour = np.median(inside, axis=0)
    for side in (crop[max(0, y - 2):y, x:x + w], crop[y + h:y + h + 2, x:x + w]):
        if side.size and (np.abs(side - colour).max(axis=2) <= _RULE_SAME_COLOUR).mean() >= 0.2:
            return False
    return True


def between_rules(page, box: dict) -> dict:
    """`box` cut to its tallest stretch clear of the thin coloured rules inside it (the red lines
    a credit line sits between), or `box` itself with none. The English was set across a rule."""
    import cv2
    ph, pw = page.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(pw, box["x"] + box["w"]), min(ph, box["y"] + box["h"])
    if x1 <= x0 or y1 <= y0:
        return box
    crop = page[y0:y1, x0:x1].astype(np.int16)
    coloured = ((crop.max(axis=2) - crop.min(axis=2)) > _TAG_SATURATION).astype(np.uint8)
    count, _, stats, _ = cv2.connectedComponentsWithStats(coloured, connectivity=8)
    edges = [(0, 0)]
    for i in range(1, count):
        x, y, w, h, _area = stats[i]
        if h <= _RULE_MAX_THICKNESS and w >= _RULE_MIN_LENGTH and w >= 4 * h \
                and _stands_alone(crop, coloured, x, y, w, h):
            edges.append((y, y + h))
    if len(edges) == 1:
        return box
    edges.sort()
    edges.append((y1 - y0, y1 - y0))
    gaps = [(edges[i][1], edges[i + 1][0]) for i in range(len(edges) - 1)]
    top, bottom = max(gaps, key=lambda g: g[1] - g[0])
    if bottom - top < 0.3 * (y1 - y0):
        return box
    return {**box, "y": y0 + top + _DECOR_GAP_PX // 2, "h": bottom - top - _DECOR_GAP_PX}


def clear_of_decoration(page, box: dict) -> dict:
    """`box` narrowed so the English stays clear of its decoration blocks; each stays on the
    side it is on. `box` itself is kept when there are none, or when little room would
    be left."""
    left, right = box["x"], box["x"] + box["w"]
    middle = (left + right) / 2
    for bx0, _, bx1, _ in decoration_blocks(page, box):
        if (bx0 + bx1) / 2 >= middle:
            right = min(right, bx0 - _DECOR_GAP_PX)
        else:
            left = max(left, bx1 + _DECOR_GAP_PX)
    if right - left < 0.3 * box["w"]:
        return box
    return {**box, "x": left, "w": right - left}


def draw_caption(pil_img, box: dict, text: str, style: dict | None = None, font_path: str | None = None):
    """Sets `text` over a painted-out caption the way the original was lettered:
    same colour and glow, a size matched to the original glyphs (shrunk only to
    fit the box), centred or left-aligned like the original lines, bold only when the
    original strokes were. Without a glow (lettering on a light background) a
    dark outline keeps it readable."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    from scanlate.scanlate_fonts import _find_font
    style = style or {"fill": (255, 255, 255), "glow": None}
    align = style.get("align", "center")
    # Bold is the default for a style that carries no measured weight.
    path = _find_font(font_path, category=style.get("weight", "bold"))
    draw = ImageDraw.Draw(pil_img)
    words = text.split()
    line_h0 = style.get("line_h") or box["h"]
    if box["h"] > 1.4 * box["w"]:
        # Vertical CJK column: English can't stack, so it reads across the column's centre.
        max_w = max(box["w"] * 3, 1)
    else:
        max_w = box["w"]
    start = max(12, int(min(line_h0 * 0.85, box["h"])))
    for size in range(start, 11, -2):
        font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
        lines, cur = [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if cur and draw.textlength(trial, font=font) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
        line_h = int(size * 1.2)
        if (all(draw.textlength(l, font=font) <= max_w for l in lines)
                and line_h * len(lines) <= max(box["h"] * _CAPTION_MAX_HEIGHT_SHARE, line_h)):
            break
    cx = box["x"] + box["w"] / 2
    top = box["y"] + (box["h"] - line_h * len(lines)) // 2
    layer = Image.new("L", pil_img.size, 0)
    layer_draw = ImageDraw.Draw(layer)
    placed = []
    for i, line in enumerate(lines):
        width = draw.textlength(line, font=font)
        x = box["x"] if align == "left" and box["h"] <= 1.4 * box["w"] else cx - width / 2
        placed.append((x, top + i * line_h, line))
        layer_draw.text((x, top + i * line_h), line, font=font, fill=255)
    if style.get("glow"):
        halo = layer.filter(ImageFilter.GaussianBlur(max(2, size // 8)))
        halo = ImageChops.add(halo, halo)
        pil_img.paste(Image.new("RGB", pil_img.size, tuple(style["glow"])), (0, 0), halo)
        stroke, stroke_fill = 0, None
    else:
        fill_luma = sum(style["fill"][:3]) / 3
        if fill_luma < _DARK_FILL_LUMA:
            # A dark outline round dark lettering only fattened it into a blob.
            stroke, stroke_fill = max(1, size // 24), (245, 245, 250)
        else:
            stroke, stroke_fill = max(2, size // 10), (20, 20, 30)
    for x, y, line in placed:
        draw.text((x, y), line, font=font, fill=tuple(style["fill"]),
                  stroke_width=stroke, stroke_fill=stroke_fill)
    return pil_img
