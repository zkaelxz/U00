"""
scanlate_detect.py -- find speech bubbles, panels and text regions on a comic page image.

Pure page-geometry detection: reads an image, returns boxes/masks, writes nothing
to the database. Inpainting, rendering, OCR and the page pipeline live in scanlate.py.
"""

import os

import numpy as np


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


_BUBBLE_ML_REPO = "ogkalu/comic-text-and-bubble-detector"


def detect_bubbles_ml(image_path: str, confidence: float = 0.25, hf_token: str = None):
    """
    Real trained bubble/text detector, as an upgrade over
    detect_bubbles_cv()'s free heuristic. Uses
    ogkalu/comic-text-and-bubble-detector (Hugging Face, Apache-2.0, 3
    classes: bubble / text-in-bubble / text-outside-bubble, boxes only
    -- the same class of model koharu and manga-image-translator use
    under the hood).

    Requires: `pip install transformers huggingface_hub`
    Downloads the model checkpoint from Hugging Face on first use
    (needs internet once; cached locally after).

    Loads through `transformers`'s own RT-DETR-v2 support: the model is
    RT-DETR-v2, not a YOLO architecture, so `ultralytics.YOLO` can never
    load it. This also means huggingface_hub's own
    HF_TOKEN-from-environment handling covers auth for free.

    NOTE: written against the documented transformers/huggingface_hub
    APIs, not verified end-to-end against the real model. Sanity-check
    on one page before relying on it for a whole batch --
    if the checkpoint ID below has moved or been renamed, swap in
    whatever comic/manga text-detection checkpoint you find current on
    Hugging Face; the rest of this function (box extraction, confidence
    filtering, reading-order sort) stays the same.
    """
    import os as _os
    from PIL import Image as _PILImage
    import torch
    from transformers import AutoImageProcessor, AutoModelForObjectDetection

    # Same gap that hit Whisper's downloads: without a token, every request
    # is anonymous and rate-limited (the "unauthenticated requests" warning).
    # from_pretrained() reads HF_TOKEN from the environment itself if no
    # explicit token is passed, so setting it here covers both paths.
    _tok = hf_token or _os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")
    if _tok:
        _os.environ.setdefault("HF_TOKEN", _tok)

    global _bubble_ml_model, _bubble_ml_processor
    if "_bubble_ml_model" not in globals():
        globals()["_bubble_ml_processor"] = AutoImageProcessor.from_pretrained(
            _BUBBLE_ML_REPO, token=_tok)
        model = AutoModelForObjectDetection.from_pretrained(_BUBBLE_ML_REPO, token=_tok)
        model.eval()
        globals()["_bubble_ml_model"] = model

    model = globals()["_bubble_ml_model"]
    processor = globals()["_bubble_ml_processor"]

    image = _PILImage.open(image_path).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        outputs = model(**inputs)
    # target_sizes takes (height, width); PIL's .size is (width, height).
    results = processor.post_process_object_detection(
        outputs, threshold=confidence, target_sizes=torch.tensor([image.size[::-1]])
    )[0]

    boxes = []
    for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
        x1, y1, x2, y2 = [float(v) for v in box.tolist()]
        boxes.append({
            "x": int(x1), "y": int(y1),
            "w": int(x2 - x1), "h": int(y2 - y1),
            "confidence": float(score),
            "label": model.config.id2label.get(int(label), str(int(label))),
        })

    boxes.sort(key=lambda b: (b["y"] // 50, -b["x"]))
    return boxes


def bubble_ml_weights_cached() -> bool:
    """True if the ML bubble detector's weights are already in the local
    Hugging Face cache. Lets detect_bubbles(backend="auto") pick the better backend automatically once someone's
    installed it, without the auto mode itself ever triggering a
    surprise first-run download -- that only happens when "ml" is
    picked explicitly."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    for filename in ("model.safetensors", "pytorch_model.bin"):
        hit = try_to_load_from_cache(repo_id=_BUBBLE_ML_REPO, filename=filename)
        if isinstance(hit, str) and os.path.exists(hit):
            return True
    return False


def _box_iou(a: dict, b: dict) -> float:
    """Intersection-over-union of two {"x","y","w","h"} boxes, 0 when they
    don't overlap at all."""
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
    iw = max(0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


# Real id2label values from ogkalu/comic-text-and-bubble-detector's own
# config.json (checked directly against the checkpoint, not this model's
# docstring paraphrase).
# Lower number wins a merge: the box literally labeled "bubble" traces the
# whole balloon, which is the cleaner crop boundary to keep over a
# "text_bubble"/"text_free" box describing the same physical object.
_LABEL_MERGE_PRIORITY = {"bubble": 0, "text_bubble": 1, "text_free": 1}


def dedupe_overlapping_boxes(boxes: list, iou_threshold: float = 0.5) -> list:
    """
    Merges boxes that describe the SAME physical balloon detected under
    more than one class -- the shape detect_bubbles_ml()'s 3-class model
    produces for every balloon: a "bubble" box and a
    "text_bubble" box for one balloon are near-identical in position and
    size by construction (one model, two classes for the same object), so
    both survive as independent regions downstream unless merged here.

    Keyed on geometry (IoU), not the label string, so this keeps working
    if a future checkpoint's class names change or a different detector
    with the same one-object/multiple-classes shape is swapped in. Two
    boxes below the threshold are always kept separately, even if close
    together -- this must not merge genuinely distinct, nearby bubbles.

    When a cluster of overlapping boxes collapses to one, keeps whichever
    box has the real "bubble" label (see _LABEL_MERGE_PRIORITY) for the
    cleanest crop boundary, then whichever has the higher detector
    confidence, then whichever appeared first. Boxes without a "label"
    (the free CV heuristic never sets one) fall back to confidence/order
    only, so this is a safe no-op for that backend.
    """
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _box_iou(boxes[i], boxes[j]) >= iou_threshold:
                union(i, j)

    def rank(b):
        return (_LABEL_MERGE_PRIORITY.get(b.get("label"), 1), -(b.get("confidence") or 0.0))

    winner_of = {}
    for i in range(n):
        root = find(i)
        current = winner_of.get(root)
        if current is None or rank(boxes[i]) < rank(boxes[current]):
            winner_of[root] = i
    keep = set(winner_of.values())
    return [b for i, b in enumerate(boxes) if i in keep]


class BubbleModelUnavailable(RuntimeError):
    """The ML detector couldn't be loaded. Carries whether the free
    heuristic already ran as a fallback, so the UI can say what happened
    instead of showing a stack trace."""

    def __init__(self, message, fell_back_to_cv=True):
        super().__init__(message)
        self.fell_back_to_cv = fell_back_to_cv


def detect_bubbles(image_path: str, backend: str = "auto", **kwargs):
    """Dispatcher: backend='cv' (free heuristic), 'ml' (trained model,
    better accuracy, heavier install), or 'auto' (default)
    -- use the ML model if its weights are already cached locally,
    the free heuristic otherwise. 'auto' never triggers a fresh
    multi-hundred-MB download on its own; pick 'ml' explicitly for that.

    If the ML backend can't run -- not installed, or the model can't be
    downloaded -- this falls back to the heuristic and raises
    BubbleModelUnavailable with the boxes still attached, so a network
    problem degrades to a working-but-rougher result rather than failing
    the page entirely.

    The ML model's own boxes are deduped before being
    returned -- its 3-class output otherwise reports the same balloon
    twice, once as "bubble" and once as "text_bubble"/"text_free".
    """
    if backend == "auto":
        backend = "ml" if bubble_ml_weights_cached() else "cv"
    if backend != "ml":
        return detect_bubbles_cv(image_path)

    try:
        return dedupe_overlapping_boxes(detect_bubbles_ml(image_path, **kwargs))
    except ImportError as exc:
        raise BubbleModelUnavailable(
            "The ML detector needs extra packages:\n"
            "    pip install transformers huggingface_hub\n\n"
            "Falling back to the free heuristic for this page."
        ) from exc
    except Exception as exc:
        from whisper_models import is_network_error
        if is_network_error(exc):
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


def bubble_shape_mask(image_path: str, box: dict, margin: int = 4):
    """The bubble's real (often oval/irregular) interior inside `box`, as
    a boolean (h, w) array for render_text_in_box(mask=...) -- found the
    same way detect_bubbles_cv() finds bubbles (light threshold, close the
    gaps text strokes punch through, take the connected region under the
    box's centre, fill its holes), then eroded by `margin` px so text keeps
    off the outline. None when there's no usable light region there (the
    box sits on artwork, or the region covers under 30% of the box) --
    callers then keep the plain rectangle layout."""
    import cv2

    gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if gray is None or box["w"] <= 2 * margin or box["h"] <= 2 * margin:
        return None
    ph, pw = gray.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(pw, box["x"] + box["w"]), min(ph, box["y"] + box["h"])
    if x1 <= x0 or y1 <= y0:
        return None
    roi = gray[y0:y1, x0:x1]

    otsu_val, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, light = cv2.threshold(roi, max(otsu_val, 180), 255, cv2.THRESH_BINARY)
    closed = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=3)
    _, labels = cv2.connectedComponents(closed, connectivity=8)
    label = labels[labels.shape[0] // 2, labels.shape[1] // 2]
    if label == 0:
        return None
    region = (labels == label).astype(np.uint8)
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(region)
    cv2.drawContours(filled, contours, -1, 1, thickness=-1)
    if margin > 0:
        filled = cv2.erode(filled, np.ones((2 * margin + 1, 2 * margin + 1), np.uint8))

    mask = np.zeros((box["h"], box["w"]), dtype=bool)
    mask[y0 - box["y"]:y1 - box["y"], x0 - box["x"]:x1 - box["x"]] = filled.astype(bool)
    if mask.sum() < 0.3 * box["w"] * box["h"]:
        return None
    return mask
