"""Balloon text colour: the original lettering's colour is sampled only when it is a real colour."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
PIL = pytest.importorskip("PIL")
from PIL import Image

import scanlate_render

BOX = {"x": 20, "y": 20, "w": 120, "h": 50}


def _page(background, ink, ink_bgr_rows=slice(35, 55), cols=slice(40, 120)):
    page = np.full((100, 160, 3), background, np.uint8)
    page[ink_bgr_rows, cols] = ink
    return page


def test_coloured_ink_on_a_light_balloon_is_sampled():
    blue_bgr = (200, 60, 30)
    assert scanlate_render.sampled_ink_color(_page(255, blue_bgr), BOX) == (30, 60, 200)


def test_plain_black_ink_stays_automatic():
    assert scanlate_render.sampled_ink_color(_page(255, (20, 20, 20)), BOX) is None


def test_slightly_tinted_black_ink_stays_automatic():
    assert scanlate_render.sampled_ink_color(_page(255, (40, 25, 20)), BOX) is None


def test_coloured_ink_on_a_dark_balloon_is_sampled():
    assert scanlate_render.sampled_ink_color(_page(20, (60, 60, 230)), BOX) == (230, 60, 60)


def test_white_ink_on_a_dark_balloon_stays_automatic():
    assert scanlate_render.sampled_ink_color(_page(20, (250, 250, 250)), BOX) is None


def test_a_region_with_almost_no_ink_stays_automatic():
    page = _page(255, (200, 60, 30), ink_bgr_rows=slice(40, 41), cols=slice(40, 45))
    assert scanlate_render.sampled_ink_color(page, BOX) is None


def test_a_hand_set_colour_beats_the_sampled_one():
    img = Image.new("RGB", (50, 50), "white")
    box = dict(BOX, text_color="#00ff00")
    assert scanlate_render._text_fill(img, box, (30, 60, 200)) == (0, 255, 0)


def _render(tmp_path, match_color):
    src, out = tmp_path / "page.png", str(tmp_path / "out.png")
    cv2.imwrite(str(src), _page(255, (200, 60, 30)))
    region = dict(BOX, kind="bubble", translated_text="Hello there", source_text="안녕", skip=0)
    scanlate_render.process_page(str(src), [region], out, match_color=match_color)
    return np.asarray(Image.open(out).convert("RGB")).astype(int)


def _blue_pixels(rgb):
    return int(((rgb[..., 2] - rgb[..., 0]) > 80).sum())


def test_the_page_is_lettered_in_the_sampled_colour_only_when_asked(tmp_path):
    assert _blue_pixels(_render(tmp_path, True)) > 0
    assert _blue_pixels(_render(tmp_path, False)) == 0
