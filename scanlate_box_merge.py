"""Joining detected boxes: dropping duplicates, merging the fragments of one balloon, and recovering form cells."""

import numpy as np

import scanlate_tuning
from scanlate_balloons import _balloon_interior


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
_TEXT_LABELS = ("text_bubble", "text_free")


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
    lines_of = {}
    for i in range(n):
        if boxes[i].get("label") in _TEXT_LABELS:
            lines_of.setdefault(find(i), []).append(
                {k: boxes[i][k] for k in ("x", "y", "w", "h")})
    kept = []
    for root, i in sorted(winner_of.items(), key=lambda t: t[1]):
        # The text boxes merged away are where the lettering lies, which
        # split_separate_lettering() must not cut through.
        kept.append(dict(boxes[i], text_boxes=lines_of[root]) if root in lines_of else boxes[i])
    return kept


_LOOSE_TEXT_LABEL = "text_free"


def _columns_of_one_balloon(gray, a: dict, b: dict) -> bool:
    """Whether two boxes of one class, close side by side or one above the
    other, are lettering inside the same balloon. The detector sometimes draws
    one box per column of a vertical balloon, so the balloon would be read,
    translated and lettered twice, the halves in the wrong order and over each
    other. The test is the page itself: the balloon found around the pair is
    the interior both boxes mostly lie inside (_balloon_interior), which two
    separate balloons never give."""
    gap_x = max(a["x"], b["x"]) - min(a["x"] + a["w"], b["x"] + b["w"])
    gap_y = max(a["y"], b["y"]) - min(a["y"] + a["h"], b["y"] + b["h"])
    if gap_x > 0 and gap_y > 0:
        return False
    gap, narrow = (gap_x, min(a["w"], b["w"])) if gap_x > 0 else (gap_y, min(a["h"], b["h"]))
    if gap > scanlate_tuning.BALLOON_COLUMN_GAP * narrow:
        return False
    x0, y0 = max(0, min(a["x"], b["x"])), max(0, min(a["y"], b["y"]))
    x1 = min(gray.shape[1], max(a["x"] + a["w"], b["x"] + b["w"]))
    y1 = min(gray.shape[0], max(a["y"] + a["h"], b["y"] + b["h"]))
    if x1 <= x0 or y1 <= y0:
        return False
    found = _balloon_interior(gray[y0:y1, x0:x1])
    if found is None:
        return False
    inside = found[0]
    for box in (a, b):
        bx0, by0 = max(box["x"], x0) - x0, max(box["y"], y0) - y0
        bx1, by1 = min(box["x"] + box["w"], x1) - x0, min(box["y"] + box["h"], y1) - y0
        if bx1 <= bx0 or by1 <= by0:
            return False
        if inside[by0:by1, bx0:bx1].mean() < scanlate_tuning.BALLOON_COLUMN_INSIDE:
            return False
    return True


def _fragments_of_one_region(a: dict, b: dict, gray=None) -> bool:
    if a.get("label") != b.get("label"):
        return False
    if _contained_share(a, b) >= scanlate_tuning.FRAGMENT_OVERLAP:
        return True
    if gray is not None and a.get("label") != _LOOSE_TEXT_LABEL and _columns_of_one_balloon(gray, a, b):
        return True
    # Lettering outside balloons has no outline between its pieces; balloons
    # side by side are separate however close.
    if a.get("label") != _LOOSE_TEXT_LABEL or not _in_line(a, b):
        return False
    # The labels in the cells of a form or table sit in line too, but a ruled line parts them.
    return gray is None or not _ruled_line_between(gray, a, b)


def _in_line(a: dict, b: dict) -> bool:
    """Whether two boxes sit one above the other sharing columns, or side by side sharing rows,
    close enough to be pieces of one line of lettering (FRAGMENT_ALIGNED, FRAGMENT_GAP)."""
    gap_limit = scanlate_tuning.FRAGMENT_GAP
    share_limit = scanlate_tuning.FRAGMENT_ALIGNED
    shared_x = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    gap_y = max(a["y"], b["y"]) - min(a["y"] + a["h"], b["y"] + b["h"])
    narrow = min(a["w"], b["w"])
    if narrow > 0 and shared_x >= share_limit * narrow and gap_y <= gap_limit * narrow:
        return True
    shared_y = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    gap_x = max(a["x"], b["x"]) - min(a["x"] + a["w"], b["x"] + b["w"])
    short = min(a["h"], b["h"])
    return short > 0 and shared_y >= share_limit * short and gap_x <= gap_limit * short


