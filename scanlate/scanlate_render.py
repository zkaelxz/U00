"""
scanlate_render.py -- the page render pass for the comic typeset.

Erases the original lettering of every region with translated text, then letters
it again: balloons through scanlate_text_layout, captions through scanlate_art_text,
sound effects and art signs as glosses beside them. Faces live in scanlate_fonts and
colours in scanlate_ink.
"""

import os
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from lib.atomic_write import replace_file

from scanlate import scanlate_art_text
from scanlate import scanlate_halo
from scanlate import scanlate_sfx
from scanlate import scanlate_tilt
from scanlate import scanlate_tuning
from scanlate.scanlate_balloons import balloon_outside_mask, bubble_erase_mask, bubble_shape_mask
from scanlate.scanlate_page_decode import decode_page, page_gray
from scanlate.scanlate_fonts import _CJK_PUNCTUATION, _PLAIN, TypesetStyle, _find_font, _note_missing_cjk_font
from scanlate.scanlate_ink import _fade_lettering, _sampled_inks, _text_fill, chosen_color
from scanlate.scanlate_inpaint import (
    erase_box, InpaintModelUnavailable, inpaint_region, widen_to_dark_strip, is_art_mistaken_for_sign, sign_text_line,
    _lettering_mask,
)
from scanlate.scanlate_lama import lama_ml_weights_cached
from scanlate.scanlate_regions import SHAPE_FITTED_KINDS, region_excluded_from_auto
from scanlate.scanlate_text_layout import _dark_under, _layout_in_mask, _symmetrised, render_text_in_box
from scanlate.scanlate_watermark import is_site_mark_region, logo_box


# Shown when a page is cleaned without the LaMa-manga weights. "auto" then
# picks OpenCV without raising, so nothing else would tell the person why
# faint ghosts of the original lettering show under the English.
LAMA_MISSING_NOTE = ("The LaMa-manga cleanup model is not installed, so the original text was "
                     "erased with plain OpenCV and may show through. To install it, open Diagnostics, then "
                     "Models, and press Download next to LaMa-manga (at the PC; about 200 MB), "
                     "then redo the page.")


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


def _tilts_and_lines(page, regions: list):
    """Per region its text angle, and for a sign whose lettering is one line on a pale band the
    (strip, angle, size) of that line, which also gives the angle: the skew detector is thrown off by
    the busy art round such a band."""
    lines = [sign_text_line(page, b) if b.get("kind") == "sign" else None for b in regions]
    # A few columns of vertical lettering can score best at a slant; the English that replaces
    # them reads across, so it is set level.
    angles = [line[1] if line else 0.0 if b.get("orientation") == "vertical"
              else scanlate_tilt.estimate_text_angle(page, b)
              for line, b in zip(lines, regions)]
    return angles, lines


def sign_fitted_size(page, box: dict, text: str, custom_fonts: dict | None = None) -> int | None:
    """The font size `text` would be set at along the one-line band behind a sign, or None when
    the sign has no such band (one below the smallest size when it fits at none). It lays the text out the way _letter_balloons does, so a text
    too long for its band is seen before it is typeset."""
    line = sign_text_line(page, box)
    if line is None:
        return None
    corridor, angle, glyph = line
    level = Image.fromarray(corridor.astype(np.uint8) * 255).rotate(angle, resample=Image.Resampling.NEAREST, expand=True)
    width, height = level.size
    font = _find_font(None, category=box.get("font_category") or "regular", custom_fonts=custom_fonts)
    laid_out = _layout_in_mask(ImageDraw.Draw(Image.new("RGB", (width, height))), text.translate(_CJK_PUNCTUATION),
                               np.asarray(level) > 127, glyph,
                               lambda size: ImageFont.truetype(font, size) if font else ImageFont.load_default(size))
    # No layout in the band at any size: the typesetter then falls back to the whole box, so the
    # text is as good as unreadable there.
    return laid_out[0].size if laid_out else scanlate_tuning.TYPESET_SMALLEST_PX - 1


def _caption_styles(page, captions: list) -> list:
    """Each caption's sampled style, with its hand-set colour over the sampled fill."""
    styles = [scanlate_art_text.caption_style(page, b) for b in captions]
    return [dict(s, fill=chosen_color(b)) if chosen_color(b) else s for b, s in zip(captions, styles)]


def _letter_rooms(page, regions: list) -> list:
    """Each region's box cut between its rules; a balloon's own shape bounds its text, and the
    coloured rings of a glow bubble would read as rules and cut it to a strip."""
    return [scanlate_art_text.between_rules(page, b)
            if (b.get("kind") or "bubble") not in SHAPE_FITTED_KINDS else b for b in regions]


