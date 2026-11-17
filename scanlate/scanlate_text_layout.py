"""Fitting translated text into a box or a balloon's outline (sizes, wrapping, hyphenation) and drawing it."""

import numpy as np
from PIL import ImageDraw, ImageFont

from scanlate import scanlate_tuning
from scanlate.scanlate_fonts import (_CJK_CHARS, _CJK_PUNCTUATION, _PLAIN, TypesetStyle, _find_font, find_cjk_font,
                            has_cjk)


def _mask_band_span(mask, top: int, bottom: int):
    """(left, right) of the widest run of columns that are inside `mask`
    on EVERY row of [top, bottom) -- the horizontal room a line of text
    drawn in that band actually has. None if the band leaves the mask or
    has no room at all."""
    if top < 0 or bottom > mask.shape[0] or bottom <= top:
        return None
    cols = np.all(mask[top:bottom], axis=0)
    best, run_start = None, None
    for i, inside in enumerate(list(cols) + [False]):
        if inside and run_start is None:
            run_start = i
        elif not inside and run_start is not None:
            if best is None or (i - run_start) > (best[1] - best[0]):
                best = (run_start, i)
            run_start = None
    return best


_DIGRAPHS = {"th", "ch", "sh", "ph", "wh", "ck", "gh", "ng"}


# A shorter word is never cut: "Hum-ans" reads worse than the same word set a
# size smaller, and a six-letter word has only that one place to break.
_MIN_HYPHENATED_LETTERS = 7


def _cut_rank(word: str, cut: int) -> int:
    """How natural a syllable break is before word[cut]: 0 between two consonants
    ("nick-name", "sun-set"), 1 after a vowel before a consonant ("house-work"),
    2 anywhere else. A digraph ("th", "ck") is never cut."""
    before, after = word[cut - 1].lower(), word[cut].lower()
    if not after.isalpha() or not before.isalpha():
        return 2
    if before + after in _DIGRAPHS:
        return 2
    if before not in "aeiouy" and after not in "aeiouy":
        return 0
    return 1 if before in "aeiouy" else 2


def _hyphen_split(word: str, fits):
    """(head + "-", tail) with the longest head that `fits`, or None when no
    split leaves at least three letters on each side. A hyphen the word already
    has ("Alma-diel") is the break when its head fits, so the word is not also
    cut again. Punctuation does not count as a letter, so "Housework?" splits
    "House-work?", never "Housewo-rk?"."""
    def letters(part):
        return sum(c.isalpha() for c in part)

    own = [i + 1 for i, c in enumerate(word)
           if c == "-" and letters(word[:i]) >= 2 and letters(word[i + 1:]) >= 2]
    for cut in reversed(own):
        if fits(word[:cut]):
            return word[:cut], word[cut:]

    if letters(word) < _MIN_HYPHENATED_LETTERS:
        return None
    fitting = [cut for cut in range(len(word) - 3, 2, -1)
               if letters(word[:cut]) >= 3 and letters(word[cut:]) >= 3 and fits(word[:cut] + "-")]
    # Among the few longest heads, the most syllable-like break wins; the longest
    # fitting head is the fallback when none reads as one.
    best = min(fitting[:4], key=lambda cut: (_cut_rank(word, cut), -cut), default=None)
    cut = best if best is not None and _cut_rank(word, best) < 2 else (fitting[0] if fitting else None)
    return (word[:cut] + "-", word[cut:]) if cut is not None else None


def _split_twice(lines) -> bool:
    """True when a word runs over two hyphens, "Hou-sewo-rk?": a line ending in
    a hyphen right after another. One split reads fine; three pieces do not."""
    return any(a.endswith("-") and b.endswith("-") for a, b in zip(lines, lines[1:]))


# Smallest share of the box width that the mask's centre of mass may sit off the box's centre
# before the layout mask is rebuilt symmetric; below it the mask is taken as it is. The mass, not
# the bounding box: a diagonal cut through one corner leaves the box nearly full width.
_MIN_OFF_CENTRE = 0.03
# Narrow balloons are off the box centre by a pixel or two from their outline alone, and
# centring them on that moved their text the wrong way, so the offset must also be this many px.
_MIN_OFF_CENTRE_PX = 8
# The balloon has to fill the box this much for the box to stand for its outline, and the
# mirrored half may add at most this share of the mask: more means the mask is not a balloon
# with a bite out of it (a tail, a box wider than the balloon).
_MIN_BALLOON_SHARE_OF_BOX = 0.75
_MAX_MIRROR_GROWTH = 0.5
# Moving a line further than this share of the box width is a different balloon shape (a glow or
# a neighbouring balloon cut the mask), not a hatched corner, and carries the text out of the
# outline where the balloon narrows.
_MAX_RECENTRE_SHIFT = 0.12


