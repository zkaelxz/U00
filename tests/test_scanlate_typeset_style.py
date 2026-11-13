"""The extension's typeset look settings (alignment, minimum size, outline) in scanlate_render."""
import numpy as np
import pytest

pytest.importorskip("PIL")
from PIL import Image

import scanlate_render
from scanlate_render import TypesetStyle

BOX = {"x": 0, "y": 0, "w": 200, "h": 120}
TEXT = "Hello there friend"


def _render(style=None, **kw):
    image = Image.new("RGB", (200, 120), "white")
    report = {}
    mask = np.ones((120, 200), dtype=bool)
    scanlate_render.render_text_in_box(image, BOX, TEXT, font_size=24, mask=mask, report=report,
                                       **({"style": style} if style else {}), **kw)
    return np.asarray(image.convert("L")), report


def _ink_columns(pixels):
    cols = np.where((pixels < 128).any(axis=0))[0]
    return cols.min(), cols.max()


def test_default_style_is_the_plain_render():
    plain, _ = _render()
    styled, _ = _render(TypesetStyle())
    assert (plain == styled).all()


def test_alignment_moves_the_lines_left_and_right():
    left, _ = _render(TypesetStyle(align="left"))
    right, _ = _render(TypesetStyle(align="right"))
    assert _ink_columns(left)[0] < _ink_columns(right)[0]
    assert _ink_columns(right)[1] > _ink_columns(left)[1]


def test_minimum_font_size_stops_the_text_shrinking_below_it():
    tight = {"x": 0, "y": 0, "w": 60, "h": 30}
    sizes = {}
    for floor in (0, 14):
        image = Image.new("RGB", (60, 30), "white")
        report = {}
        scanlate_render.render_text_in_box(image, tight, "a long sentence that must shrink", font_size=24,
                                           report=report, style=TypesetStyle(min_font=floor))
        sizes[floor] = report["size"]
    assert sizes[0] < 14 <= sizes[14]


def test_outline_adds_dark_edge_pixels_around_white_text():
    def render(stroke):
        image = Image.new("RGB", (200, 120), (90, 90, 90))
        scanlate_render.render_text_in_box(image, BOX, TEXT, font_size=24, fill=(255, 255, 255),
                                           style=TypesetStyle(stroke=stroke))
        return np.asarray(image.convert("L"))
    assert (render(True) < 60).sum() > (render(False) < 60).sum() == 0


def test_a_bad_style_falls_back_to_defaults():
    style = TypesetStyle(min_font=999, align="sideways")
    assert style.min_font == 40 and style.align == "auto"


def test_the_bridge_reads_the_popup_style_and_ignores_junk():
    from services import extension_bridge_service as svc
    assert svc._typeset_style(None) is None
    assert svc._typeset_style("x") is None
    style = svc._typeset_style({"align": "right", "min_font": "12", "stroke": True})
    assert (style.align, style.min_font, style.stroke) == ("right", 12, True)
    assert svc._typeset_style({"min_font": "abc", "stroke": "yes"}).min_font == 0


def test_a_stronger_erase_clears_more_around_the_lettering():
    cv2 = pytest.importorskip("cv2")
    import scanlate_inpaint

    def fringe_left(grow):
        page = np.full((80, 120, 3), 255, np.uint8)
        cv2.putText(page, "HI", (30, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
        # a faint fringe 6px out, the kind of ghost the plain erase leaves behind
        page[20:60, 24:26] = 215
        scanlate_inpaint.inpaint_region("", {"x": 10, "y": 10, "w": 100, "h": 60}, image=page,
                                        backend="cv", grow=grow)
        return int((page[20:60, 24:26] < 250).sum())
    assert fringe_left(1.0) > 0
    assert fringe_left(2.0) < fringe_left(1.0)


def test_balloon_fill_flattens_speckle_inside_the_mask_only():
    from PIL import Image
    rng = np.random.default_rng(1)
    base = np.full((60, 60, 3), 240, np.uint8)
    base[rng.random((60, 60)) < 0.1] = 200
    image = Image.fromarray(base)
    mask = np.zeros((60, 60), dtype=bool)
    mask[10:50, 10:50] = True
    scanlate_render._fill_balloon(image, {"x": 0, "y": 0}, mask, 1.0)
    out = np.asarray(image)
    assert (out[10:50, 10:50] == 240).all()
    assert (out[0:5] == base[0:5]).all()


def test_the_bridge_reads_erase_and_fill_with_limits():
    from services import extension_bridge_service as svc
    style = svc._typeset_style({"erase_grow": "9", "bubble_fill": "250"})
    assert (style.erase_grow, style.bubble_fill) == (3.0, 100)


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", float("nan"), float("inf"), None, "abc", [], {}])
def test_non_finite_or_junk_numbers_fall_back_to_the_defaults(bad):
    style = TypesetStyle(min_font=bad, erase_grow=bad, bubble_fill=bad)
    assert (style.min_font, style.erase_grow, style.bubble_fill) == (0, 1.0, 0)


def test_the_bridge_survives_nan_and_inf_from_a_request():
    from services import extension_bridge_service as svc
    style = svc._typeset_style({"min_font": "inf", "erase_grow": "nan", "bubble_fill": "inf"})
    assert (style.min_font, style.erase_grow, style.bubble_fill) == (0, 1.0, 0)
