"""
scanlate_regions.py -- structured text regions for the Scanlate pipeline.

Classifies the raw boxes a detector returns (bubble, narration, sign, SFX),
guesses each region's language and writing direction, and decides which
regions the automated inpaint-and-replace pass leaves alone.
"""

from dataclasses import dataclass, asdict
from typing import Optional

import re

import numpy as np

import scanlate_tuning

from scanlate_detect import (TEXT_REGION_KINDS, _holds_balloon_interior, classify_text_regions, detect_panels,
                             inset_box_for_ocr, page_gray)


# Region kinds whose text is fitted to the bubble's own outline rather than
# its bounding rectangle. Narration boxes are rectangles already; signs and
# SFX sit on artwork, where "the light region around the box" isn't a shape.
SHAPE_FITTED_KINDS = ("bubble", "thought")

# Every kind a stored region can hold: the detector's own, plus "caption" for
# lettering on the art (scanlate_art_text), which the render paints out and
# re-letters in its own style rather than fitting into a box.
REGION_KINDS = tuple(TEXT_REGION_KINDS) + ("caption",)

_CJK_LANGUAGES = ("ja", "zh", "ko")


def region_excluded_from_auto(region: dict) -> bool:
    """True for a region the automated inpaint-and-replace pass (and the
    translate call) leaves alone: an SFX region, unless the person set its
    per-region include_sfx override. classify_text_regions()' own note is
    the reason -- SFX lettering is usually part of the art -- so SFX is
    flagged for manual review rather than painted over by default."""
    return (region.get("kind") == "sfx") and not region.get("include_sfx")


def _reads_as_japanese_sentence(text: str) -> bool:
    """Kanji together with hiragana is how a Japanese sentence is written; a
    sound effect is kana alone or a single kanji, so this text is speech
    sitting on dark art (a vertical line the detector took for lettering on
    the picture), not an onomatopoeia."""
    import re
    return bool(re.search(r"[぀-ゟ]", text) and re.search(r"[一-鿿]", text))


_KATAKANA_EFFECT = re.compile(r"^[゠-ヿー・…！？!?\s]*([゠-ヿ])\1{2,}[゠-ヿー・…！？!?\s]*$")


def flag_katakana_effects(regions: list) -> int:
    """Marks as sound effects the regions the detector called speech whose text
    is katakana alone with one kana repeated three or more times ("キュルルル",
    "ゴゴゴ"). Katakana speech does not stretch a syllable that way, and an effect
    typeset as English dialogue ("Kyuru-ru-ru...") sits on the art as a stray
    word. A region the person set to include_sfx is left as it is. Returns the count."""
    changed = 0
    for region in regions:
        if region.get("kind") != "bubble" or region.get("include_sfx"):
            continue
        if _KATAKANA_EFFECT.match((region.get("source_text") or "").strip()):
            region["kind"] = "sfx"
            changed += 1
    return changed


def reclassify_wordy_sfx(regions: list) -> int:
    """Turns a region the detector called an SFX into a sign when its text is
    too long to be one, or reads as a Japanese sentence. The detector sees
    only pixels, so a caption on dark art looks the same as a stylised sound
    effect until the text is read.
    Leaves a region the person set to include_sfx alone. Returns the count."""
    changed = 0
    for region in regions:
        if region.get("kind") != "sfx" or region.get("include_sfx"):
            continue
        text = (region.get("source_text") or "").strip()
        chars = len("".join(text.split()))
        lines = len([ln for ln in text.splitlines() if ln.strip()])
        if (chars > scanlate_tuning.SFX_MAX_CHARS or lines > scanlate_tuning.SFX_MAX_LINES
                or _reads_as_japanese_sentence(text)):
            region["kind"] = "sign"
            changed += 1
    return changed


_STRAY_LEADING_MARK = re.compile(
    r"^[0OoＯ０]\s?(?=[\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af])")


def strip_stray_sfx_mark(text: str, kind: str) -> str:
    """Drops a lone "0"/"O" glued to the front of a CJK sound effect. OCR
    reads the round start of a stroke or a speck of screentone as one, and a
    short effect has nothing around it to outvote the error. Only effects
    are touched: in a sentence a leading digit can be real."""
    if kind != "sfx":
        return text
    return _STRAY_LEADING_MARK.sub("", text or "", count=1)


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


def _widest_gap(profile) -> int:
    """Widest run of empty positions between the first and last inked one."""
    idx = np.flatnonzero(profile)
    return int(np.diff(idx).max()) - 1 if len(idx) > 1 else 0


