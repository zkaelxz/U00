"""Panels for the comic reader's panel-by-panel view: pages cut along their gutters, straight or slanted."""
import numpy as np

from scanlate import scanlate_tuning

# The page is measured at this height, so the fractions below mean the same on any scan.
_WORK_H = 1000
# A gutter line may cross this share of ink: a caption or a balloon laid over it.
_GUTTER_INK = 0.15
# A gutter is a gutter only with a straight edge on both sides: a panel frame, or art cut
# straight. That is what tells it from white sky or the space round a webtoon balloon.
_EDGE = 0.5
# A gutter thinner than _THICK only counts between two drawn frames.
_THIN_EDGE = 0.7
_THICK = 0.006
_EDGE_DEPTH = 0.004
# Smaller parts (a strip of art between two straight lines, a mark beyond a frame) join
# their nearer neighbour instead of becoming a panel.
_MIN_W, _MIN_H, _MIN_AREA = 0.08, 0.05, 0.025
# Slanted gutters up to about 20 degrees, tried in steps of 1% of the region's width.
_MAX_SLANT, _SLANT_STEP = 0.36, 0.01
# A sheared line that crosses less than this share of the region says nothing.
_MIN_LINE = 0.6
# Pages with more of their area in colour than this are webtoon art, where a slanted light
# band is a beam, not a gutter; manga scans come in at 0.
_COLOUR_SHARE = 0.01
_MAX_DEPTH = 10


def find_panels(image) -> list:
    """Panel boxes {x, y, w, h, order} in pixels of `image` (BGR), or [] when the page has no
    two clear panels: webtoon art, a cover, a splash page. `order` is the box's place in
    the cutting, for in_reading_order."""
    import cv2

    h, w = image.shape[:2]
    scale = _WORK_H / h
    small = cv2.resize(image, (max(1, round(w * scale)), _WORK_H), interpolation=cv2.INTER_AREA)
    sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1]
    cutter = _Cutter(_ink(small), slanted=float((sat > 60).mean()) <= _COLOUR_SHARE)
    leaves = cutter.split(np.ones(cutter.ink.shape, bool), ())
    if len(leaves) < 2:
        return []
    return [{"x": round(x0 / scale), "y": round(y0 / scale), "w": round((x1 - x0) / scale),
             "h": round((y1 - y0) / scale), "order": order}
            for (x0, y0, x1, y1), order in leaves]


def in_reading_order(boxes: list, right_to_left: bool) -> list:
    """`boxes` from find_panels, rows top first and side by side from the right for manga."""
    def key(box):
        return [count - 1 - i if across and right_to_left else i for across, i, count in box["order"]]
    return sorted(boxes, key=key)


