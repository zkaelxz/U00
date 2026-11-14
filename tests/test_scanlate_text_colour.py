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


def test_anti_aliased_edges_do_not_wash_out_the_sampled_colour():
    from PIL import ImageDraw, ImageFont
    font = ImageFont.load_default(24)
    img = Image.new("RGB", (200, 80), "white")
    ImageDraw.Draw(img).text((10, 20), "Yes Hello", font=font, fill=(214, 130, 40))
    page = np.asarray(img)[:, :, ::-1].copy()
    assert scanlate_render.sampled_ink_color(page, {"x": 0, "y": 0, "w": 200, "h": 80}) == (214, 130, 40)


def test_a_dent_in_the_balloon_does_not_pull_the_text_off_centre():
    mask = np.zeros((60, 100), bool)
    mask[5:55, 5:95] = True
    mask[15:45, 75:95] = False  # hatching bites the right edge
    assert scanlate_render._without_small_dents(mask)[30, 90]


def test_the_notch_between_two_lobes_is_kept():
    mask = np.zeros((60, 100), bool)
    mask[5:55, 5:40] = True
    mask[5:55, 60:95] = True
    assert not scanlate_render._without_small_dents(mask)[30, 50]
    assert scanlate_render._without_small_dents(None) is None


def _round_balloon(hatched_corner):
    yy, xx = np.mgrid[:325, :311]
    mask = ((xx - 155) / 150.0) ** 2 + ((yy - 162) / 158.0) ** 2 <= 1
    if hatched_corner:
        mask &= (xx - 155) + (155 - yy) < 40  # hatching cut off the upper right on a diagonal
    return mask


def _line_centres(mask, rect_layout):
    from PIL import Image, ImageDraw, ImageFont
    draw = ImageDraw.Draw(Image.new("RGB", (311, 325)))
    laid = scanlate_render._layout_balloon_text(
        draw, "Me? Why?", mask, 28, lambda sz: ImageFont.load_default(sz), rect_layout)
    return [(left + right) / 2 for _, left, right, _ in laid[1]]


def test_hatching_cut_off_one_side_does_not_pull_the_text_off_the_balloon_centre():
    for rect_layout in (False, True):
        centres = _line_centres(_round_balloon(True), rect_layout)
        assert all(abs(c - 155) < 4 for c in centres), (rect_layout, centres)


def test_a_cut_that_leaves_the_mask_nearly_full_width_is_still_seen():
    mask = _round_balloon(False)
    yy, xx = np.mgrid[:325, :311]
    mask &= (xx - 155) + (155 - yy) < 120  # shallower cut: the bounding box stays full width
    cols = np.nonzero(mask.any(axis=0))[0]
    assert cols[-1] - cols[0] > 0.9 * 311
    assert scanlate_render._symmetrised(mask) is not None


def test_a_balloon_that_is_already_centred_is_laid_out_as_before():
    mask = _round_balloon(False)
    assert scanlate_render._symmetrised(mask) is None
