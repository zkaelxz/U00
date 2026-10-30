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