def trim_to_lettering(gray, box: dict) -> dict:
    """The caption box cut to be centred on its lettering. The detector's box
    can run on over a site watermark beside the frame, which pulls the box
    off-centre, so the text is typeset off-centre in its frame and the stamp
    is read as part of the caption. The side with more room is cut back to
    the smaller half-width; the box is returned unchanged when the lettering
    is already centred or no single block of it is found."""
    h, w = gray.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    roi = gray[y0:min(h, box["y"] + box["h"]), x0:min(w, box["x"] + box["w"])]
    if roi.size == 0 or roi.shape[1] < 2:
        return box
    darkest, median = float(np.percentile(roi, 1)), float(np.median(roi))
    ink = roi < darkest + scanlate_tuning.TRIM_INK_LEVEL * (median - darkest)
    cols = np.flatnonzero(ink.sum(axis=0) >= 2)
    if len(cols) == 0:
        return box
    bridge = scanlate_tuning.TRIM_BRIDGE * roi.shape[0]
    blocks = np.split(cols, np.flatnonzero(np.diff(cols) > bridge) + 1)
    main = max(blocks, key=lambda c: int(ink[:, c].sum()))
    centre = (int(main[0]) + int(main[-1])) / 2
    if abs(centre - roi.shape[1] / 2) < scanlate_tuning.TRIM_OFF_CENTRE * roi.shape[1]:
        return box
    half = min(centre, roi.shape[1] - centre)
    left = x0 + int(round(centre - half))
    return dict(box, x=left, w=int(round(2 * half)))


def _letter_ink(gray, box: dict):
    """Boolean mask of the lettering inside `box` (inset as for OCR), or None
    for an empty crop. Ink is the minority shade; marks touching the crop's
    edge are the bubble's outline curving into its corners, not lettering."""
    import cv2
    inner = inset_box_for_ocr(box)
    h, w = gray.shape[:2]
    x, y = max(0, inner["x"]), max(0, inner["y"])
    roi = gray[y:min(h, y + inner["h"]), x:min(w, x + inner["w"])]
    if not roi.size:
        return None
    ink = ((roi < 128) if roi.mean() >= 128 else (roi > 128)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    rh, rw = ink.shape
    keep = [i for i in range(1, n)
            if stats[i, cv2.CC_STAT_LEFT] > 0 and stats[i, cv2.CC_STAT_TOP] > 0
            and stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] < rw
            and stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] < rh]
    return np.isin(labels, keep)


_SPLIT_GLYPH_MAX_SPAN = 0.12     # of the box's short side
_SPLIT_GLYPH_MIN_PX = 8
_SPLIT_JOIN_SPAN = 0.12          # lettering closer than this is one block
_SPLIT_MIN_GLYPHS = 4
_SPLIT_MIN_BOX_SIDE = 300
_SPLIT_PAD_SPAN = 0.08
_SPLIT_ROW_GAP = 1.5             # of the taller mark's height, between letters of a line


