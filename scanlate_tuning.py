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
# A balloon shaded grey inside is found by retrying at this level when nothing
# is as light as CV_LIGHT_FLOOR; at least this share of the rim of the region it
# finds must be darker than CV_GREY_RIM_LEVEL (a drawn outline), or it is a patch
# of art fading out, which a blur leaves lighter than any outline.
CV_GREY_PAPER_FLOOR = 110
CV_GREY_RIM_LEVEL = 80
CV_GREY_RIM_DARK_SHARE = 0.7
CV_MIN_AREA_FRAC = 0.0015
CV_MAX_AREA_FRAC = 0.2
CV_MAX_ASPECT = 12.0
CV_MIN_FILL = 0.55              # region area / its bounding box: bubbles are convex-ish
# Outline length over its convex hull's: line art crossing a white panel cuts
# deep notches into it, while a balloon's outline (tail included) stays smooth.
CV_MAX_RAGGEDNESS = 1.3
# A burst balloon's rim is a ring of hairline spikes, which reads as ragged; it is a balloon when
# there are many notches all round and the outline with them filled in is an oval.
CV_BURST_MIN_NOTCHES = 12
CV_BURST_NOTCH_PX = 3
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
# A lettering box the model scores from this up to ML_CONFIDENCE is kept when it is a cell of a
# form beside a kept one (scanlate_detect._form_cells): the four labels of one form on S library
# p68 scored 0.15-0.31.
ML_FORM_CELL_CONFIDENCE = 0.1
# Two boxes are one balloon when they overlap this much (IoU), or when the
# smaller lies this far inside the larger: the model reports a balloon and the
# text inside it as separate classes.
ML_MERGE_IOU = 0.5
ML_MERGE_CONTAINED = 0.8

# --- Fragments of one balloon (scanlate_detect.merge_fragment_boxes) ---
# Two boxes of one class are one region when the smaller lies this far inside
# the other: the model draws stacked, partly overlapping boxes on one balloon.
FRAGMENT_OVERLAP = 0.4
# Lettering outside balloons in a line (a vertical title read as one box per character) is one
# region when they share this much of the narrower one's extent across the
# line and the gap between them is under FRAGMENT_GAP of that narrower
# extent. A real balloon's outline keeps two neighbouring balloons further apart.
FRAGMENT_ALIGNED = 0.6
FRAGMENT_GAP = 0.3
# A ruled line between two cells of a form (scanlate_detect._ruled_line_between) is at least this much
# darker than the paper, runs across at least RULE_SPAN of the area the two boxes cover, and is
# broken by no gap over RULE_MAX_GAP_PX. The rules on S library p68 are about 25-60 levels darker
# than their grey cells.
RULE_CONTRAST = 15
RULE_SPAN = 0.8
RULE_MAX_GAP_PX = 3

# Two boxes touching or this close (as a share of the narrower one's width, or
# height when stacked) are the columns of one balloon when this share of each
# lies inside the one balloon found around them.
BALLOON_COLUMN_GAP = 0.6
BALLOON_COLUMN_INSIDE = 0.7
# A balloon interior covering this share of its box means the box lies within
# the balloon (there is no outline in it to spare).
BALLOON_FILLS_BOX = 0.9
# Grey art beside a balloon counts as outside it when it survives an opening of
# this size: screentone's grey fringes round its dots do not, and lettering that
# touches screentone would otherwise be left half-erased.
ERASE_FLAT_TONE_PX = 7
# Ink or tone at least this many px across, reaching the box edge, is art the
# balloon is drawn over, not lettering joined to its outline: a burst outline
# is thinner than this.
ERASE_SOLID_ART_PX = 11

# --- Region kinds (scanlate_detect.classify_text_regions) ---
# A box at least this share light (gray above the level) is a balloon or a
# caption box rather than lettering on the art.
CLASSIFY_LIGHT_LEVEL = 200
CLASSIFY_MIN_LIGHT_SHARE = 0.55
# A box this much filled by an outlined balloon interior is a balloon whatever its shade.
CLASSIFY_BALLOON_FILL = 0.5
# Lettering on dark, busy art is first guessed to be a sound effect. Once it
# is read, a sound effect is a few big characters: more characters than this,
# or more lines, is a caption or sign on the art and is translated like one.
SFX_MAX_CHARS = 12
SFX_MAX_LINES = 2
# A guessed sign (kind confidence at or below the first value) whose lettering mask covers more
# than the second share of its box is art the detector boxed (a boot, a face), not lettering:
# real lettering leaves paper showing between the strokes. Measured on 615 real regions: the
# four mistaken boxes cover 85.5-95.5%.
ART_SIGN_MAX_CONFIDENCE = 0.5
ART_SIGN_MIN_MASK_SHARE = 0.85
# A box wider than this many times its height is a caption strip (real lettering over art, as
# #108's was); the boxes taken for a boot or an effect were tall.
ART_SIGN_MAX_ASPECT = 2.0