def _in_rooms(regions: list, rooms: list) -> list:
    """`regions` with each one's box replaced by its room. The rooms are read before the erase:
    a credit line sits between two red rules and its English belongs there, not across a rule."""
    return [{**b, **room} for b, room in zip(regions, rooms)]


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
            (b.get("kind") or "bubble") in (*SHAPE_FITTED_KINDS, "sign") for b in to_render) else None)
        # Lettering on the artwork is painted out and re-lettered in its own
        # style (scanlate_art_text); bubbles keep the balloon path below.
        captions = [b for b in to_render if b.get("kind") == "caption"]
        to_render = [b for b in to_render if b.get("kind") != "caption"]
        to_render, art_signs = _split_art_signs(page, to_render)
        caption_styles = _caption_styles(page, captions)
        caption_rooms = [scanlate_art_text.clear_of_decoration(page, b) for b in captions]
        letter_rooms = _letter_rooms(page, to_render)
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
        for b, style, room in zip(captions, caption_styles, caption_rooms):
            scanlate_art_text.draw_caption(pil_img, room, b["translated_text"], style,
                                           font_path=font_path)
        _note_missing_cjk_font(notes, to_render)
        _letter_balloons(pil_img, image_path, _in_rooms(to_render, letter_rooms), gray,
                         font_path, custom_fonts, inks, rect_layout, look, angles, fades, lines)
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

    def draw(target, b, shape_mask, size, ink, report=None, forced=None, outlined=False):
        # Only a mask that centring would rebuild needs the dark pixels, so other balloons are
        # drawn exactly as before.
        extra: dict[str, Any] = ({"dark": _dark_under(gray, b, shape_mask.shape)}
                                 if shape_mask is not None and gray is not None and _symmetrised(shape_mask) is not None
                                 else {})
        render_text_in_box(target, _lettering_box(gray, b) if shape_mask is None else b, b["translated_text"],
                           font_size=size, font_path=font_path,
                           font_category=b.get("font_category") or "regular",
                           custom_fonts=custom_fonts, mask=shape_mask, rect_layout=rect_layout,
                           style=style.outlined() if outlined else style,
                           fill=forced or _text_fill(target, b, ink), report=report, **extra)

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
        # A tilted balloon keeps the plain look: the halo is cut from the unrotated lettering.
        if not tilted:
            if style.halo and m is None and scanlate_halo.over_art(pil_img, b):
                _draw_with_halo(pil_img, b, m, size, draw)
            else:
                draw(pil_img, b, m, size, ink)
        if fade:
            lettered = pil_img.crop(region)
            _fade_lettering(before, lettered, *fade)
            pil_img.paste(lettered, region[:2])


def _lettering_box(gray, b: dict) -> dict:
    """`b` narrowed to where the original lettering stood, for a sign whose box is the round or
    spiked balloon it sits in: English wrapped to the whole box ran past a rim narrower than the box.
    Marks that touch the box edge or are huge (the rim, art) are not lettering. `b` itself when the
    lettering is not clearly narrower, or when none is found."""
    if gray is None or (b.get("kind") or "bubble") in SHAPE_FITTED_KINDS:
        return b
    import cv2
    x0, y0 = max(0, b["x"]), max(0, b["y"])
    roi = gray[y0:b["y"] + b["h"], x0:b["x"] + b["w"]]
    if roi.shape[0] < 20 or roi.shape[1] < 20:
        return b
    count, _, stats, _ = cv2.connectedComponentsWithStats((_lettering_mask(roi) > 0).astype(np.uint8), connectivity=8)
    rh, rw = roi.shape
    marks = [stats[i] for i in range(1, count)
             if stats[i][0] > 0 and stats[i][1] > 0 and stats[i][0] + stats[i][2] < rw and stats[i][1] + stats[i][3] < rh
             and stats[i][4] < scanlate_tuning.LETTERING_BOX_MAX_MARK_SHARE * rw * rh and stats[i][3] >= 8]
    if not marks:
        return b
    left, right = min(int(m[0]) for m in marks), max(int(m[0] + m[2]) for m in marks)
    top, bottom = min(int(m[1]) for m in marks), max(int(m[1] + m[3]) for m in marks)
    pad_x, pad_y = int((right - left) * scanlate_tuning.LETTERING_BOX_PAD), int((bottom - top) * scanlate_tuning.LETTERING_BOX_PAD_Y)
    new_left, new_right = max(0, left - pad_x), min(rw, right + pad_x)
    new_top, new_bottom = max(0, top - pad_y), min(rh, bottom + pad_y)
    narrow = new_right - new_left <= scanlate_tuning.LETTERING_BOX_MAX_SHARE * rw
    short = new_bottom - new_top <= scanlate_tuning.LETTERING_BOX_MAX_SHARE * rh
    if not (narrow or short):
        return b
    return dict(b, x=x0 + new_left if narrow else b["x"], w=new_right - new_left if narrow else b["w"],
                y=y0 + new_top if short else b["y"], h=new_bottom - new_top if short else b["h"])


def _draw_with_halo(pil_img, b: dict, shape_mask, size: int, draw) -> None:
    """Lettering over art: a soft glow, then the text outlined, in light or dark chosen from the art
    around it. `draw` is the balloon lettering closure, called with a fixed colour and a black canvas to
    learn where the glyphs fall."""
    canvas = Image.new("RGB", pil_img.size, (0, 0, 0))
    draw(canvas, b, shape_mask, size, None, forced=(255, 255, 255))
    text, edge = scanlate_halo.colours(pil_img, b)
    scanlate_halo.paint(pil_img, canvas.convert("L").point(lambda v: 255 if v > 0 else 0), edge, size, b)
    draw(pil_img, b, shape_mask, size, None, forced=text, outlined=True)


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
    from scanlate.scanlate_panels import detect_panels
    from scanlate.scanlate_regions import _panel_for_box
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
    if b.get("kind") == "sign":
        b = widen_to_dark_strip(page, b)
    inpaint_region(image_path, b, image=page, inside=inside, spare_art=shape_fitted,
                   on_art=b.get("kind") == "sign", grow=grow)
