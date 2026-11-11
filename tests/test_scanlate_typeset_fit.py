"""Typesetting fit: translated text stays inside its box. Pillow only (no
OpenCV), so these run on the core install CI uses."""

import numpy as np
import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image

import scanlate_render

BOX = {"x": 100, "y": 20, "w": 90, "h": 60}


def _ink_columns(img):
    dark = np.asarray(img.convert("L")) < 128
    cols = np.flatnonzero(dark.any(axis=0))
    return int(cols.min()), int(cols.max())


def test_a_word_wider_than_the_box_is_shrunk_to_fit_it():
    img = Image.new("RGB", (300, 100), "white")
    scanlate_render.render_text_in_box(img, dict(BOX), "Unbelievable!", font_size=18)
    left, right = _ink_columns(img)
    assert left >= BOX["x"] and right < BOX["x"] + BOX["w"], (left, right)


def test_without_a_system_font_the_text_still_shrinks_to_fit(monkeypatch):
    monkeypatch.setattr(scanlate_render, "_find_font", lambda *a, **k: None)
    box = dict(BOX, w=60)
    img = Image.new("RGB", (300, 100), "white")
    scanlate_render.render_text_in_box(img, box, "Unbelievable!", font_size=18)
    left, right = _ink_columns(img)
    assert left >= box["x"] and right < box["x"] + box["w"], (left, right)


def test_text_that_fits_keeps_its_size():
    big, small = Image.new("RGB", (300, 100), "white"), Image.new("RGB", (300, 100), "white")
    scanlate_render.render_text_in_box(big, dict(BOX, w=180), "Hi", font_size=18)
    scanlate_render.render_text_in_box(small, dict(BOX, w=180), "Hi", font_size=10)
    width = lambda img: _ink_columns(img)[1] - _ink_columns(img)[0]
    assert width(big) > width(small)


def test_a_long_word_in_a_narrow_balloon_is_hyphenated_instead_of_shrunk_tiny(monkeypatch):
    mask = np.ones((120, 48), dtype=bool)
    img = Image.new("RGB", (200, 150), "white")
    box = {"x": 10, "y": 10, "w": 48, "h": 120}
    calls = []
    real = scanlate_render._layout_at_size

    def spy(draw, words, mask_, mh, font, hyphenate):
        out = real(draw, words, mask_, mh, font, hyphenate)
        if out is not None:
            calls.append((font.size, [ln for ln, *_ in out[1]]))
        return out

    monkeypatch.setattr(scanlate_render, "_layout_at_size", spy)
    scanlate_render.render_text_in_box(img, box, "Unbelievable extraordinary", font_size=18, mask=mask)
    size, lines = calls[0]
    assert size >= 13, (size, lines)
    assert any(ln.endswith("-") for ln in lines), lines
    assert "".join(ln.rstrip("-") for ln in lines).replace(" ", "") == "Unbelievableextraordinary"


@pytest.mark.parametrize("text", [
    "Unbelievable",
    "Unbelievable extraordinary",
])
def test_rect_fallback_hyphenates_a_long_word_instead_of_drawing_it_wider_than_the_box(text):
    box = {"x": 100, "y": 10, "w": 48, "h": 120}
    img = Image.new("RGB", (300, 150), "white")
    scanlate_render.render_text_in_box(img, dict(box), text, font_size=18)
    left, right = _ink_columns(img)
    assert left >= box["x"] and right < box["x"] + box["w"], (left, right)


def test_rect_fallback_keeps_normal_text_inside_the_box():
    box = {"x": 100, "y": 10, "w": 120, "h": 80}
    img = Image.new("RGB", (300, 120), "white")
    scanlate_render.render_text_in_box(img, dict(box), "Hello there, how are you?", font_size=18)
    dark = np.asarray(img.convert("L")) < 128
    rows, cols = np.flatnonzero(dark.any(axis=1)), np.flatnonzero(dark.any(axis=0))
    assert cols.min() >= box["x"] and cols.max() < box["x"] + box["w"]
    assert rows.min() >= box["y"] and rows.max() < box["y"] + box["h"]


def test_a_hyphen_split_breaks_between_syllables_and_leaves_three_letters_after_it():
    fits = lambda head: len(head) <= 6
    assert scanlate_render._hyphen_split("Housework?", fits) == ("House-", "work?")
    # The tail "rk?" has two letters, so that cut is refused.
    assert scanlate_render._hyphen_split("Housewo", lambda head: len(head) <= 3) is None


def test_a_word_is_not_cut_into_three_pieces_while_a_smaller_size_would_avoid_it():
    assert scanlate_render._split_twice(["Housew-", "or-", "k?"])
    assert not scanlate_render._split_twice(["House-", "work?", "Hi-", "ya"])


def test_a_narrow_balloon_never_shows_a_word_split_across_two_hyphens():
    mask = np.ones((180, 44), dtype=bool)
    img = Image.new("RGB", (120, 200), "white")
    box = {"x": 10, "y": 10, "w": 44, "h": 180}
    seen = []
    real = scanlate_render._layout_at_size

    def spy(draw, words, mask_, mh, font, hyphenate):
        out = real(draw, words, mask_, mh, font, hyphenate)
        if out is not None:
            seen.append([ln for ln, *_ in out[1]])
        return out

    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(scanlate_render, "_layout_at_size", spy)
        scanlate_render.render_text_in_box(
            img, box, "Housework? Because it's absolutely forbidden", font_size=14, mask=mask)
    assert not scanlate_render._split_twice(seen[-1]), seen[-1]


def test_rect_layout_keeps_the_line_breaks_of_the_translation():
    box = {"x": 10, "y": 10, "w": 280, "h": 180}
    img = Image.new("RGB", (300, 200), "white")
    scanlate_render.render_text_in_box(img, dict(box), "Ch. 1 ... 5\nCh. 2 ... 27\nCh. 3 ... 43",
                                       font_size=18)
    dark = np.asarray(img.convert("L")) < 128
    rows = np.flatnonzero(dark.any(axis=1))
    # Three short lines stack; run together they would sit on one row band.
    bands = 1 + int((np.diff(rows) > 3).sum())
    assert bands == 3


def test_a_compound_breaks_between_its_two_consonants_not_after_a_stray_vowel():
    assert scanlate_render._hyphen_split("nickname", lambda head: len(head) <= 7) == ("nick-", "name")


def test_a_digraph_is_not_cut():
    head, tail = scanlate_render._hyphen_split("something", lambda head: len(head) <= 5)
    assert not (head.endswith("t-") and tail.startswith("h"))


def test_a_word_that_already_has_a_hyphen_breaks_there_and_is_not_cut_again():
    assert scanlate_render._hyphen_split("Alma-diel.", lambda head: len(head) <= 6) == ("Alma-", "diel.")
    assert scanlate_render._hyphen_split("Amae-san.", lambda head: len(head) <= 5) == ("Amae-", "san.")


def test_an_own_hyphen_whose_head_does_not_fit_falls_back_to_a_new_split():
    head, tail = scanlate_render._hyphen_split("Alma-diel.", lambda head: len(head) <= 4)
    assert head.endswith("-") and head + tail != "Alma-diel."
