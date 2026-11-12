"""Scanlate layout on pages like real ones: columns of one balloon, thought dots, column order, cleanup."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import ocr_layout
import scanlate
import scanlate_detect
import scanlate_inpaint
import scanlate_regions


def _page(balloons, size=(300, 400), ground=70):
    """Grey page with white ellipse balloons (black outline) and dark lettering blocks."""
    img = np.full((size[0], size[1]), ground, np.uint8)
    for cx, cy, ax, ay in balloons:
        cv2.ellipse(img, (cx, cy), (ax, ay), 0, 0, 360, 0, -1)
        cv2.ellipse(img, (cx, cy), (ax - 3, ay - 3), 0, 0, 360, 255, -1)
    return img


def _box(x, y, w, h, label="text_bubble"):
    return {"x": x, "y": y, "w": w, "h": h, "label": label, "confidence": 0.5}


def _letters(img, x, y, w, h):
    for i in range(0, h, 22):
        img[y + i:y + i + 16, x:x + w] = 0


def test_two_columns_of_one_balloon_become_one_box():
    img = _page([(150, 150, 80, 110)])
    _letters(img, 175, 80, 24, 140)
    _letters(img, 120, 90, 24, 120)
    left, right = _box(115, 85, 35, 130), _box(168, 75, 38, 150)
    out = scanlate_detect.merge_fragment_boxes([left, right], img)
    assert len(out) == 1
    assert (out[0]["x"], out[0]["y"]) == (115, 75)


def test_boxes_of_two_separate_balloons_stay_apart():
    img = _page([(90, 150, 60, 100), (215, 150, 60, 100)])
    _letters(img, 80, 90, 24, 120)
    _letters(img, 205, 90, 24, 120)
    out = scanlate_detect.merge_fragment_boxes([_box(70, 80, 45, 140), _box(195, 80, 45, 140)], img)
    assert len(out) == 2


def test_adjacent_balloons_with_touching_boxes_stay_apart():
    img = _page([(95, 150, 55, 100), (200, 150, 55, 100)])
    _letters(img, 85, 90, 22, 120)
    _letters(img, 190, 90, 22, 120)
    out = scanlate_detect.merge_fragment_boxes([_box(60, 70, 100, 160), _box(150, 70, 100, 160)], img)
    assert len(out) == 2


@pytest.mark.parametrize("text, dropped", [
    ("o o", True), ("000o", True), ("O", True), ("o.o", True),
    ("……", False), ("我走了", False), ("ok", False), ("", False), ("0 yen", False),
])
def test_thought_dots_are_not_text(text, dropped):
    assert scanlate._is_thought_dots(text, "zh") is dropped


def test_latin_source_lettering_is_never_dropped_as_dots():
    assert scanlate._is_thought_dots("o o", "en") is False


def test_two_short_columns_read_right_to_left_even_if_called_horizontal():
    lines = ocr_layout.lines_from_boxes(
        ["不明白", "我完全"],
        [[[10, 12], [38, 12], [38, 90], [10, 90]], [[50, 5], [78, 5], [78, 80], [50, 80]]])
    assert ocr_layout.layout_text(lines, vertical=False) == "我完全\n不明白"


def test_horizontal_rows_still_read_left_to_right():
    lines = ocr_layout.lines_from_boxes(
        ["B", "A"], [[[100, 10], [180, 10], [180, 30], [100, 30]], [[10, 12], [90, 12], [90, 32], [10, 32]]])
    assert ocr_layout.layout_text(lines, vertical=False) == "A\nB"


def test_balloon_on_dark_art_is_erased_inside_its_outline_only():
    img = _page([(150, 150, 70, 100)])
    _letters(img, 135, 90, 24, 120)
    box = {"x": 70, "y": 40, "w": 160, "h": 220}   # reaches past the balloon on every side
    mask = scanlate_detect.bubble_erase_mask("x.png", box, image=img)
    assert mask is not None and not mask.all()
    assert not mask[150 - box["y"], 150 - 69 - box["x"]]      # on the outline ring, 69 px left of centre
    assert mask[150 - box["y"], 150 - box["x"]]


def _ghosting_balloon(noise_std):
    rng = np.random.default_rng(0)
    gray = np.full((120, 100), 250, np.uint8)
    gray[30:90, 40:60] = 20
    gray = np.clip(gray + rng.normal(0, noise_std, gray.shape), 0, 255).astype(np.uint8)
    halo = cv2.dilate((gray < 100).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    gray[halo & (gray > 200)] = 215                     # the faint ring a JPEG leaves
    roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    return gray, roi


def test_faint_halo_round_lettering_is_painted_out_in_a_plain_white_balloon():
    gray, roi = _ghosting_balloon(1.0)
    mask = scanlate_inpaint._lettering_mask(gray)
    inside = np.ones(gray.shape, bool)
    inpainted = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    flat = scanlate_inpaint._flatten_plain_paper(roi, inpainted, mask, inside)
    core = np.zeros(gray.shape, bool)
    core[6:-6, 6:-6] = True
    assert cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)[core].min() >= 245


def test_a_toned_balloon_is_left_as_inpainted():
    rng = np.random.default_rng(1)
    gray = np.clip(rng.normal(200, 25, (120, 100)), 0, 255).astype(np.uint8)
    roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mask = np.zeros(gray.shape, np.uint8)
    inpainted = roi.copy()
    out = scanlate_inpaint._flatten_plain_paper(roi, inpainted, mask, np.ones(gray.shape, bool))
    assert out is inpainted


def test_ellipsis_column_does_not_shrink_the_measured_lettering_size():
    gray = np.full((200, 80), 255, np.uint8)
    gray[20:150, 20:42] = 0       # a 22 px glyph column
    gray[160:176, 28:37] = 0      # its ellipsis: a 9 px run
    box = {"x": 0, "y": 0, "w": 80, "h": 200}
    assert scanlate_regions.lettering_size(gray, box, "vertical") >= 20


def test_a_sky_that_fades_behind_the_lettering_is_not_whitened_into_a_box():
    gray = np.full((140, 200), 254, np.uint8)
    gray[:50, :90] = 238                              # cloud: darker, far from the letters
    gray[60:110, 110:180] = 20
    roi = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    mask = scanlate_inpaint._lettering_mask(gray)
    inpainted = cv2.inpaint(roi, mask, 5, cv2.INPAINT_TELEA)
    out = scanlate_inpaint._flatten_plain_paper(roi, inpainted, mask, np.ones(gray.shape, bool))
    assert out is inpainted
    assert cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)[10:40, 10:80].max() < 245

