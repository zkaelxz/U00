"""Fitting translated text into a box or a balloon's outline (sizes, wrapping, hyphenation) and drawing it."""

import weakref

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


def _layout_in_mask(draw, text: str, mask, font_size: int, load_font, style=_PLAIN, balance=True):
    """Shape-aware counterpart to render_text_in_box()'s rectangle layout
    tries the largest font first, and for each size the
    fewest lines first, centring the block vertically and giving each line
    only the width the mask has at that line's height -- so text in an
    oval bubble narrows toward the top and bottom instead of running into
    the corners of its bounding rectangle. A word too wide for any line is
    hyphenated rather than shrunk below the comfortable size, because a
    narrow vertical balloon otherwise forces tiny letters. Returns (font,
    [(line, left, right, top)]) or None if nothing fits even at the minimum
    size, in which case the caller falls back to the plain rectangle layout. With `balance`,
    the lines of the size that fits are balanced (_balanced)."""
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
                return _balanced(draw, words, laid_out) if balance else laid_out
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


def _layout_in_rectangle(draw, text: str, mask, font_size: int, load_font, style=_PLAIN,
                         balance=True):
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
                               font_size, load_font, style, balance)
    if laid_out is None:
        return None
    font, placed = laid_out
    return font, [(line, l + left, r + left, t + top) for line, l, r, t in placed]


def _fill_spans(words, spans, limits, width_of, hyphenate, top0, line_height):
    """First-fit: each line takes words until the next one would pass its limit. Returns
    (placed, words left over, whether a word was hyphenated)."""
    queue = list(words)
    placed, split_any = [], False
    for i, ((left, right), limit) in enumerate(zip(spans, limits)):
        cur = ""
        while queue:
            trial = f"{cur} {queue[0]}".strip()
            if width_of(trial) > limit:
                split = None if cur or not hyphenate else _hyphen_split(
                    queue[0], lambda t: width_of(t) <= limit)
                if split:
                    cur = split[0]
                    queue[0] = split[1]
                    split_any = True
                break
            cur = trial
            queue.pop(0)
        if not cur:
            break  # a word that doesn't fit this line at all
        placed.append((cur, left, right, top0 + i * line_height))
    return placed, queue, split_any


# Finest step, in percent of each line's width, when looking for the narrowest balanced fill.
_BALANCE_STEPS = 100


def _narrowest(fits, hi: int, lo: int = 1):
    """The smallest value in lo..hi for which fits(value) is not None, and its result: a
    binary search, since a narrower limit never fits words a wider one could not."""
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        got = fits(mid)
        if got is not None:
            best, hi = got, mid - 1
        else:
            lo = mid + 1
    return best