def split_separate_lettering(gray, box: dict) -> list:
    """The boxes for a large region whose lettering falls into separate blocks,
    or [box] when it is one block. Two caption frames drawn close together are
    detected as one region, and one translation was then fitted across both and
    laid over their border. Each block of lettering, grown by a margin, becomes
    a region of its own, kept inside `box`. Glyphs are the small compact marks;
    frames and art are larger or sparser and do not count."""
    import cv2
    if min(box["w"], box["h"]) < _SPLIT_MIN_BOX_SIDE:
        return [box]
    h, w = gray.shape[:2]
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    roi = gray[y0:min(h, y0 + box["h"]), x0:min(w, x0 + box["w"])]
    if not roi.size:
        return [box]
    # A tall balloon of vertical lettering is one region however its columns
    # fall apart: the gaps between phrases would otherwise cut it into pieces
    # that are erased and fitted separately. Horizontal lettering in one
    # outline still splits, since lobes and joined frames are drawn that way.
    # The probe is CJK because vertical lettering is only ever CJK.
    if _holds_balloon_interior(roi) and estimate_text_orientation(gray, box, "zh") == "vertical":
        return [box]
    ink = (roi < int(min(max(150, float(np.median(roi)) * 0.8), 240))).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    side = min(roi.shape[:2])
    limit = _SPLIT_GLYPH_MAX_SPAN * side
    compact = [i for i in range(1, n)
               if min(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) >= _SPLIT_GLYPH_MIN_PX
               and max(stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]) <= limit
               and stats[i, cv2.CC_STAT_AREA] >= 0.25 * stats[i, cv2.CC_STAT_WIDTH] * stats[i, cv2.CC_STAT_HEIGHT]]
    if not compact:
        return [box]
    # Lettering is one size; an ornament's curl is larger, a speck smaller. The
    # median is no judge on a frame with many small curls or a stamp, though, so
    # marks standing in a row of like marks are letters whatever their size.
    in_rows = set(_in_a_row(stats, compact))
    typical = float(np.median([stats[i, cv2.CC_STAT_HEIGHT] for i in compact]))
    glyphs = np.zeros(ink.shape, np.uint8)
    for i in compact:
        if i in in_rows or 0.6 * typical <= stats[i, cv2.CC_STAT_HEIGHT] <= 1.6 * typical:
            glyphs[labels == i] = 1
    typical = float(np.median([stats[i, cv2.CC_STAT_HEIGHT] for i in in_rows or compact]))
    join = max(3, int(_SPLIT_JOIN_SPAN * side)) | 1
    blocks = cv2.dilate(glyphs, np.ones((join, join), np.uint8))
    count, block_labels, block_stats, _ = cv2.connectedComponentsWithStats(blocks, connectivity=8)
    found = []
    for i in range(1, count):
        if int(glyphs[block_labels == i].sum()) >= 1:
            ys, xs = np.nonzero((block_labels == i) & (glyphs > 0))
            if len(xs) >= _SPLIT_MIN_GLYPHS * _SPLIT_GLYPH_MIN_PX ** 2 // 2:
                found.append((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    if len(found) < 2:
        return [box]
    found = [_with_dashes(f, stats, limit, typical) for f in found]
    pad = int(_SPLIT_PAD_SPAN * side)
    pieces = []
    for fx0, fy0, fx1, fy1 in found:
        nx0, ny0 = max(0, fx0 - pad), max(0, fy0 - pad)
        nx1, ny1 = min(roi.shape[1], fx1 + pad), min(roi.shape[0], fy1 + pad)
        pieces.append((x0 + nx0, y0 + ny0, x0 + nx1, y0 + ny1))
    bounds = (x0, y0, x0 + roi.shape[1], y0 + roi.shape[0])
    pieces = _around_text_boxes(pieces, box.get("text_boxes") or (), pad, bounds)
    if len(pieces) < 2:
        return [box]
    return [dict(box, x=a, y=b, w=c - a, h=d - b) for a, b, c, d in pieces]


def _overlap(a: tuple, b: tuple) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _around_text_boxes(pieces: list, text_boxes, pad: int, bounds: tuple) -> list:
    """`pieces` (x0, y0, x1, y1), each grown over a text box of the detector's that
    it alone cuts into. On a noisy background most letters of a line can fail the
    glyph test, so the one piece on the line covered only a few of them and the
    rest was neither erased nor translated. A text box that two pieces share is
    left split: that is two frames the detector boxed as one."""
    def clip(a):
        return (max(bounds[0], a[0]), max(bounds[1], a[1]), min(bounds[2], a[2]), min(bounds[3], a[3]))

    def union(a, b):
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))

    lines = []
    for t in text_boxes:
        line = clip((t["x"], t["y"], t["x"] + t["w"], t["y"] + t["h"]))
        if line[2] > line[0] and line[3] > line[1]:
            lines.append((line, clip((line[0] - pad, line[1] - pad, line[2] + pad, line[3] + pad))))
    for line, padded in lines:
        cut = [k for k, p in enumerate(pieces) if _overlap(p, line)]
        if len(cut) == 1:
            pieces[cut[0]] = union(pieces[cut[0]], padded)
    return pieces


def _with_dashes(block: tuple, stats, limit: float, reach: float) -> tuple:
    """`block` grown over the dash, full stop or ellipsis dot ("바로 이곳—", "그래.")
    standing at the end of its line: too small to count as a letter, but still ink
    the render must paint out, or it is left floating beside the English."""
    import cv2
    x0, y0, x1, y1 = block
    for i in range(1, len(stats)):
        x, y, w, h = (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                      int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))
        dash = min(w, h) < _SPLIT_GLYPH_MIN_PX <= max(w, h) <= limit and max(w, h) >= 2 * min(w, h)
        thin = dash or max(w, h) < _SPLIT_GLYPH_MIN_PX + 2
        on_the_line = y0 <= y + h / 2.0 <= y1
        near = x - x1 <= reach and x0 - (x + w) <= reach
        if thin and on_the_line and near and (x + w > x1 or x < x0):
            x0, x1 = min(x0, x), max(x1, x + w)
    return (x0, y0, x1, y1)


