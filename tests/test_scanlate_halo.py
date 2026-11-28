import numpy as np
from PIL import Image

from scanlate import scanlate_halo, scanlate_render
from scanlate.scanlate_fonts import TypesetStyle

BOX = {"x": 20, "y": 20, "w": 160, "h": 60}


def _page(grey, noise=0, seed=1):
    rng = np.random.default_rng(seed)
    arr = np.full((100, 200, 3), grey, np.float32) + rng.normal(0, noise, (100, 200, 3))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def test_a_flat_area_needs_no_halo_and_a_textured_one_does():
    assert not scanlate_halo.over_art(_page(240), BOX)
    assert scanlate_halo.over_art(_page(120, noise=30), BOX)


def test_a_balloon_the_finder_missed_is_not_floating_text():
    page = _page(255)
    arr = np.asarray(page).copy()
    arr[10:90, 40:160] = np.random.default_rng(2).integers(60, 130, (80, 120, 3), dtype=np.uint8)
    disc_on_paper = Image.fromarray(arr)
    assert not scanlate_halo.over_art(disc_on_paper, dict(BOX, x=20, y=10, w=160, h=80))


def test_lettering_is_light_on_dark_art_and_dark_on_light_art():
    light_text, light_edge = scanlate_halo.colours(_page(40), BOX)
    dark_text, dark_edge = scanlate_halo.colours(_page(220), BOX)
    assert sum(light_text) > 600 and sum(light_edge) < 100
    assert sum(dark_text) < 100 and sum(dark_edge) > 600


def test_the_halo_darkens_the_art_round_light_lettering_and_stays_in_the_box():
    page = _page(90, noise=25)
    before = np.asarray(page).astype(int).sum()
    mask = Image.new("L", page.size, 0)
    mask.paste(255, (60, 40, 140, 60))
    scanlate_halo.paint(page, mask, (14, 14, 20), 20, BOX)
    after = np.asarray(page).astype(int)
    assert after.sum() < before
    assert np.array_equal(after[:, :5], np.asarray(_page(90, noise=25)).astype(int)[:, :5])


def _typeset(style):
    page = _page(80, noise=25)
    region = dict(BOX, translated_text="Placeholder line one", kind="sign", font_size=24)
    scanlate_render._letter_balloons(page, "x.png", [region], None, None, None, [None], style=style)
    return np.asarray(page).astype(int)


def test_halo_is_on_by_default_and_halo_false_gives_the_plain_render():
    default = _typeset(TypesetStyle())
    assert np.array_equal(default, _typeset(TypesetStyle(halo=True)))
    assert not np.array_equal(default, _typeset(TypesetStyle(halo=False)))


def test_a_box_wholly_off_the_page_is_not_art_and_paints_nothing():
    off = dict(BOX, x=500)
    page = _page(120, noise=30)
    assert not scanlate_halo.over_art(page, off)
    scanlate_halo.paint(page, Image.new("L", page.size, 255), (0, 0, 0), 20, off)
    assert np.array_equal(np.asarray(page), np.asarray(_page(120, noise=30)))


def test_a_sign_is_set_where_its_lettering_stood_when_the_box_is_the_wide_balloon_round_it():
    gray = np.full((200, 400), 200, np.uint8)
    gray[:, :6] = 0                                  # the rim, touching the box edge
    for row in (60, 100, 140):                        # three lines of lettering in the middle
        gray[row:row + 18, 140:260] = 20
    box = {"x": 0, "y": 0, "w": 400, "h": 200, "kind": "sign"}
    narrowed = scanlate_render._lettering_box(gray, box)
    assert narrowed["x"] > 60 and narrowed["x"] + narrowed["w"] < 340
    assert scanlate_render._lettering_box(gray, dict(box, kind="bubble")) == dict(box, kind="bubble")
    assert scanlate_render._lettering_box(None, box) == box
