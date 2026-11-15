"""Balloon text set in the roomiest rectangle (scanlate_rect_layout)."""
import numpy as np
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw

import scanlate_render


def _ellipse_mask(w, h):
    yy, xx = np.mgrid[0:h, 0:w]
    return ((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2 <= 1


def test_roomiest_rectangle_is_the_largest_all_inside_rectangle():
    mask = np.zeros((6, 8), dtype=bool)
    mask[1:5, 2:7] = True        # 4 x 5 = 20
    mask[5, 0:2] = True          # stray inside pixels elsewhere must not win
    assert scanlate_render._roomiest_rectangle(mask) == (1, 2, 5, 7)


def test_roomiest_rectangle_of_an_empty_mask_is_none():
    assert scanlate_render._roomiest_rectangle(np.zeros((4, 4), dtype=bool)) is None


def test_rectangle_layout_gives_every_line_the_same_span_inside_the_mask():
    mask = _ellipse_mask(220, 160)
    draw = ImageDraw.Draw(Image.new("RGB", (220, 160)))
    from PIL import ImageFont
    laid = scanlate_render._layout_in_rectangle(
        draw, "this is a fairly long line of translated text", mask, 20,
        lambda sz: ImageFont.load_default(sz))
    assert laid is not None
    _font, lines = laid
    assert len(lines) > 1
    assert len({(l, r) for _t, l, r, _top in lines}) == 1
    for _t, l, r, top in lines:
        assert mask[top, l] and mask[top, r - 1]


def test_a_rectangle_that_is_a_small_part_of_the_balloon_defers_to_the_outline():
    mask = np.zeros((100, 100), dtype=bool)
    mask[:, 45:55] = True
    mask[45:55, :] = True        # a cross: the best rectangle is a bar
    draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    from PIL import ImageFont
    assert scanlate_render._layout_in_rectangle(
        draw, "hello there", mask, 18, lambda sz: ImageFont.load_default(sz)) is None


def test_render_text_in_box_uses_the_rectangle_only_when_asked():
    mask = _ellipse_mask(220, 160)
    box = {"x": 0, "y": 0, "w": 220, "h": 160}
    text = "this is a fairly long line of translated text"

    def drawn(**kw):
        img = Image.new("RGB", (220, 160), "white")
        scanlate_render.render_text_in_box(img, box, text, font_size=20, mask=mask, **kw)
        return np.array(img)

    plain, rect = drawn(), drawn(rect_layout=True)
    assert not np.array_equal(plain, rect)
    top, left, bottom, right = scanlate_render._roomiest_rectangle(mask)
    ink = np.argwhere(rect.min(axis=2) < 128)
    assert ink[:, 0].min() >= top and ink[:, 0].max() < bottom
    assert ink[:, 1].min() >= left and ink[:, 1].max() < right
