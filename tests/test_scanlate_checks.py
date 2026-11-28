"""The checks that judge a rendered balloon: fill leaking out, off-centre or tiny text, lettering left behind."""

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from scanlate import scanlate_checks as checks  # noqa: E402

BOX = {"x": 0, "y": 0, "w": 200, "h": 300}


def _balloon():
    img = np.full((300, 200), 90, np.uint8)
    cv2.ellipse(img, (100, 150), (80, 130), 0, 0, 360, 255, -1)
    interior = np.zeros(img.shape, np.uint8)
    cv2.ellipse(interior, (100, 150), (80, 130), 0, 0, 360, 1, -1)
    return img, interior


def _words(img, cx, cy, height=14, rows=3):
    for i in range(rows):
        top = cy + (i - rows // 2) * (height + 8)
        for k in range(4):
            img[top:top + height, cx - 40 + k * 22:cx - 40 + k * 22 + 12] = 0


def test_a_clean_render_passes_every_check():
    original, interior = _balloon()
    rendered = original.copy()
    _words(rendered, 100, 150)
    erased = original.copy()
    assert checks.region_problems(original, rendered, erased, BOX, interior) == []


def test_a_fill_that_follows_the_box_instead_of_the_balloon_is_a_leak():
    original, interior = _balloon()
    rendered = original.copy()
    rendered[:, :] = 255
    _words(rendered, 100, 150)
    assert "fill-outside-balloon" in checks.region_problems(original, rendered, None, BOX, interior)


def test_text_pushed_to_one_side_is_off_centre():
    original, interior = _balloon()
    rendered = original.copy()
    _words(rendered, 70, 150)
    assert "off-centre" in checks.region_problems(original, rendered, None, BOX, interior)


def test_letters_too_small_to_read_are_flagged():
    original, interior = _balloon()
    rendered = original.copy()
    _words(rendered, 100, 150, height=5)
    assert "tiny-text" in checks.region_problems(original, rendered, None, BOX, interior)


def test_source_lettering_the_erase_missed_is_flagged():
    original, interior = _balloon()
    rendered = original.copy()
    _words(rendered, 100, 150)
    erased = original.copy()
    erased[120:200, 60:140] = 0
    assert "source-left" in checks.region_problems(original, rendered, erased, BOX, interior)


def test_a_balloon_with_no_text_has_nothing_to_measure():
    original, interior = _balloon()
    assert checks.text_offset(original, BOX, interior) is None
    assert checks.median_letter_height(original, BOX, interior) is None


def test_text_extent_is_the_english_the_render_added():
    erased = np.full((300, 200), 255, np.uint8)
    erased[10:20, 10:20] = 0
    rendered = erased.copy()
    rendered[100:130, 50:150] = 0
    assert checks.text_extent(rendered, erased, BOX) == (50, 100, 150, 130)
    assert checks.text_extent(erased, erased, BOX) is None


def test_two_balloons_whose_english_runs_into_each_other_are_both_flagged():
    far = (0, 0, 50, 20)
    assert checks.colliding_texts([far, (100, 100, 150, 120), None]) == set()
    assert checks.colliding_texts([far, (25, 5, 80, 25), (300, 300, 320, 310)]) == {0, 1}
    assert checks.colliding_texts([far, (49, 19, 90, 40)]) == set()
