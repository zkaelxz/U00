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


def _overlap(a: tuple, b: dict) -> int:
    """Area where rectangle `a` (left, top, width, height) overlaps box `b`."""
    ox = min(a[0] + a[2], b["x"] + b["w"]) - max(a[0], b["x"])
    oy = min(a[1] + a[3], b["y"] + b["h"]) - max(a[1], b["y"])
    return ox * oy if ox > 0 and oy > 0 else 0


def _candidates(region: dict, width: int, height: int):
    """Top-left corners round the effect, nearest sides first: right, below, left, above,
    each lined up with the effect's start, middle and end so a crowded side still has a slot."""
    x, y, w, h = region["x"], region["y"], region["w"], region["h"]
    for dy in (0, (h - height) // 2, h - height):
        yield x + w + _GAP_PX, y + dy
    for dx in (0, (w - width) // 2, w - width):
        yield x + dx, y + h + _GAP_PX
    for dy in (0, (h - height) // 2, h - height):
        yield x - _GAP_PX - width, y + dy
    for dx in (0, (w - width) // 2, w - width):
        yield x + dx, y - _GAP_PX - height


def _origin(region: dict, width: int, height: int, page_w: int, page_h: int,
            avoid: tuple = (), bounds: dict | None = None):
    """Top-left of the gloss: the first spot beside the effect inside `bounds` (default the
    page) that covers none of the boxes in `avoid`, else the spot covering the least of them;
    with nothing to avoid, the right side pushed back inside the page when no side fits."""
    area = bounds or {"x": 0, "y": 0, "w": page_w, "h": page_h}
    best = None
    spots = list(_candidates(region, width, height))
    # With nothing to avoid, only the first spot on each side is tried, as it always was.
    for left, top in (spots if (avoid or bounds) else spots[::3]):
        if not (0 <= left and left + width <= page_w and 0 <= top and top + height <= page_h):
            continue
        outside = width * height - _overlap((left, top, width, height), area)
        cost = outside + sum(_overlap((left, top, width, height), other) for other in avoid)
        if cost == 0:
            return left, top
        if best is None or cost < best[0]:
            best = (cost, left, top)
    if best is not None and (avoid or bounds):
        return best[1], best[2]
    x, y, w = region["x"], region["y"], region["w"]
    return (min(max(0, x + w + _GAP_PX), max(0, page_w - width)),
            min(max(0, y), max(0, page_h - height)))


def draw_gloss(pil_img, region: dict, font_path: str | None = None, avoid: tuple = (),
               bounds: dict | None = None) -> None:
    """Sets `region`'s translation beside it. `avoid`: boxes the gloss must not cover (other
    lettering); `bounds`: the box it must stay in (the panel). A narrower wrap is tried
    when the first one has no free spot."""
    from PIL import ImageDraw, ImageFont
    from scanlate_render import _find_font
    path = _find_font(font_path, category="regular")
    size = int(min(max(min(region["w"], region["h"]) * 0.4, _MIN_PX), _MAX_PX))
    font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
    draw = ImageDraw.Draw(pil_img)
    page_w, page_h = pil_img.size
    line_h = int(size * 1.2)
    best = None
    for share in ((_MAX_WIDTH_SHARE, _MAX_WIDTH_SHARE / 2, _MAX_WIDTH_SHARE / 4) if (avoid or bounds)
                  else (_MAX_WIDTH_SHARE,)):
        lines = _wrap(draw, region["translated_text"].strip(), font, int(page_w * share))
        width = int(max(draw.textlength(line, font=font) for line in lines))
        left, top = _origin(region, width, line_h * len(lines), page_w, page_h, avoid, bounds)
        rect = (left, top, width, line_h * len(lines))
        area = bounds or {"x": 0, "y": 0, "w": page_w, "h": page_h}
        cost = width * line_h * len(lines) - _overlap(rect, area) + sum(_overlap(rect, o) for o in avoid)
        if best is None or cost < best[0]:
            best = (cost, lines, left, top)
        if cost == 0:
            break
    _, lines, left, top = best
    stroke = max(2, size // 8)
    for i, line in enumerate(lines):
        # A white face with a dark outline reads on light and dark art alike.
        draw.text((left, top + i * line_h), line, font=font, fill=(255, 255, 255),
                  stroke_width=stroke, stroke_fill=(0, 0, 0))
