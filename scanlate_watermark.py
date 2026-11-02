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

# Names of reader sites whose stamp has no address in it. OCR drops the dots
# of a stamped address often enough that the bare name has to match as well.
_SITE_NAMES = ("marumaru", "마루마루", "newtoki", "뉴토끼", "toonkor", "툰코", "copymanga",
               "拷贝漫画", "拷貝漫畫", "mangadex", "twmanga", "kuaikan", "快看漫画", "快看漫畫")

# A repeat counts as one stamp only while it stays in the same spot. The
# stamp is a fixed overlay, whereas a line of dialogue that happens to repeat
# (a name, a sigh) moves with its balloon.
_SAME_SPOT = 0.08            # of the page's width / height, centre to centre
_EDGE_BAND = 0.18            # of the page's width / height from each edge
_MIN_REPEAT_CHARS = 3

_STRIP = re.compile(r"[\W_]+", re.UNICODE)


def _normal(text: str | None) -> str:
    return _STRIP.sub("", (text or "")).lower()


def looks_like_site_mark(text: str) -> bool:
    """True when the text is a web address or a known reader site's name."""
    text = text or ""
    if _DOMAIN.search(text):
        return True
    squashed = _normal(text)
    return any(name in squashed for name in _SITE_NAMES)


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
    regions from a page of the same size). Returns how many were flagged."""
    flagged = 0
    for region in regions:
        if region.get("skip") or not (region.get("source_text") or "").strip():
            continue
        mark = looks_like_site_mark(region["source_text"])
        if (not mark and width > 0 and height > 0
                and len(_normal(region["source_text"])) >= _MIN_REPEAT_CHARS):
            centre = _centre(region, width, height)
            mark = _on_edge(centre) and any(
                _same_stamp(region, centre, earlier, width, height) for earlier in earlier_pages)
        if mark:
            region["skip"] = True
            flagged += 1
    return flagged
