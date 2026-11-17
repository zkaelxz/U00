"""Lettering faces for the comic typeset: font lookup, the look settings, CJK glyph cover and style sampling."""

import math
import os
import re
from typing import Any

from scanlate.scanlate_page_decode import page_gray


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
_BUNDLED_FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
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
