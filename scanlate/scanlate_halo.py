"""Halo for lettering set over artwork: picks light or dark lettering from the art around it and paints a soft contrasting glow under it, so the smear a cleaned patch leaves is not seen."""
import numpy as np
from PIL import Image, ImageChops, ImageFilter

from scanlate import scanlate_tuning


def over_art(page, box: dict) -> bool:
    """True when `box` holds floating lettering on artwork: the cleaned area is textured (the erase
    left a smear the lettering would show), and it is not a balloon. A balloon the outline finder
    missed (a spiked burst) shows as a box whose corners are paper or other art and whose centre is
    its fill; such lettering sits in its own fill and needs no halo."""
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(page.width, box["x"] + box["w"]), min(page.height, box["y"] + box["h"])
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    gray = np.asarray(page.crop((x0, y0, x1, y1)).convert("L")).astype(float)
    if float(gray.std()) < scanlate_tuning.HALO_MIN_TEXTURE:
        return False
    h, w = gray.shape
    ch, cw = max(2, int(h * 0.12)), max(2, int(w * 0.12))
    corners = [gray[:ch, :cw], gray[:ch, -cw:], gray[-ch:, :cw], gray[-ch:, -cw:]]
    centre = float(np.median(gray[int(h * 0.3):int(h * 0.7), int(w * 0.3):int(w * 0.7)]))
    apart = sum(abs(float(np.median(c)) - centre) >= scanlate_tuning.HALO_BALLOON_CONTRAST for c in corners)
    return apart < 2


def colours(page, box: dict) -> tuple:
    """(lettering colour, outline and halo colour): light lettering on dark art and the reverse,
    judged from the cleaned area the lettering sits on, not the margin round it, which can be paper
    while the lettering sits on a dark disc."""
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(page.width, box["x"] + box["w"]), min(page.height, box["y"] + box["h"])
    gray = np.asarray(page.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))).convert("L"))
    if float(np.median(gray)) < scanlate_tuning.HALO_LIGHT_TEXT_BELOW:
        return (236, 236, 236), (14, 14, 20)
    return (18, 18, 24), (250, 250, 250)


def paint(page, text_mask: Image.Image, edge: tuple, size: int, box: dict) -> None:
    """Pastes a soft glow in the surrounding background colour, leaning slightly to `edge`, behind the lettering whose coverage is `text_mask` ("L"),
    limited to `box` with a feathered edge so it does not tint the art beside a disc the box fits.
    Only the box and the glow's reach are blurred: a long page has many such regions."""
    reach = max(2, size // 5)
    margin = 3 * reach
    x0, y0 = max(0, box["x"] - margin), max(0, box["y"] - margin)
    x1, y1 = min(page.width, box["x"] + box["w"] + margin), min(page.height, box["y"] + box["h"] + margin)
    if x1 <= x0 or y1 <= y0:
        return
    window = (x0, y0, x1, y1)
    halo = text_mask.crop(window).filter(ImageFilter.MaxFilter(2 * (reach // 2) + 1)).filter(ImageFilter.GaussianBlur(reach))
    halo = ImageChops.add(halo, halo).point(lambda v: int(v * scanlate_tuning.HALO_STRENGTH))
    inside = Image.new("L", halo.size, 0)
    inside.paste(255, (box["x"] - x0, box["y"] - y0, box["x"] + box["w"] - x0, box["y"] + box["h"] - y0))
    inside = inside.filter(ImageFilter.GaussianBlur(reach / 2))
    # The glow takes the colour of the art round it, blurred so the smear the erase left is not carried
    # into it, with only a slight lean toward the contrasting colour: a dark or white glow read as a
    # patch laid over the page.
    background = page.crop(window).filter(ImageFilter.GaussianBlur(max(reach * 3, size // 2)))
    glow = Image.blend(background, Image.new("RGB", halo.size, edge), scanlate_tuning.HALO_TINT)
    page.paste(glow, (x0, y0), ImageChops.multiply(halo, inside))
    if sum(edge) < 3 * 128:
        # Light lettering gets a tight dark shadow beneath the soft glow, the way official releases
        # lift it off busy art; dark lettering has the light glow only.
        shift = max(1, size // 14)
        shadow = Image.new("L", text_mask.size, 0)
        shadow.paste(text_mask, (shift, shift))
        shadow = shadow.crop(window).filter(ImageFilter.GaussianBlur(max(1.0, reach / 2)))
        shadow = shadow.point(lambda v: int(v * scanlate_tuning.HALO_SHADOW))
        page.paste(Image.new("RGB", shadow.size, edge), (x0, y0), ImageChops.multiply(shadow, inside))