def _symmetrised(mask):
    """`mask` joined with its mirror image about the box's centre column, or None when it should
    stay as it is. Hatching inside a balloon is kept out of the layout mask, which leaves the
    balloon's usable part off the box's centre and sets the text there; the box is the balloon's
    extent, so its centre is the balloon's, and the intact side stands in for the hatched one."""
    if mask is None or not mask.any():
        return None
    h, w = mask.shape
    cols = np.nonzero(mask.any(axis=0))[0]
    rows = np.nonzero(mask.any(axis=1))[0]
    if (cols[-1] - cols[0] + 1 < _MIN_BALLOON_SHARE_OF_BOX * w
            or rows[-1] - rows[0] + 1 < _MIN_BALLOON_SHARE_OF_BOX * h):
        return None
    if abs(np.nonzero(mask)[1].mean() - (w - 1) / 2) < max(_MIN_OFF_CENTRE * w, _MIN_OFF_CENTRE_PX):
        return None
    joined = mask | mask[:, ::-1]
    return joined if joined.sum() <= (1 + _MAX_MIRROR_GROWTH) * mask.sum() else None


def _inner_symmetric(mask):
    """`mask` where its mirror image about the box's centre column is inside it too: a centred
    layout that never leaves the balloon, because both sides are real, for when the joined mask
    reaches over an outline that the balloon's lopsided shape put nearer on one side."""
    inner = mask & mask[:, ::-1]
    return inner if inner.any() else None


def _layout_in_mask(draw, text: str, mask, font_size: int, load_font, style=_PLAIN):
    """Shape-aware counterpart to render_text_in_box()'s rectangle layout
    tries the largest font first, and for each size the
    fewest lines first, centring the block vertically and giving each line
    only the width the mask has at that line's height -- so text in an
    oval bubble narrows toward the top and bottom instead of running into
    the corners of its bounding rectangle. A word too wide for any line is
    hyphenated rather than shrunk below the comfortable size, because a
    narrow vertical balloon otherwise forces tiny letters. Returns (font,
    [(line, left, right, top)]) or None if nothing fits even at the minimum
    size, in which case the caller falls back to the plain rectangle layout."""
    words = text.split()
    if not words:
        return None
    mh = mask.shape[0]
    comfort = scanlate_tuning.TYPESET_COMFORT_PX
    smallest = scanlate_tuning.TYPESET_SMALLEST_PX
    # Whole words down to the comfortable size, then hyphenated words down
    # to it, then smaller still, each time without a word cut in three pieces;
    # that is allowed only as a last resort.
    # The side padding holds only down to the comfortable size: in a balloon too narrow for it
    # there, text reaching the outline reads better than text shrunk below that size.
    pad_em = scanlate_tuning.TYPESET_SIDE_PAD_EM if _has_notch(mask) else 0.0
    passes = ((False, comfort, True, pad_em), (True, comfort, False, pad_em))
    if pad_em:
        passes += ((False, comfort, True, 0.0), (True, comfort, False, 0.0))
    passes += ((True, smallest, False, 0.0), (True, smallest, True, 0.0))
    for hyphenate, floor, split_twice_ok, pad_em in passes:
        for size in range(font_size, style.floor(floor, font_size) - 1, -1):
            laid_out = _layout_at_size(draw, words, mask, mh, load_font(size), hyphenate, pad_em)
            if laid_out is not None and (split_twice_ok
                                         or not _split_twice([ln[0] for ln in laid_out[1]])):
                return laid_out
    return None


# A balloon whose convex hull is this much bigger than it has a notch, lobe or tail; an oval's
# hull adds well under 1%, the two-lobe and tailed balloons whose lines ran onto the outline 12-50%.
_NOTCH_HULL_EXCESS = 0.05


def _has_notch(mask) -> bool:
    if not mask.any():
        return False
    try:
        import cv2
    except ImportError:
        # OpenCV is optional and only adds the side padding; without it the text is laid out
        # unpadded, as it was before the padding existed.
        return False
    hull = np.zeros(mask.shape, np.uint8)
    cv2.fillPoly(hull, [cv2.convexHull(cv2.findNonZero(mask.astype(np.uint8)))], 1)
    return hull.sum() > (1 + _NOTCH_HULL_EXCESS) * mask.sum()


