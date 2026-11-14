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


def _faded_page(top_bgr, bottom_bgr):
    page = np.full((100, 160, 3), 255, np.uint8)
    for row in range(35, 55):
        share = (row - 35) / 19
        page[row, 40:120] = [round(t * (1 - share) + b * share) for t, b in zip(top_bgr, bottom_bgr)]
    return page


def test_lettering_that_fades_between_two_colours_is_sampled_as_a_fade():
    fade = scanlate_render.sampled_ink_fade(_faded_page((30, 120, 230), (200, 40, 120)), BOX)
    assert fade is not None
    top, bottom = fade
    assert top[0] > bottom[0] and top[2] < bottom[2]  # orange-ish above, purple-ish below


@pytest.mark.parametrize("ink", [(30, 60, 200), (20, 20, 20), (250, 250, 250)])
def test_single_colour_lettering_is_not_a_fade(ink):
    assert scanlate_render.sampled_ink_fade(_faded_page(ink, ink), BOX) is None


def test_art_touching_the_box_edge_is_not_read_as_a_fade():
    page = _faded_page((30, 60, 200), (30, 60, 200))
    page[:, :10] = (40, 160, 40)  # a leaf running down the box edge
    page[:15, :] = (60, 40, 220)  # a rose along the top
    assert scanlate_render.sampled_ink_fade(page, BOX) is None


def test_two_unrelated_colours_with_nothing_between_are_not_a_fade():
    page = np.full((100, 160, 3), 255, np.uint8)
    page[30:38, 40:120] = (30, 60, 200)
    page[38:47, 40:120] = (200, 200, 60)
    page[47:55, 40:120] = (40, 170, 40)
    assert scanlate_render.sampled_ink_fade(page, BOX) is None


def test_a_two_line_balloon_with_a_step_between_the_lines_is_still_a_fade():
    page = np.full((100, 160, 3), 255, np.uint8)
    page[20:40, 40:120] = (160, 100, 50)
    page[60:80, 40:120] = (110, 70, 40)
    assert scanlate_render.sampled_ink_fade(page, BOX) is not None


def test_the_fade_recolours_the_drawn_lettering_from_top_to_bottom():
    from PIL import ImageDraw
    before = Image.new("RGB", (120, 60), "white")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((10, 5, 110, 55), fill=(230, 120, 30))
    scanlate_render._fade_lettering(before, after, (230, 120, 30), (120, 40, 200))
    assert after.getpixel((60, 6))[2] < 80      # still orange at the top
    assert after.getpixel((60, 54))[2] > 150    # purple at the bottom
    assert after.getpixel((2, 2)) == (255, 255, 255)  # paper is left alone


def test_a_hand_set_colour_beats_a_sampled_fade(tmp_path):
    src, out = tmp_path / "page.png", str(tmp_path / "out.png")
    cv2.imwrite(str(src), _faded_page((30, 120, 230), (200, 40, 120)))
    region = dict(BOX, kind="bubble", translated_text="Hello there", source_text="안녕", skip=0,
                  text_color="#00ff00")
    scanlate_render.process_page(str(src), [region], out, match_color=True)
    rendered = np.asarray(Image.open(out).convert("RGB")).astype(int)
    green = (rendered[:, :, 1] > 200) & (rendered[:, :, 0] < 60) & (rendered[:, :, 2] < 60)
    assert green.any()


def test_a_faded_original_is_lettered_as_a_fade(tmp_path):
    src, out = tmp_path / "page.png", str(tmp_path / "out.png")
    page = np.full((200, 320, 3), 255, np.uint8)
    for row in range(50, 110):
        share = (row - 50) / 59
        page[row, 50:230] = [round(t * (1 - share) + b * share) for t, b in zip((30, 120, 230), (200, 40, 120))]
    cv2.imwrite(str(src), page)
    region = {"x": 20, "y": 20, "w": 240, "h": 120, "kind": "bubble", "translated_text": "Hello there",
              "source_text": "안녕", "skip": 0}
    scanlate_render.process_page(str(src), [region], out, match_color=True)
    rendered = np.asarray(Image.open(out).convert("RGB")).astype(int)
    coloured = (rendered.max(axis=2) - rendered.min(axis=2)) > 60
    rows = np.where(coloured.any(axis=1))[0]
    top = rendered[rows.min() + 1][coloured[rows.min() + 1]].mean(axis=0)
    bottom = rendered[rows.max() - 1][coloured[rows.max() - 1]].mean(axis=0)
    assert top[0] > bottom[0] + 30 and bottom[2] > top[2] + 30
