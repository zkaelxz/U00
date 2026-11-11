"""
scanlate_watermark.py -- spots a source site's watermark among a page's text regions.

A reader site stamps its name or address on every page. OCR reads it like any
other lettering, so without this it is translated and typeset onto the art.
"""

import re
from collections.abc import Sequence

_DOMAIN = re.compile(
    r"(https?://|www\.|\b[a-z0-9][a-z0-9-]{1,}\.(?:com|net|org|co|kr|cc|me|tv|xyz|top|site|"
    r"info|io|jp|cn|tw|to|app|vip|club|moe)\b)", re.I)

# An address stamped under a Korean or CJK site name has no Latin label before
# the dot, and OCR garbles the name itself ("토끼뉴소.com"), so the dot-and-suffix
# after any CJK character is enough to call it an address.
_CJK_DOMAIN = re.compile(
    r"[\u3040-\u9fff\uac00-\ud7af]\.(?:com|net|org|co|kr|cc|me|tv|xyz|top|site|"
    r"info|io|jp|cn|tw|to|app|vip|club|moe)\b", re.I)

# Names of reader sites whose stamp has no address in it. OCR drops the dots
# of a stamped address often enough that the bare name has to match as well.
_SITE_NAMES = ("marumaru", "마루마루", "newtoki", "뉴토끼", "newtojuso", "뉴토주소", "toonkor", "툰코",
               "copymanga",
               "拷贝漫画", "拷貝漫畫", "mangadex", "twmanga", "kuaikan", "快看漫画", "快看漫畫")

# Reader-site ads read as dialogue once translated. "Fastest webtoon" and
# "search on Google" are the ad's own phrases, rare in a story.
_PROMO = re.compile(r"가장\s*빠른\s*웹[툰튼]|구글\s*검색")

# A stamp line by itself: the site's name cut short, or "미리보기" (preview)
# with a digit read in for a stroke ("미12보7"). Matched only against a whole
# line, since these are ordinary syllables inside a sentence.
_STAMP_LINE = re.compile(r"^(?:뉴토끼?|미[리\d]{1,3}보[기\d]?)$")

# A repeat counts as one stamp only while it stays in the same spot. The
# stamp is a fixed overlay, whereas a line of dialogue that happens to repeat
# (a name, a sigh) moves with its balloon.
_SAME_SPOT = 0.08            # of the page's width / height, centre to centre
_EDGE_BAND = 0.18            # of the page's width / height from each edge
_MIN_REPEAT_CHARS = 3
# What must be left of a region once its stamp lines are dropped for it to be
# lettering that happens to share a box with the stamp, not the stamp itself.
_MIN_LETTERING_CHARS = 6

_STRIP = re.compile(r"[\W_]+", re.UNICODE)


def _normal(text: str | None) -> str:
    return _STRIP.sub("", (text or "")).lower()


def looks_like_site_mark(text: str) -> bool:
    """True when the text is a web address or a known reader site's name."""
    text = text or ""
    if _DOMAIN.search(text) or _CJK_DOMAIN.search(text) or _PROMO.search(_STRIP.sub("", text)):
        return True
    squashed = _normal(text)
    return any(name in squashed for name in _SITE_NAMES)


def _is_stamp_line(line: str) -> bool:
    return looks_like_site_mark(line) or bool(_STAMP_LINE.match(_normal(line)))


def _without_mark_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _is_stamp_line(line))


def _centre(region: dict, width: float, height: float):
    return ((region["x"] + region["w"] / 2) / width, (region["y"] + region["h"] / 2) / height)


def _on_edge(centre) -> bool:
    return any(c < _EDGE_BAND or c > 1 - _EDGE_BAND for c in centre)


def _same_stamp(region: dict, centre, others: Sequence[dict], width: float, height: float) -> bool:
    text = _normal(region.get("source_text"))
    for other in others:
        if _normal(other.get("source_text")) != text:
            continue
        ox, oy = _centre(other, width, height)
        if abs(ox - centre[0]) <= _SAME_SPOT and abs(oy - centre[1]) <= _SAME_SPOT:
            return True
    return False


def flag_site_marks(regions: list, earlier_pages: Sequence[list] = (), width: int = 0, height: int = 0) -> int:
    """Sets skip on every region that is a site watermark, so it is neither
    translated nor painted over. A region is one when its text is an address or
    a known site name, or when it sits in the page's edge band with the same
    text and place as a region on one of `earlier_pages` (each a list of
    regions from a page of the same size). A region that also holds real lettering
    keeps it: only the stamp's lines are cut from its text. Returns how many
    were flagged."""
    flagged = 0
    for region in regions:
        if region.get("skip") or not (region.get("source_text") or "").strip():
            continue
        mark = looks_like_site_mark(region["source_text"])
        lines = region["source_text"].splitlines()
        if not mark and len(lines) > 1 and any(_STAMP_LINE.match(_normal(ln)) for ln in lines):
            # A cut-short name only counts beside other lines: alone it could be a word.
            mark = True
        if mark:
            # A caption box near the stamp can take its lines into one region.
            # Skipping that region would drop the caption with the stamp, so
            # only the stamp's own lines are removed from its text.
            lettering = _without_mark_lines(region["source_text"])
            if len(_normal(lettering)) >= _MIN_LETTERING_CHARS:
                region["source_text"] = lettering
                mark = False
        if (not mark and width > 0 and height > 0
                and len(_normal(region["source_text"])) >= _MIN_REPEAT_CHARS):
            centre = _centre(region, width, height)
            mark = _on_edge(centre) and any(
                _same_stamp(region, centre, earlier, width, height) for earlier in earlier_pages)
        if mark:
            region["skip"] = True
            flagged += 1
    return flagged


