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
text in a table before final render -- see the Scanlate tab in app.py.
"""

import os
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def detect_bubbles_cv(image_path: str, min_area_frac: float = 0.0015,
                       max_area_frac: float = 0.35, debug: bool = False):
    """
    Heuristic speech-bubble detector for a typical comic page.

    The key signal is that a speech bubble is an ENCLOSED light region that
    does not touch the edge of the page, while the page background and the
    gutters between panels do. Filtering on that removes the background
    without needing to know anything about the artwork.

    Steps: threshold for light regions, close small gaps in bubble
    outlines, take connected components, drop anything touching the image
    border, then filter what's left on size, aspect ratio and how
    completely it fills its bounding box (bubbles are convex-ish; ragged
    artwork highlights are not).

    Returns boxes sorted in manga reading order (top-to-bottom, then
    right-to-left). Works on light-background pages, which is the normal
    case; pass debug=True to get the rejection reasons back.
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    total_area = float(h * w)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Otsu adapts to scans that aren't pure white, with a fixed floor so a
    # dark page can't drag the threshold down into the artwork.
    otsu_val, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thresh_val = max(otsu_val, 180)
    _, light = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)

    # Close the gaps that text strokes punch through the bubble interior,
    # so a bubble becomes one solid component rather than many fragments.
    kernel = np.ones((7, 7), np.uint8)
    closed = cv2.morphologyEx(light, cv2.MORPH_CLOSE, kernel, iterations=3)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)

    boxes, rejected = [], []
    for i in range(1, n_labels):          # 0 is background
        x, y, bw, bh, area = (stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP],
                              stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT],
                              stats[i, cv2.CC_STAT_AREA])

        # The page background and gutters reach the edge; bubbles don't.
        if x <= 1 or y <= 1 or (x + bw) >= (w - 1) or (y + bh) >= (h - 1):
            rejected.append((x, y, bw, bh, "touches page edge"))
            continue
        if area < total_area * min_area_frac:
            rejected.append((x, y, bw, bh, "too small"))
            continue
        if area > total_area * max_area_frac:
            rejected.append((x, y, bw, bh, "too large"))
            continue

        aspect = bw / max(bh, 1)
        if aspect > 12 or aspect < 0.08:
            rejected.append((x, y, bw, bh, "implausible aspect ratio"))
            continue
        # A bubble fills most of its bounding box; ragged highlights don't.
        if area / float(max(bw * bh, 1)) < 0.55:
            rejected.append((x, y, bw, bh, "too sparse to be a bubble"))
            continue
        if bw < 25 or bh < 18:
            rejected.append((x, y, bw, bh, "smaller than readable text"))
            continue

        boxes.append({"x": int(x), "y": int(y), "w": int(bw), "h": int(bh)})

    boxes.sort(key=lambda b: (b["y"] // max(int(h * 0.08), 1), -b["x"]))
    if debug:
        return boxes, rejected
    return boxes


def inset_box_for_ocr(box: dict, frac: float = 0.10, min_inset: int = 4, max_inset: int = 15) -> dict:
    """
    Shrinks a detected bubble box slightly before it's cropped for OCR.

    Found by direct testing: OCRing the box exactly as detected -- border
    included -- can make Tesseract return nothing at all, or a few stray
    characters instead of the real text, because the bubble's own outline
    reads as a large enclosing shape it can't segment past. Trimming a
    small margin off each side keeps the outline out of the crop for the
    common case (straight or gently-curved bubble edges); an unusually
    round bubble can still leave some curve in the corners, since this is
    a plain rectangular inset, not a shape-aware mask -- OCR text is
    already meant to be checked/edited before it's used, same as any
    other auto-detected box or translation in this pipeline.
    """
    inset_x = max(min_inset, min(int(box["w"] * frac), max_inset))
    inset_y = max(min_inset, min(int(box["h"] * frac), max_inset))
    # Never inset past the box's own center -- a box smaller than 2x the
    # inset would otherwise collapse to zero or negative size.
    inset_x = min(inset_x, box["w"] // 2 - 1) if box["w"] > 2 else 0
    inset_y = min(inset_y, box["h"] // 2 - 1) if box["h"] > 2 else 0
    inset_x, inset_y = max(inset_x, 0), max(inset_y, 0)
    return {
        "x": box["x"] + inset_x, "y": box["y"] + inset_y,
        "w": box["w"] - 2 * inset_x, "h": box["h"] - 2 * inset_y,
    }


def detect_bubbles_ml(image_path: str, confidence: float = 0.25, hf_token: str = None):
    """
    Real trained bubble/text detector, as an upgrade over
    detect_bubbles_cv()'s free heuristic. Uses a YOLO-family model
    fine-tuned for comic/manga text-region detection (the same class
    of model koharu and manga-image-translator use under the hood).

    Requires: `pip install ultralytics huggingface_hub`
    Downloads the model checkpoint from Hugging Face on first use
    (needs internet once; cached locally after).

    NOTE: written against the documented ultralytics/huggingface_hub
    APIs but not run end-to-end in the environment this was built in
    (no network access there to download a model or test inference).
    Sanity-check on one page before relying on it for a whole batch --
    if the specific checkpoint ID below has moved or been renamed,
    swap in whatever comic/manga text-detection YOLO checkpoint you
    find current on Hugging Face; the rest of this function (box
    extraction, confidence filtering, reading-order sort) stays the same.
    """
    import os as _os
    from huggingface_hub import hf_hub_download
    from ultralytics import YOLO

    # Same gap that hit Whisper's downloads: without a token, every request
    # is anonymous and rate-limited (the "unauthenticated requests" warning).
    # hf_hub_download reads HF_TOKEN from the environment itself, so setting
    # it here is enough -- no need to pass it through every call below.
    _tok = hf_token or _os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")
    if _tok:
        _os.environ.setdefault("HF_TOKEN", _tok)

    global _bubble_ml_model
    if "_bubble_ml_model" not in globals():
        model_path = hf_hub_download(
            repo_id="ogkalu/comic-text-and-bubble-detector",
            filename="comic-text-and-bubble-detector.pt",
        )
        globals()["_bubble_ml_model"] = YOLO(model_path)

    model = globals()["_bubble_ml_model"]
    results = model.predict(image_path, conf=confidence, verbose=False)

    boxes = []
    for result in results:
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            boxes.append({
                "x": int(x1), "y": int(y1),
                "w": int(x2 - x1), "h": int(y2 - y1),
                "confidence": float(box.conf[0]),
            })

    boxes.sort(key=lambda b: (b["y"] // 50, -b["x"]))
    return boxes


class BubbleModelUnavailable(RuntimeError):
    """The ML detector couldn't be loaded. Carries whether the free
    heuristic already ran as a fallback, so the UI can say what happened
    instead of showing a stack trace."""

    def __init__(self, message, fell_back_to_cv=True):
        super().__init__(message)
        self.fell_back_to_cv = fell_back_to_cv


def detect_bubbles(image_path: str, backend: str = "cv", **kwargs):
    """Dispatcher: backend='cv' (free, default) or 'ml' (trained model,
    better accuracy, heavier install).

    If the ML backend can't run -- not installed, or the model can't be
    downloaded -- this falls back to the heuristic and raises
    BubbleModelUnavailable with the boxes still attached, so a network
    problem degrades to a working-but-rougher result rather than failing
    the page entirely.
    """
    if backend != "ml":
        return detect_bubbles_cv(image_path)

    try:
        return detect_bubbles_ml(image_path, **kwargs)
    except ImportError as exc:
        raise BubbleModelUnavailable(
            "The ML detector needs extra packages:\n"
            "    pip install ultralytics huggingface_hub\n\n"
            "Falling back to the free heuristic for this page."
        ) from exc
    except Exception as exc:
        from core import _is_network_error
        if _is_network_error(exc):
            raise BubbleModelUnavailable(
                "Couldn't download the bubble-detection model -- this is a network "
                "problem, not a problem with your image.\n\n"
                "If huggingface.co is blocked by a DNS blocker on your network "
                "(Pi-hole, AdGuard), whitelist:\n"
                "  huggingface.co\n  cdn-lfs.huggingface.co\n  cdn-lfs-us-1.hf.co\n  hf.co\n\n"
                "Falling back to the free heuristic for this page."
            ) from exc
        raise BubbleModelUnavailable(
            f"The ML detector failed: {type(exc).__name__}: {exc}\n\n"
            "Falling back to the free heuristic for this page."
        ) from exc


def inpaint_region(image_path: str, box: dict, out_path: str = None, padding: int = 4):
    """Removes text within `box` using OpenCV inpainting so the
    translated text has a clean background. Returns the path to the
    (possibly newly-created) cleaned image; if out_path is None,
    overwrites nothing and returns a PIL Image instead."""
    import cv2
    img = cv2.imread(image_path)
    h, w = img.shape[:2]
    x = max(0, box["x"] - padding)
    y = max(0, box["y"] - padding)
    bw = min(w - x, box["w"] + 2 * padding)
    bh = min(h - y, box["h"] + 2 * padding)

    roi = img[y:y + bh, x:x + bw]
    gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # Text is the dark pixels inside an otherwise light bubble
    _, mask = cv2.threshold(gray_roi, 150, 255, cv2.THRESH_BINARY_INV)
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)

    inpainted_roi = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    img[y:y + bh, x:x + bw] = inpainted_roi

    if out_path:
        cv2.imwrite(out_path, img)
        return out_path
    return img


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


def render_text_in_box(image, box: dict, text: str, font_size: int = 18,
                        font_path: str = None, font_category: str = "regular",
                        custom_fonts: dict = None, fill=(0, 0, 0), align="center"):
    """
    image: PIL Image (already inpainted/cleaned) -- mutated in place.
    Auto-shrinks font_size until the wrapped text fits the box height;
    word-wraps to fit box width. Horizontal text layout only -- no
    vertical CJK rendering (that's koharu's specialty, not replicated
    here).

    font_path: an explicit per-bubble override (always wins). Otherwise
    font_category (one of FONT_CATEGORIES, normally auto-filled from
    sample_text_style()'s detection at bubble-detection time) picks
    between a bold/regular/handwritten face via _find_font() -- see that
    function for the full resolution order and custom_fonts.
    """
    draw = ImageDraw.Draw(image)
    resolved_font_path = _find_font(font_path, category=font_category, custom_fonts=custom_fonts)
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
                                 usage_cb=None):
    """
    Translates a page's bubble texts with awareness of what happened on
    prior pages, the way Torii's context-passing works for manga --
    keeps character voice and ongoing plot threads consistent across a
    whole chapter instead of each page being translated in isolation.

    previous_context: a short rolling summary carried from the last
    page's translate call (see below). Returns (translations, new_context)
    -- pass new_context into the next page's call to keep the chain going.
    """
    from translate_engines import call_llm_json
    import re, json

    if not getattr(engine, "supports_reference", False):
        # Pure-MT engines can't do context-aware translation or summarization
        return engine.translate_batch(texts, {"drama_meta": drama_meta}), previous_context

    context_block = f"\n\nContext from previous pages: {previous_context}" if previous_context else ""
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    prompt = (
        "Translate these manga/comic speech bubble texts into natural English, in reading "
        "order, keeping character voice and plot consistent with the context below if any."
        + context_block + f"\n\nBubbles on this page:\n{numbered}\n\n"
        'Return ONLY a JSON object: {"translations": ["...", ...], "context_summary": '
        '"1-2 sentence summary of what just happened, to carry into the next page"}. '
        "No preamble, no markdown fences."
    )
    text = call_llm_json(engine, prompt, max_tokens=1500, fallback=None, usage_cb=usage_cb)
    if text is None:
        return [""] * len(texts), previous_context

    text = re.sub(r"^```json|^```|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        translations = data.get("translations", [""] * len(texts))
        new_context = data.get("context_summary", previous_context)
    except json.JSONDecodeError:
        translations, new_context = [""] * len(texts), previous_context
    return translations, new_context


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
                  custom_fonts: dict = None):
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
    """
    import shutil

    def has_real_text(b):
        return not b.get("skip") and b.get("translated_text", "").strip()

    skipped_blank = [b for b in bubbles if not b.get("skip") and not b.get("translated_text", "").strip()]

    working_path = out_path + ".tmp.png"
    shutil.copy(image_path, working_path)
    for b in bubbles:
        if not has_real_text(b):
            continue
        inpaint_region(working_path, b, out_path=working_path)

    pil_img = Image.open(working_path).convert("RGB")
    for b in bubbles:
        if not has_real_text(b):
            continue
        render_text_in_box(pil_img, b, b["translated_text"],
                            font_size=b.get("font_size", 18), font_path=font_path,
                            font_category=b.get("font_category") or "regular",
                            custom_fonts=custom_fonts)
    pil_img.save(out_path)
    if os.path.exists(working_path):
        os.remove(working_path)
    return out_path, skipped_blank


# ---------------------------------------------------------------------------
# Panel detection & webtoon long-strip handling
# ---------------------------------------------------------------------------

def detect_panels(image_path: str, min_panel_frac: float = 0.02):
    """
    Finds comic panels by looking for the gutters (whitespace channels)
    between them. Returns boxes in reading order.

    Useful for two things: translating only the panels you're actually
    looking at, and giving the translator panel-level context instead of
    a flat list of bubbles with no sense of which are in the same beat.
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Panels are bounded by dark borders on a light page (or vice versa);
    # inverting and closing turns panel interiors into solid blobs.
    _, thresh = cv2.threshold(gray, 240, 255, cv2.THRESH_BINARY_INV)
    closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    total_area = h * w
    panels = []
    for c in contours:
        x, y, pw, ph = cv2.boundingRect(c)
        if (pw * ph) < total_area * min_panel_frac:
            continue
        if pw < w * 0.1 or ph < h * 0.03:
            continue
        panels.append({"x": x, "y": y, "w": pw, "h": ph})

    # Manga reading order: top-to-bottom, then right-to-left within a row.
    panels.sort(key=lambda p: (p["y"] // max(int(h * 0.08), 1), -p["x"]))
    return panels


def split_webtoon_strip(image_path: str, target_height: int = 1600, overlap: int = 100):
    """
    Webtoons are single continuous vertical strips, often 10,000+ pixels
    tall. Treating one as a "page" breaks bubble detection (everything
    is tiny relative to the canvas) and blows past image size limits.

    This slices the strip at whitespace gaps near the target height, so
    cuts land between panels rather than through artwork. `overlap`
    keeps a small band shared between adjacent slices so a bubble
    straddling a cut isn't lost.

    Returns a list of {"y_start", "y_end", "index"} describing the
    slices, without writing files -- the caller decides what to save.
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    if h <= target_height:
        return [{"y_start": 0, "y_end": h, "index": 0}]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # A row is "empty" if it's nearly uniform -- that's a gutter.
    row_std = gray.std(axis=1)
    empty_rows = row_std < 6

    slices = []
    y = 0
    idx = 0
    while y < h:
        ideal_end = min(y + target_height, h)
        if ideal_end >= h:
            slices.append({"y_start": y, "y_end": h, "index": idx})
            break
        # Search a window around the ideal cut for the emptiest row.
        search_lo = max(y + int(target_height * 0.6), 0)
        search_hi = min(y + int(target_height * 1.25), h - 1)
        window = empty_rows[search_lo:search_hi]
        cut = ideal_end
        if window.any():
            empty_idxs = np.flatnonzero(window)
            # prefer the empty row closest to the ideal cut point
            cut = search_lo + int(empty_idxs[np.argmin(np.abs(
                (empty_idxs + search_lo) - ideal_end))])
        slices.append({"y_start": y, "y_end": cut, "index": idx})
        y = max(cut - overlap, cut) if overlap == 0 else max(cut - overlap, y + 1)
        idx += 1
        if idx > 200:  # guard against pathological images
            break
    return slices


def save_webtoon_slices(image_path: str, out_dir: str, target_height: int = 1600,
                         overlap: int = 100):
    """Writes the slices from split_webtoon_strip() to disk. Returns a
    list of {"path", "y_start", "y_end", "index"}."""
    import cv2
    import os as _os

    _os.makedirs(out_dir, exist_ok=True)
    img = cv2.imread(image_path)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    out = []
    for s in split_webtoon_strip(image_path, target_height, overlap):
        crop = img[s["y_start"]:s["y_end"], :]
        p = _os.path.join(out_dir, f"strip_{s['index']:04d}.png")
        cv2.imwrite(p, crop)
        out.append({"path": p, **s})
    return out


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


def classify_text_regions(image_path: str, boxes):
    """
    Sorts detected text regions into bubbles, narration boxes, signs, and
    sound effects using cheap local geometry -- no model download.

    Why it matters: these want different treatment. Bubbles get clean
    inpaint-and-replace. Signs are part of the art and are usually better
    served by a small overlay or a margin note than by painting over the
    artwork. SFX often shouldn't be touched at all, since the lettering
    IS the art.

    Heuristic and imperfect -- exposed for review rather than applied
    silently.
    """
    import cv2
    import numpy as np

    img = cv2.imread(image_path)
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

        if mean_val > 200 and fill_uniformity > 0.45:
            # Light, uniform interior -- a balloon or a caption box.
            # Caption boxes tend to be wide, short, and page-edge aligned.
            edge_aligned = x < w * 0.05 or (x + bw) > w * 0.95
            kind = "narration" if (aspect > 3.0 and edge_aligned) else "bubble"
            conf = 0.75
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
