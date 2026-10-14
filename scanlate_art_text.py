"""
scanlate_art_text.py -- lettering drawn straight onto the artwork (titles,
narration over a splash page): find it, paint it out and set English over it.

The bubble detector never sees this text, so the page's other steps leave it
Chinese. Finding it needs the OCR detector (a brightness rule also matches
lightning and highlights); painting it out needs the LaMa-manga model, because
plain OpenCV inpainting leaves ghosts of the letters on busy art.
"""

import numpy as np

from scanlate_inpaint import background_is_dark

# Paddle's detector runs out of memory on a full-height webtoon page, so the
# page is shrunk to this on its longest side for detection only.
_DETECT_LIMIT_SIDE = 1280

_MIN_LINE_HEIGHT = 28          # px; smaller marks are watermarks, not captions
_MIN_CONFIDENCE = 0.6
_MASK_GROW = 27                # covers the glow around the letters


def _overlap(a: dict, b: dict) -> float:
    """Share of box `a` that lies inside box `b`."""
    ox = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    oy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    if ox <= 0 or oy <= 0:
        return 0.0
    return ox * oy / float(a["w"] * a["h"])


def group_lines(lines: list) -> list:
    """Joins text lines that belong to one caption (stacked closely, sharing
    horizontal extent) into one box with the lines' texts in reading order.
    lines: [{"x","y","w","h","text"}, ...]."""
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
             "source_text": "".join(l["text"] for l in g["lines"])} for g in groups]


def find_art_captions(image_path: str, lang: str, existing_boxes: list, image=None) -> list:
    """Caption boxes on the artwork that no existing region covers, as
    {"x","y","w","h","source_text"} in full-page pixels."""
    import ocr
    from scanlate_detect import decode_page
    page = image if image is not None else decode_page(image_path)
    h, w = page.shape[:2]
    scale = min(1.0, _DETECT_LIMIT_SIDE / float(max(h, w)))
    lines = []
    for text, conf, (x, y, bw, bh) in ocr.paddle_lines(image_path, lang, scale):
        if conf < _MIN_CONFIDENCE or not text.strip() or bh < _MIN_LINE_HEIGHT:
            continue
        box = {"x": x, "y": y, "w": bw, "h": bh}
        if any(_overlap(box, e) > 0.5 for e in existing_boxes):
            continue
        lines.append({**box, "text": text.strip()})
    return group_lines(lines)


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
    mask = np.zeros((ph, pw), np.uint8)
    inner = (ink & _inside(gray.shape, box, x0, y0)).astype(np.uint8) * 255
    mask[y0:y1, x0:x1] = cv2.dilate(inner, np.ones((_MASK_GROW, _MASK_GROW), np.uint8))
    return mask


def _inside(shape, box, x0, y0):
    inside = np.zeros(shape, bool)
    inside[max(0, box["y"] - y0):box["y"] + box["h"] - y0,
           max(0, box["x"] - x0):box["x"] + box["w"] - x0] = True
    return inside


def paint_out_captions(page, boxes: list, backend: str = "auto"):
    """Erases each caption's lettering from `page` (a BGR array, changed in
    place) with the LaMa-manga model. Returns True when the model ran, False
    when OpenCV did instead (its result is ghostier). Raises nothing for a
    missing model: the caller shows the OpenCV result with a note."""
    import cv2
    from scanlate_inpaint import InpaintModelUnavailable, _resolve_inpaint_backend, _run_ml_inpaint
    ran_model = _resolve_inpaint_backend(backend) == "ml"
    for box in boxes:
        mask = caption_mask(page, box)
        ys, xs = np.nonzero(mask)
        if not len(xs):
            continue
        y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        roi, roi_mask = page[y0:y1, x0:x1], mask[y0:y1, x0:x1]
        try:
            if not ran_model:
                raise InpaintModelUnavailable("model not downloaded")
            page[y0:y1, x0:x1] = _run_ml_inpaint(roi, roi_mask)
        except Exception:
            ran_model = False
            page[y0:y1, x0:x1] = cv2.inpaint(roi, roi_mask, 5, cv2.INPAINT_TELEA)
    return ran_model


def draw_caption(pil_img, box: dict, text: str, font_path: str = None):
    """Sets `text` over a painted-out caption: white with a dark outline so it
    reads on whatever the art is, wrapped to the box and as large as fits."""
    from PIL import ImageDraw, ImageFont
    from scanlate_render import _find_font
    draw = ImageDraw.Draw(pil_img)
    path = _find_font(font_path, category="bold")
    words = text.split()
    for size in range(max(12, int(min(box["h"] * 0.9, box["w"] * 0.5))), 11, -2):
        font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
        stroke = max(2, size // 10)
        lines, cur = [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            if cur and draw.textlength(trial, font=font) > box["w"]:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        lines.append(cur)
        line_h = int(size * 1.2)
        fits_width = all(draw.textlength(l, font=font) <= box["w"] for l in lines)
        if fits_width and line_h * len(lines) <= box["h"] * 1.5:
            break
    top = box["y"] + (box["h"] - line_h * len(lines)) // 2
    for i, line in enumerate(lines):
        width = draw.textlength(line, font=font)
        draw.text((box["x"] + (box["w"] - width) / 2, top + i * line_h), line, font=font,
                  fill=(255, 255, 255), stroke_width=stroke, stroke_fill=(20, 20, 30))
    return pil_img
