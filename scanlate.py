"""
scanlate.py -- hybrid manga/comic typesetting pipeline.

  1. detect_bubbles_cv(): free, local, OpenCV-only heuristic detection.
     Looks for large, mostly-uniform light-colored regions with a
     defined border -- the classic speech-bubble shape. No model
     download needed, works reasonably on typical clean scans. Misses
     irregular/borderless bubbles and dense sound-effect lettering.

  2. detect_bubbles_ml(): optional hook for a real trained detector
     (the same kind koharu/manga-image-translator use, e.g.
     ogkalu/comic-text-and-bubble-detector on Hugging Face) for
     meaningfully better accuracy. Heavier install, not run/verified in
     this environment -- wire in a real inference call here if you set
     one up locally.

  3. inpaint_region(): removes the original text from a detected box
     using OpenCV inpainting, so the translated text has a clean
     background to sit on.

  4. render_text_in_box(): auto-sizes and word-wraps translated text
     into a box using Pillow, shrinking the font until it fits.

Hybrid workflow: run detection + inpainting + auto-placement first,
then let the person review/adjust each bubble's box, font size, and
text in a table before final render -- the Scanlate page in the web app.
"""

import os
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
from memory_headroom import HeadroomError
from PIL import Image, ImageDraw, ImageFont

from scanlate_detect import (
    BubbleModelUnavailable, bubble_shape_mask, classify_text_regions, detect_bubbles,
    detect_bubbles_cv, detect_panels, inset_box_for_ocr, split_webtoon_strip,
)
from scanlate_inpaint import InpaintModelUnavailable, inpaint_region


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


def translate_page_with_context(texts, engine, drama_meta: dict, previous_context: str = "",
                                 usage_cb=None, glossary_terms=None):
    """
    Translates a page's bubble texts with awareness of what happened on
    prior pages, the way Torii's context-passing works for manga --
    keeps character voice and ongoing plot threads consistent across a
    whole chapter instead of each page being translated in isolation.

    previous_context: a short rolling summary carried from the last
    page's translate call (see below). Returns (translations, new_context)
    -- pass new_context into the next page's call to keep the chain going.

    glossary_terms: rows from db.list_glossary_terms() for the drama's
    series -- the same lookup Workspace's own translation uses,
    rendered through translation_guide.build_glossary_block() so
    honorifics (category "honorific") and every other fixed term reach
    comic translations under the same rules as subtitles.
    """
    from translate_engines import call_llm_json
    from translation_guide import build_glossary_block
    import re, json

    if not getattr(engine, "supports_reference", False):
        # Pure-MT engines can't do context-aware translation or summarization
        return engine.translate_batch(texts, {"drama_meta": drama_meta,
                                              "glossary_terms": glossary_terms}), previous_context

    context_block = f"\n\nContext from previous pages: {previous_context}" if previous_context else ""
    glossary_block = build_glossary_block(glossary_terms)
    glossary_block = f"\n\n{glossary_block}" if glossary_block else ""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "Translate these manga/comic speech bubble texts into natural English, in reading "
        "order, keeping character voice and plot consistent with the context below if any."
        + glossary_block + context_block + f"\n\nBubbles on this page:\n{numbered}\n\n"
        'Return ONLY a JSON object: {"translations": ["...", ...], "context_summary": '
        '"1-2 sentence summary of what just happened, to carry into the next page"}. '
        "No preamble, no markdown fences."
    )
    # A failure to get a usable answer -- no response, unparseable JSON, or a
    # response missing the "translations" key entirely -- is signaled as
    # `None`, never as a same-length list of blanks. A same-length blank list
    # would sail straight through translate_page_bubbles()'s length check
    # (the lengths *do* match) and overwrite every eligible bubble's real
    # translation with "", including a hand-edited one -- silently, with no
    # error surfaced anywhere. `None` forces the caller to treat this the
    # same "don't apply a result there's no safe way to trust" way it
    # already treats a length mismatch. A genuinely empty `"translations":
    # []` (the model legitimately found nothing to translate) is returned
    # as-is, distinct from this failure signal.
    text = call_llm_json(engine, prompt, max_tokens=1500, fallback=None, usage_cb=usage_cb)
    if text is None:
        return None, previous_context

    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None, previous_context
    if "translations" not in data:
        return None, previous_context
    return data["translations"], data.get("context_summary", previous_context)


