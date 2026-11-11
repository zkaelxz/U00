"""scanlate_sfx.py -- the small English gloss set beside a sound effect that stays on the art."""

# A gloss is a note beside the lettering, not a replacement for it: the effect is
# part of the picture, so it is never painted out.
_MIN_PX = 14
_MAX_PX = 30
_GAP_PX = 6
_MAX_WIDTH_SHARE = 0.4        # of the page's width, so a long answer wraps instead of crossing the page


def is_glossed(region: dict) -> bool:
    """True for a left-as-it-is sound effect that has a translation to set beside it."""
    return (region.get("kind") == "sfx" and not region.get("include_sfx") and not region.get("skip")
            and bool((region.get("translated_text") or "").strip()))


def _wrap(draw, text: str, font, max_width: int) -> list:
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and draw.textlength(trial, font=font) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current] if current else lines


def _origin(region: dict, width: int, height: int, page_w: int, page_h: int):
    """Top-left of the gloss: the first side of the effect (right, below, left,
    above) where it fits on the page, else the right side pushed back inside."""
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    for left, top in ((x + w + _GAP_PX, y), (x, y + h + _GAP_PX),
                      (x - _GAP_PX - width, y), (x, y - _GAP_PX - height)):
        if 0 <= left and left + width <= page_w and 0 <= top and top + height <= page_h:
            return left, top
    return (min(max(0, x + w + _GAP_PX), max(0, page_w - width)),
            min(max(0, y), max(0, page_h - height)))


def draw_gloss(pil_img, region: dict, font_path: str | None = None) -> None:
    from PIL import ImageDraw, ImageFont
    from scanlate_render import _find_font
    path = _find_font(font_path, category="regular")
    size = int(min(max(min(region["w"], region["h"]) * 0.4, _MIN_PX), _MAX_PX))
    font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    draw = ImageDraw.Draw(pil_img)
    page_w, page_h = pil_img.size
    lines = _wrap(draw, region["translated_text"].strip(), font, int(page_w * _MAX_WIDTH_SHARE))
    line_h = int(size * 1.2)
    width = int(max(draw.textlength(line, font=font) for line in lines))
    left, top = _origin(region, width, line_h * len(lines), page_w, page_h)
    stroke = max(2, size // 8)
    for i, line in enumerate(lines):
        # A white face with a dark outline reads on light and dark art alike.
        draw.text((left, top + i * line_h), line, font=font, fill=(255, 255, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
