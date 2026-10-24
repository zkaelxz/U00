"""
scanlate_tuning.py -- the tunable numbers of the Scanlate pipeline, in one place.

The values were set against the synthetic-page bench (`python -m
tools.scanlate_bench`, docs/technical-notes.md); its `--set NAME=VALUE` tries
another value for one run without editing this file. Pipeline code reads them
as `scanlate_tuning.NAME` at call time, never as a default argument, so an
override reaches the next page.
"""

# --- Bubble detection, OpenCV heuristic (scanlate_detect.detect_bubbles_cv) ---
# A bubble's inside is at least this light, whatever Otsu picks on a dark page.
CV_LIGHT_FLOOR = 180
# Dark lines are thickened by this many pixels before the light regions are
# labelled, so a scan's broken, anti-aliased outline still closes a bubble off.
CV_SEAL_PX = 1
CV_MIN_AREA_FRAC = 0.0015
CV_MAX_AREA_FRAC = 0.2
CV_MAX_ASPECT = 12.0
CV_MIN_FILL = 0.55              # region area / its bounding box: bubbles are convex-ish
# Outline length over its convex hull's: line art crossing a white panel cuts
# deep notches into it, while a balloon's outline (tail included) stays smooth.
CV_MAX_RAGGEDNESS = 1.3
CV_MIN_W = 25
CV_MIN_H = 18
# Share of the inside that is lettering: an empty white shape is artwork.
CV_MIN_INK = 0.015
# Lettering keeps clear of a bubble's outline; screentone, hatching and line
# art reach it. The rim is the outer CV_RIM_FRAC of the way to the centre.
CV_RIM_FRAC = 0.25
CV_MAX_RIM_INK = 0.02
# The box around everything dark inside, over the region's area: lettering is
# a block in the middle, line art on a white panel is spread across it.
CV_MAX_INK_SPREAD = 0.75
# A region with no lettering is kept only as an empty balloon: oval (this IoU
# with the ellipse fitted to it) and this much lighter than the ring just
# outside its outline.
CV_EMPTY_OVAL_IOU = 0.9
CV_EMPTY_CONTRAST = 40

# --- Bubble detection, ML model (scanlate_detect.detect_bubbles_ml) ---
ML_CONFIDENCE = 0.25
# Two boxes are one balloon when they overlap this much (IoU), or when the
# smaller lies this far inside the larger: the model reports a balloon and the
# text inside it as separate classes.
ML_MERGE_IOU = 0.5
ML_MERGE_CONTAINED = 0.8

# --- Region kinds (scanlate_detect.classify_text_regions) ---
# A box at least this share light (gray above the level) is a balloon or a
# caption box rather than lettering on the art.
CLASSIFY_LIGHT_LEVEL = 200
CLASSIFY_MIN_LIGHT_SHARE = 0.55

# --- Text direction (scanlate_regions.estimate_text_orientation) ---
# Lettering this many times wider than tall is horizontal, as tall as wide
# vertical; anything squarer goes by its widest gap.
ORIENTATION_ASPECT = 1.8

# --- Panels (scanlate_detect.detect_panels) ---
# Gutters are anything within this many gray levels of the page's margin.
PANEL_PAPER_MARGIN = 15

# --- Reading order (scanlate_regions.analyze_page_regions) ---
# Japanese manga reads right to left; Chinese manhua and Korean manhwa left to right.
RIGHT_TO_LEFT_LANGUAGES = ("ja",)
# Two regions share a row when their vertical overlap is at least this share
# of the shorter one; otherwise the higher one is read first.
READING_ROW_OVERLAP = 0.3

# --- OCR crop (scanlate.ocr_box_region) ---
OCR_INSET_FRAC = 0.10
OCR_INSET_MIN = 4
OCR_INSET_MAX = 15
# For these backends, a crop whose paper varies by more than this (standard
# deviation, gray levels) is a noisy scan and is denoised before OCR; a clean
# crop is not. Tesseract and manga-ocr read the noisy crops better as they are.
OCR_DENOISE_BACKENDS = ("paddle",)
OCR_DENOISE_MIN_NOISE = 3.0

# --- Typesetting (scanlate_render) ---
# The English starts at the source lettering's size (column width or line
# height) times this, kept within START_MIN..START_MAX, and shrinks until it
# fits, down to SMALLEST; DEFAULT when the lettering can't be measured.
TYPESET_FONT_PER_SOURCE_PX = 1.0
TYPESET_DEFAULT_FONT_PX = 18
TYPESET_START_MIN_PX = 12
TYPESET_START_MAX_PX = 40
TYPESET_SMALLEST_PX = 8
# Space between lines, and between the text and a balloon's outline.
TYPESET_LINE_GAP_PX = 4
TYPESET_OUTLINE_MARGIN_PX = 2


def reads_right_to_left(language: str) -> bool:
    return language in RIGHT_TO_LEFT_LANGUAGES


def apply_overrides(values: dict) -> None:
    """Sets each NAME to VALUE (strings, as typed on a command line), converted
    to the type the value already has. ValueError for an unknown name or a
    value that does not convert."""
    module = globals()
    for name, raw in values.items():
        if not name.isupper() or name not in module:
            raise ValueError(f"Unknown scanlate_tuning value: {name}")
        current = module[name]
        try:
            if isinstance(current, bool):
                value = str(raw).lower() in ("1", "true", "yes", "on")
            elif isinstance(current, tuple):
                value = tuple(v.strip() for v in str(raw).split(",") if v.strip())
            else:
                value = type(current)(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}: {raw!r} is not a {type(current).__name__}") from exc
        module[name] = value
