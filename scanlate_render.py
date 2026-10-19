"""
scanlate_render.py -- typesetting: font choice, text fitting and the page render pass.

Picks a face per bubble, wraps and sizes translated text into a box (or into the
bubble's own outline), inpaints the original lettering and draws the result.
"""

import os

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import scanlate_art_text
from scanlate_detect import bubble_shape_mask, decode_page, page_gray
from scanlate_inpaint import InpaintModelUnavailable, background_is_dark, inpaint_region
from scanlate_regions import SHAPE_FITTED_KINDS, region_excluded_from_auto


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
_SYSTEM_FONT_CANDIDATES = {
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ],
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ],
    "handwritten": [
        "C:\\Windows\\Fonts\\comic.ttf",  # Comic Sans MS -- present on most Windows installs
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


def _layout_in_mask(draw, text: str, mask, font_size: int, load_font):
    """Shape-aware counterpart to render_text_in_box()'s rectangle layout
    tries the largest font first, and for each size the
    fewest lines first, centring the block vertically and giving each line
    only the width the mask has at that line's height -- so text in an
    oval bubble narrows toward the top and bottom instead of running into
    the corners of its bounding rectangle. Returns (font, [(line, left,
    right, top)]) or None if nothing fits even at the minimum size, in
    which case the caller falls back to the plain rectangle layout."""
    words = text.split()
    if not words:
        return None
    mh = mask.shape[0]
    for size in range(font_size, 7, -1):
        font = load_font(size)
        line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + 4
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
            placed, wi = [], 0
            for i, (left, right) in enumerate(spans):
                cur = ""
                while wi < len(words):
                    trial = f"{cur} {words[wi]}".strip()
                    if width_of(trial) > right - left:
                        break
                    cur, wi = trial, wi + 1
                if not cur:
                    break  # a word that doesn't fit this line at all
                placed.append((cur, left, right, top0 + i * line_height))
            if wi == len(words):
                return font, placed
    return None


def render_text_in_box(image, box: dict, text: str, font_size: int = 18,
                        font_path: str = None, font_category: str = "regular",
                        custom_fonts: dict = None, fill=(0, 0, 0), align="center",
                        mask=None):
    """
    image: PIL Image (already inpainted/cleaned) -- mutated in place.
    Auto-shrinks font_size until the wrapped text fits the box height;
    word-wraps to fit box width. Horizontal text layout only -- no
    vertical CJK rendering (that's koharu's specialty, not replicated
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
            else ImageFont.load_default())
        if laid_out is not None:
            font, placed = laid_out
            for line, left, right, top in placed:
                bbox = draw.textbbox((0, 0), line, font=font)
                line_w = bbox[2] - bbox[0]
                lx = left + (max(0, (right - left - line_w) // 2) if align == "center" else 0)
                # textbbox's own left offset, so the ink starts where it was measured
                draw.text((box["x"] + lx - bbox[0], box["y"] + top), line, font=font, fill=fill)
            return image

    size = font_size

    def wrap_and_measure(sz):
        font = ImageFont.truetype(resolved_font_path, sz) if resolved_font_path else ImageFont.load_default()
        words = text.split()
        lines, cur = [], ""
        for word in words:
            trial = f"{cur} {word}".strip()
            bbox = draw.textbbox((0, 0), trial, font=font)
            if bbox[2] - bbox[0] > box["w"] - 8 and cur:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + 4
        return font, lines, line_height

    font, lines, line_height = wrap_and_measure(size)
    while (line_height * len(lines) > box["h"] - 8) and size > 8:
        size -= 1
        font, lines, line_height = wrap_and_measure(size)

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


def _text_fill(pil_img, box: dict):
    """White lettering on a dark balloon, black otherwise."""
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = np.asarray(pil_img.crop((x, y, x + box["w"], y + box["h"])).convert("L"))
    if roi.size and background_is_dark(roi):
        return (255, 255, 255)
    return (0, 0, 0)


def bulk_render_pages(pages_with_bubbles: list, out_dir: str, font_path: str = None,
                       custom_fonts: dict = None):
    """
    pages_with_bubbles: list of (image_path, bubbles, out_filename) tuples.
    Renders every page and zips the results -- matches Torii's "download
    as ZIP" for a whole translated chapter instead of one page at a time.
    Returns the path to the zip file. A single page's render failure
    doesn't stop the rest (isolated per-page, same pattern as the
    dubbing/translation reliability work).
    """
    import zipfile
    import os as os_module

    os_module.makedirs(out_dir, exist_ok=True)
    rendered_paths = []
    errors = []
    blank_text_report = {}
    for image_path, bubbles, out_filename in pages_with_bubbles:
        out_path = os_module.path.join(out_dir, out_filename)
        try:
            _, skipped_blank = process_page(image_path, bubbles, out_path, font_path=font_path,
                                             custom_fonts=custom_fonts)
            rendered_paths.append(out_path)
            if skipped_blank:
                blank_text_report[out_filename] = len(skipped_blank)
        except Exception as e:
            errors.append({"file": out_filename, "error": str(e)})

    zip_path = os_module.path.join(out_dir, "typeset_pages.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in rendered_paths:
            zf.write(p, os_module.path.basename(p))
    return zip_path, errors, blank_text_report


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
        for b in to_render:
            try:
                inpaint_region(image_path, b, image=page)
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
            shape_mask = (bubble_shape_mask(image_path, b, image=gray)
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


