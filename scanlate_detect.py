"""
scanlate_detect.py -- find speech bubbles, panels and text regions on a comic page image.

Pure page-geometry detection: reads an image, returns boxes/masks, writes nothing
to the database. Inpainting, rendering, OCR and the page pipeline live in scanlate.py.
"""

import os
import time
from typing import Literal, overload

import numpy as np

import scanlate_tuning


def decode_page(image_path: str):
    """One page as an OpenCV BGR array, for a whole detect or render pass to
    share. Read through Pillow: cv2.imread can't open non-ASCII paths on
    Windows, and Pillow's pixel grid (no EXIF rotation) is the one the page's
    stored size and every crop already use. ValueError if unreadable."""
    from PIL import Image
    try:
        with Image.open(image_path) as opened:
            if "A" in opened.getbands() or "transparency" in opened.info:
                # Plain RGB conversion keeps whatever colour sits under a
                # transparent pixel, usually black.
                rgba = opened.convert("RGBA")
                flat = Image.new("RGB", rgba.size, (255, 255, 255))
                flat.paste(rgba, mask=rgba.getchannel("A"))
                rgb = np.asarray(flat)
            else:
                rgb = np.asarray(opened.convert("RGB"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read image: {image_path}") from exc
    return np.ascontiguousarray(rgb[:, :, ::-1])


def page_bgr(image_path: str, image=None):
    """The page as an OpenCV BGR array: `image` when the caller already
    decoded it, so one page pass decodes the file once, else a fresh read
    (None if unreadable)."""
    if image is not None:
        return image
    import cv2
    return cv2.imread(image_path)


def page_gray(image_path: str, image=None):
    """page_bgr() in grayscale; `image` may already be gray or BGR."""
    import cv2
    if image is None:
        return cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


@overload
def detect_bubbles_cv(image_path: str, min_area_frac: float | None = None,
                      max_area_frac: float | None = None, debug: Literal[False] = False,
                      image=None) -> list: ...
@overload
def detect_bubbles_cv(image_path: str, min_area_frac: float | None = None,
                      max_area_frac: float | None = None, *, debug: Literal[True],
                      image=None) -> tuple[list, list]: ...
def detect_bubbles_cv(image_path: str, min_area_frac: float | None = None,
                       max_area_frac: float | None = None, debug: bool = False, image=None):
    """
    Heuristic speech-bubble detector for a typical comic page.

    A speech bubble is an ENCLOSED light region: its own outline cuts it off
    from the panel and the page, which reach the page edge. Each light region
    is labelled as it is, with its outline intact (closing the gaps text
    strokes punch through used to erase thin outlines too, so a bubble merged
    with the white page around it and was thrown out). Text strokes then show
    up as holes; filling them gives the bubble's real shape.

    What survives the size, aspect and fill checks must also look like a
    lettered balloon: some ink inside, and none near its outline (screentone,
    hatching and line art reach the edge of a region; lettering keeps clear of
    it). A region with no lettering is kept only when it stands out lighter than
    what surrounds it, as an empty balloon on a toned panel does; an empty white
    shape on a white panel is artwork. The thresholds live in scanlate_tuning.

    Returns boxes sorted top-to-bottom, then right-to-left; the page's reading
    order is decided later (scanlate_regions). Pass debug=True to get the
    rejection reasons back. `image`: the page already decoded as BGR (see
    page_bgr()).
    """
    import cv2
    tune = scanlate_tuning

    min_area_frac = tune.CV_MIN_AREA_FRAC if min_area_frac is None else min_area_frac
    max_area_frac = tune.CV_MAX_AREA_FRAC if max_area_frac is None else max_area_frac
    img = page_bgr(image_path, image)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    total_area = float(h * w)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Otsu adapts to scans that aren't pure white, with a fixed floor so a
    # dark page can't drag the threshold down into the artwork.
    otsu_val, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, light = cv2.threshold(gray, max(otsu_val, tune.CV_LIGHT_FLOOR), 255, cv2.THRESH_BINARY)
    seal = int(tune.CV_SEAL_PX)
    if seal > 0:
        light = cv2.erode(light, np.ones((2 * seal + 1, 2 * seal + 1), np.uint8))
    # 4-connectivity: a diagonal step must not leak a bubble through its outline.
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(light, connectivity=4)

    boxes, rejected = [], []
    for i in range(1, n_labels):          # 0 is the dark ink
        x, y, bw, bh = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                        int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        if bw * bh < total_area * min_area_frac:
            continue                      # specks and letter counters: not worth a reason each
        if x <= 1 + seal or y <= 1 + seal or (x + bw) >= (w - 1 - seal) or (y + bh) >= (h - 1 - seal):
            rejected.append((x, y, bw, bh, "touches page edge"))
            continue
        aspect = bw / max(bh, 1)
        if aspect > tune.CV_MAX_ASPECT or aspect < 1.0 / tune.CV_MAX_ASPECT:
            rejected.append((x, y, bw, bh, "implausible aspect ratio"))
            continue
        if bw < tune.CV_MIN_W or bh < tune.CV_MIN_H:
            rejected.append((x, y, bw, bh, "smaller than readable text"))
            continue
        region = (labels[y:y + bh, x:x + bw] == i).astype(np.uint8)
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        filled = np.zeros_like(region)
        cv2.drawContours(filled, contours, -1, 1, thickness=-1)
        area = int(filled.sum())
        if area < total_area * min_area_frac:
            rejected.append((x, y, bw, bh, "too small"))
            continue
        if area > total_area * max_area_frac:
            rejected.append((x, y, bw, bh, "too large"))
            continue
        # A bubble fills most of its bounding box; ragged highlights don't.
        if area / float(bw * bh) < tune.CV_MIN_FILL:
            rejected.append((x, y, bw, bh, "too sparse to be a bubble"))
            continue
        outline = max(contours, key=cv2.contourArea)
        hull_len = cv2.arcLength(cv2.convexHull(outline), True)
        if hull_len and cv2.arcLength(outline, True) / hull_len > tune.CV_MAX_RAGGEDNESS:
            rejected.append((x, y, bw, bh, "ragged outline"))
            continue
        problem = _not_lettered(gray, region, filled, contours, x, y)
        if problem:
            rejected.append((x, y, bw, bh, problem))
            continue
        x0, y0 = max(0, x - seal), max(0, y - seal)
        boxes.append({"x": x0, "y": y0, "w": min(w, x + bw + seal) - x0,
                      "h": min(h, y + bh + seal) - y0})

    boxes.sort(key=lambda b: (b["y"] // max(int(h * 0.08), 1), -b["x"]))
    if debug:
        return boxes, rejected
    return boxes


def _not_lettered(gray, region, filled, contours, x, y):
    """Why a bubble-shaped light region doesn't hold a balloon's lettering,
    or None when it does. Its marks are the holes in the region: lettering
    keeps clear of the outline and sits in a block, where tone, hatching and
    line art reach the outline or spread across the whole region."""
    import cv2
    tune = scanlate_tuning
    area = int(filled.sum())
    holes = (filled > 0) & (region == 0)
    dist = cv2.distanceTransform(filled, cv2.DIST_L2, 3)
    rim = (filled > 0) & (dist < tune.CV_RIM_FRAC * float(dist.max()))
    if rim.any() and holes[rim].mean() > tune.CV_MAX_RIM_INK:
        return "artwork reaches its outline"
    ys, xs = np.nonzero(holes)
    if len(xs) and ((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
                    > tune.CV_MAX_INK_SPREAD * area):
        return "marks spread across it"
    if holes.sum() / float(area) < tune.CV_MIN_INK and not (
            _oval(filled, contours) and _stands_out(gray, filled, x, y)):
        return "no lettering inside"
    return None


def _oval(filled, contours) -> bool:
    """True when the region is close to the ellipse fitted to its outline:
    an empty balloon is oval, a patch of white sky above a dark fill is not."""
    import cv2
    biggest = max(contours, key=cv2.contourArea)
    if len(biggest) < 5:
        return False
    ellipse = np.zeros_like(filled)
    cv2.ellipse(ellipse, cv2.fitEllipse(biggest), 1, thickness=-1)
    inter = float(np.logical_and(ellipse, filled).sum())
    union = float(np.logical_or(ellipse, filled).sum())
    return union > 0 and inter / union >= scanlate_tuning.CV_EMPTY_OVAL_IOU


def _stands_out(gray, filled, x: int, y: int) -> bool:
    """True when the region `filled` (a mask at (x, y)) is clearly lighter
    than a ring a few pixels outside it, past its outline."""
    import cv2
    pad = 14
    ph, pw = gray.shape[:2]
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(pw, x + filled.shape[1] + pad), min(ph, y + filled.shape[0] + pad)
    local = np.zeros((y1 - y0, x1 - x0), np.uint8)
    local[y - y0:y - y0 + filled.shape[0], x - x0:x - x0 + filled.shape[1]] = filled
    near = cv2.dilate(local, np.ones((9, 9), np.uint8))
    far = cv2.dilate(local, np.ones((2 * pad + 1, 2 * pad + 1), np.uint8))
    ring = (far > 0) & (near == 0)
    if not ring.any():
        return False
    roi = gray[y0:y1, x0:x1]
    return float(roi[local > 0].mean()) - float(roi[ring].mean()) >= scanlate_tuning.CV_EMPTY_CONTRAST


def inset_box_for_ocr(box: dict, frac: float | None = None, min_inset: int | None = None,
                      max_inset: int | None = None) -> dict:
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
    frac = scanlate_tuning.OCR_INSET_FRAC if frac is None else frac
    min_inset = scanlate_tuning.OCR_INSET_MIN if min_inset is None else min_inset
    max_inset = scanlate_tuning.OCR_INSET_MAX if max_inset is None else max_inset
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


def detect_bubbles_ml(image_path: str, confidence: float | None = None, hf_token: str | None = None,
                      image=None):
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
    load it.

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
    _tok = hf_token or _os.environ.get("HF_TOKEN") or _os.environ.get("HUGGINGFACE_TOKEN")

    global _bubble_ml_model, _bubble_ml_processor
    if "_bubble_ml_model" not in globals():
        try:
            import qwen3_native
            offline = qwen3_native._offline_kwargs()
            import model_pins
            revision = model_pins.revision(_BUBBLE_ML_REPO)
            with model_pins.fetching(_BUBBLE_ML_REPO):
                globals()["_bubble_ml_processor"] = AutoImageProcessor.from_pretrained(
                    _BUBBLE_ML_REPO, revision=revision, token=_tok, **offline)
                model = AutoModelForObjectDetection.from_pretrained(
                    _BUBBLE_ML_REPO, revision=revision, token=_tok, **offline)
            model.eval()
            globals()["_bubble_ml_model"] = model
        except Exception as exc:
            # detect_bubbles remembers only failures to load the model, never a
            # per-page inference error.
            setattr(exc, "ml_load_failure", True)
            raise

    model = globals()["_bubble_ml_model"]
    processor = globals()["_bubble_ml_processor"]
    if confidence is None:
        confidence = scanlate_tuning.ML_CONFIDENCE

    image = (_PILImage.fromarray(np.ascontiguousarray(image[:, :, ::-1])) if image is not None
             else _PILImage.open(image_path).convert("RGB"))
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
    import model_pins
    for filename in ("model.safetensors", "pytorch_model.bin"):
        hit = try_to_load_from_cache(repo_id=_BUBBLE_ML_REPO, filename=filename,
                                     revision=model_pins.revision(_BUBBLE_ML_REPO))
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


def _contained_share(a: dict, b: dict) -> float:
    """How much of the smaller of two boxes lies inside the other, 0..1."""
    iw = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    ih = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    smaller = min(a["w"] * a["h"], b["w"] * b["h"])
    if iw <= 0 or ih <= 0 or smaller <= 0:
        return 0.0
    return iw * ih / smaller


# Real id2label values from ogkalu/comic-text-and-bubble-detector's own
# config.json (checked directly against the checkpoint, not this model's
# docstring paraphrase).
# Lower number wins a merge: the box literally labeled "bubble" traces the
# whole balloon, which is the cleaner crop boundary to keep over a
# "text_bubble"/"text_free" box describing the same physical object.
_LABEL_MERGE_PRIORITY = {"bubble": 0, "text_bubble": 1, "text_free": 1}


def dedupe_overlapping_boxes(boxes: list, iou_threshold: float | None = None) -> list:
    """
    Merges boxes that describe the SAME physical balloon detected under
    more than one class -- the shape detect_bubbles_ml()'s 3-class model
    produces for every balloon: a "bubble" box and a
    "text_bubble" box for one balloon are near-identical in position and
    size by construction (one model, two classes for the same object), so
    both survive as independent regions downstream unless merged here.

    A box lying almost wholly inside another (scanlate_tuning.ML_MERGE_CONTAINED
    of its own area) is the same balloon too: the model's "text_bubble" box
    hugs the lettering, well inside the "bubble" box, so their IoU is often
    under the threshold and the balloon came out as two regions, OCR'd,
    translated and lettered twice. Only boxes of different classes merge
    this way: a small balloon tucked inside a big one's box is its own balloon.

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

    if iou_threshold is None:
        iou_threshold = scanlate_tuning.ML_MERGE_IOU
    for i in range(n):
        for j in range(i + 1, n):
            if (_box_iou(boxes[i], boxes[j]) >= iou_threshold
                    or (boxes[i].get("label") != boxes[j].get("label")
                        and _contained_share(boxes[i], boxes[j]) >= scanlate_tuning.ML_MERGE_CONTAINED)):
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


_LOOSE_TEXT_LABEL = "text_free"


def _fragments_of_one_region(a: dict, b: dict) -> bool:
    if a.get("label") != b.get("label"):
        return False
    if _contained_share(a, b) >= scanlate_tuning.FRAGMENT_OVERLAP:
        return True
    # Lettering outside balloons has no outline between its pieces; balloons
    # side by side are separate however close.
    if a.get("label") != _LOOSE_TEXT_LABEL:
        return False
    gap_limit = scanlate_tuning.FRAGMENT_GAP
    share_limit = scanlate_tuning.FRAGMENT_ALIGNED
    # Stacked: they share columns and sit one above the other.
    shared_x = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    gap_y = max(a["y"], b["y"]) - min(a["y"] + a["h"], b["y"] + b["h"])
    narrow = min(a["w"], b["w"])
    if narrow > 0 and shared_x >= share_limit * narrow and gap_y <= gap_limit * narrow:
        return True
    # Side by side: they share rows and sit one beside the other.
    shared_y = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    gap_x = max(a["x"], b["x"]) - min(a["x"] + a["w"], b["x"] + b["w"])
    short = min(a["h"], b["h"])
    return short > 0 and shared_y >= share_limit * short and gap_x <= gap_limit * short


def merge_fragment_boxes(boxes: list) -> list:
    """One box per balloon or title: boxes of the same class that overlap
    heavily (stacked duplicates on one balloon) or sit in a tight line (a
    vertical title the detector cut into one box per character) become the
    box around them all. dedupe_overlapping_boxes() only joins different
    classes, so same-class fragments came through as separate regions, each
    OCR'd, translated and lettered over the others. The merged box keeps the
    best-ranked fragment's other fields."""
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _fragments_of_one_region(boxes[i], boxes[j]):
                parent[find(i)] = find(j)

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)
    merged = []
    for members in clusters.values():
        if len(members) == 1:
            merged.append((members[0], boxes[members[0]]))
            continue
        group = [boxes[i] for i in members]
        best = min(group, key=lambda b: (_LABEL_MERGE_PRIORITY.get(b.get("label"), 1),
                                         -(b.get("confidence") or 0.0)))
        x1, y1 = min(b["x"] for b in group), min(b["y"] for b in group)
        x2, y2 = max(b["x"] + b["w"] for b in group), max(b["y"] + b["h"] for b in group)
        merged.append((members[0], dict(best, x=x1, y=y1, w=x2 - x1, h=y2 - y1)))
    return [b for _, b in sorted(merged, key=lambda t: t[0])]


class BubbleModelUnavailable(RuntimeError):
    """The ML detector couldn't be loaded. Carries whether the free
    heuristic already ran as a fallback, so the UI can say what happened
    instead of showing a stack trace."""

    def __init__(self, message, fell_back_to_cv=True):
        super().__init__(message)
        self.fell_back_to_cv = fell_back_to_cv


# A model that failed to load fails again the same way on the next page, after
# another network timeout. A run forgets this on start (forget_ml_failure), and
# it lapses by itself so a fixed connection is not blocked for long. Only the
# fixed message text is kept: an exception object would keep the decoded page
# alive through its traceback.
_ML_FAILURE_MEMORY_SECONDS = 300
_ml_failure = None            # (monotonic time, message)

# Ends every BubbleModelUnavailable message, so a caller can tell this fallback
# apart from the page's other warnings (no bubbles found, captions unread).
ML_FALLBACK_SENTENCE = "Falling back to the free heuristic for this page."


def forget_ml_failure() -> None:
    global _ml_failure
    _ml_failure = None


def detect_bubbles(image_path: str, backend: str = "auto", image=None, **kwargs):
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

    `image`: the page already decoded as BGR (see page_bgr()).
    """
    global _ml_failure
    if backend == "auto":
        backend = "ml" if bubble_ml_weights_cached() else "cv"
    if backend != "ml":
        return detect_bubbles_cv(image_path, image=image)

    remembered = _ml_failure          # one read: forget_ml_failure() may clear it meanwhile
    if remembered and time.monotonic() - remembered[0] < _ML_FAILURE_MEMORY_SECONDS:
        raise BubbleModelUnavailable(remembered[1])
    try:
        return dedupe_overlapping_boxes(detect_bubbles_ml(image_path, image=image, **kwargs))
    except ImportError as exc:
        message = ("The ML detector needs extra packages:\n"
                   "    pip install transformers huggingface_hub\n\n" + ML_FALLBACK_SENTENCE)
        cause, remember = exc, True
    except Exception as exc:
        from whisper_models import is_network_error
        cause = exc
        if is_network_error(exc):
            message = ("Couldn't download the bubble-detection model -- this is a network "
                       "problem, not a problem with your image.\n\n"
                       "If huggingface.co is blocked by a DNS blocker on your network "
                       "(Pi-hole, AdGuard), whitelist:\n"
                       "  huggingface.co\n  cdn-lfs.huggingface.co\n  cdn-lfs-us-1.hf.co\n  hf.co\n\n"
                       + ML_FALLBACK_SENTENCE)
            remember = getattr(exc, "ml_load_failure", False)
        else:
            message = (f"The ML detector failed: {type(exc).__name__}: {exc}\n\n"
                       + ML_FALLBACK_SENTENCE)
            remember = False
    if remember:
        _ml_failure = (time.monotonic(), message)
    raise BubbleModelUnavailable(message) from cause


# ---------------------------------------------------------------------------
# Panel detection & webtoon long-strip handling
# ---------------------------------------------------------------------------

def detect_panels(image_path: str, min_panel_frac: float = 0.02, image=None):
    """
    Finds comic panels by looking for the gutters (whitespace channels)
    between them. Returns boxes in reading order.

    Useful for two things: translating only the panels you're actually
    looking at, and giving the translator panel-level context instead of
    a flat list of bubbles with no sense of which are in the same beat.
    `image`: the page already decoded as BGR (see page_bgr()).
    """
    import cv2
    import numpy as np

    img = page_bgr(image_path, image)
    if img is None:
        raise ValueError(f"Could not read image: {image_path}")
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Panels are bounded by dark borders on a light page (or vice versa);
    # inverting and closing turns panel interiors into solid blobs. Gutters
    # are the paper's own shade, which on a scan or a yellowed page sits well
    # under pure white: a fixed cut at 240 made the whole page one panel.
    edge = max(2, min(h, w) // 50)
    paper = float(np.median(np.concatenate([gray[:edge].ravel(), gray[-edge:].ravel(),
                                            gray[:, :edge].ravel(), gray[:, -edge:].ravel()])))
    cut = min(240.0, paper - scanlate_tuning.PANEL_PAPER_MARGIN)
    # A median blur first: scan noise in a gutter would otherwise close it up.
    _, thresh = cv2.threshold(cv2.medianBlur(gray, 5), cut, 255, cv2.THRESH_BINARY_INV)
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


_MAX_WEBTOON_SLICES = 200
_MIN_TAIL_MARGIN_PX = 60


class StripTooLong(ValueError):
    """A strip that would need more than _MAX_WEBTOON_SLICES slices."""


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
        if h - cut <= overlap + min(_MIN_TAIL_MARGIN_PX, target_height // 4):
            # A tail this short would be a page of just the overlap band.
            slices.append({"y_start": y, "y_end": h, "index": idx})
            break
        slices.append({"y_start": y, "y_end": cut, "index": idx})
        y = max(cut - overlap, cut) if overlap == 0 else max(cut - overlap, y + 1)
        idx += 1
        if idx >= _MAX_WEBTOON_SLICES:
            # Stopping here would silently drop the rest of the strip.
            raise StripTooLong(f"This strip needs more than {_MAX_WEBTOON_SLICES} slices; "
                             "split it into shorter images before importing it.")
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


def _balloon_interior(roi):
    """(filled interior, light-outside mask) of the balloon under the centre
    of `roi` (gray), or None. The interior is the light region, labelled with
    its outline intact, whose outline (holes filled) encloses the centre;
    the centre usually falls on a letter, so the region itself rarely covers
    it. When the box is wider than the balloon, the paper around it is a
    ring that also "encloses" the centre, so a region touching the box edge
    loses to a nested one that doesn't, if that one is big enough to be the
    balloon. The outside mask is the light paper that touches the box
    edge and isn't the interior."""
    import cv2

    otsu_val, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, light = cv2.threshold(roi, max(otsu_val, scanlate_tuning.CV_LIGHT_FLOOR), 255,
                             cv2.THRESH_BINARY)
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


def bubble_erase_mask(image_path: str, box: dict, image=None):
    """Where cleanup may erase inside `box`: the balloon's interior plus any
    lettering joined to its outline (a stroke touching the outline bends the
    light region's edge around it, so the interior alone would keep that
    stroke). Those strokes are the marks inside the interior's convex hull
    that sit deeper than the outline is thick, measured from the paper
    outside; art beyond the outline is nearer that paper than this, or
    outside the hull. None as bubble_shape_mask() returns it."""
    import cv2

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
    filled, outside = found
    if filled.sum() < 0.3 * box["w"] * box["h"]:
        return None
    if outside.any():
        depth = cv2.distanceTransform((1 - outside).astype(np.uint8), cv2.DIST_L2, 3)
        edge = filled - cv2.erode(filled, np.ones((3, 3), np.uint8))
        thickness = float(np.median(depth[edge > 0])) if edge.any() else 0.0
        contours, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = np.zeros_like(filled)
        cv2.fillPoly(hull, [cv2.convexHull(np.vstack(contours))], 1)
        filled = filled | ((hull > 0) & (depth > thickness + 1)).astype(np.uint8)
    else:
        # No paper outside the balloon in the box: the box lies within it.
        filled = np.ones_like(filled)
    return _to_box_mask(box, area, filled)
