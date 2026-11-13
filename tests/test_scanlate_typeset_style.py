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