def _in_a_row(stats, candidates: list) -> list:
    """The candidate marks that have a neighbour of like height on their own line,
    close by: letters. The curls of a corner ornament are alone, or far from the
    next one across the frame. Taking the median height over every mark let the
    many small curls decide the size of lettering, so a caption's letters were
    dropped as too tall and the ornaments kept as text."""
    import cv2

    def box(i):
        return (int(stats[i, cv2.CC_STAT_LEFT]), int(stats[i, cv2.CC_STAT_TOP]),
                int(stats[i, cv2.CC_STAT_WIDTH]), int(stats[i, cv2.CC_STAT_HEIGHT]))

    kept = []
    for i in candidates:
        x, y, w, h = box(i)
        for j in candidates:
            if j == i:
                continue
            x2, y2, w2, h2 = box(j)
            shared = min(y + h, y2 + h2) - max(y, y2)
            gap = max(x2 - (x + w), x - (x2 + w2), 0)
            if (shared >= 0.5 * min(h, h2) and 0.5 <= h / float(h2) <= 2.0
                    and gap <= _SPLIT_ROW_GAP * max(h, h2)):
                kept.append(i)
                break
    return kept


def _runs(profile, bridge: int) -> list:
    """Lengths of the inked runs in a 1-D profile, joining runs split by
    fewer than `bridge` empty positions."""
    idx = np.flatnonzero(profile)
    if not len(idx):
        return []
    runs, start = [], idx[0]
    for a, b in zip(idx[:-1], idx[1:]):
        if b - a - 1 >= bridge:
            runs.append(a - start + 1)
            start = b
    runs.append(idx[-1] - start + 1)
    return runs


def lettering_size(gray, box: dict, orientation: str | None):
    """The source lettering's size in px -- the width of its columns for
    vertical text, the height of its lines for horizontal -- or None when no
    lettering is found. Used to start the English at a size matching the
    original instead of one fixed size for every balloon."""
    letters = _letter_ink(gray, box)
    if letters is None or not letters.any():
        return None
    profile = letters.any(axis=0) if orientation == "vertical" else letters.any(axis=1)
    runs = [r for r in _runs(profile, bridge=3) if r >= 6]
    if not runs:
        return None
    # An ellipsis or a punctuation mark is a thin run beside the glyph columns;
    # counting it pulled the median down and the English started too small.
    runs = [r for r in runs if r >= scanlate_tuning.LETTERING_RUN_OF_LARGEST * max(runs)]
    return float(np.median(runs))


def lettering_fill(gray, box: dict):
    """Share of the box's OCR-inset area taken by the block around all its
    lettering, or None when no lettering is found. A small share means short
    text in a large balloon."""
    letters = _letter_ink(gray, box)
    if letters is None or not letters.any():
        return None
    rows, cols = np.flatnonzero(letters.any(axis=1)), np.flatnonzero(letters.any(axis=0))
    block = (rows[-1] - rows[0] + 1) * (cols[-1] - cols[0] + 1)
    return min(1.0, block / float(letters.shape[0] * letters.shape[1]))


def estimate_text_orientation(gray, box: dict, language: str) -> str:
    """"vertical" or "horizontal" for the text inside `box` of a grayscale
    page. Only CJK text is ever vertical. CJK characters leave gaps in both
    directions, so counting ink runs can't tell lines from characters: a
    single horizontal line reads as many columns. Instead the lettering's own
    block decides -- a clearly wide block is horizontal and a clearly tall one
    vertical (scanlate_tuning.ORIENTATION_ASPECT) -- and a squarish block goes
    by its widest gap, since the space between lines (or columns) is wider than
    any space between characters along one. Marks touching the crop's edge are
    the bubble's outline curving into its corners, not lettering (_letter_ink).
    A tie falls back to the box's own shape (tall = vertical)."""
    if language not in _CJK_LANGUAGES:
        return "horizontal"
    letters = _letter_ink(gray, box)
    if letters is not None and letters.any():
        cols, rows = letters.any(axis=0), letters.any(axis=1)
        col_idx, row_idx = np.flatnonzero(cols), np.flatnonzero(rows)
        aspect = (col_idx[-1] - col_idx[0] + 1) / float(row_idx[-1] - row_idx[0] + 1)
        limit = scanlate_tuning.ORIENTATION_ASPECT
        if aspect >= limit:
            return "horizontal"
        if aspect <= 1.0 / limit:
            return "vertical"
        col_gap, row_gap = _widest_gap(cols), _widest_gap(rows)
        if col_gap != row_gap:
            return "vertical" if col_gap > row_gap else "horizontal"
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


