"""
scanlate_regions.py -- structured text regions for the Scanlate pipeline.

Classifies the raw boxes a detector returns (bubble, narration, sign, SFX),
guesses each region's language and writing direction, and decides which
regions the automated inpaint-and-replace pass leaves alone.
"""

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np

import scanlate_tuning

from scanlate_detect import classify_text_regions, detect_panels, inset_box_for_ocr, page_gray


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


def _widest_gap(profile) -> int:
    """Widest run of empty positions between the first and last inked one."""
    idx = np.flatnonzero(profile)
    return int(np.diff(idx).max()) - 1 if len(idx) > 1 else 0


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


def lettering_size(gray, box: dict, orientation: str):
    """The source lettering's size in px -- the width of its columns for
    vertical text, the height of its lines for horizontal -- or None when no
    lettering is found. Used to start the English at a size matching the
    original instead of one fixed size for every balloon."""
    letters = _letter_ink(gray, box)
    if letters is None or not letters.any():
        return None
    profile = letters.any(axis=0) if orientation == "vertical" else letters.any(axis=1)
    runs = [r for r in _runs(profile, bridge=3) if r >= 6]
    return float(np.median(runs)) if runs else None


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
    import functools
    frac = scanlate_tuning.READING_ROW_OVERLAP
    rows = []
    for i in sorted(range(len(boxes)), key=lambda k: (boxes[k]["y"], boxes[k]["x"])):
        if rows and _vertical_overlap(boxes[rows[-1][0]], boxes[i]) >= frac:
            rows[-1].append(i)
        else:
            rows.append([i])

    def centre(b):
        return b["x"] + b["w"] / 2.0

    def before(i, j):
        a, b = boxes[i], boxes[j]
        shared = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
        if shared >= 0.5 * min(a["w"], b["w"]):
            return -1 if a["y"] < b["y"] else (1 if a["y"] > b["y"] else 0)
        ahead = centre(a) > centre(b) if right_to_left else centre(a) < centre(b)
        return -1 if ahead else 1

    return [i for row in rows for i in sorted(row, key=functools.cmp_to_key(before))]


def analyze_page_regions(image_path: str, boxes: list, source_language: str,
                         page_id: int = None, image=None) -> list:
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
