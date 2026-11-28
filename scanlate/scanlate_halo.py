"""Halo for lettering set over artwork: picks light or dark lettering from the art around it and paints a soft contrasting glow under it, so the smear a cleaned patch leaves is not seen."""
import numpy as np
from PIL import Image, ImageChops, ImageFilter

from scanlate import scanlate_tuning


def over_art(page, box: dict) -> bool:
    """True when the cleaned area under `box` is textured: lettering there shows the smear the
    erase left. A flat balloon, light or dark, is plain paper and needs no halo."""
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(page.width, box["x"] + box["w"]), min(page.height, box["y"] + box["h"])
    if x1 - x0 < 4 or y1 - y0 < 4:
        return False
    crop = page.crop((x0, y0, x1, y1))
    return float(np.asarray(crop.convert("L")).std()) >= scanlate_tuning.HALO_MIN_TEXTURE


def colours(page, box: dict) -> tuple:
    """(lettering colour, outline and halo colour): light lettering on dark art and the reverse,
    judged from the cleaned area the lettering sits on, not the margin round it, which can be paper
    while the lettering sits on a dark disc."""
    x0, y0 = max(0, box["x"]), max(0, box["y"])
    x1, y1 = min(page.width, box["x"] + box["w"]), min(page.height, box["y"] + box["h"])
    gray = np.asarray(page.crop((x0, y0, max(x0 + 1, x1), max(y0 + 1, y1))).convert("L"))
    if float(np.median(gray)) < scanlate_tuning.HALO_LIGHT_TEXT_BELOW:
        return (246, 246, 246), (14, 14, 20)
    return (18, 18, 24), (250, 250, 250)


def paint(page, text_mask: Image.Image, edge: tuple, size: int, box: dict) -> None:
    """Pastes the soft glow of `edge` colour behind the lettering whose coverage is `text_mask` ("L"),
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
    page.paste(Image.new("RGB", halo.size, edge), (x0, y0), ImageChops.multiply(halo, inside))