def _vertical_overlap(a: dict, b: dict) -> float:
    """Shared height of two boxes over the shorter one's, 0..1."""
    shared = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(shared, 0) / float(max(min(a["h"], b["h"]), 1))


def reading_sequence(boxes: list, right_to_left: bool) -> list:
    """Indices of `boxes` (panels, or the regions of one panel) in reading
    order: rows from the top, and within a row from the right for manga and
    from the left otherwise. A box joins the row of the box above it when
    they overlap vertically by scanlate_tuning.READING_ROW_OVERLAP of the
    shorter one; two boxes in one row that sit one above the other (same
    column) are read top first. Grouping by overlap rather than by fixed
    height bands keeps two balloons side by side together however their tops
    happen to fall."""
    frac = scanlate_tuning.READING_ROW_OVERLAP
    rows = []
    for i in sorted(range(len(boxes)), key=lambda k: (boxes[k]["y"], boxes[k]["x"])):
        if rows and _vertical_overlap(boxes[rows[-1][0]], boxes[i]) >= frac:
            rows[-1].append(i)
        else:
            rows.append([i])
    return [i for row in rows for i in _row_order(boxes, row, right_to_left)]


def _row_order(boxes: list, row: list, right_to_left: bool) -> list:
    """One row's boxes in reading order. Boxes that share half the narrower
    one's width form a column, read top first; columns are read by their
    centre. Grouping first gives one total order, which pairwise rules
    (same column -> by top, otherwise -> by centre) can't: a wide box over
    two narrow ones shares a column with both while they don't share one."""
    columns = []
    for i in sorted(row, key=lambda k: (boxes[k]["x"], boxes[k]["y"])):
        b = boxes[i]
        if columns:
            left, right, members = columns[-1]
            shared = min(right, b["x"] + b["w"]) - max(left, b["x"])
            if shared >= 0.5 * min(b["w"], right - left):
                columns[-1] = (left, max(right, b["x"] + b["w"]), members + [i])
                continue
        columns.append((b["x"], b["x"] + b["w"], [i]))
    columns.sort(key=lambda c: (c[0] + c[1]) / 2.0, reverse=right_to_left)
    return [i for _, _, members in columns
            for i in sorted(members, key=lambda k: (boxes[k]["y"], boxes[k]["x"]))]


def analyze_page_regions(image_path: str, boxes: list, source_language: str,
                         page_id: int | None = None, image=None) -> list:
    """Turns raw detector boxes into TextRegion objects: classifies each
    one (bubble/narration/sign/sfx/thought), assigns it to a panel, reads
    its text orientation, and puts the lot in reading order (reading_sequence())
    -- panel by panel, then within each panel, right to left for a source
    language read that way (scanlate_tuning.RIGHT_TO_LEFT_LANGUAGES) and left
    to right otherwise. Regions outside every panel come after the panelled
    ones, in the same order among themselves. `image`: the page already
    decoded as BGR (see decode_page())."""
    classified = classify_text_regions(image_path, boxes, image=image)
    gray = page_gray(image_path, image)
    if gray is not None:
        classified = [trim_to_lettering(gray, b) if b.get("kind") == "narration" else b
                      for b in classified]
    try:
        panels = detect_panels(image_path, image=image)
    except ValueError:
        panels = []
    rtl = scanlate_tuning.reads_right_to_left(source_language)
    panels = [panels[i] for i in reading_sequence(panels, rtl)]

    by_panel = {}
    for i, b in enumerate(classified):
        by_panel.setdefault(_panel_for_box(b, panels), []).append(i)
    rank = {}
    for panel_id in sorted(by_panel, key=lambda p: len(panels) if p is None else p):
        members = by_panel[panel_id]
        for k in reading_sequence([classified[i] for i in members], rtl):
            rank[members[k]] = len(rank)

    staged = []
    for i, b in enumerate(classified):
        panel_id = _panel_for_box(b, panels)
        orientation = (estimate_text_orientation(gray, b, source_language)
                       if gray is not None else "horizontal")
        staged.append((rank[i], i, b, panel_id, orientation))
    staged.sort(key=lambda t: t[0])

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
