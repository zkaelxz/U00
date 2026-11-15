"""Reading order and furigana filtering for OCR engines that report a box per recognised line."""
from typing import NamedTuple

import scanlate_tuning


class OcrLine(NamedTuple):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def lines_from_boxes(texts, boxes) -> list[OcrLine] | None:
    """Pairs each text with the bounding rectangle of its quad. None when the
    engine gave no usable boxes, so the caller keeps the engine's own order
    rather than guessing one."""
    texts = list(texts or ())
    if boxes is None or len(boxes) != len(texts) or not texts:
        return None
    lines = []
    for text, quad in zip(texts, boxes):
        xs, ys = [float(p[0]) for p in quad], [float(p[1]) for p in quad]
        lines.append(OcrLine(text, min(xs), min(ys), max(xs), max(ys)))
    return lines


def _span(line: OcrLine, vertical: bool) -> tuple[float, float, float, float]:
    """(thickness_lo, thickness_hi, length_lo, length_hi): thickness runs across
    the text direction (x for columns, y for rows), length along it."""
    if vertical:
        return line.x0, line.x1, line.y0, line.y1
    return line.y0, line.y1, line.x0, line.x1


def _overlap(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> float:
    return max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))


def drop_furigana(lines: list[OcrLine], vertical: bool) -> list[OcrLine]:
    """Drops a line that is clearly thinner than a larger line beside it and
    runs along it. Ruby sits next to its base column, not inside it, so a
    small line whose centre lies within the larger one's thickness (a "!" in
    the column, a narrow line of the same column) is kept, as is any small
    text with no larger neighbour (narration, a thought bubble)."""
    ratio = scanlate_tuning.FURIGANA_MAX_THICKNESS_RATIO
    spans = [_span(line, vertical) for line in lines]
    kept = []
    for i, line in enumerate(lines):
        t_lo, t_hi, l_lo, l_hi = spans[i]
        thickness, length = t_hi - t_lo, l_hi - l_lo
        centre = (t_lo + t_hi) / 2
        is_ruby = False
        for j, (o_lo, o_hi, ol_lo, ol_hi) in enumerate(spans):
            other = o_hi - o_lo
            if j == i or thickness >= ratio * other or length <= 0:
                continue
            runs_along = _overlap(l_lo, l_hi, ol_lo, ol_hi) >= 0.3 * length
            beside = not o_lo <= centre <= o_hi
            gap = max(o_lo - t_hi, t_lo - o_hi, 0.0)
            # Ruby hugs its base; a farther line is a separate caption.
            if runs_along and beside and gap <= other:
                is_ruby = True
                break
        if not is_ruby:
            kept.append(line)
    return kept


def reading_order(lines: list[OcrLine], vertical: bool) -> list[OcrLine]:
    """Vertical: columns right to left, each top to bottom. Horizontal: rows
    top to bottom, each left to right. Lines join a column or row by box
    overlap, because a column's x drifts from line to line."""
    groups: list[list] = []   # [lo, hi, lines] on the thickness axis
    key = (lambda ln: -(ln.x0 + ln.x1)) if vertical else (lambda ln: ln.y0 + ln.y1)
    for line in sorted(lines, key=key):
        lo, hi, _, _ = _span(line, vertical)
        for group in groups:
            if _overlap(lo, hi, group[0], group[1]) >= 0.5 * min(hi - lo, group[1] - group[0]):
                group[0], group[1] = min(group[0], lo), max(group[1], hi)
                group[2].append(line)
                break
        else:
            groups.append([lo, hi, [line]])
    groups.sort(key=lambda g: -(g[0] + g[1]) if vertical else g[0] + g[1])
    ordered = []
    for _lo, _hi, members in groups:
        members.sort(key=(lambda ln: ln.y0) if vertical else (lambda ln: ln.x0))
        ordered.extend(members)
    return ordered


def looks_vertical(lines: list[OcrLine]) -> bool:
    """Whether most lines are columns: clearly taller than wide. The caller's
    orientation guess is a coarse one for a squarish balloon (two short
    columns), and reading those left to right swaps them, so the boxes the
    engine reported get the last word. One-character lines say nothing."""
    tall = sum(1 for ln in lines if ln.y1 - ln.y0 >= 1.5 * (ln.x1 - ln.x0))
    return tall * 2 > len(lines)


def layout_text(lines: list[OcrLine], vertical: bool) -> str:
    vertical = vertical or looks_vertical(lines)
    return "\n".join(line.text for line in reading_order(drop_furigana(lines, vertical), vertical))