def _layout_at_size(draw, words, mask, mh, font, hyphenate, pad_em=0.0):
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + scanlate_tuning.TYPESET_LINE_GAP_PX

    def width_of(t):
        return _ink_width(draw, t, font)

    pad = int(pad_em * (line_height - scanlate_tuning.TYPESET_LINE_GAP_PX))
    for n_lines in range(1, max(mh // line_height, 0) + 1):
        top0 = (mh - line_height * n_lines) // 2
        found = [_mask_band_span(mask, top0 + i * line_height, top0 + (i + 1) * line_height)
                 for i in range(n_lines)]
        spans = [(left + pad, right - pad) for left, right in (sp for sp in found if sp is not None)
                 if right - left > 2 * pad]
        if len(spans) != len(found):
            continue
        placed, queue, _ = _fill_spans(words, spans, [right - left for left, right in spans],
                                       width_of, hyphenate, top0, line_height)
        if not queue:
            return font, placed
    return None


def _balanced(draw, words, laid_out):
    """`laid_out` (font, lines) with its breaks balanced. First-fit leaves all the slack in the
    last line ("did that to / me"); every line is narrowed by the same share until one more step
    would need another line: same size, same line count, only the breaks move. A layout with a
    hyphenated word, or of one line, is returned as it is."""
    font, placed = laid_out
    if len(placed) < 2 or " ".join(line for line, *_ in placed).split() != words:
        return laid_out

    def width_of(t):
        return _ink_width(draw, t, font)

    spans = [(left, right) for _, left, right, _ in placed]
    full = [right - left for left, right in spans]
    top0, line_height = placed[0][3], placed[1][3] - placed[0][3]

    def fits(pct):
        got, left_over, _ = _fill_spans(words, spans, [w * pct // _BALANCE_STEPS for w in full],
                                        width_of, False, top0, line_height)
        return got if not left_over and len(got) == len(placed) else None

    # The first-fit lines already fit at the share their widest line needs, so the search starts
    # there rather than at 100%.
    hi = max(-(-_BALANCE_STEPS * width_of(line) // w) for (line, *_), w in zip(placed, full))
    return font, _narrowest(fits, min(hi, _BALANCE_STEPS)) or placed


# Per font, the ink width of each text measured with it. Fitting measures the same line prefixes
# again for every size, every centring candidate and every balancing step, and measuring is most
# of the time lettering takes. Fonts are made per page (render_text_in_box's `fonts`), so their
# widths go with them.
_INK_WIDTHS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _ink_width(draw, text: str, font) -> int:
    widths = _INK_WIDTHS.setdefault(font, {})
    key = (draw.fontmode, text)
    if key not in widths:
        bbox = draw.textbbox((0, 0), text, font=font)
        widths[key] = bbox[2] - bbox[0]
    return widths[key]


def _wrap_words(draw, text: str, font, avail: int, hyphenate: bool, balance: bool = True) -> list:
    """`text` wrapped into lines no wider than `avail`; with `hyphenate`, a word
    wider than a line is split with a hyphen instead of overflowing. With `balance`, lines of
    whole words are balanced: the narrowest width that keeps the same number of lines, so the
    last line is not a stub."""
    def width_of(t):
        return _ink_width(draw, t, font)

    lines = _first_fit_words(text, avail, hyphenate, width_of)
    words = text.split()
    if not balance or len(lines) < 2 or " ".join(lines).split() != words \
            or any(width_of(line) > avail for line in lines):
        return lines
    # The first-fit lines fit at the width of their widest line, and nothing fits narrower than
    # the widest word, so the search needs only the widths in between.
    widest_line = max(map(width_of, lines))
    return _narrowest(lambda w: (got if len(got := _first_fit_words(text, w, False, width_of))
                                 == len(lines) and all(width_of(ln) <= w for ln in got)
                                 else None), widest_line,
                      lo=min(max(map(width_of, words)), widest_line)) or lines


def _first_fit_words(text: str, avail: int, hyphenate: bool, width_of) -> list:
    words = text.split()
    lines, cur = [], ""
    while words:
        word = words.pop(0)
        trial = f"{cur} {word}".strip()
        if width_of(trial) <= avail or not cur and not hyphenate:
            cur = trial
            continue
        if not cur:
            split = _hyphen_split(word, lambda t: width_of(t) <= avail)
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
                         style=_PLAIN, dark=None, balance=True):
    """The balloon's layout: the roomiest rectangle when asked and it fits, else the outline.
    `dark`: boolean array the size of `mask`, True where the page is dark ink, so that centring
    may spread the text over hatching but not over art, a frame or the outline. `balance`:
    balance the chosen layout's lines (_balanced); the size and line count are the same either way."""
    words = text.split()

    # Balancing is the costly part and changes neither size nor line count, so the candidates
    # are laid out first-fit and only the one that is checked against the page or kept is balanced.
    def lay_out(m):
        laid_out = (_layout_in_rectangle(draw, text, m, font_size, load_font, style, balance=False)
                    if rect_layout else None)
        return laid_out or _layout_in_mask(draw, text, m, font_size, load_font, style, balance=False)

    free = lay_out(mask)
    joined = _symmetrised(mask)
    for symmetric in ((joined, _inner_symmetric(mask)) if joined is not None else ()):
        centred = lay_out(symmetric) if symmetric is not None else None
        # Centring narrows each line, so it must not cost the balloon a smaller size or a
        # different number of lines.
        if centred is not None and (free is None or (
                centred[0].size >= free[0].size and len(centred[1]) == len(free[1])
                and not _recentred_too_far(free, centred, mask.shape[1]))):
            if balance or dark is not None:
                # The page is checked under the lines as they are drawn.
                centred = _balanced(draw, words, centred)
            if not _on_dark_ink(draw, centred, mask, dark):
                return centred
    return _balanced(draw, words, free) if balance and free is not None else free


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
                        style: TypesetStyle = _PLAIN, dark=None, balance: bool = True,
                        fonts: dict | None = None):
    """
    image: PIL Image (already inpainted/cleaned) -- mutated in place.
    report: when given, receives {"size": the font size that was drawn}.
    balance: balance the wrapped lines so the last one is not a stub; a pass that only wants
    the size can turn it off, since balancing never changes the size or the line count.
    fonts: a dict the caller keeps over several calls for one page, so that they share the font
    objects and with them the widths already measured (_INK_WIDTHS).
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

    load_font = _font_loader(resolved_font_path, {} if fonts is None else fonts)
    if mask is not None:
        laid_out = _layout_balloon_text(draw, text, mask, font_size, load_font, rect_layout, style, dark,
                                        balance)
        if laid_out is not None:
            if report is not None:
                report["size"] = laid_out[0].size
            _draw_laid_out(draw, box, laid_out, align, fill, style)
            return image

    def wrap_lines(font, hyphenate, balance):
        # A line break the translation carries (a table of contents, a list)
        # stays one; only each line is wrapped to the box.
        return [wrapped for part in text.split("\n")
                for wrapped in _wrap_words(draw, part, font, box["w"] - 8, hyphenate, balance)]

    def wrap_and_measure(sz, hyphenate):
        # load_default() without a size is one fixed size, so the fit loop below
        # could never shrink the text when no system font is installed.
        font = load_font(sz)
        # Balanced lines are as many and no wider, so the fit is tried on first-fit lines and
        # only the size that is drawn is balanced.
        lines = wrap_lines(font, hyphenate, False)
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

    if balance:
        lines = wrap_lines(font, hyphenate, True)

    if report is not None:
        report["size"] = size
    _draw_box_lines(draw, box, lines, font, line_height, align, fill, style)
    return image


def _font_loader(path: str | None, fonts: dict):
    """load_font(size) for `path`, making one font object per face and size in `fonts`, so the
    widths measured with it are reused (_INK_WIDTHS)."""
    def load_font(sz):
        if (path, sz) not in fonts:
            fonts[(path, sz)] = ImageFont.truetype(path, sz) if path else ImageFont.load_default(sz)
        return fonts[(path, sz)]
    return load_font


def _draw_box_lines(draw, box: dict, lines, font, line_height, align, fill, style) -> None:
    """The rectangle layout's lines, centred vertically in `box`."""
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


def _dark_under(gray, box: dict, shape) -> "np.ndarray":
    """Boolean array of `shape` (the box's): True where the page is dark ink; False off the page."""
    dark = np.zeros(shape, bool)
    x0, y0 = max(box["x"], 0), max(box["y"], 0)
    x1, y1 = min(box["x"] + shape[1], gray.shape[1]), min(box["y"] + shape[0], gray.shape[0])
    if x1 > x0 and y1 > y0:
        dark[y0 - box["y"]:y1 - box["y"], x0 - box["x"]:x1 - box["x"]] = gray[y0:y1, x0:x1] < _DARK_GRAY
    return dark
