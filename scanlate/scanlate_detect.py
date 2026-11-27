"""
scanlate_detect.py -- find speech bubbles and text regions on a comic page image.

Pure page-geometry detection: reads an image, returns boxes, writes nothing to
the database. Joining boxes lives in scanlate_box_merge, balloon kinds and masks
in scanlate_balloons, panels and webtoon slicing in scanlate_panels, and page
decoding in scanlate_page_decode. Inpainting, rendering, OCR and the page
pipeline live in scanlate.py.
"""

import os
import threading
import time
from typing import Literal, overload

import numpy as np

from lib import device_use
from scanlate import scanlate_tuning
from scanlate.scanlate_box_merge import _LOOSE_TEXT_LABEL, _form_cells, dedupe_overlapping_boxes
from scanlate.scanlate_page_decode import page_bgr, page_gray


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
        if (hull_len and cv2.arcLength(outline, True) / hull_len > tune.CV_MAX_RAGGEDNESS
                and not _burst_rim(filled, outline)):
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


def _burst_rim(filled, outline) -> bool:
    """True for a burst (starburst) balloon: its rim is a ring of hairline notches, so the outline
    is ragged, yet there are many of them all round and with them filled in the shape is an oval.
    A white panel that line art cuts has a few notches, or a shape that is not oval."""
    import cv2
    tune = scanlate_tuning
    hull = cv2.convexHull(outline, returnPoints=False)
    if len(outline) < 5 or hull is None or len(hull) < 4:
        return False
    defects = cv2.convexityDefects(outline, hull)
    if defects is None:
        return False
    deep = [d for d in defects.reshape(-1, 4) if d[3] / 256.0 >= tune.CV_BURST_NOTCH_PX]
    if len(deep) < tune.CV_BURST_MIN_NOTCHES:
        return False
    closed = np.zeros_like(filled)
    cv2.drawContours(closed, [cv2.convexHull(outline)], -1, 1, thickness=-1)
    return _oval(closed, [cv2.convexHull(outline)])


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
    outputs = _detector_outputs(model, inputs, torch)
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

    boxes.sort(key=_reading_order)
    return boxes


# The detector's copy on the GPU under "cuda", made on the first page when the GPU has room. The
# CPU copy stays the master, as LaMa's does (scanlate_lama): a full GPU costs one page its speed,
# a broken one goes back to the CPU for a few minutes. Measured on the V100 PC (2026-11-23), the CPU
# run was about 0.8 s of a page's 0.9 s of reading.
_bubble_ml_gpu: dict = {}
_bubble_ml_gpu_broken: list = []
# A fault sends pages to the CPU for this long only, like the text reader's GPU pause.
_GPU_OFF_SECONDS = 300
_bubble_ml_gpu_off_until = 0.0
# Quick read and Scanlate can detect at once; one thread's fault must not clear the copy the other is using.
_bubble_ml_gpu_lock = threading.Lock()
# The weights, CUDA's own share and one page's working memory, generous like LaMa's first load.
_GPU_DETECTOR_MB = 800


def _detector_on_gpu(model, torch):
    """The GPU copy when Settings allows GPU use and it fits (or is already there), else None."""
    cuda = getattr(torch, "cuda", None)
    if time.monotonic() < _bubble_ml_gpu_off_until or cuda is None or not cuda.is_available():
        return None
    import stored_settings
    if not stored_settings.read("use_gpu"):
        return None
    if "cuda" not in _bubble_ml_gpu:
        import vram
        try:
            # Also honours Settings > Keep free memory for the GPU.
            vram.check_fits("Balloon detector", required_mb=_GPU_DETECTOR_MB)
        except Exception:
            return None
        import copy
        _bubble_ml_gpu["cuda"] = copy.deepcopy(model).to("cuda")
    return _bubble_ml_gpu["cuda"]


def _detector_outputs(model, inputs, torch):
    """The model's outputs for `inputs`, run on the GPU when it can be, with the tensors the
    post-processing reads on the CPU either way."""
    import vram
    with torch.no_grad(), vram.comic_gpu_use():
        with _bubble_ml_gpu_lock:
            try:
                gpu = _detector_on_gpu(model, torch)
                if gpu is not None:
                    outputs = gpu(**{key: value.to("cuda") for key, value in inputs.items()})
                    for key, value in outputs.items():
                        if hasattr(value, "cpu"):
                            outputs[key] = value.cpu()
                    device_use.note("balloon detector", "GPU")
                    return outputs
            except Exception as exc:
                # A GPU that filled up since (Ollama, a transcription) costs only this page; any
                # other fault would repeat on every page, so the CPU is kept for a while.
                if not isinstance(exc, getattr(torch.cuda, "OutOfMemoryError", ())):
                    global _bubble_ml_gpu_off_until
                    _bubble_ml_gpu_broken.append(type(exc).__name__)
                    _bubble_ml_gpu_off_until = time.monotonic() + _GPU_OFF_SECONDS
                    _bubble_ml_gpu.clear()
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
        device_use.note("balloon detector", "CPU")
        return model(**inputs)


def _reading_order(box: dict) -> tuple:
    return box["y"] // 50, -box["x"]


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


def release_bubble_model() -> None:
    """Drops the cached ML bubble detector and its GPU copy; it reloads on the next ML page."""
    _bubble_ml_gpu.clear()
    globals().pop("_bubble_ml_model", None)
    globals().pop("_bubble_ml_processor", None)


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
        threshold = kwargs.pop("confidence", None) or scanlate_tuning.ML_CONFIDENCE
        found = detect_bubbles_ml(image_path, image=image,
                                  confidence=min(threshold, scanlate_tuning.ML_FORM_CELL_CONFIDENCE),
                                  **kwargs)
        kept = [b for b in found if b.get("confidence", 1.0) >= threshold]
        weak = [b for b in found if b.get("confidence", 1.0) < threshold]
        gray = (page_gray(image_path, image)
                if weak and any(b.get("label") == _LOOSE_TEXT_LABEL for b in kept) else None)
        cells = _form_cells(weak, kept, gray) if gray is not None else []
        if cells:
            kept = sorted(kept + cells, key=_reading_order)
        return dedupe_overlapping_boxes(kept)
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