# A rectangle smaller than this share of the balloon would set the text far smaller than the
# balloon allows, so the outline layout is used instead.
_MIN_RECT_SHARE = 0.45


def _roomiest_rectangle(mask):
    """(top, left, bottom, right) of the largest-area axis-aligned rectangle with every pixel
    inside `mask`, or None for an empty mask. Row-by-row histogram of column heights with a
    stack, so a bubble-sized mask costs one pass."""
    heights = np.zeros(mask.shape[1], dtype=int)
    best, best_area = None, 0
    for y in range(mask.shape[0]):
        heights = np.where(mask[y], heights + 1, 0)
        stack = []                       # (start column, height) with rising heights
        for x, h in enumerate(list(heights) + [0]):
            start = x
            while stack and stack[-1][1] >= h:
                s, sh = stack.pop()
                if sh * (x - s) > best_area:
                    best_area, best = sh * (x - s), (y - int(sh) + 1, s, y + 1, x)
                start = s
            stack.append((start, h))
    return best


def _layout_in_rectangle(draw, text: str, mask, font_size: int, load_font, style=_PLAIN):
    """_layout_in_mask() inside the roomiest rectangle of the balloon: even lines instead of
    ones that follow the outline. Returns the same (font, lines) with the lines placed in the
    mask's coordinates, or None when the rectangle is too small a part of the balloon or the
    text does not fit it, so the caller uses the outline layout."""
    found = _roomiest_rectangle(mask)
    if found is None:
        return None
    top, left, bottom, right = found
    if (bottom - top) * (right - left) < _MIN_RECT_SHARE * int(mask.sum()):
        return None
    laid_out = _layout_in_mask(draw, text, np.ones((bottom - top, right - left), dtype=bool),
                               font_size, load_font, style)
    if laid_out is None:
        return None
    font, placed = laid_out
    return font, [(line, l + left, r + left, t + top) for line, l, r, t in placed]


