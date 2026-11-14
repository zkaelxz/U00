"""
scanlate_render.py -- typesetting: font choice, text fitting and the page render pass.

Picks a face per bubble, wraps and sizes translated text into a box (or into the
bubble's own outline), inpaints the original lettering and draws the result.
"""

import math
import os
import re
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lib.atomic_write import replace_file

import scanlate_art_text
import scanlate_sfx
import scanlate_tilt
import scanlate_tuning
from scanlate_detect import (balloon_outside_mask, bubble_erase_mask, bubble_shape_mask, decode_page,
                             page_gray)
from scanlate_inpaint import (
    erase_box, InpaintModelUnavailable, background_is_dark, inpaint_region, is_art_mistaken_for_sign,
    lama_ml_weights_cached, sign_text_line,
)
from scanlate_regions import SHAPE_FITTED_KINDS, region_excluded_from_auto
from scanlate_watermark import is_site_mark_region, logo_box


# Shown when a page is cleaned without the LaMa-manga weights. "auto" then
# picks OpenCV without raising, so nothing else would tell the person why
# faint ghosts of the original lettering show under the English.
LAMA_MISSING_NOTE = ("The LaMa-manga cleanup model is not installed, so the original text was "
                     "erased with plain OpenCV and may show through. To install it, open Diagnostics, then "
                     "Models, and press Download next to LaMa-manga (at the PC; about 200 MB), "
                     "then redo the page.")

# One of these three, matching sample_text_style()'s "suggested_style"
# output exactly -- a bubble's auto-detected (or manually overridden)
# font_category is one of these, letting the renderer pick a face that
# roughly matches the original lettering's weight/character instead of
# using the same font for every bubble on a page.
FONT_CATEGORIES = ["regular", "bold", "handwritten"]