_MAX_CONTEXT_CHARS = 1000


def translate_regions_by_id(texts_by_id: dict, engine, drama_meta: dict,
                            previous_context: str = "", usage_cb=None, glossary_terms=None):
    """Id-keyed page translation, replacing the positional
    translate_page_with_context: translate_page_bubbles (and so the
    extension bridge) and the API jobs go through this. `texts_by_id`
    maps each region's id to its source text. Returns ({id: translation},
    new_context), or (None, previous_context) when the answer can't be
    trusted as a whole: no answer, unparseable JSON, not an object keyed
    by id, any sent id missing or not a string, or an id that wasn't sent.
    Nothing is ever matched by position, so a short or reordered answer
    applies nothing.

    Engines without the JSON prompt path (pure MT, or no LLM client) are called
    once per region with a single text, so each answer belongs to exactly
    one id; they carry no rolling context."""
    import json
    import re
    from translate_engines import (LANGUAGE_NAMES, extract_first_json_value,
                                   build_translation_context, call_llm_json)
    from translation_guide import build_glossary_block

    ids = [str(k) for k in texts_by_id]
    if not ids:
        return {}, previous_context
    if (not getattr(engine, "supports_reference", False)
            or getattr(engine, "client", True) is None):
        # The shared context carries the drama's source_language (NLLB defaults
        # to Chinese without it).
        context = build_translation_context(engine, drama_meta, glossary_terms=glossary_terms)
        out = {}
        for key, text in zip(ids, texts_by_id.values()):
            result = engine.translate_batch([text], dict(context))
            if usage_cb and hasattr(engine, "last_usage"):     # per call: it is reset each time
                usage_cb(engine.last_usage.get("input_tokens", 0),
                         engine.last_usage.get("output_tokens", 0))
            if not isinstance(result, (list, tuple)) or len(result) != 1 \
                    or not isinstance(result[0], str):
                return None, previous_context
            out[key] = result[0]
        return out, previous_context

    source_name = LANGUAGE_NAMES.get((drama_meta or {}).get("source_language") or "zh",
                                     "source-language")
    context_block = f"\n\nContext from previous pages: {previous_context}" if previous_context else ""
    glossary_block = build_glossary_block(glossary_terms)
    glossary_block = f"\n\n{glossary_block}" if glossary_block else ""
    payload = json.dumps([{"id": k, "text": t} for k, t in zip(ids, texts_by_id.values())],
                         ensure_ascii=False)
    prompt = (
        f"Translate these {source_name} manga/comic text regions into natural English, keeping "
        "character voice and plot consistent with the context below if any. They are listed in reading "
        "order; each has an id."
        + glossary_block + context_block + f"\n\nRegions on this page (JSON):\n{payload}\n\n"
        'Return ONLY a JSON object: {"translations": {"<id>": "<English text>", ...}, '
        '"context_summary": "1-2 sentence summary of what just happened, to carry into the '
        'next page"}. Use every id exactly once, as given. No preamble, no markdown fences.'
    )
    text = call_llm_json(engine, prompt, max_tokens=max(1500, 200 * len(ids)), fallback=None,
                         usage_cb=usage_cb)
    if not text:
        return None, previous_context
    data = extract_first_json_value(
        re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip())
    if not isinstance(data, dict) or not isinstance(data.get("translations"), dict):
        return None, previous_context
    got = {str(k): v for k, v in data["translations"].items()}
    if set(got) != set(ids) or not all(isinstance(v, str) for v in got.values()):
        return None, previous_context
    summary = data.get("context_summary")
    new_context = (summary.strip()[:_MAX_CONTEXT_CHARS]
                   if isinstance(summary, str) and summary.strip() else previous_context)
    return got, new_context


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

    The working copy is a unique temp file next to out_path (removed even
    on failure), so two renders of the same page can't collide on it; the
    result replaces out_path atomically. With a `notes` list, an ML
    inpaint that fell back to OpenCV is recorded there (once) and the render
    goes on with the OpenCV result instead of raising.
    """
    import shutil
    import tempfile

    def in_auto_pass(b):
        return not b.get("skip") and not region_excluded_from_auto(b)

    def has_real_text(b):
        return in_auto_pass(b) and (b.get("translated_text") or "").strip()

    skipped_blank = [b for b in bubbles
                     if in_auto_pass(b) and not (b.get("translated_text") or "").strip()]

    out_dir = os.path.dirname(os.path.abspath(out_path))
    fd, working_path = tempfile.mkstemp(prefix=".typeset_", suffix=".tmp.png", dir=out_dir)
    os.close(fd)
    fd, result_path = tempfile.mkstemp(prefix=".typeset_", suffix=".out.png", dir=out_dir)
    os.close(fd)
    try:
        shutil.copy(image_path, working_path)
        for b in bubbles:
            if not has_real_text(b):
                continue
            try:
                inpaint_region(working_path, b, out_path=working_path)
            except InpaintModelUnavailable as exc:
                # The OpenCV result is already in working_path.
                if notes is None:
                    raise
                if not any(n[1] == "inpaint_fallback" for n in notes if len(n) > 1):
                    notes.append(("warning", "inpaint_fallback", str(exc)))

        with Image.open(working_path) as opened:
            pil_img = opened.convert("RGB")
        for b in bubbles:
            if not has_real_text(b):
                continue
            shape_mask = (bubble_shape_mask(image_path, b)
                          if (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS else None)
            render_text_in_box(pil_img, b, b["translated_text"],
                                font_size=b.get("font_size", 18), font_path=font_path,
                                font_category=b.get("font_category") or "regular",
                                custom_fonts=custom_fonts, mask=shape_mask)
        pil_img.save(result_path, "PNG")
        os.replace(result_path, out_path)
    finally:
        for leftover in (working_path, result_path):
            if os.path.exists(leftover):
                os.remove(leftover)
    return out_path, skipped_blank


def slice_webtoon_to_files(image_path: str, out_dir: str, target_height: int = 1600,
                           overlap: int = 100) -> list:
    """Writes each split_webtoon_strip() slice as its own PNG in
    `out_dir`; returns their paths in reading order."""
    import cv2

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    paths = []
    for s in split_webtoon_strip(image_path, target_height, overlap):
        path = os.path.join(out_dir, f"slice_{s['index']:03d}.png")
        cv2.imwrite(path, img[s["y_start"]:s["y_end"]])
        paths.append(path)
    return paths


def sample_text_style(image_path: str, box) -> dict:
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
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
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


def export_font_style_report(bubbles: list, out_path: str) -> str:
    """
    Dumps each bubble's box + detected/current font_category (and the
    raw style-sample fields, when present) to a JSON file -- a reviewable,
    reusable record of the style decisions this page was rendered with,
    the same idea as BalloonsTranslator's font-detection-to-JSON export
    (there, meant for handing off to Photoshop for external relettering;
    here, meant for reviewing what got auto-detected, or reusing the same
    choices on a re-render after editing translated text).
    """
    import json
    report = [{
        "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"],
        "font_category": b.get("font_category") or "regular",
        "ink_ratio": b.get("ink_ratio"),
        "irregular": b.get("irregular"),
    } for b in bubbles]
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path


# ---------------------------------------------------------------------------
# OCR backend auto-routing and manual-region OCR
# ---------------------------------------------------------------------------

def auto_ocr_backend(source_language: str, prefer_paddle_vl_manga: bool = False) -> str:
    """
    Default OCR backend by source language, the way comic-translate does
    but with maintained choices -- manga_ocr for Japanese (already a
    Baihe dependency), paddle for Chinese, paddle for Korean too
    (PaddleOCR supports both, unlike Tesseract-vs-nothing before this).
    Explicitly not Pororo for Korean (comic-translate's own choice) --
    checked its maintenance status directly: even a Hugging Face mirror
    of just its OCR piece exists specifically because people are worried
    about the main library's long-term upkeep, not worth taking on.

    prefer_paddle_vl_manga: opt-in second Japanese backend
    (jzhang533/PaddleOCR-VL-For-Manga) -- not a default swap, since its
    own model card only benchmarks against base PaddleOCR-VL, not
    against manga-ocr; which one's actually better for a given source is
    a real head-to-head call, not something this function decides.

    Always overridable manually -- this only picks the default; see
    ocr_box_region()'s own `backend` parameter.
    """
    if source_language == "ja":
        return "paddle_vl_manga" if prefer_paddle_vl_manga else "manga_ocr"
    if source_language in ("zh", "ko"):
        return "paddle"
    return "tesseract"


def ocr_box_region(image_path: str, box: dict, source_language: str, backend: str = None,
                    chinese_script: str = "simplified", tesseract_cmd: str = None,
                    prefer_paddle_vl_manga: bool = False) -> str:
    """
    Crops `box` out of image_path -- inset first via inset_box_for_ocr(),
    same reason as auto-detected bubbles (OCRing a bubble's own border
    can make some backends return nothing at all) -- and OCRs it with
    the auto-routed backend for source_language, or an explicit
    `backend` override.

    Shared by auto-detected bubbles and manual-region
    OCR: draw/type a box the auto-detector missed and run OCR on it,
    instead of requiring the translated text to be typed in by hand.
    """
    import ocr as ocr_module
    import tempfile
    from PIL import Image as _PILImage

    resolved_backend = backend or auto_ocr_backend(source_language, prefer_paddle_vl_manga)
    ocr_box = inset_box_for_ocr(box)
    tmp_path = None
    with _PILImage.open(image_path) as img:
        crop = img.crop((ocr_box["x"], ocr_box["y"],
                          ocr_box["x"] + ocr_box["w"], ocr_box["y"] + ocr_box["h"]))
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            crop.save(tmp.name)
            tmp_path = tmp.name
    try:
        return ocr_module.extract_text_from_images(
            [tmp_path], backend=resolved_backend, source_language=source_language,
            chinese_script=chinese_script, tesseract_cmd=tesseract_cmd).strip()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# PDF import/export
# ---------------------------------------------------------------------------

def pdf_to_page_images(pdf_path: str, out_dir: str, prefix: str = "page") -> tuple:
    """
    Splits a PDF into per-page images on import -- raws and finished
    scanlations are commonly shared as PDFs (Torii added this for the
    same reason, v2.0.6.1); Scanlate's own page uploader only ever took
    png/jpg/jpeg before this.

    Uses pypdf (BSD-3, genuinely permissive) to pull each page's
    embedded raster image, which is what a scanned-manga PDF actually
    contains -- one full-page image per PDF page, not vector content to
    render. PyMuPDF/fitz would also do this (and can additionally
    rasterize vector pages), but its own license is AGPL-3.0, not the
    permissive license the roadmap step that asked for this assumed --
    checked directly rather than taken on faith, and not pulled in.

    A page with no embedded image (a text/vector-only PDF page) is
    skipped, not a hard failure. Returns (image_paths, skipped_pages) --
    skipped_pages is a list of 0-based page indices, so the caller can
    say which pages didn't come through instead of silently losing them.
    """
    from pypdf import PdfReader

    os.makedirs(out_dir, exist_ok=True)
    reader = PdfReader(pdf_path)
    image_paths, skipped_pages = [], []
    for i, page in enumerate(reader.pages):
        images = list(page.images)
        if not images:
            skipped_pages.append(i)
            continue
        # A scanned page sometimes carries a small embedded logo/watermark
        # alongside the real page scan -- the largest image is the page.
        largest = max(images, key=lambda im: im.image.size[0] * im.image.size[1])
        out_path = os.path.join(out_dir, f"{prefix}_{i:04d}.png")
        largest.image.convert("RGB").save(out_path)
        image_paths.append(out_path)
    return image_paths, skipped_pages


# ---------------------------------------------------------------------------
# Bulk find-and-replace across a drama's saved bubble text
# ---------------------------------------------------------------------------

def bulk_find_replace_preview(items: list, find: str, replace: str, text_field: str = "translated_text",
                               case_sensitive: bool = False, use_regex: bool = False) -> list:
    """
    Previews a bulk find-and-replace across an already-translated
    project's text before anything is applied -- same "don't silently
    overwrite" pattern used everywhere else in this app. Distinct from
    the glossary (shapes *future* translations) and translation memory
    (*suggests* reuse going forward): this retroactively corrects text
    already saved across many rows at once (a name translated
    inconsistently before a glossary entry existed, a typo that repeats).

    items: dicts carrying whatever identifying fields the caller needs
    (Scanlate: "id"/"page_idx" from db.list_bubbles_for_drama(); the
    novel/workspace lines: "idx", or "id" once Line rows are
    saved) plus text_field itself -- "translated_text" for a Scanlate
    bubble (the default, unchanged from before this parameter existed),
    "en" for a drama's translated lines. Returns only the items that
    actually change, each as the original dict plus "old_text"/"new_text".
    Nothing here touches the database -- the caller applies each match
    (by whichever id field it needs) only after the person reviews this
    list.
    """
    import re

    if not find:
        return []
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = find if use_regex else re.escape(find)
    try:
        compiled = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError(f"Invalid find pattern: {exc}") from exc

    matches = []
    for item in items:
        old_text = item.get(text_field) or ""
        if not compiled.search(old_text):
            continue
        new_text = compiled.sub(replace, old_text)
        if new_text != old_text:
            match = dict(item)
            match["old_text"] = old_text
            match["new_text"] = new_text
            matches.append(match)
    return matches


# ---------------------------------------------------------------------------
# Structured text regions, SFX handling, shape-aware fitting, and the shared
# per-page / whole-chapter detect+OCR+translate pipeline
# ---------------------------------------------------------------------------

# Region kinds whose text is fitted to the bubble's own outline rather than
# its bounding rectangle. Narration boxes are rectangles already; signs and
# SFX sit on artwork, where "the light region around the box" isn't a shape.
SHAPE_FITTED_KINDS = ("bubble", "thought")

_CJK_LANGUAGES = ("ja", "zh", "ko")


def region_excluded_from_auto(region: dict) -> bool:
    """True for a region the automated inpaint-and-replace pass (and the
    translate call) leaves alone: an SFX region, unless the person set its
    per-region include_sfx override. classify_text_regions()' own note is
    the reason -- SFX lettering is usually part of the art -- so SFX is
    flagged for manual review rather than painted over by default."""
    return (region.get("kind") == "sfx") and not region.get("include_sfx")


def detect_script_language(text: str, fallback: str) -> str:
    """Best guess at a region's language from the script its OCR'd text is
    written in: any kana means Japanese, any Hangul means Korean. Han-only
    text is ambiguous between Chinese and all-kanji Japanese, so it keeps
    the drama's own source language when that's either of those. Latin-only
    text on a CJK-source page is most often English lettering (signs, SFX).
    No text at all, or nothing recognisable, keeps `fallback`."""
    import re
    text = text or ""
    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af\u1100-\u11ff]", text):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", text):
        return fallback if fallback in ("ja", "zh") else "zh"
    if re.search(r"[A-Za-z]", text) and fallback in _CJK_LANGUAGES:
        return "en"
    return fallback


def _count_runs(profile, min_gap: int = 2) -> int:
    """Number of separate ink runs in a 1-D projection profile, ignoring
    gaps narrower than min_gap (anti-aliasing noise inside one glyph)."""
    runs, gap, in_run = 0, min_gap, False
    for has_ink in profile:
        if has_ink:
            if not in_run and gap >= min_gap:
                runs += 1
            in_run, gap = True, 0
        else:
            in_run = False
            gap += 1
    return runs


def estimate_text_orientation(gray, box: dict, language: str) -> str:
    """"vertical" or "horizontal" for the text inside `box` of a grayscale
    page. Only CJK text is ever vertical; for those, the ink's projection
    profile decides -- vertical text shows up as several separate columns
    and one unbroken run top to bottom, horizontal text the other way
    round. A tie falls back to the box's own shape (tall = vertical)."""
    if language not in _CJK_LANGUAGES:
        return "horizontal"
    inner = inset_box_for_ocr(box)
    h, w = gray.shape[:2]
    x, y = max(0, inner["x"]), max(0, inner["y"])
    roi = gray[y:min(h, y + inner["h"]), x:min(w, x + inner["w"])]
    if roi.size:
        ink = roi < 128 if roi.mean() >= 128 else roi > 128
        col_runs = _count_runs(ink.any(axis=0))
        row_runs = _count_runs(ink.any(axis=1))
        if col_runs != row_runs:
            return "vertical" if col_runs > row_runs else "horizontal"
    return "vertical" if box["h"] > 1.2 * box["w"] else "horizontal"


def _panel_for_box(box: dict, panels: list):
    """Index of the panel (from detect_panels(), already in reading order)
    that overlaps this box the most -- a bubble spilling across a gutter
    still belongs to one beat. None if it overlaps no panel at all."""
    best, best_area = None, 0
    for i, p in enumerate(panels):
        ox = min(box["x"] + box["w"], p["x"] + p["w"]) - max(box["x"], p["x"])
        oy = min(box["y"] + box["h"], p["y"] + p["h"]) - max(box["y"], p["y"])
        if ox > 0 and oy > 0 and ox * oy > best_area:
            best, best_area = i, ox * oy
    return best


@dataclass
class TextRegion:
    """One detected text region as a single structured object,
    instead of fields scattered across detection, classification
    and panel functions.

    confidence is the detector's own score -- only the ML detector has
    one; the free OpenCV heuristic leaves it None rather than inventing a
    number. kind/kind_confidence come from classify_text_regions(),
    panel_id from detect_panels() (None when no panel contains it),
    orientation from estimate_text_orientation(), and language starts as
    the drama's source language and is refined from the OCR'd text's
    script once OCR has run (detect_script_language())."""
    x: int
    y: int
    w: int
    h: int
    reading_order: int
    language: str
    confidence: Optional[float]
    orientation: str
    panel_id: Optional[int]
    kind: str
    kind_confidence: float
    page_id: Optional[int] = None

    @property
    def bbox(self) -> tuple:
        return (self.x, self.y, self.w, self.h)

    def to_bubble(self) -> dict:
        """The dict shape db.save_bubbles()/process_page() work with."""
        d = asdict(self)
        d.pop("page_id")
        return d


def analyze_page_regions(image_path: str, boxes: list, source_language: str,
                         page_id: int = None) -> list:
    """Turns raw detector boxes into TextRegion objects: classifies each
    one (bubble/narration/sign/sfx/thought), assigns it to a panel, reads
    its text orientation, and puts the lot in reading order -- panel by
    panel (detect_panels() is already in manga reading order), keeping the
    detector's own order within a panel. Regions outside every panel keep
    their detector order after the panelled ones."""
    import cv2

    classified = classify_text_regions(image_path, boxes)
    gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    try:
        panels = detect_panels(image_path)
    except ValueError:
        panels = []

    staged = []
    for i, b in enumerate(classified):
        panel_id = _panel_for_box(b, panels)
        orientation = (estimate_text_orientation(gray, b, source_language)
                       if gray is not None else "horizontal")
        staged.append((panel_id if panel_id is not None else len(panels), i, b, panel_id, orientation))
    staged.sort(key=lambda t: (t[0], t[1]))

    regions = []
    for order, (_, _, b, panel_id, orientation) in enumerate(staged):
        conf = b.get("confidence")
        regions.append(TextRegion(
            x=int(b["x"]), y=int(b["y"]), w=int(b["w"]), h=int(b["h"]),
            reading_order=order, language=source_language,
            confidence=float(conf) if conf is not None else None,
            orientation=orientation, panel_id=panel_id,
            kind=b.get("kind", "bubble"), kind_confidence=float(b.get("kind_confidence", 0.0)),
            page_id=page_id))
    return regions


def detect_and_ocr_page(image_path: str, source_language: str, detect_backend: str = "auto",
                        hf_token: str = None, ocr_backend: str = None, tesseract_cmd: str = None,
                        prefer_paddle_vl_manga: bool = False, page_id: int = None):
    """Detect -> structure (analyze_page_regions) -> OCR -> font-style
    sample for one page. Shared by the single-page Detect button and the
    whole-chapter batch (batch_process_pages()), so both do exactly the
    same thing per page.

    Returns (bubbles, notes): bubbles are TextRegion.to_bubble() dicts with
    source_text filled in and translated_text left empty for the translate
    step; notes are (level, message) pairs -- "warning" when the ML
    detector fell back to the heuristic, "error" when nothing was found --
    for the caller to show."""
    notes = []
    try:
        boxes = detect_bubbles(image_path, backend=detect_backend, hf_token=hf_token)
    except BubbleModelUnavailable as exc:
        notes.append(("warning", str(exc)))
        boxes = detect_bubbles_cv(image_path)

    if not boxes:
        _, rejected = detect_bubbles_cv(image_path, debug=True)
        reasons = {}
        for r in rejected:
            reasons[r[4]] = reasons.get(r[4], 0) + 1
        detail = ("Rejected candidates: "
                  + ", ".join(f"{n}× {why}" for why, n in
                              sorted(reasons.items(), key=lambda x: -x[1]))
                  ) if reasons else "No light enclosed regions found at all."
        notes.append(("error",
            "**No bubbles detected on this page.**\n\n"
            f"{detail}\n\n"
            "Detection looks for enclosed light regions that don't touch the page "
            "edge. It struggles with borderless bubbles, dark/inverted panels, "
            "very low-contrast scans, and text drawn straight onto artwork.\n\n"
            "What to try: the ML backend if you can reach Hugging Face, or add "
            "boxes by hand with '➕ Add a bubble manually' below."))
        return [], notes

    bubbles = []
    for region in analyze_page_regions(image_path, boxes, source_language, page_id=page_id):
        b = region.to_bubble()
        # ocr_box_region() insets the box (a border can blank OCR).
        try:
            b["source_text"] = ocr_box_region(
                image_path, b, source_language, backend=ocr_backend,
                tesseract_cmd=tesseract_cmd, prefer_paddle_vl_manga=prefer_paddle_vl_manga)
        except HeadroomError:
            raise  # blank text would hide the refusal
        except Exception:
            b["source_text"] = ""
        b["language"] = detect_script_language(b["source_text"], b["language"])
        # Classical-CV style guess (see sample_text_style()), reviewable
        # per bubble before render.
        style = sample_text_style(image_path, b)
        b["font_category"] = style["suggested_style"]
        b["ink_ratio"] = style.get("ink_ratio")
        b["irregular"] = style.get("irregular")
        b.update(translated_text="", font_size=18, skip=False, include_sfx=False)
        bubbles.append(b)
    return bubbles, notes


def translate_page_bubbles(bubbles: list, engine, drama_meta: dict, previous_context: str = "",
                           glossary_terms=None, usage_cb=None) -> str:
    """Translates a page's bubbles from their CURRENT source_text -- so an
    OCR mistake fixed by hand in the review step is what
    reaches the translation call, not the raw OCR output. Skipped regions
    and SFX left out of the automated pass (region_excluded_from_auto())
    aren't sent at all and keep whatever translated_text they had; neither
    are regions with no source text.

    Mutates `bubbles` in place and returns the new rolling context for the
    next page. Answers are matched to bubbles by explicit id
    (translate_regions_by_id); an answer that is unreadable, short, padded,
    reordered-by-position or names an unknown id is rejected outright
    (ValueError) and no bubble is touched."""
    eligible = [b for b in bubbles
                if not b.get("skip") and not region_excluded_from_auto(b)
                and (b.get("source_text") or "").strip()]
    if not eligible:
        return previous_context
    # Each region goes out under an explicit key and is applied through a
    # key -> region map (translate_regions_by_id): a short, reordered or
    # padded answer applies nothing, never a shifted one.
    by_key = {str(i): b for i, b in enumerate(eligible)}
    result, new_context = translate_regions_by_id(
        {k: b["source_text"] for k, b in by_key.items()}, engine, drama_meta,
        previous_context=previous_context, usage_cb=usage_cb, glossary_terms=glossary_terms)
    if result is None:
        raise ValueError("The translation service's answer couldn't be matched to the bubbles "
                         "by id (unreadable, short, padded or with unknown ids) -- not applied, "
                         "since there's no safe way to trust it.")
    for key, text in result.items():
        by_key[key]["translated_text"] = text or ""
    return new_context


def batch_process_pages(pages: list, source_language: str, save_fn, engine=None,
                        drama_meta: dict = None, glossary_terms=None, previous_context: str = "",
                        usage_cb=None, progress_cb=None, **detect_kwargs) -> dict:
    """Detect + OCR + translate across a chapter's saved pages,
    reusing the exact per-page functions the single-page Detect
    button uses, in page order, carrying the rolling prior-page context
    from one page to the next.

    pages: [{"id": page_id, "image_path": path}, ...] in reading order.
    save_fn(page_id, bubbles) persists each page (db.save_bubbles in the
    app). engine=None does detect+OCR only. detect_kwargs go to
    detect_and_ocr_page() (detect_backend, hf_token, ocr_backend, ...).
    progress_cb(done, total, page) is called after each page.

    One page failing doesn't stop the rest (same per-page isolation as
    bulk_render_pages()); a failed translation still saves that page's
    OCR text. Returns {"processed": [{"page_id", "bubbles", "notes",
    "context"}], "errors": [{"page_id", "error"}], "context": final
    rolling context}. Each processed entry's own "context"
    is the rolling context AS OF right after that page -- the
    caller's own per-page context store should key off that, not just
    the run's single final "context" value, so a later out-of-order
    re-run of an earlier page in this same run can still find its real
    predecessor's context instead of this whole run's last page's."""
    report = {"processed": [], "errors": [], "context": previous_context}
    context = previous_context
    for done, page in enumerate(pages, start=1):
        try:
            bubbles, notes = detect_and_ocr_page(
                page["image_path"], source_language, page_id=page["id"], **detect_kwargs)
            if engine is not None and bubbles:
                try:
                    context = translate_page_bubbles(
                        bubbles, engine, drama_meta or {}, previous_context=context,
                        glossary_terms=glossary_terms, usage_cb=usage_cb)
                except Exception as exc:
                    notes.append(("warning", f"Translation failed ({exc}) -- OCR text was "
                                             f"still saved; retranslate this page from its "
                                             f"review step."))
            save_fn(page["id"], bubbles)
            report["processed"].append({"page_id": page["id"], "bubbles": len(bubbles),
                                        "notes": notes, "context": context})
        except Exception as exc:
            report["errors"].append({"page_id": page["id"],
                                     "error": f"{type(exc).__name__}: {exc}"})
        if progress_cb:
            progress_cb(done, len(pages), page)
    report["context"] = context
    return report