def _layout_at_size(draw, words, mask, mh, font, hyphenate, pad_em=0.0):
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + scanlate_tuning.TYPESET_LINE_GAP_PX
    widths = {}

    def width_of(t):
        if t not in widths:
            bb = draw.textbbox((0, 0), t, font=font)
            widths[t] = bb[2] - bb[0]
        return widths[t]

    pad = int(pad_em * (line_height - scanlate_tuning.TYPESET_LINE_GAP_PX))
    for n_lines in range(1, max(mh // line_height, 0) + 1):
        top0 = (mh - line_height * n_lines) // 2
        found = [_mask_band_span(mask, top0 + i * line_height, top0 + (i + 1) * line_height)
                 for i in range(n_lines)]
        spans = [(left + pad, right - pad) for left, right in (sp for sp in found if sp is not None)
                 if right - left > 2 * pad]
        if len(spans) != len(found):
            continue
        queue = list(words)
        placed = []
        for i, (left, right) in enumerate(spans):
            cur = ""
            while queue:
                trial = f"{cur} {queue[0]}".strip()
                if width_of(trial) > right - left:
                    split = None if cur or not hyphenate else _hyphen_split(
                        queue[0], lambda t: width_of(t) <= right - left)
                    if split:
                        cur = split[0]
                        queue[0] = split[1]
                    break
                cur = trial
                queue.pop(0)
            if not cur:
                break  # a word that doesn't fit this line at all
            placed.append((cur, left, right, top0 + i * line_height))
        if not queue:
            return font, placed
    return None


def _ink_width(draw, text: str, font) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _wrap_words(draw, text: str, font, avail: int, hyphenate: bool) -> list:
    """`text` wrapped into lines no wider than `avail`; with `hyphenate`, a word
    wider than a line is split with a hyphen instead of overflowing."""
    words = text.split()
    lines, cur = [], ""
    while words:
        word = words.pop(0)
        trial = f"{cur} {word}".strip()
        if _ink_width(draw, trial, font) <= avail or not cur and not hyphenate:
            cur = trial
            continue
        if not cur:
            split = _hyphen_split(word, lambda t: _ink_width(draw, t, font) <= avail)
            if split:
                lines.append(split[0])
                words.insert(0, split[1])
            else:
                cur = word
            continue
        lines.append(cur)
        words.insert(0, word)
        cur = ""
    if cur:
        lines.append(cur)
    return lines


def _layout_balloon_text(draw, text: str, mask, font_size: int, load_font, rect_layout: bool,
                         style=_PLAIN, dark=None):
    """The balloon's layout: the roomiest rectangle when asked and it fits, else the outline.
    `dark`: boolean array the size of `mask`, True where the page is dark ink, so that centring
    may spread the text over hatching but not over art, a frame or the outline."""
    def lay_out(m):
        laid_out = (_layout_in_rectangle(draw, text, m, font_size, load_font, style)
                    if rect_layout else None)
        return laid_out or _layout_in_mask(draw, text, m, font_size, load_font, style)

    free = lay_out(mask)
    joined = _symmetrised(mask)
    for symmetric in ((joined, _inner_symmetric(mask)) if joined is not None else ()):
        centred = lay_out(symmetric) if symmetric is not None else None
        # Centring narrows each line, so it must not cost the balloon a smaller size or a
        # different number of lines.
        if centred is not None and (free is None or (
                centred[0].size >= free[0].size and len(centred[1]) == len(free[1])
                and not _recentred_too_far(free, centred, mask.shape[1]))) \
                and not _on_dark_ink(draw, centred, mask, dark):
            return centred
    return free


# Light hatching is mostly paper, so a sliver of dark pixels is what it takes to tell it from
# art, a frame or the outline under the part of a line that lies outside the layout mask.
_MAX_DARK_SHARE_OUTSIDE_MASK = 0.02
_DARK_GRAY = 128


def _on_dark_ink(draw, laid_out, mask, dark) -> bool:
    """True when the part of `laid_out`'s lines outside `mask` lies over dark page pixels."""
    if dark is None:
        return False
    font, placed = laid_out
    outside = dark_outside = 0
    for line, left, right, top in placed:
        bbox = draw.textbbox((0, 0), line, font=font)
        x0 = int((left + right - (bbox[2] - bbox[0])) / 2)
        rows = slice(max(top, 0), max(top + bbox[3] - bbox[1], 0))
        cols = slice(max(x0, 0), max(x0 + bbox[2] - bbox[0], 0))
        free_part = ~mask[rows, cols]
        outside += int(free_part.sum())
        dark_outside += int((dark[rows, cols] & free_part).sum())
    return outside > 0 and dark_outside / outside > _MAX_DARK_SHARE_OUTSIDE_MASK


def _recentred_too_far(free, centred, width: int) -> bool:
    """True when the text of `centred` sits further than _MAX_RECENTRE_SHIFT of `width` sideways
    from where `free` put it (compared by the mean centre of their lines, which may differ in
    number and height)."""
    def mean_centre(laid_out):
        return sum((left + right) / 2 for _, left, right, _ in laid_out[1]) / len(laid_out[1])

    return abs(mean_centre(centred) - mean_centre(free)) > _MAX_RECENTRE_SHIFT * width


def _draw_laid_out(draw, box: dict, laid_out, align: str, fill, style=_PLAIN) -> None:
    font, placed = laid_out
    align = align if style.align == "auto" else style.align
    for line, left, right, top in placed:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        room = max(0, right - left - line_w)
        lx = left + (room // 2 if align == "center" else room if align == "right" else 0)
        # textbbox's own left offset, so the ink starts where it was measured
        style.draw_text(draw, (box["x"] + lx - bbox[0], box["y"] + top), line, font, fill)


def render_text_in_box(image, box: dict, text: str, font_size: int = 18,
                        font_path: str | None = None, font_category: str = "regular",
                        custom_fonts: dict | None = None, fill=(0, 0, 0), align="center",
                        mask=None, report: dict | None = None, rect_layout: bool = False,
                        style: TypesetStyle = _PLAIN, dark=None):
    """
    image: PIL Image (already inpainted/cleaned) -- mutated in place.
    report: when given, receives {"size": the font size that was drawn}.
    Auto-shrinks font_size until the wrapped text fits the box (a single
    word wider than the box shrinks it too); word-wraps to fit box width.
    Horizontal text layout only -- no vertical CJK rendering (that's koharu's specialty, not replicated
    here).

    mask: optional boolean array the size of the box (h, w), True inside
    the bubble's real shape -- see bubble_shape_mask(). When given, each
    line's width follows the shape rather than the
    bounding rectangle; if the text can't fit the shape at any size, this
    falls back to the rectangle layout below rather than dropping text.

    font_path: an explicit per-bubble override (always wins). Otherwise
    font_category (one of FONT_CATEGORIES, normally auto-filled from
    sample_text_style()'s detection at bubble-detection time) picks
    between a bold/regular/handwritten face via _find_font() -- see that
    function for the full resolution order and custom_fonts.
    """
    text = text.translate(_CJK_PUNCTUATION)
    draw = ImageDraw.Draw(image)
    resolved_font_path = _find_font(font_path, category=font_category, custom_fonts=custom_fonts)
    if has_cjk(text):
        # One face for the whole region: the CJK fonts carry Latin too, and
        # mixing faces per glyph would break the width measuring used to wrap.
        cjk_font = find_cjk_font()
        if cjk_font:
            resolved_font_path = cjk_font
        else:
            text = " ".join(_CJK_CHARS.sub("", text).split())

    if mask is not None:
        load_font = (lambda sz: ImageFont.truetype(resolved_font_path, sz) if resolved_font_path
                     else ImageFont.load_default(sz))
        laid_out = _layout_balloon_text(draw, text, mask, font_size, load_font, rect_layout, style, dark)
        if laid_out is not None:
            if report is not None:
                report["size"] = laid_out[0].size
            _draw_laid_out(draw, box, laid_out, align, fill, style)
            return image

    def wrap_and_measure(sz, hyphenate):
        # load_default() without a size is one fixed size, so the fit loop below
        # could never shrink the text when no system font is installed.
        font = (ImageFont.truetype(resolved_font_path, sz) if resolved_font_path
                else ImageFont.load_default(sz))
        # A line break the translation carries (a table of contents, a list)
        # stays one; only each line is wrapped to the box.
        lines = [wrapped for part in text.split("\n")
                 for wrapped in _wrap_words(draw, part, font, box["w"] - 8, hyphenate)]
        line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + scanlate_tuning.TYPESET_LINE_GAP_PX
        return font, lines, line_height

    def overflows(font, lines, line_height, split_twice_ok=True):
        # Wrapping never splits a word unless hyphenating, so a word wider than
        # the box is otherwise only brought inside it by a smaller size.
        return (line_height * len(lines) > box["h"] - 8
                or any(_ink_width(draw, line, font) > box["w"] - 8 for line in lines)
                or (not split_twice_ok and _split_twice(lines)))

    # Whole words down to the comfortable size, then hyphenated words down to
    # it, and only then at any size -- as _layout_in_mask() does.
    comfort = scanlate_tuning.TYPESET_COMFORT_PX
    smallest = scanlate_tuning.TYPESET_SMALLEST_PX
    for hyphenate, floor, split_twice_ok in ((False, comfort, True), (True, comfort, False),
                                             (True, smallest, False), (True, smallest, True)):
        for size in list(range(font_size, style.floor(floor, font_size) - 1, -1)) or [font_size]:
            font, lines, line_height = wrap_and_measure(size, hyphenate)
            if not overflows(font, lines, line_height, split_twice_ok):
                break
        else:
            continue
        break
    # Nothing fit: the smallest size is what is drawn.

    if report is not None:
        report["size"] = size
    total_h = line_height * len(lines)
    start_y = box["y"] + max(0, (box["h"] - total_h) // 2)
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        side = align if style.align == "auto" else style.align
        if side == "center":
            lx = box["x"] + max(0, (box["w"] - line_w) // 2)
        elif side == "right":
            lx = box["x"] + max(4, box["w"] - line_w - 4)
        else:
            lx = box["x"] + 4
        style.draw_text(draw, (lx, start_y + i * line_height), line, font, fill)
    return image


def _dark_under(gray, box: dict, shape) -> "np.ndarray":
    """Boolean array of `shape` (the box's): True where the page is dark ink; False off the page."""
    dark = np.zeros(shape, bool)
    x0, y0 = max(box["x"], 0), max(box["y"], 0)
    x1, y1 = min(box["x"] + shape[1], gray.shape[1]), min(box["y"] + shape[0], gray.shape[0])
    if x1 > x0 and y1 > y0:
        dark[y0 - box["y"]:y1 - box["y"], x0 - box["x"]:x1 - box["x"]] = gray[y0:y1, x0:x1] < _DARK_GRAY
    return dark