# Common system font fallbacks per category, tried in order. "handwritten"
# has NO reliable brush/handwriting-style font on a stock install of any
# OS -- unlike regular/bold, there's no universal system font that looks
# handwritten, so it degrades to a regular font unless a custom one is
# supplied via `custom_fonts`. That's a real, honest limitation, not a
# bug: matching decorative/brush lettering needs either a real font file
# (upload one -- see the Scanlate tab) or a trained font-classifier model
# like BalloonsTranslator's YuzuMarker.FontDetection, neither of which
# this ships with by default.
# Windows can live on any drive; WINDIR names the real folder.
_WINDOWS_FONTS = os.path.join(os.environ.get("WINDIR") or "C:\\Windows", "Fonts")
# Comic Neue (SIL Open Font License, assets/fonts/): a comic-lettering face that
# is on every machine, so a page does not depend on which system fonts exist.
_BUNDLED_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")
_SYSTEM_FONT_CANDIDATES = {
    "bold": [
        os.path.join(_BUNDLED_FONTS, "ComicNeue-Bold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        os.path.join(_WINDOWS_FONTS, "arialbd.ttf"),
    ],
    "regular": [
        os.path.join(_BUNDLED_FONTS, "ComicNeue-Regular.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        os.path.join(_WINDOWS_FONTS, "arial.ttf"),
    ],
    "handwritten": [
        os.path.join(_WINDOWS_FONTS, "comic.ttf"),  # Comic Sans MS -- present on most Windows installs
        "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf",
        os.path.join(_BUNDLED_FONTS, "ComicNeue-Bold.ttf"),
    ],
}


def _finite(value: Any, default: float) -> float:
    """`value` as a finite number, else `default`: these arrive from a request, and "nan" or
    "inf" would otherwise raise or slip past the clamps."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


class TypesetStyle:
    """Look settings a person picks for the on-page typeset (extension popup); the
    defaults reproduce the plain render, so nothing changes until one is set."""
    ALIGNS = ("auto", "left", "center", "right")
    # Fonts a person can pick for the lettering, each shipped under the SIL Open Font
    # License in assets/fonts/. "default" keeps the per-bubble face choice.
    FONTS = {"bangers": "Bangers-Regular.ttf"}

    def __init__(self, min_font: Any = 0, stroke: bool = False, align: str = "auto",
                 erase_grow: Any = 1.0, bubble_fill: Any = 0, font: str = "default"):
        self.font = font if font in self.FONTS else "default"
        self.min_font = int(max(0, min(_finite(min_font, 0), 40)))
        self.erase_grow = max(1.0, min(_finite(erase_grow, 1.0), 3.0))
        self.bubble_fill = int(max(0, min(_finite(bubble_fill, 0), 100)))
        self.stroke = bool(stroke)
        self.align = align if align in self.ALIGNS else "auto"

    def font_file(self) -> str | None:
        """The chosen bundled font's path, or None to keep the usual face choice."""
        name = self.FONTS.get(self.font)
        path = os.path.join(_BUNDLED_FONTS, name) if name else None
        return path if path and os.path.exists(path) else None

    def floor(self, floor: int, font_size: int) -> int:
        """`floor` raised to the chosen minimum, never above the size the fit starts at,
        or the search below would have no sizes to try."""
        return min(max(floor, self.min_font), font_size)

    def draw_text(self, draw, xy, text, font, fill) -> None:
        if not self.stroke:
            draw.text(xy, text, font=font, fill=fill)
            return
        # Outline in the colour that contrasts with the fill, so it reads on any balloon.
        light = sum(fill[:3]) / 3 > 127 if isinstance(fill, tuple) else False
        draw.text(xy, text, font=font, fill=fill, stroke_width=max(1, round(getattr(font, "size", 14) / 14)),
                  stroke_fill=(0, 0, 0) if light else (255, 255, 255))


_PLAIN = TypesetStyle()


def _find_font(font_path: str | None = None, category: str = "regular", custom_fonts: dict | None = None):
    """
    Resolution order: an explicit font_path always wins (a per-bubble
    manual override); then a custom font file uploaded for this
    category (custom_fonts: {category: path}, see the Scanlate tab's
    font upload); then this category's own system-font fallbacks; then
    "regular"'s fallbacks if the category itself has none installed
    (relevant mainly for "handwritten" -- see FONT_CATEGORIES' own note);
    None (Pillow's own last-resort bitmap font) if nothing at all is found.
    """
    if font_path and os.path.exists(font_path):
        return font_path
    custom_fonts = custom_fonts or {}
    custom = custom_fonts.get(category)
    if custom and os.path.exists(custom):
        return custom
    for c in _SYSTEM_FONT_CANDIDATES.get(category, []):
        if os.path.exists(c):
            return c
    if category != "regular":
        for c in _SYSTEM_FONT_CANDIDATES.get("regular", []):
            if os.path.exists(c):
                return c
    return None


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


# The bundled Latin fonts have no glyphs for these, so they would typeset as
# empty boxes; engines often carry them over from the Japanese.
_CJK_PUNCTUATION = str.maketrans({
    "「": '"', "」": '"', "『": '"', "』": '"', "【": "[", "】": "]",
    "～": "~", "〜": "~", "、": ",", "。": ".", "！": "!", "？": "?",
    "：": ":", "；": ";", "（": "(", "）": ")", "　": " ", "・": " ",
})


# Kanji, kana and hangul that survive in the English text; the Latin fonts
# above would draw each of them as an empty box.
_CJK_CHARS = re.compile("[\u1100-\u11ff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

CJK_FONT_MISSING_NOTE = ("Some kanji in the English text were left out because no font with "
                         "Japanese characters is installed on this PC.")

_CJK_FONT_CANDIDATES = [
    os.path.join(_WINDOWS_FONTS, "YuGothM.ttc"), os.path.join(_WINDOWS_FONTS, "msgothic.ttc"),
    os.path.join(_WINDOWS_FONTS, "meiryo.ttc"), os.path.join(_WINDOWS_FONTS, "msyh.ttc"),
    os.path.join(_WINDOWS_FONTS, "malgun.ttf"), os.path.join(_WINDOWS_FONTS, "simhei.ttf"),
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def has_cjk(text: str) -> bool:
    return bool(_CJK_CHARS.search(text))


def find_cjk_font() -> str | None:
    """A system font that covers CJK, or None. Nothing is bundled: the fonts
    that cover CJK are large and each has its own license."""
    return next((c for c in _CJK_FONT_CANDIDATES if os.path.exists(c)), None)


def _note_missing_cjk_font(notes, regions) -> None:
    if notes is not None and not find_cjk_font() and any(
            has_cjk(b["translated_text"]) for b in regions):
        notes.append(("warning", "cjk_font_missing", CJK_FONT_MISSING_NOTE))


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


def sibling_size_caps(entries: list) -> list:
    """Start sizes for a second render so balloons that were lettered alike are
    typeset alike. `entries` is [(start, fitted)] per balloon: the size it began
    at (from its source lettering) and the size that fitted. One balloon too
    narrow for its word shrinks well below its start while a roomier neighbour
    with similar source lettering keeps its full size, and the pair then reads as
    two different sizes of voice ("Mommy!" beside "Dad!"). Balloons whose starts
    are within scanlate_tuning.TYPESET_SIBLING_RATIO of each other share the
    smallest fitted/start ratio, so each keeps its source proportion, and none
    drops below TYPESET_SIBLING_MAX_SHRINK of the size it fitted."""
    t = scanlate_tuning
    caps = []
    for start, fitted in entries:
        ratios = [f / s for s, f in entries
                  if s and max(s, start) / min(s, start) <= t.TYPESET_SIBLING_RATIO]
        r = max(min(ratios), t.TYPESET_SIBLING_MIN_RATIO) if ratios else 1.0
        cap = max(int(start * r), int(fitted * t.TYPESET_SIBLING_MAX_SHRINK))
        caps.append(min(start, max(cap, t.TYPESET_COMFORT_PX)) if fitted > cap else start)
    return caps


def chosen_color(box: dict):
    """The region's hand-set text_color ("#rrggbb") as RGB, or None when it
    has none (or a malformed one, which falls back to the automatic fill)."""
    value = str(box.get("text_color") or "")
    if len(value) == 7 and value[0] == "#":
        try:
            return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            return None
    return None


# Below this spread between the strongest and weakest channel the ink is grey: anti-aliased black
# or white lettering reads slightly tinted and must stay plain black or white.
_INK_MIN_CHROMA = 48
_INK_MIN_PIXELS = 30
_INK_MIN_DISTANCE = 90


def sampled_ink_color(page_bgr, box: dict):
    """The typical colour of the region's original lettering as RGB, or None when it is
    plain black or white (or too little ink was found to say). Taken from the page before
    the lettering is erased."""
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.size == 0:
        return None
    # Ink is whatever differs strongly from the balloon's own colour, so a saturated red on a
    # dark balloon counts although its channel average is not bright.
    background = np.median(roi.reshape(-1, 3), axis=0)
    distance = np.abs(roi.astype(int) - background.astype(int)).max(axis=2)
    ink = distance > _INK_MIN_DISTANCE
    if int(ink.sum()) < _INK_MIN_PIXELS:
        return None
    # Anti-aliased edges blend toward the paper, so the median of all ink pixels reads washed
    # out (a deep orange comes back pale); the pixels farthest from the paper are the pen colour.
    core = ink & (distance >= np.median(distance[ink]))
    b, g, r = (int(v) for v in np.median(roi[core], axis=0))
    if max(r, g, b) - min(r, g, b) < _INK_MIN_CHROMA:
        return None
    return (r, g, b)


# A fade needs the lettering's top and bottom halves to differ clearly and both to be a real colour.
_FADE_MIN_DISTANCE = 55
_FADE_MAX_OFF_LINE = 50
_RING_MIN_SPAN = 0.85
_FADE_MIN_END_CHROMA = 30


def sampled_ink_fade(page_bgr, box: dict):
    """(top, bottom) RGB colours of the region's original lettering when it fades from one real
    colour to another down its height, else None. Read from the page before the erase, like
    sampled_ink_color; a single colour, black or white lettering, or too little ink gives None."""
    h, w = page_bgr.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = page_bgr[y:min(h, y + box["h"]), x:min(w, x + box["w"])]
    if roi.size == 0:
        return None
    import cv2
    background = np.median(roi.reshape(-1, 3), axis=0)
    distance = np.abs(roi.astype(int) - background.astype(int)).max(axis=2)
    # Art, a leaf or an outline around the lettering reaches the box edge; lettering strokes
    # sit inside it, so any stroke component touching the edge is not counted.
    ink = _without_border_components(distance > _INK_MIN_DISTANCE)
    if int(ink.sum()) < 2 * _INK_MIN_PIXELS:
        return None
    rows = np.where(ink.any(axis=1))[0]
    # The outer thirds, not the halves: each half of a fade already averages over half the change.
    third = max(1, (rows.max() - rows.min() + 1) // 3)
    top_end, bottom_end = rows.min() + third, rows.max() + 1 - third
    bands = [slice(0, top_end), slice(top_end, bottom_end), slice(bottom_end, roi.shape[0])]
    colours = []
    for part in bands:
        d, px = distance[part], roi[part]
        # Stroke interiors only: thin marks (a balloon outline, a glow) have none, and an
        # outline of another colour hugs the stroke's edge.
        m = cv2.erode(ink[part].astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        if int(m.sum()) < _INK_MIN_PIXELS:
            # A two-line balloon has no ink in the middle third; that is a step, not a gap to reject.
            colours.append(None)
            continue
        core = m & (d >= np.median(d[m]))
        b, g, r = (int(v) for v in np.median(px[core], axis=0))
        colours.append((r, g, b))
    top, middle, bottom = colours
    if top is None or bottom is None:
        return None
    chromas = [max(c) - min(c) for c in (top, bottom)]
    # A dark end of a real fade is duller than the light end (dark purple to lavender), but a
    # grey end is plain black or white lettering.
    if max(chromas) < _INK_MIN_CHROMA or min(chromas) < _FADE_MIN_END_CHROMA:
        return None
    span = np.array(bottom, float) - np.array(top, float)
    if np.abs(span).max() < _FADE_MIN_DISTANCE:
        return None
    # A real fade passes smoothly through the middle; two unrelated colours (a red rose over a
    # green leaf) do not land between each other.
    if middle is not None:
        along = float(np.dot(np.array(middle, float) - top, span) / np.dot(span, span))
        off = np.abs(np.array(middle, float) - (np.array(top, float) + along * span)).max()
        if not 0.1 <= along <= 0.9 or off > _FADE_MAX_OFF_LINE:
            return None
    return top, bottom


def _without_border_components(ink):
    """`ink` (bool array) minus every connected component that touches the array's edge, and
    minus specks (stray ornament fragments) far smaller than the largest stroke component."""
    import cv2
    _, labels = cv2.connectedComponents(ink.astype(np.uint8), connectivity=8)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    inside = ink & ~np.isin(labels, edge[edge > 0])
    # A balloon outline is one component reaching nearly all the way round the box; lettering
    # is many small ones, so such a ring is not lettering however far it sits from the edge.
    for label in np.unique(labels[inside]):
        ys, xs = np.where(labels == label)
        if (ys.max() - ys.min() + 1) >= _RING_MIN_SPAN * ink.shape[0] and \
                (xs.max() - xs.min() + 1) >= _RING_MIN_SPAN * ink.shape[1]:
            inside &= labels != label
    sizes = np.bincount(labels[inside])
    if sizes.size > 1:
        sizes[0] = 0
        specks = np.where(sizes < 0.08 * sizes.max())[0]
        inside &= ~np.isin(labels, specks)
    return inside


def _fade_lettering(before, after, top, bottom) -> None:
    """Recolours, in place, the lettering drawn between `before` and `after` (the same region
    of the page, PIL images) from the solid `top` colour it was drawn in to a vertical fade
    from `top` to `bottom`. The lettering's own coverage is kept, so its anti-aliased edges
    stay soft."""
    b = np.asarray(before.convert("RGB")).astype(float)
    a = np.asarray(after.convert("RGB")).astype(float)
    away = np.array(top, float) - b
    weight = (a - b) * away
    share = np.clip(weight.sum(axis=2) / np.maximum((away * away).sum(axis=2), 1.0), 0.0, 1.0)
    rows = np.where((share > 0.1).any(axis=1))[0]
    if rows.size < 2:
        return
    t = np.clip((np.arange(b.shape[0]) - rows.min()) / max(rows.max() - rows.min(), 1), 0.0, 1.0)
    colour = np.array(top, float)[None, :] * (1 - t[:, None]) + np.array(bottom, float)[None, :] * t[:, None]
    out = b + share[:, :, None] * (colour[:, None, :] - b)
    after.paste(Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)))


def _sampled_inks(page, regions: list, match_color: bool):
    """Per region, the colour its original lettering was drawn in and the (top, bottom) pair
    when it faded; both None unless `match_color`. A fade starts from its top colour."""
    inks = [sampled_ink_color(page, b) if match_color else None for b in regions]
    fades = [sampled_ink_fade(page, b) if match_color and chosen_color(b) is None else None
             for b in regions]
    return [fade[0] if fade else ink for ink, fade in zip(inks, fades)], fades


def _tilts_and_lines(page, regions: list):
    """Per region its text angle, and for a sign whose lettering is one line on a pale band the
    (strip, angle, size) of that line, which also gives the angle: the skew detector is thrown off by
    the busy art round such a band."""
    lines = [sign_text_line(page, b) if b.get("kind") == "sign" else None for b in regions]
    angles = [line[1] if line else scanlate_tilt.estimate_text_angle(page, b)
              for line, b in zip(lines, regions)]
    return angles, lines


def _text_fill(pil_img, box: dict, sampled=None):
    """The hand-set colour if any, then the colour sampled from the original lettering
    (when the person turned that on); else white lettering on a dark balloon,
    black otherwise."""
    chosen = chosen_color(box)
    if chosen is not None:
        return chosen
    if sampled is not None:
        return sampled
    x, y = max(0, box["x"]), max(0, box["y"])
    roi = np.asarray(pil_img.crop((x, y, x + box["w"], y + box["h"])).convert("L"))
    if roi.size and background_is_dark(roi):
        return (255, 255, 255)
    return (0, 0, 0)


def process_page(image_path: str, bubbles: list, out_path: str, font_path: str | None = None,
                  custom_fonts: dict | None = None, notes: list | None = None,
                  match_color: bool = False, rect_layout: bool = False,
                  look: TypesetStyle = _PLAIN):
    """
    Full render pass: inpaint every bubble that has real translated text,
    then draw that text into the cleaned box. `bubbles` is a list of dicts
    with x,y,w,h,translated_text,font_size,skip (matches the DB rows
    from db.load_bubbles()).

    A bubble with blank translated text is treated the same as a skipped
    one -- the original is left untouched. Erasing the source text and
    putting nothing back is strictly worse than leaving it alone: it used
    to happen because the "inpaint" and "draw text" passes checked
    different conditions (inpaint ran for every non-skipped bubble; only
    the draw pass checked for actual text), so a bubble that was detected
    but never got real OCR/translation -- e.g. a wrongly-sized box, or a
    failed auto-translate -- rendered as a blank white rectangle with the
    original text silently erased underneath it and nothing to show for it.

    Returns (out_path, skipped) -- skipped lists bubbles that had no
    translated text, so the caller can warn about them rather than the
    person only discovering a blank spot after the fact.

    A region classified as SFX is left alone unless its include_sfx
    override is set -- see region_excluded_from_auto().
    Speech/thought bubbles get their text fitted to the bubble's real
    shape (bubble_shape_mask()), not just its rectangle.

    The page is decoded once: every bubble is erased on that one array, the
    text drawn on it, and the PNG encoded once, to a unique temp file next to
    out_path (removed even on failure) that then replaces out_path
    atomically, so two renders of the same page can't collide. With a
    `notes` list, an ML inpaint that fell back to OpenCV is recorded there
    (once) and the render goes on with the OpenCV result instead of raising.
    `rect_layout` sets balloon text in the largest rectangle inside the balloon (see
    _roomiest_rectangle) instead of following its outline. `match_color` letters a balloon in the colour of its original lettering when that was
    a real colour (see sampled_ink_color); black and white lettering stays automatic.
    `look` (TypesetStyle) sets the lettering's alignment, minimum size and outline.
    """
    import tempfile

    font_path = font_path or look.font_file()

    def in_auto_pass(b):
        return not b.get("skip") and not region_excluded_from_auto(b)

    def has_real_text(b):
        return in_auto_pass(b) and (b.get("translated_text") or "").strip()

    skipped_blank = [b for b in bubbles
                     if in_auto_pass(b) and not (b.get("translated_text") or "").strip()]
    to_render = [b for b in bubbles if has_real_text(b)]

    out_dir = os.path.dirname(os.path.abspath(out_path))
    fd, result_path = tempfile.mkstemp(prefix=".typeset_", suffix=".out.png", dir=out_dir)
    os.close(fd)
    try:
        page = decode_page(image_path)
        # Shape masks follow the original lettering, so they are taken before
        # any bubble is erased.
        gray = (page_gray(image_path, page) if any(
            (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS for b in to_render) else None)
        # Lettering on the artwork is painted out and re-lettered in its own
        # style (scanlate_art_text); bubbles keep the balloon path below.
        captions = [b for b in to_render if b.get("kind") == "caption"]
        to_render = [b for b in to_render if b.get("kind") != "caption"]
        to_render, art_signs = _split_art_signs(page, to_render)
        caption_styles = [scanlate_art_text.caption_style(page, b) for b in captions]
        caption_styles = [dict(s, fill=chosen_color(b)) if chosen_color(b) else s
                          for b, s in zip(captions, caption_styles)]
        # The ink is read before the erase below removes it.
        inks, fades = _sampled_inks(page, to_render, match_color)
        angles, lines = _tilts_and_lines(page, to_render)
        _erase_site_stamps(page, bubbles)
        if notes is not None and to_render and not lama_ml_weights_cached():
            notes.append(("error", "inpaint_unavailable", LAMA_MISSING_NOTE))
        _erase_lettering_of(image_path, to_render, page, gray, notes, look.erase_grow)

        if captions and not scanlate_art_text.paint_out_captions(page, captions, backend="auto"):
            if notes is not None and not any(n[1] == "inpaint_fallback" for n in notes if len(n) > 1):
                notes.append(("warning", "inpaint_fallback",
                              "The LaMa-manga model is not available, so the captions on the art "
                              "were painted out with plain OpenCV, which leaves faint ghosts."))

        pil_img = Image.fromarray(np.ascontiguousarray(page[:, :, ::-1]))
        for b, style in zip(captions, caption_styles):
            scanlate_art_text.draw_caption(pil_img, b, b["translated_text"], style,
                                           font_path=font_path)
        _note_missing_cjk_font(notes, to_render)
        _letter_balloons(pil_img, image_path, to_render, gray, font_path, custom_fonts, inks,
                         rect_layout, look, angles, fades, lines)
        _draw_glosses(pil_img, page, bubbles, art_signs, font_path)
        pil_img.save(result_path, "PNG")
        replace_file(result_path, out_path)
    finally:
        if os.path.exists(result_path):
            os.remove(result_path)
    return out_path, skipped_blank


# A balloon's tone or hatching bites a dent out of the light interior; filling a dent that small
# keeps each line centred on the balloon instead of on the part the hatching left. A bigger
# notch (the waist of a two-lobe balloon) is real shape and stays.
_MAX_DENT_FILL = 1.2
# A dent is tone on the balloon's paper when at least this share of it is no darker than
# CV_GREY_PAPER_FLOOR, and at most _DENT_MAX_OUTSIDE of it is the page's paper beyond the outline.
# The spikes and speed lines that a jagged burst balloon meets are 55-90% light between their
# strokes, and lines set into them ran onto the art.
_DENT_MIN_PAPER = 0.9
_DENT_MAX_OUTSIDE = 0.25


def _without_small_dents(mask, page=None, outside=None, page_edge=None):
    """`mask` with the dents in it filled in, when its convex hull adds at most _MAX_DENT_FILL of
    its area. With `page` (the box's gray crop) only the parts of the hull that are
    tone on the balloon's paper are filled: the notch where a lobe or the balloon's tail meets it
    holds the dark art round the balloon, or the page's paper beyond its outline (`outside`, the
    light paper reaching the box edge), and text set across it ran out of the balloon. A dent
    touching `page_edge` (see _page_edge()) is filled whatever its tone."""
    if mask is None or not mask.any():
        return mask
    import cv2
    hull = np.zeros(mask.shape, np.uint8)
    cv2.fillPoly(hull, [cv2.convexHull(cv2.findNonZero(mask.astype(np.uint8)))], 1)
    if hull.sum() > _MAX_DENT_FILL * mask.sum():
        return mask
    if page is None:
        return hull.astype(bool)
    paper = page >= scanlate_tuning.CV_GREY_PAPER_FLOOR
    outside = np.zeros(mask.shape, bool) if outside is None else outside
    count, parts = cv2.connectedComponents(((hull > 0) & ~mask).astype(np.uint8), connectivity=8)
    filled = mask.copy()
    for i in range(1, count):
        part = parts == i
        # A balloon cut off by the page's edge runs on past it, so a dent reaching that edge is
        # its own lettering cut in half, not art beside it, however dark its strokes.
        cut_by_page = page_edge is not None and page_edge[part].any()
        if ((cut_by_page or paper[part].mean() >= _DENT_MIN_PAPER)
                and outside[part].mean() <= _DENT_MAX_OUTSIDE):
            filled |= part
    return filled


def _page_edge(page_shape, box: dict, shape):
    """Boolean `shape` array of `box`: True on the page's outermost rows and columns and off the page."""
    edge = np.zeros(shape, bool)
    ph, pw = page_shape[:2]
    if box["y"] <= 0: edge[: 1 - box["y"], :] = True
    if box["x"] <= 0: edge[:, : 1 - box["x"]] = True
    if box["y"] + shape[0] >= ph: edge[ph - 1 - box["y"]:, :] = True
    if box["x"] + shape[1] >= pw: edge[:, pw - 1 - box["x"]:] = True
    return edge


def _box_gray(gray, box: dict, shape):
    """The page's gray values under `box`, as an array of `shape` (the box's); 0 off the page."""
    crop = np.zeros(shape, np.uint8)
    x0, y0 = max(box["x"], 0), max(box["y"], 0)
    x1, y1 = min(box["x"] + shape[1], gray.shape[1]), min(box["y"] + shape[0], gray.shape[0])
    if x1 > x0 and y1 > y0:
        crop[y0 - box["y"]:y1 - box["y"], x0 - box["x"]:x1 - box["x"]] = gray[y0:y1, x0:x1]
    return crop


def _layout_mask(image_path: str, b: dict, gray):
    """The shape a balloon's text is set in: its interior with hatching dents filled in."""
    mask = bubble_shape_mask(image_path, b, image=gray, margin=scanlate_tuning.TYPESET_OUTLINE_MARGIN_PX)
    if mask is None:
        return None
    return _without_small_dents(mask, _box_gray(gray, b, mask.shape),
                                balloon_outside_mask(image_path, b, image=gray),
                                _page_edge(gray.shape, b, mask.shape))


def _letter_balloons(pil_img, image_path: str, to_render: list, gray,
                     font_path: str | None, custom_fonts: dict | None, inks: list,
                     rect_layout: bool = False, style: TypesetStyle = _PLAIN,
                     angles: list | None = None,
                     fades: "list[tuple | None] | None" = None,
                     lines: "list[tuple | None] | None" = None) -> None:
    """Draws each balloon's text: a scratch pass finds the size each one fits at,
    then the real pass draws with sibling balloons held to a shared ratio. A balloon
    whose original lettering was tilted (`angles`, degrees) is set at that angle."""
    masks = [_layout_mask(image_path, b, gray) if (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS
             else None for b in to_render]

    def draw(target, b, shape_mask, size, ink, report=None):
        # Only a mask that centring would rebuild needs the dark pixels, so other balloons are
        # drawn exactly as before.
        extra: dict[str, Any] = ({"dark": _dark_under(gray, b, shape_mask.shape)}
                                 if shape_mask is not None and gray is not None and _symmetrised(shape_mask) is not None
                                 else {})
        render_text_in_box(target, b, b["translated_text"], font_size=size,
                           font_path=font_path,
                           font_category=b.get("font_category") or "regular",
                           custom_fonts=custom_fonts, mask=shape_mask, rect_layout=rect_layout, style=style,
                           fill=_text_fill(target, b, ink), report=report, **extra)

    starts = [b.get("font_size", 18) for b in to_render]
    scratch = pil_img.copy()
    fitted = []
    for b, m, start, ink in zip(to_render, masks, starts, inks):
        rep = {}
        draw(scratch, b, m, start, ink, rep)
        fitted.append(rep.get("size", start))
    if style.bubble_fill:
        for b, m in zip(to_render, masks):
            if m is not None:
                _fill_balloon(pil_img, b, m, style.bubble_fill / 100)
    sizes = sibling_size_caps(list(zip(starts, fitted)))
    for b, m, size, ink, angle, fade, line in zip(to_render, masks, sizes, inks,
                                                  angles or [0.0] * len(to_render),
                                                  fades or [None] * len(to_render),
                                                  lines or [None] * len(to_render)):
        region = (max(0, b["x"]), max(0, b["y"]), b["x"] + b["w"], b["y"] + b["h"])
        before = pil_img.crop(region) if fade else None
        tilted = False
        if angle or line:
            fill = _text_fill(pil_img, b, ink)
            tilted = scanlate_tilt.draw_tilted(
                pil_img, b, line[0] if line else m, angle, min(size, line[2]) if line else size,
                lambda target, shifted, sz, shifted_mask: render_text_in_box(
                    target, shifted, b["translated_text"], font_size=sz, font_path=font_path,
                    font_category=b.get("font_category") or "regular", custom_fonts=custom_fonts,
                    mask=shifted_mask, rect_layout=rect_layout, style=style, fill=fill))
        if not tilted:
            draw(pil_img, b, m, size, ink)
        if fade:
            lettered = pil_img.crop(region)
            _fade_lettering(before, lettered, *fade)
            pil_img.paste(lettered, region[:2])


def _dark_under(gray, box: dict, shape) -> "np.ndarray":
    """Boolean array of `shape` (the box's): True where the page is dark ink; False off the page."""
    dark = np.zeros(shape, bool)
    x0, y0 = max(box["x"], 0), max(box["y"], 0)
    x1, y1 = min(box["x"] + shape[1], gray.shape[1]), min(box["y"] + shape[0], gray.shape[0])
    if x1 > x0 and y1 > y0:
        dark[y0 - box["y"]:y1 - box["y"], x0 - box["x"]:x1 - box["x"]] = gray[y0:y1, x0:x1] < _DARK_GRAY
    return dark


def _split_art_signs(page, regions: list):
    """(regions to erase and letter, regions that are art mistaken for signs). The art is
    not erased: its translation is set beside it, as a sound effect's is."""
    if not any(b.get("kind") == "sign" for b in regions):
        return regions, []
    import cv2
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    art = [b for b in regions if is_art_mistaken_for_sign(gray, b)]
    return [b for b in regions if not any(b is a for a in art)], art


def _draw_glosses(pil_img, page, bubbles: list, art_signs: list, font_path) -> None:
    """Sets beside each left-as-is sound effect its translation, and the same for each art
    sign, inside its panel and clear of every other region, whose lettering is still there."""
    for b in bubbles:
        if scanlate_sfx.is_glossed(b):
            scanlate_sfx.draw_gloss(pil_img, b, font_path=font_path)
    if not art_signs:
        return
    from scanlate_detect import detect_panels
    from scanlate_regions import _panel_for_box
    try:
        panels = detect_panels("", image=page)
    except ValueError:
        panels = []
    for a in art_signs:
        index = _panel_for_box(a, panels)
        scanlate_sfx.draw_gloss(pil_img, a, font_path=font_path,
                                avoid=tuple(b for b in bubbles if b is not a),
                                bounds=panels[index] if index is not None else None)


def _fill_balloon(pil_img, box: dict, mask, share: float) -> None:
    """Blends the balloon's inside toward its own paper colour, so faint ghosts of the
    erased lettering and screentone speckle fade; the outline stays because the mask is
    inset from it. `share` 1.0 is a flat fill."""
    x, y = box["x"], box["y"]
    h, w = mask.shape
    region = np.asarray(pil_img.crop((x, y, x + w, y + h))).astype(float)
    if region.shape[:2] != mask.shape or not mask.any():
        return
    paper = np.median(region[mask], axis=0)
    region[mask] = region[mask] * (1 - share) + paper * share
    pil_img.paste(Image.fromarray(region.astype(np.uint8)), (x, y))


def _erase_site_stamps(page, bubbles: list) -> None:
    """Paints out each skipped region that is a site's stamp, over the logo's area."""
    page_h, page_w = page.shape[:2]
    for b in bubbles:
        if is_site_mark_region(b):
            erase_box(page, logo_box(b, page_w, page_h))


def _erase_lettering_of(image_path: str, regions: list, page, gray, notes, grow: float = 1.0) -> None:
    """Erases each region's original lettering from `page`. A LaMa failure is
    recorded once in `notes` and the render goes on with the OpenCV result that
    is already in `page`; with no `notes` list it is raised."""
    for b in regions:
        try:
            erase_lettering(image_path, b, page, gray, grow)
        except InpaintModelUnavailable as exc:
            if notes is None:
                raise
            if not any(n[1] == "inpaint_fallback" for n in notes if len(n) > 1):
                notes.append(("warning", "inpaint_fallback", str(exc)))


def erase_lettering(image_path: str, b: dict, page, gray, grow: float = 1.0) -> None:
    """Erases region `b`'s original lettering from `page` (BGR, in place). A
    speech or thought balloon is erased only inside its outline, so the
    outline and the art around it survive; other kinds, and a balloon whose
    shape can't be found, are erased across their box. `gray`: the page in
    grayscale before any erasing, or None when no region is shape-fitted.
    InpaintModelUnavailable as inpaint_region() raises it."""
    inside = None
    if gray is not None and (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS:
        inside = bubble_erase_mask(image_path, b, image=gray)
    shape_fitted = gray is not None and (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS
    inpaint_region(image_path, b, image=page, inside=inside, spare_art=shape_fitted,
                   on_art=b.get("kind") == "sign", grow=grow)


def sample_text_style(image_path: str, box, image=None) -> dict:
    """
    Samples a text region to guess whether it needs a decorative face --
    handwritten/brush lettering versus standard print.

    Doesn't identify the actual typeface (that needs a trained classifier,
    like BalloonsTranslator's YuzuMarker.FontDetection model -- a real,
    heavier alternative not implemented here); it estimates stroke weight
    and irregularity via classical CV so the renderer can pick a closer-
    matching font CATEGORY instead of defaulting every bubble on a page
    to the same bold sans. "suggested_style" is one of FONT_CATEGORIES
    and is what the Scanlate tab auto-fills each bubble's font_category
    with at detection time -- reviewable/overridable per bubble before
    render, same hybrid-workflow pattern as bubble detection itself.
    `image`: the page already decoded, gray or BGR (see page_gray()).
    """
    import cv2
    import numpy as np

    img = page_gray(image_path, image)
    if img is None:
        return {"weight": "regular", "irregular": False, "suggested_style": "regular"}
    h, w = img.shape[:2]
    x, y = max(0, box["x"]), max(0, box["y"])
    bw, bh = min(box["w"], w - x), min(box["h"], h - y)
    if bw <= 0 or bh <= 0:
        return {"weight": "regular", "irregular": False, "suggested_style": "regular"}

    roi = img[y:y + bh, x:x + bw]
    _, binary = cv2.threshold(roi, 128, 255, cv2.THRESH_BINARY_INV)
    ink_ratio = float(binary.mean()) / 255.0

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    heights = [cv2.boundingRect(c)[3] for c in contours if cv2.contourArea(c) > 8]
    # Print lettering has consistent glyph heights; handwriting varies.
    irregular = bool(heights) and (np.std(heights) / max(np.mean(heights), 1)) > 0.45

    return {
        "weight": "bold" if ink_ratio > 0.22 else "regular",
        "irregular": irregular,
        "ink_ratio": round(ink_ratio, 3),
        "suggested_style": ("handwritten" if irregular else
                            ("bold" if ink_ratio > 0.22 else "regular")),
    }