def _ink(small):
    """True where the page isn't its paper (or its black gutters, on a dark page)."""
    import cv2

    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 3)
    sat = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)[:, :, 1]
    h, w = gray.shape
    e = max(2, min(h, w) // 50)
    paper = float(np.median(np.concatenate([gray[:e].ravel(), gray[-e:].ravel(),
                                            gray[:, :e].ravel(), gray[:, -e:].ravel()])))
    if paper < 128:
        return np.abs(gray.astype(int) - paper) > 20
    cut = min(235.0, paper - scanlate_tuning.PANEL_PAPER_MARGIN)
    return ~((gray >= cut) & (sat < 40))


def _runs(flags) -> list:
    """[start, end) of each run of True."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], flags.astype(np.int8), [0]])))
    return list(zip(edges[::2], edges[1::2]))


def _sheared(ink, mask, slant):
    """Each pixel's line when lines drop `slant` pixels per column, and each line's ink share
    (1 for a line too short to judge)."""
    h, w = ink.shape
    off = np.round(np.arange(w) * slant).astype(int)
    line = np.arange(h)[:, None] - off[None, :] + off.max()
    n = h + off.max() - off.min()
    inked = np.bincount(line.ravel(), weights=(ink & mask).ravel(), minlength=n)
    crossed = np.bincount(line.ravel(), weights=mask.ravel(), minlength=n)
    share = np.where(crossed > 0, inked / np.maximum(crossed, 1), 1.0)
    share[crossed < _MIN_LINE * crossed.max()] = 1.0
    return share, line


class _Cutter:
    def __init__(self, ink, slanted: bool):
        self.ink = ink
        self.h, self.w = ink.shape
        self.slants = int(_MAX_SLANT / _SLANT_STEP) if slanted else 0
        self.thick = max(3, round(_THICK * self.h))
        self.depth = max(2, round(_EDGE_DEPTH * self.h))

    def split(self, mask, order) -> list:
        """[(box, order)] of the panels in `mask`, cut along gutters until none is left."""
        box = self._content(mask)
        if box is None:
            return []
        if len(order) < _MAX_DEPTH:
            for across in (False, True):
                parts = self._cut(mask, box, across)
                if len(parts) >= 2:
                    return [leaf for i, part in enumerate(parts)
                            for leaf in self.split(part, order + ((across, i, len(parts)),))]
        return [(box, order)]

    def _content(self, mask):
        """The ink's box inside `mask`, ignoring rows and columns with only specks."""
        inked = self.ink & mask
        rows = np.flatnonzero(inked.sum(axis=1) > 0.005 * max(1, mask.sum(axis=1).max()))
        cols = np.flatnonzero(inked.sum(axis=0) > 0.005 * max(1, mask.sum(axis=0).max()))
        if not len(rows) or not len(cols):
            return None
        return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1

    def _cut(self, mask, box, across: bool) -> list:
        """The parts of `mask` either side of its gutters: rows of panels, or (across) the
        panels side by side in a row. Straight gutters first, then slanted ones."""
        x0, y0, x1, y1 = box
        ink, sub = self.ink[y0:y1, x0:x1], mask[y0:y1, x0:x1]
        if across:
            ink, sub = ink.T, sub.T
        for k in [0] + [s for i in range(1, self.slants + 1) for s in (i, -i)]:
            share, line = _sheared(ink, sub, k * _SLANT_STEP)
            gutters = self._gutters(share)
            if not gutters:
                continue
            bounds = [0] + [g for run in gutters for g in run] + [len(share)]
            parts = []
            for a, b in zip(bounds[::2], bounds[1::2]):
                local = sub & (line >= a) & (line < b)
                part = np.zeros_like(mask)
                part[y0:y1, x0:x1] = local.T if across else local
                parts.append(part)
            parts = self._join_small(parts)
            if len(parts) >= 2:
                return parts
        return []

    def _gutters(self, share) -> list:
        out = []
        for a, b in _runs(share <= _GUTTER_INK):
            # A one-pixel white line is part of the drawing, not a gutter.
            if a == 0 or b == len(share) or b - a < 2:
                continue
            before = share[max(0, a - self.depth):a]
            after = share[b:b + self.depth]
            before, after = before[before < 1].max(initial=0), after[after < 1].max(initial=0)
            need = _EDGE if b - a >= self.thick else _THIN_EDGE
            if before >= need and after >= need:
                out.append((int(a), int(b)))
        return out

    def _small(self, box) -> bool:
        x0, y0, x1, y1 = box
        return (x1 - x0 < _MIN_W * self.w or y1 - y0 < _MIN_H * self.h
                or (x1 - x0) * (y1 - y0) < _MIN_AREA * self.w * self.h)

    def _join_small(self, parts) -> list:
        boxed = [(p, b) for p in parts if (b := self._content(p)) is not None]
        while len(boxed) > 1:
            i = next((i for i, (_, b) in enumerate(boxed) if self._small(b)), None)
            if i is None:
                break
            part, box = boxed.pop(i)
            near = [n for n in (i - 1, i) if 0 <= n < len(boxed)]
            j = min(near, key=lambda n: _gap(boxed[n][1], box))
            joined = boxed[j][0] | part
            boxed[j] = (joined, self._content(joined) or box)
        return [p for p, _ in boxed] if len(boxed) > 1 else []


def _gap(a, b) -> int:
    return max(a[0] - b[2], b[0] - a[2], a[1] - b[3], b[1] - a[3], 0)
