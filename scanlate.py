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

import numpy as np
from memory_headroom import HeadroomError
from ocr import auto_ocr_backend

from scanlate_detect import (
    BubbleModelUnavailable, decode_page, detect_bubbles, detect_bubbles_cv, inset_box_for_ocr,
    merge_fragment_boxes, page_gray, split_webtoon_strip,
)
import scanlate_tuning
from scanlate_regions import analyze_page_regions, detect_script_language, lettering_size
from scanlate_render import sample_text_style
from scanlate_translate import translate_page_bubbles


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


def clean_ocr_crop(crop):
    """`crop` (a PIL image) ready for OCR. A noisy scan's crop -- its paper
    varies by more than scanlate_tuning.OCR_DENOISE_MIN_NOISE gray levels --
    gets a 3 px median blur and its contrast stretched to full black and
    white: on the Scanlate bench that took PaddleOCR from 29% to 9% character
    error on scanned pages, while on clean pages the same step cost every
    backend a few points, so a clean crop is returned as it is (and only the
    backends in OCR_DENOISE_BACKENDS get it at all)."""
    try:
        import cv2
    except ImportError:
        return crop
    from PIL import Image as _PILImage
    gray = np.asarray(crop.convert("L"))
    if gray.size < 64:
        return crop
    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    paper = gray[gray > otsu]
    if paper.size < 32 or float(paper.std()) < scanlate_tuning.OCR_DENOISE_MIN_NOISE:
        return crop
    smooth = cv2.medianBlur(gray, 3)
    lo, hi = np.percentile(smooth, 2), np.percentile(smooth, 98)
    if hi - lo < 30:
        return crop
    stretched = np.clip((smooth.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255)
    return _PILImage.fromarray(stretched.astype(np.uint8))


def ocr_box_region(image_path: str, box: dict, source_language: str, backend: str = None,
                    chinese_script: str = "simplified", tesseract_cmd: str = None,
                    prefer_paddle_vl_manga: bool = False, image=None) -> str:
    """
    Crops `box` out of image_path -- inset first via inset_box_for_ocr(),
    same reason as auto-detected bubbles (OCRing a bubble's own border
    can make some backends return nothing at all) -- and OCRs it with
    the auto-routed backend for source_language, or an explicit
    `backend` override.

    Shared by auto-detected bubbles and manual-region
    OCR: draw/type a box the auto-detector missed and run OCR on it,
    instead of requiring the translated text to be typed in by hand.

    image: the page already decoded as BGR (see decode_page()), so a page's
    bubbles share one decode; image_path is then not read.
    """
    import ocr as ocr_module
    import tempfile
    from PIL import Image as _PILImage

    resolved_backend = backend or auto_ocr_backend(source_language, prefer_paddle_vl_manga)
    ocr_box = inset_box_for_ocr(box)
    tmp_path = None
    if image is not None:
        x0, y0 = max(0, ocr_box["x"]), max(0, ocr_box["y"])
        region = image[y0:max(y0, ocr_box["y"] + ocr_box["h"]),
                       x0:max(x0, ocr_box["x"] + ocr_box["w"])]
        if not region.size:
            return ""
        crop = _PILImage.fromarray(np.ascontiguousarray(region[:, :, ::-1]))
        if resolved_backend in scanlate_tuning.OCR_DENOISE_BACKENDS:
            crop = clean_ocr_crop(crop)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            crop.save(tmp.name)
            tmp_path = tmp.name
    else:
        with _PILImage.open(image_path) as img:
            crop = img.crop((ocr_box["x"], ocr_box["y"],
                              ocr_box["x"] + ocr_box["w"], ocr_box["y"] + ocr_box["h"]))
            if resolved_backend in scanlate_tuning.OCR_DENOISE_BACKENDS:
                crop = clean_ocr_crop(crop)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                crop.save(tmp.name)
                tmp_path = tmp.name
    try:
        return ocr_module.extract_text_from_images(
            [tmp_path], backend=resolved_backend, source_language=source_language,
            chinese_script=chinese_script, tesseract_cmd=tesseract_cmd,
            **({"vertical": True} if box.get("orientation") == "vertical" else {})).strip()
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


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


def detect_and_ocr_page(image_path: str, source_language: str, detect_backend: str = "auto",
                        hf_token: str = None, ocr_backend: str = None, tesseract_cmd: str = None,
                        prefer_paddle_vl_manga: bool = False, page_id: int = None,
                        art_captions: bool = False):
    """Detect -> structure (analyze_page_regions) -> OCR -> font-style
    sample for one page. Shared by the single-page Detect button and the
    run service's per-page loop, so both do exactly the same thing per page.

    Returns (bubbles, notes): bubbles are TextRegion.to_bubble() dicts with
    source_text filled in and translated_text left empty for the translate
    step; notes are (level, message) pairs -- "warning" when the ML
    detector fell back to the heuristic, "error" when nothing was found --
    for the caller to show.

    art_captions: also find lettering drawn straight onto the artwork (see
    scanlate_art_text), which the bubble detector never sees, and return it as
    regions of kind "caption" after the bubbles.

    The page is decoded once (decode_page()) and that array shared by every
    step, instead of each step and each bubble reading the file again."""
    notes = []
    try:
        page = decode_page(image_path)
    except ValueError:
        # Each step then reads the file itself, so an unreadable page fails
        # (or falls back) exactly where and how it did before.
        page = None
    gray = page_gray(image_path, page) if page is not None else None
    try:
        boxes = detect_bubbles(image_path, backend=detect_backend, hf_token=hf_token, image=page)
    except BubbleModelUnavailable as exc:
        notes.append(("warning", str(exc)))
        boxes = detect_bubbles_cv(image_path, image=page)
    boxes = merge_fragment_boxes(boxes)

    captions = None
    if not boxes:
        # A page of lettering on the art alone is not "no bubbles found".
        if art_captions:
            captions = _art_caption_regions(image_path, source_language, [], page, notes)
        if not captions:
            _note_no_bubbles(image_path, page, notes)
            return [], notes

    bubbles = []
    ocr_failed = 0
    for region in analyze_page_regions(image_path, boxes, source_language, page_id=page_id,
                                       image=page):
        b = region.to_bubble()
        # ocr_box_region() insets the box (a border can blank OCR).
        try:
            b["source_text"] = ocr_box_region(
                image_path, b, source_language, backend=ocr_backend,
                tesseract_cmd=tesseract_cmd, prefer_paddle_vl_manga=prefer_paddle_vl_manga,
                image=page)
        except HeadroomError:
            raise  # blank text would hide the refusal
        except Exception:
            b["source_text"] = ""
            ocr_failed += 1
        b["language"] = detect_script_language(b["source_text"], b["language"])
        # Classical-CV style guess (see sample_text_style()), reviewable
        # per bubble before render.
        style = sample_text_style(image_path, b, image=gray)
        b["font_category"] = style["suggested_style"]
        b["ink_ratio"] = style.get("ink_ratio")
        b["irregular"] = style.get("irregular")
        b.update(translated_text="", font_size=suggest_font_size(gray, b), skip=False,
                 include_sfx=False)
        bubbles.append(b)
    if art_captions:
        bubbles += (captions if captions is not None
                    else _art_caption_regions(image_path, source_language, bubbles, page, notes))
    if ocr_failed:
        # Fixed text: the exception may carry a model path, and the caller
        # needs "OCR broke" kept apart from "the bubble holds no text".
        notes.append(("error", f"OCR failed on {ocr_failed} of {len(bubbles)} regions. Check "
                               "that the OCR backend is installed (Diagnostics)."))
    return bubbles, notes


def suggest_font_size(gray, region: dict) -> int:
    """The size the English starts at for a region: its source lettering's
    size scaled by scanlate_tuning.TYPESET_FONT_PER_SOURCE_PX, within
    TYPESET_START_MIN_PX..TYPESET_START_MAX_PX; the render then shrinks it to fit
    the balloon. TYPESET_DEFAULT_FONT_PX when no lettering is measurable."""
    t = scanlate_tuning
    size = lettering_size(gray, region, region.get("orientation")) if gray is not None else None
    if not size:
        return t.TYPESET_DEFAULT_FONT_PX
    return int(min(max(round(size * t.TYPESET_FONT_PER_SOURCE_PX), t.TYPESET_START_MIN_PX),
                   t.TYPESET_START_MAX_PX))


def _note_no_bubbles(image_path: str, page, notes: list) -> None:
    _, rejected = detect_bubbles_cv(image_path, debug=True, image=page)
    reasons = {}
    for r in rejected:
        reasons[r[4]] = reasons.get(r[4], 0) + 1
    top = sorted(reasons.items(), key=lambda x: -x[1])[:3]
    detail = (" Skipped: " + ", ".join(f"{n} {why}" for why, n in top) + ".") if top else ""
    # A warning, not an error: covers and splash art legitimately have no bubbles.
    notes.append(("warning",
        "No bubbles found on this page. That is normal for covers and splash art; "
        "otherwise try the ML detector (Advanced) or add boxes by hand." + detail))


def _art_caption_regions(image_path: str, source_language: str, bubbles: list, page,
                         notes: list) -> list:
    """Regions (kind "caption") for lettering on the artwork that no bubble
    covers, with the OCR detector's own text. Appended after the bubbles in
    reading order. A failure leaves the page with its bubbles and a note."""
    import scanlate_art_text
    try:
        found = scanlate_art_text.find_art_captions(image_path, source_language, bubbles,
                                                    image=page)
    except HeadroomError:
        raise
    except Exception as exc:
        notes.append(("warning", f"Captions on the art could not be read "
                                 f"({type(exc).__name__}); the bubbles were still translated."))
        return []
    regions = []
    for i, c in enumerate(found):
        regions.append({
            "x": c["x"], "y": c["y"], "w": c["w"], "h": c["h"],
            "reading_order": len(bubbles) + i,
            "language": detect_script_language(c["source_text"], source_language),
            "confidence": None,
            "orientation": "vertical" if c["h"] > 1.4 * c["w"] else "horizontal",
            "panel_id": None, "kind": "caption", "kind_confidence": 0.5,
            "source_text": c["source_text"], "font_category": "bold",
            "ink_ratio": None, "irregular": None,
            "translated_text": "", "font_size": 18, "skip": False, "include_sfx": False})
    return regions


def join_caption_across_pages(previous: dict, bubbles: list, engine,
                              drama_meta: dict, context: str, glossary_terms, usage_cb,
                              save_fn=None) -> str:
    """A caption cut by the page break is OCR'd as two fragments that translate
    badly alone. Translates the pair as one line and splits the English back
    between the pages by the share of source each held, changing both regions
    in place; `save_fn(page_id, bubbles)`, when given, saves the previous page
    again with its half. A failed translation keeps both per-page translations;
    a memory refusal, a cancel or a stopped run is raised, as for a page."""
    import scanlate_art_text
    from engine_backends.run_limits import TranslateRunStopped
    from engine_backends.shared import LLMTaskTimeout, TranslationCancelled

    def live_captions(items):
        return [b for b in items if b.get("kind") == "caption" and not b.get("skip")
                and (b.get("source_text") or "").strip() and (b.get("translated_text") or "").strip()]

    uppers, lowers = live_captions(previous["bubbles"]), live_captions(bubbles)
    if not uppers or not lowers:
        return context
    upper = max(uppers, key=lambda b: b["y"] + b["h"])
    lower = min(lowers, key=lambda b: b["y"])
    if not scanlate_art_text.continues_across_pages(upper, lower, previous["height"]):
        return context
    joined = {"kind": "caption", "skip": False, "include_sfx": False,
              "source_text": upper["source_text"] + lower["source_text"], "translated_text": ""}
    try:
        new_context = translate_page_bubbles([joined], engine, drama_meta, previous_context=context,
                                             glossary_terms=glossary_terms, usage_cb=usage_cb)
    except (HeadroomError, LLMTaskTimeout, TranslateRunStopped, TranslationCancelled):
        raise
    except Exception:
        return context
    halves = scanlate_art_text.split_by_source_share(
        joined["translated_text"], upper["source_text"], lower["source_text"])
    if halves is None:
        return new_context
    upper["translated_text"], lower["translated_text"] = halves
    if save_fn is not None:
        save_fn(previous["id"], previous["bubbles"])
    return new_context