# --- Narration box trim (scanlate_regions.trim_to_lettering) ---
# A caption box the detector stretched over a watermark is cut back to the
# lettering when the lettering sits off-centre by this share of the box width.
TRIM_OFF_CENTRE = 0.1
# Ink is darker than the darkest shade plus this share of the way to the median.
TRIM_INK_LEVEL = 0.25
# Lettering columns closer than this share of the box height are one block.
TRIM_BRIDGE = 0.25

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
# A column or line of the source lettering thinner than this share of the
# thickest is punctuation, not a glyph row, and is left out of its size.
LETTERING_RUN_OF_LARGEST = 0.6
# The English starts at the source lettering's size (column width or line
# height) times this, kept within START_MIN..START_MAX, and shrinks until it
# fits, down to SMALLEST; DEFAULT when the lettering can't be measured.
TYPESET_FONT_PER_SOURCE_PX = 1.0
TYPESET_DEFAULT_FONT_PX = 18
# With no measurable lettering the start size is this share of the box's short
# side (never below DEFAULT), so a big balloon starts big.
TYPESET_UNMEASURED_PER_BOX_SIDE = 0.11
TYPESET_START_MIN_PX = 12
TYPESET_START_MAX_PX = 40
# Lettering whose block takes less than GROW_BELOW_FILL of its box starts the
# English larger, by up to GROW_MAX times, so a short line fills its balloon.
TYPESET_GROW_BELOW_FILL = 0.25
TYPESET_GROW_MAX = 1.8
TYPESET_SMALLEST_PX = 8
# Below this height English is hard to read, so a word too wide for its
# balloon is hyphenated before the text shrinks past it.
TYPESET_COMFORT_PX = 13
# Space between lines, and between the text and a balloon's outline.
# Balloons whose starting sizes are within this ratio count as lettered alike, and
# the cramped ones among them set a shared fit ratio; no balloon loses more than
# 1 - MAX_SHRINK of the size it fitted, nor takes a ratio below MIN_RATIO.
TYPESET_SIBLING_RATIO = 1.35
TYPESET_SIBLING_MIN_RATIO = 0.7
TYPESET_SIBLING_MAX_SHRINK = 0.75
TYPESET_LINE_GAP_PX = 4
TYPESET_OUTLINE_MARGIN_PX = 2
# In a balloon with a notch, lobe or tail, each line keeps this share of its lettering's height
# clear of the outline on both sides; with the outline margin alone, one long line ran from edge to
# edge of a round lobe. Ovals get none: there it only shrank text that already fitted.
TYPESET_SIDE_PAD_EM = 0.25
# Lettering is followed when the angle at which its ink lines up into the sharpest rows is
# between MIN and MAX degrees and lines it up at least MIN_GAIN times better than level
# does; a flatter slant is scan noise and a steeper one is a vertical column. A mark
# touching the box edge is outline or art when longer than GLYPH_MAX_OF_BOX of the box, and
# so is a long mark more than LINE_MIN_ELONGATION times longer than thick anywhere in it.
# The tilted text may leave the balloon by at most MAX_OUTSIDE_SHARE of its ink, else it
# is set level.
TILT_MIN_DEG = 10.0
TILT_MAX_DEG = 45.0
TILT_MIN_GAIN = 1.25
TILT_MIN_INK_PX = 40
TILT_GLYPH_MAX_OF_BOX = 0.4
TILT_LINE_MIN_ELONGATION = 8.0
TILT_MAX_OUTSIDE_SHARE = 0.005

# --- Cleanup (scanlate_inpaint) ---
# A balloon whose paper (gray levels, among the lighter half of its pixels)
# varies by no more than this is flat white, and is repainted in one colour.
INPAINT_FLAT_PAPER_STD = 6.0
# Paper further than the halo from any lettering that is darker than the rest by
# more than SHADE_DEPTH, in more than this share of its pixels, is sky, cloud or
# tone, not paper: whitening it printed a hard-edged white box. Real balloons
# measured 0 to 0.01 there, a sky 0.15.
INPAINT_FLAT_PAPER_MAX_SHADED = 0.05
INPAINT_FLAT_PAPER_SHADE_DEPTH = 6

# --- OCR line layout (ocr_layout) ---
# A line thinner than this share of a larger line beside it is furigana. Ruby
# is about half the base glyph, and its box is padded, so 0.6 separates it
# from a base line while leaving small narration (which has no larger
# neighbour) alone.
FURIGANA_MAX_THICKNESS_RATIO = 0.6


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

# --- Lettering on a pale band over busy art (scanlate_inpaint._strokes_on_a_line) ---
# When the lettering mask covers more than this share of a sign's box the paper-relative cut-off
# is reading the art as lettering (a white caption band set over black-and-white art).
LINE_ONLY_MASK_SHARE = 0.85
# A glyph is a dark mark (below INK_LEVEL) of at least MIN_AREA pixels, MIN_SPAN to MAX_SPAN (a
# share of the crop height) across, whose 9 px ring is at least RING_PALE_SHARE paper (at or
# above PAPER_LEVEL).
LINE_INK_LEVEL = 140
LINE_PAPER_LEVEL = 200
LINE_RING_PALE_SHARE = 0.6
LINE_GLYPH_MIN_AREA = 6
LINE_GLYPH_MIN_SPAN = 5
LINE_GLYPH_MAX_SPAN = 0.4
# The line is fitted through glyphs of at least ANCHOR_AREA; it needs MIN_GLYPHS of them
# spread over MIN_LENGTH_IN_GLYPHS glyph heights, and a glyph joins it within REACH heights.
LINE_ANCHOR_AREA = 25
# and at least ANCHOR_THICKNESS thick both ways: thin stripes of hatching are not letters.
LINE_ANCHOR_THICKNESS = 4
LINE_MIN_GLYPHS = 4
LINE_MIN_LENGTH_IN_GLYPHS = 3.0
LINE_REACH = 0.5
# The strip the English is set in is the lettering line closed over its gaps (CLOSE glyph heights)
# and widened (MARGIN glyph heights) so the text has a little air round it.
LINE_CORRIDOR_CLOSE = 2.5
LINE_CORRIDOR_MARGIN = 0.7
# The English is started at this share of the original glyph size: the strip is one glyph high,
# and the typesetter's own start size (set for the whole box) does not shrink into it.
LINE_TEXT_SIZE_OF_GLYPH = 1.0