def _ruled_line_between(gray, a: dict, b: dict) -> bool:
    """Whether a straight line darker than the paper runs right across the area the two boxes
    cover, between their centres: the rule between two cells of a form. A letter's stroke is
    shorter than the area is wide, so lettering cut into pieces has none."""
    import cv2

    x0, y0 = max(0, min(a["x"], b["x"])), max(0, min(a["y"], b["y"]))
    x1 = min(gray.shape[1], max(a["x"] + a["w"], b["x"] + b["w"]))
    y1 = min(gray.shape[0], max(a["y"] + a["h"], b["y"] + b["h"]))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return False
    crop = gray[y0:y1, x0:x1]
    dark = (crop.astype(int) < np.median(crop) - scanlate_tuning.RULE_CONTRAST).astype(np.uint8) * 255
    centres = [(box["x"] + box["w"] / 2 - x0, box["y"] + box["h"] / 2 - y0) for box in (a, b)]
    stacked = abs(centres[1][1] - centres[0][1]) >= abs(centres[1][0] - centres[0][0])
    across = (x1 - x0) if stacked else (y1 - y0)
    length = int(scanlate_tuning.RULE_SPAN * across)
    # Half-degree angles: at a whole degree a thin rule at a slant drifts across the bins and is
    # found only in pieces.
    segments = cv2.HoughLinesP(dark, 1, np.pi / 360, threshold=max(10, length // 2),
                               minLineLength=length, maxLineGap=scanlate_tuning.RULE_MAX_GAP_PX)
    # Both ends near the edges of the area: a rule crosses its cell, a stroke stops inside the box.
    reach = (1 - scanlate_tuning.RULE_SPAN) / 2 * across
    for px, py, qx, qy in ([] if segments is None else segments.reshape(-1, 4).tolist()):
        lo, hi = sorted((px, qx) if stacked else (py, qy))
        if lo > reach or hi < across - 1 - reach:
            continue
        if _segments_cross((px, py), (qx, qy), *centres):
            return True
    return False


def _segments_cross(p, q, r, s) -> bool:
    def side(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return side(p, q, r) * side(p, q, s) < 0 and side(r, s, p) * side(r, s, q) < 0


def _form_cells(weak: list, kept: list, gray) -> list:
    """The `weak` lettering boxes (scored under the model's threshold) that are cells of a form
    whose other cells were kept: in line with a kept or already-taken lettering box, parted from
    it by a ruled line, and overlapping no box already kept or taken. The labels of one form score
    alike, some just over the threshold and some just under, so the threshold alone keeps half a
    form; the model's weak boxes that span several labels overlap the labels' own boxes."""
    taken = []
    left = sorted((b for b in weak if b.get("label") == _LOOSE_TEXT_LABEL),
                  key=lambda b: -b.get("confidence", 0.0))
    while True:
        for w in left:
            placed = kept + taken
            if any(_contained_share(w, b) > 0 for b in placed):
                continue
            if any(b.get("label") == _LOOSE_TEXT_LABEL and _in_line(w, b)
                   and _ruled_line_between(gray, w, b) for b in placed):
                taken.append(w)
                left = [b for b in left if b is not w]
                break
        else:
            return taken


def merge_fragment_boxes(boxes: list, gray=None) -> list:
    """One box per balloon or title: boxes of the same class that overlap
    heavily (stacked duplicates on one balloon) or sit in a tight line (a
    vertical title the detector cut into one box per character) become the
    box around them all. dedupe_overlapping_boxes() only joins different
    classes, so same-class fragments came through as separate regions, each
    OCR'd, translated and lettered over the others. The merged box keeps the
    best-ranked fragment's other fields. `gray`: the page in grayscale, which
    lets the columns of one vertical balloon be joined (_columns_of_one_balloon)."""
    n = len(boxes)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if _fragments_of_one_region(boxes[i], boxes[j], gray):
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
        joined = dict(best, x=x1, y=y1, w=x2 - x1, h=y2 - y1)
        text_boxes = [t for b in group for t in b.get("text_boxes", ())]
        if text_boxes:
            joined["text_boxes"] = text_boxes
        merged.append((members[0], joined))
    return [b for _, b in sorted(merged, key=lambda t: t[0])]