_FRAGMENT_MAX_WIDTH = 0.06    # of the page's width
_FRAGMENT_MAX_HEIGHT = 0.04   # of the page's height
_FRAGMENT_EDGE_PX = 3


def flag_edge_fragments(regions: list, width: int, height: int) -> int:
    """Sets skip on a tiny region that touches the page's edge. The detector
    boxes scan-edge debris and a clipped corner of a stamp, and OCR then reads
    it as a word ("Miyamura Village" from a few pixels of a corner), which
    would be translated and typeset onto the art. Real lettering is never
    this small and flush against the edge. Returns how many were flagged."""
    if width <= 0 or height <= 0:
        return 0
    flagged = 0
    for region in regions:
        if region.get("skip"):
            continue
        x, y, w, h = (int(region.get(k) or 0) for k in ("x", "y", "w", "h"))
        if w > width * _FRAGMENT_MAX_WIDTH or h > height * _FRAGMENT_MAX_HEIGHT:
            continue
        if min(x, y, width - (x + w), height - (y + h)) <= _FRAGMENT_EDGE_PX:
            region["skip"] = True
            flagged += 1
    return flagged


_NUMBER_MAX_WIDTH = 0.05      # of the page's width
_NUMBER_MAX_HEIGHT = 0.03     # of the page's height
_NUMBER_BAND = 0.08           # of the page's height, from the top or bottom edge
# A webtoon strip is far taller than wide, so the height fractions alone would
# open a margin thousands of pixels deep: both are also capped by the width.
_NUMBER_MAX_HEIGHT_PER_WIDTH = 0.06
_NUMBER_BAND_PER_WIDTH = 0.12
_NUMBER_MAX_CHARS = 3


def flag_page_numbers(regions: list, width: int, height: int) -> int:
    """Sets skip on a page number: a few characters in a tiny box inside the
    top or bottom margin. An italic "88" in a corner was read as the sound
    "おお" and typeset as "Wow", and OCR does the same with other numerals. A
    region this small and this short in the margin is never dialogue, because
    balloons sit inside the panels. Returns how many were flagged."""
    if width <= 0 or height <= 0:
        return 0
    max_h = min(height * _NUMBER_MAX_HEIGHT, width * _NUMBER_MAX_HEIGHT_PER_WIDTH)
    band = min(height * _NUMBER_BAND, width * _NUMBER_BAND_PER_WIDTH)
    flagged = 0
    for region in regions:
        if region.get("skip"):
            continue
        text = _normal(region.get("source_text"))
        if not text or len(text) > _NUMBER_MAX_CHARS:
            continue
        x, y, w, h = (int(region.get(k) or 0) for k in ("x", "y", "w", "h"))
        if w > width * _NUMBER_MAX_WIDTH or h > max_h:
            continue
        centre_y = y + h / 2
        if centre_y < band or centre_y > height - band:
            region["skip"] = True
            flagged += 1
    return flagged


def is_site_mark_region(region: dict) -> bool:
    """A skipped region whose text is a site's stamp: the one skipped kind the
    render paints out, because the stamp is not part of the art."""
    return bool(region.get("skip")) and looks_like_site_mark(region.get("source_text") or "")


# The OCR box of a stamp holds only its address strip; the logo is a square
# about as wide as that strip, sitting above it.
_LOGO_SIDE_PER_STRIP_WIDTH = 1.15
_LOGO_MARGIN_PX = 8


def logo_box(region: dict, page_width: int, page_height: int) -> dict:
    """The area a stamp covers, grown from its OCR box: wider by a margin and
    reaching upward by the logo's side, clamped to the page."""
    side = int(region["w"] * _LOGO_SIDE_PER_STRIP_WIDTH) + 2 * _LOGO_MARGIN_PX
    left = max(0, region["x"] - _LOGO_MARGIN_PX)
    right = min(page_width, region["x"] + region["w"] + _LOGO_MARGIN_PX)
    bottom = min(page_height, region["y"] + region["h"] + _LOGO_MARGIN_PX)
    top = max(0, bottom - max(side, region["h"] + 2 * _LOGO_MARGIN_PX))
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}
