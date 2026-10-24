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
