"""Tilted original lettering: the angle is read from the ink and the English follows it."""

import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from PIL import Image, ImageDraw

import scanlate_tilt
from scanlate_render import render_text_in_box

BOX = {"x": 20, "y": 20, "w": 200, "h": 160}


def _page_with_blocks(angle, count=4, size=22, gap=34):
    """White page with `count` dark squares in a line through the box centre at `angle`."""
    page = np.full((200, 240, 3), 255, np.uint8)
    cx, cy = 120, 100
    for i in range(count):
        d = (i - (count - 1) / 2) * gap
        x, y = int(cx + d * math.cos(math.radians(angle))), int(cy + d * math.sin(math.radians(angle)))
        page[y - size // 2:y + size // 2, x - size // 2:x + size // 2] = 20
    return page


@pytest.mark.parametrize("angle", [-30, -12, 12, 30])
def test_tilted_single_line_is_measured(angle):
    assert scanlate_tilt.estimate_text_angle(_page_with_blocks(angle), BOX) == pytest.approx(angle, abs=4)


def test_level_line_has_no_angle():
    assert scanlate_tilt.estimate_text_angle(_page_with_blocks(7), BOX) == 0.0


def test_vertical_column_has_no_angle():
    assert scanlate_tilt.estimate_text_angle(_page_with_blocks(85), BOX) == 0.0


def test_staggered_level_lines_have_no_angle():
    """Two level lines, the second indented, read as a slanted blob to a plain axis fit."""
    page = np.full((200, 240, 3), 255, np.uint8)
    for col in range(30, 190, 30):
        page[60:84, col:col + 22] = 20
    for col in range(60, 210, 30):
        page[110:134, col:col + 22] = 20
    assert scanlate_tilt.estimate_text_angle(page, BOX) == 0.0


def test_tilted_line_cut_tight_by_the_box_is_still_measured():
    page = _page_with_blocks(25)
    ys, xs = np.nonzero(page[:, :, 0] < 128)
    tight = {"x": int(xs.min()), "y": int(ys.min()), "w": int(xs.max() - xs.min() + 1),
             "h": int(ys.max() - ys.min() + 1)}
    assert scanlate_tilt.estimate_text_angle(page, tight) == pytest.approx(25, abs=4)


def test_long_mark_along_the_box_edge_is_not_lettering():
    page = _page_with_blocks(25)
    page[190:198, :] = 20
    assert scanlate_tilt.estimate_text_angle(page, BOX) == pytest.approx(25, abs=4)


def test_block_of_several_lines_has_no_angle():
    page = np.full((200, 240, 3), 255, np.uint8)
    for row in (60, 100, 140):
        for col in (70, 110, 150):
            page[row - 10:row + 10, col - 10:col + 10] = 20
    assert scanlate_tilt.estimate_text_angle(page, BOX) == 0.0


def _draw(text):
    def draw_text(target, box, size):
        render_text_in_box(target, box, text, font_size=size, fill=(0, 0, 0))
    return draw_text


def test_tilted_text_is_drawn_at_the_angle():
    page = Image.new("RGB", (240, 200), (255, 255, 255))
    assert scanlate_tilt.draw_tilted(page, BOX, None, 20.0, 30, _draw("Education"))
    ys, xs = np.nonzero(np.asarray(page.convert("L")) < 128)
    axis = np.linalg.eigh(np.cov(np.vstack([xs, ys])))[1][:, 1]
    assert (math.degrees(math.atan2(axis[1], axis[0])) + 90) % 180 - 90 == pytest.approx(20, abs=5)


def test_text_that_would_leave_the_balloon_is_left_to_the_level_layout():
    page = Image.new("RGB", (240, 200), (255, 255, 255))
    before = np.asarray(page).copy()
    narrow = np.zeros((BOX["h"], BOX["w"]), bool)
    narrow[70:90, :] = True
    assert not scanlate_tilt.draw_tilted(page, BOX, narrow, 40.0, 30, _draw("Education"))
    assert (np.asarray(page) == before).all()


def test_light_text_keeps_its_colour_when_tilted():
    page = Image.new("RGB", (240, 200), (0, 0, 0))
    draw_text = lambda target, box, size: render_text_in_box(target, box, "Hello", font_size=size,
                                                              fill=(255, 255, 255))
    assert scanlate_tilt.draw_tilted(page, BOX, None, -15.0, 30, draw_text)
    assert np.asarray(page).max() == 255
