"""
scanlate_render.py -- typesetting: font choice, text fitting and the page render pass.

Picks a face per bubble, wraps and sizes translated text into a box (or into the
bubble's own outline), inpaints the original lettering and draws the result.
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import scanlate_art_text
import scanlate_tuning
from scanlate_detect import bubble_erase_mask, bubble_shape_mask, decode_page, page_gray
from scanlate_inpaint import (
    InpaintModelUnavailable, background_is_dark, inpaint_region, lama_ml_weights_cached,
)
from scanlate_regions import SHAPE_FITTED_KINDS, region_excluded_from_auto


# Shown when a page is cleaned without the LaMa-manga weights. "auto" then
# picks OpenCV without raising, so nothing else would tell the person why
# faint ghosts of the original lettering show under the English.
LAMA_MISSING_NOTE = ("The LaMa-manga cleanup model is not installed, so the original text was "
                     "erased with plain OpenCV and may show through.")

# One of these three, matching sample_text_style()'s "suggested_style"
# output exactly -- a bubble's auto-detected (or manually overridden)
# font_category is one of these, letting the renderer pick a face that
# roughly matches the original lettering's weight/character instead of
# using the same font for every bubble on a page.
FONT_CATEGORIES = ["regular", "bold", "handwritten"]

# Common system font fallbacks per category, tried in order. "handwritten"
# has NO reliable brush/handwriting-style font on a stock install of any
# OS -- unlike regular/bold, there's no universal system font that looks
# handwritten, so it degrades to a regular font unless a custom one is
# supplied via `custom_fonts`. That's a real, honest limitation, not a
# bug: matching decorative/brush lettering needs either a real font file
# (upload one -- see the Scanlate tab) or a trained font-classifier model
# like BalloonsTranslator's YuzuMarker.FontDetection, neither of which
# this ships with by default.
# Windows can live on any drive; WINDIR names the real folder.
_WINDOWS_FONTS = os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts")
_SYSTEM_FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        os.path.join(_WINDOWS_FONTS, "arialbd.ttf"),
    ],
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        os.path.join(_WINDOWS_FONTS, "arial.ttf"),
    ],
    "handwritten": [
        os.path.join(_WINDOWS_FONTS, "comic.ttf"),  # Comic Sans MS -- present on most Windows installs
        "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
    ],
}


def _find_font(font_path: str = None, category: str = "regular", custom_fonts: dict = None):
    """
    Resolution order: an explicit font_path always wins (a per-bubble
    manual override); then a custom font file uploaded for this
    category (custom_fonts: {category: path}, see the Scanlate tab's
    font upload); then this category's own system-font fallbacks; then
    "regular"'s fallbacks if the category itself has none installed
    (relevant mainly for "handwritten" -- see FONT_CATEGORIES' own note);
    None (Pillow's own last-resort bitmap font) if nothing at all is found.
    """
    if font_path and os.path.exists(font_path):
        return font_path
    custom_fonts = custom_fonts or {}
    custom = custom_fonts.get(category)
    if custom and os.path.exists(custom):
        return custom
    for c in _SYSTEM_FONT_CANDIDATES.get(category, []):
        if os.path.exists(c):
            return c
    if category != "regular":
        for c in _SYSTEM_FONT_CANDIDATES.get("regular", []):
            if os.path.exists(c):
                return c
    return None


def _mask_band_span(mask, top: int, bottom: int):
    """(left, right) of the widest run of columns that are inside `mask`
    on EVERY row of [top, bottom) -- the horizontal room a line of text
    drawn in that band actually has. None if the band leaves the mask or
    has no room at all."""
    if top < 0 or bottom > mask.shape[0] or bottom <= top:
        return None
    cols = np.all(mask[top:bottom], axis=0)
    best, run_start = None, None
    for i, inside in enumerate(list(cols) + [False]):
        if inside and run_start is None:
            run_start = i
        elif not inside and run_start is not None:
            if best is None or (i - run_start) > (best[1] - best[0]):
                best = (run_start, i)
            run_start = None
    return best


def _hyphen_split(word: str, fits):
    """(head + "-", tail) with the longest head that `fits`, or None when no
    split leaves at least three letters on each side."""
    for cut in range(len(word) - 3, 2, -1):
        head = word[:cut] + "-"
        if fits(head):
            return head, word[cut:]
    return None


def _layout_in_mask(draw, text: str, mask, font_size: int, load_font):
    """Shape-aware counterpart to render_text_in_box()'s rectangle layout
    tries the largest font first, and for each size the
    fewest lines first, centring the block vertically and giving each line
    only the width the mask has at that line's height -- so text in an
    oval bubble narrows toward the top and bottom instead of running into
    the corners of its bounding rectangle. A word too wide for any line is
    hyphenated rather than shrunk below the comfortable size, because a
    narrow vertical balloon otherwise forces tiny letters. Returns (font,
    [(line, left, right, top)]) or None if nothing fits even at the minimum
    size, in which case the caller falls back to the plain rectangle layout."""
    words = text.split()
    if not words:
        return None
    mh = mask.shape[0]
    comfort = scanlate_tuning.TYPESET_COMFORT_PX
    smallest = scanlate_tuning.TYPESET_SMALLEST_PX
    # Whole words down to the comfortable size, then hyphenated words down
    # to it, and only then whole words at any size.
    passes = ((False, comfort), (True, comfort), (True, smallest))
    for hyphenate, floor in passes:
        for size in range(font_size, floor - 1, -1):
            laid_out = _layout_at_size(draw, words, mask, mh, load_font(size), hyphenate)
            if laid_out is not None:
                return laid_out
    return None


def _layout_at_size(draw, words, mask, mh, font, hyphenate):
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + scanlate_tuning.TYPESET_LINE_GAP_PX
    widths = {}

    def width_of(t):
        if t not in widths:
            bb = draw.textbbox((0, 0), t, font=font)
            widths[t] = bb[2] - bb[0]
        return widths[t]

    for n_lines in range(1, max(mh // line_height, 0) + 1):
        top0 = (mh - line_height * n_lines) // 2
        spans = [_mask_band_span(mask, top0 + i * line_height, top0 + (i + 1) * line_height)
                 for i in range(n_lines)]
        if any(sp is None for sp in spans):
            continue
        queue = list(words)
        placed = []
        for i, (left, right) in enumerate(spans):
            cur = ""
            while queue:
                trial = f"{cur} {queue[0]}".strip()
                if width_of(trial) > right - left:
                    split = None if cur or not hyphenate else _hyphen_split(
                        queue[0], lambda t: width_of(t) <= right - left)
                    if split:
                        cur = split[0]
                        queue[0] = split[1]
                    break
                cur = trial
                queue.pop(0)
            if not cur:
                break  # a word that doesn't fit this line at all
            placed.append((cur, left, right, top0 + i * line_height))
        if not queue:
            return font, placed
    return None


def _ink_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_words(draw, text: str, font, avail: int, hyphenate: bool) -> list:
    """`text` wrapped into lines no wider than `avail`; with `hyphenate`, a word
    wider than a line is split with a hyphen instead of overflowing."""
    words = text.split()
    lines, cur = [], ""
    while words:
        word = words.pop(0)
        trial = f"{cur} {word}".strip()
        if _ink_width(draw, trial, font) <= avail or not cur and not hyphenate:
            cur = trial
            continue
        if not cur:
            split = _hyphen_split(word, lambda t: _ink_width(draw, t, font) <= avail)
            if split:
                lines.append(split[0])
                words.insert(0, split[1])
            else:
                cur = word
            continue
        lines.append(cur)
        words.insert(0, word)
        cur = ""
    if cur:
        lines.append(cur)
    return lines


def render_text_in_box(image, box: dict, text: str, font_size: int = 18,
                        font_path: str = None, font_category: str = "regular",
                        custom_fonts: dict = None, fill=(0, 0, 0), align="center",
                        mask=None):
    """
    image: PIL Image (already inpainted/cleaned) -- mutated in place.
    Auto-shrinks font_size until the wrapped text fits the box (a single
    word wider than the box shrinks it too); word-wraps to fit box width.
    Horizontal text layout only -- no vertical CJK rendering (that's koharu's specialty, not replicated
    here).

    mask: optional boolean array the size of the box (h, w), True inside
    the bubble's real shape -- see bubble_shape_mask(). When given, each
    line's width follows the shape rather than the
    bounding rectangle; if the text can't fit the shape at any size, this
    falls back to the rectangle layout below rather than dropping text.

    font_path: an explicit per-bubble override (always wins). Otherwise
    font_category (one of FONT_CATEGORIES, normally auto-filled from
    sample_text_style()'s detection at bubble-detection time) picks
    between a bold/regular/handwritten face via _find_font() -- see that
    function for the full resolution order and custom_fonts.
    """
    draw = ImageDraw.Draw(image)
    resolved_font_path = _find_font(font_path, category=font_category, custom_fonts=custom_fonts)

    if mask is not None:
        laid_out = _layout_in_mask(
            draw, text, mask, font_size,
            lambda sz: ImageFont.truetype(resolved_font_path, sz) if resolved_font_path
            else ImageFont.load_default(sz))
        if laid_out is not None:
            font, placed = laid_out
            for line, left, right, top in placed:
                bbox = draw.textbbox((0, 0), line, font=font)
                line_w = bbox[2] - bbox[0]
                lx = left + (max(0, (right - left - line_w) // 2) if align == "center" else 0)
                # textbbox's own left offset, so the ink starts where it was measured
                draw.text((box["x"] + lx - bbox[0], box["y"] + top), line, font=font, fill=fill)
            return image

    def wrap_and_measure(sz, hyphenate):
        # load_default() without a size is one fixed size, so the fit loop below
        # could never shrink the text when no system font is installed.
        font = (ImageFont.truetype(resolved_font_path, sz) if resolved_font_path
                else ImageFont.load_default(sz))
        lines = _wrap_words(draw, text, font, box["w"] - 8, hyphenate)
        line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + scanlate_tuning.TYPESET_LINE_GAP_PX
        return font, lines, line_height

    def overflows(font, lines, line_height):
        # Wrapping never splits a word unless hyphenating, so a word wider than
        # the box is otherwise only brought inside it by a smaller size.
        return (line_height * len(lines) > box["h"] - 8
                or any(_ink_width(draw, line, font) > box["w"] - 8 for line in lines))

    # Whole words down to the comfortable size, then hyphenated words down to
    # it, and only then at any size -- as _layout_in_mask() does.
    comfort = scanlate_tuning.TYPESET_COMFORT_PX
    smallest = scanlate_tuning.TYPESET_SMALLEST_PX
    for hyphenate, floor in ((False, comfort), (True, comfort), (True, smallest)):
        for size in list(range(font_size, floor - 1, -1)) or [font_size]:
            font, lines, line_height = wrap_and_measure(size, hyphenate)
            if not overflows(font, lines, line_height):
                break
        else:
            continue
        break
    # Nothing fit: the smallest size is what is drawn.

    total_h = line_height * len(lines)
    start_y = box["y"] + max(0, (box["h"] - total_h) // 2)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        if align == "center":
            lx = box["x"] + max(0, (box["w"] - line_w) // 2)
        else:
            lx = box["x"] + 4
        draw.text((lx, start_y + i * line_height), line, font=font, fill=fill)
    return image


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


def _text_fill(pil_img, box: dict):
    """The hand-set colour if any; else white lettering on a dark balloon,
    black otherwise."""
    chosen = chosen_color(box)
    if chosen is not None:
        return chosen
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = np.asarray(pil_img.crop((x, y, x + box["w"], y + box["h"])).convert("L"))
    if roi.size and background_is_dark(roi):
        return (255, 255, 255)
    return (0, 0, 0)


def process_page(image_path: str, bubbles: list, out_path: str, font_path: str = None,
                  custom_fonts: dict = None, notes: list = None):
    """
    Full render pass: inpaint every bubble that has real translated text,
    then draw that text into the cleaned box. `bubbles` is a list of dicts
    with x,y,w,h,translated_text,font_size,skip (matches the DB rows
    from db.load_bubbles()).

    A bubble with blank translated text is treated the same as a skipped
    one -- the original is left untouched. Erasing the source text and
    putting nothing back is strictly worse than leaving it alone: it used
    to happen because the "inpaint" and "draw text" passes checked
    different conditions (inpaint ran for every non-skipped bubble; only
    the draw pass checked for actual text), so a bubble that was detected
    but never got real OCR/translation -- e.g. a wrongly-sized box, or a
    failed auto-translate -- rendered as a blank white rectangle with the
    original text silently erased underneath it and nothing to show for it.

    Returns (out_path, skipped) -- skipped lists bubbles that had no
    translated text, so the caller can warn about them rather than the
    person only discovering a blank spot after the fact.

    A region classified as SFX is left alone unless its include_sfx
    override is set -- see region_excluded_from_auto().
    Speech/thought bubbles get their text fitted to the bubble's real
    shape (bubble_shape_mask()), not just its rectangle.

    The page is decoded once: every bubble is erased on that one array, the
    text drawn on it, and the PNG encoded once, to a unique temp file next to
    out_path (removed even on failure) that then replaces out_path
    atomically, so two renders of the same page can't collide. With a
    `notes` list, an ML inpaint that fell back to OpenCV is recorded there
    (once) and the render goes on with the OpenCV result instead of raising.
    """
    import tempfile

    def in_auto_pass(b):
        return not b.get("skip") and not region_excluded_from_auto(b)

    def has_real_text(b):
        return in_auto_pass(b) and (b.get("translated_text") or "").strip()

    skipped_blank = [b for b in bubbles
                     if in_auto_pass(b) and not (b.get("translated_text") or "").strip()]
    to_render = [b for b in bubbles if has_real_text(b)]

    out_dir = os.path.dirname(os.path.abspath(out_path))
    fd, result_path = tempfile.mkstemp(prefix=".typeset_", suffix=".out.png", dir=out_dir)
    os.close(fd)
    try:
        page = decode_page(image_path)
        # Shape masks follow the original lettering, so they are taken before
        # any bubble is erased.
        gray = (page_gray(image_path, page)
                if any((b.get("kind") or "bubble") in SHAPE_FITTED_KINDS for b in to_render)
                else None)
        # Lettering on the artwork is painted out and re-lettered in its own
        # style (scanlate_art_text); bubbles keep the balloon path below.
        captions = [b for b in to_render if b.get("kind") == "caption"]
        to_render = [b for b in to_render if b.get("kind") != "caption"]
        caption_styles = [scanlate_art_text.caption_style(page, b) for b in captions]
        caption_styles = [dict(s, fill=chosen_color(b)) if chosen_color(b) else s
                          for b, s in zip(captions, caption_styles)]
        if notes is not None and to_render and not lama_ml_weights_cached():
            notes.append(("error", "inpaint_unavailable", LAMA_MISSING_NOTE))
        for b in to_render:
            try:
                erase_lettering(image_path, b, page, gray)
            except InpaintModelUnavailable as exc:
                # The OpenCV result is already in `page`.
                if notes is None:
                    raise
                if not any(n[1] == "inpaint_fallback" for n in notes if len(n) > 1):
                    notes.append(("warning", "inpaint_fallback", str(exc)))

        if captions and not scanlate_art_text.paint_out_captions(page, captions, backend="auto"):
            if notes is not None and not any(n[1] == "inpaint_fallback" for n in notes if len(n) > 1):
                notes.append(("warning", "inpaint_fallback",
                              "The LaMa-manga model is not available, so the captions on the art "
                              "were painted out with plain OpenCV, which leaves faint ghosts."))

        pil_img = Image.fromarray(np.ascontiguousarray(page[:, :, ::-1]))
        for b, style in zip(captions, caption_styles):
            scanlate_art_text.draw_caption(pil_img, b, b["translated_text"], style,
                                           font_path=font_path)
        for b in to_render:
            shape_mask = (bubble_shape_mask(image_path, b, image=gray,
                                            margin=scanlate_tuning.TYPESET_OUTLINE_MARGIN_PX)
                          if (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS else None)
            render_text_in_box(pil_img, b, b["translated_text"],
                                font_size=b.get("font_size", 18), font_path=font_path,
                                font_category=b.get("font_category") or "regular",
                                custom_fonts=custom_fonts, mask=shape_mask,
                                fill=_text_fill(pil_img, b))
        pil_img.save(result_path, "PNG")
        os.replace(result_path, out_path)
    finally:
        if os.path.exists(result_path):
            os.remove(result_path)
    return out_path, skipped_blank


def erase_lettering(image_path: str, b: dict, page, gray) -> None:
    """Erases region `b`'s original lettering from `page` (BGR, in place). A
    speech or thought balloon is erased only inside its outline, so the
    outline and the art around it survive; other kinds, and a balloon whose
    shape can't be found, are erased across their box. `gray`: the page in
    grayscale before any erasing, or None when no region is shape-fitted.
    InpaintModelUnavailable as inpaint_region() raises it."""
    inside = None
    if gray is not None and (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS:
        inside = bubble_erase_mask(image_path, b, image=gray)
    inpaint_region(image_path, b, image=page, inside=inside)


def sample_text_style(image_path: str, box, image=None) -> dict:
    """
    Samples a text region to guess whether it needs a decorative face --
    handwritten/brush lettering versus standard print.

    Doesn't identify the actual typeface (that needs a trained classifier,
    like BalloonsTranslator's YuzuMarker.FontDetection model -- a real,
    heavier alternative not implemented here); it estimates stroke weight
    and irregularity via classical CV so the renderer can pick a closer-
    matching font CATEGORY instead of defaulting every bubble on a page
    to the same bold sans. "suggested_style" is one of FONT_CATEGORIES
    and is what the Scanlate tab auto-fills each bubble's font_category
    with at detection time -- reviewable/overridable per bubble before
    render, same hybrid-workflow pattern as bubble detection itself.
    `image`: the page already decoded, gray or BGR (see page_gray()).
    """
    import cv2
    import numpy as np

    img = page_gray(image_path, image)
    if img is None:
        return {"weight": "regular", "irregular": False, "suggested_style": "regular"}
    h, w = img.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    bw, bh = min(box["w"], w - x), min(box["h"], h - y)
    if bw <= 0 or bh <= 0:
        return {"weight": "regular", "irregular": False, "suggested_style": "regular"}

    roi = img[y:y + bh, x:x + bw]
    _, binary = cv2.threshold(roi, 128, 255, cv2.THRESH_BINARY_INV)
    ink_ratio = float(binary.mean()) / 255.0

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    heights = [cv2.boundingRect(c)[3] for c in contours if cv2.contourArea(c) > 8]
    # Print lettering has consistent glyph heights; handwriting varies.
    irregular = bool(heights) and (np.std(heights) / max(np.mean(heights), 1)) > 0.45

    return {
        "weight": "bold" if ink_ratio > 0.22 else "regular",
        "irregular": irregular,
        "ink_ratio": round(ink_ratio, 3),
        "suggested_style": ("handwritten" if irregular else
                            ("bold" if ink_ratio > 0.22 else "regular")),
    }
