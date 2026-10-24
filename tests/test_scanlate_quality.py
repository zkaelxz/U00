"""Scanlate quality fixes measured by tools/scanlate_bench.py: bubbles on white
panels are found, empty art and white panels are not, a balloon and its text
box are one region, text direction and reading order follow the lettering and
the language, a lettered balloon is a bubble (not a sign), cleanup keeps the
outline and the art around it, and the English starts at the source size.
Every page is drawn in the test."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # requirements-media.txt, not core

import scanlate
import scanlate_detect
import scanlate_regions
import scanlate_render
import scanlate_tuning
from scanlate_inpaint import inpaint_region


def _save(tmp_path, img, name="page.png"):
    path = str(tmp_path / name)
    cv2.imwrite(path, img)
    return path


def _letters(img, x, y, n, size=24, step=30, vertical=False, gap=6):
    """`n` glyph-like blocks (a box with a stroke through it), separate from
    each other as CJK characters are."""
    for k in range(n):
        cx, cy = (x, y + k * step) if vertical else (x + k * step, y)
        cv2.rectangle(img, (cx, cy), (cx + size - gap, cy + size - gap), 0, 2)
        cv2.line(img, (cx + 3, cy + size // 2 - 3), (cx + size - gap - 3, cy + size // 2 - 3), 0, 2)


def _white_panel_page():
    """A white page and white panel, one lettered oval balloon with a 3 px
    outline, an empty oval drawn as art, and line art crossing the panel."""
    img = np.full((900, 700), 255, np.uint8)
    cv2.rectangle(img, (40, 40), (660, 860), 0, 4)
    cv2.ellipse(img, (480, 250), (110, 150), 0, 0, 360, 0, 3)
    _letters(img, 470, 150, 6, vertical=True)
    _letters(img, 430, 170, 5, vertical=True)
    cv2.ellipse(img, (200, 600), (90, 60), 0, 0, 360, 0, 3)      # empty: art
    cv2.line(img, (40, 450), (660, 820), 0, 3)
    cv2.line(img, (300, 40), (120, 860), 0, 2)
    return img


class TestDetector:
    def test_a_lettered_balloon_on_a_white_panel_is_found(self, tmp_path):
        boxes = scanlate_detect.detect_bubbles_cv(_save(tmp_path, _white_panel_page()))
        assert len(boxes) == 1
        b = boxes[0]
        assert abs(b["x"] - 370) < 10 and abs(b["y"] - 100) < 10
        assert abs(b["w"] - 220) < 15 and abs(b["h"] - 300) < 15

    def test_screentone_panel_is_not_a_bubble(self, tmp_path):
        img = np.full((900, 700), 255, np.uint8)
        cv2.rectangle(img, (40, 40), (660, 400), 0, 4)
        for yy in range(48, 395, 7):
            for xx in range(48, 655, 7):
                cv2.circle(img, (xx, yy), 1, 150, -1)
        _letters(img, 300, 200, 4)
        boxes, rejected = scanlate_detect.detect_bubbles_cv(_save(tmp_path, img), debug=True)
        assert boxes == []
        assert rejected

    def test_a_balloon_breaking_the_panel_border_is_found(self, tmp_path):
        img = np.full((900, 800), 255, np.uint8)
        cv2.rectangle(img, (40, 40), (660, 500), 0, 4)
        cv2.ellipse(img, (620, 200), (100, 70), 0, 0, 360, 255, -1)
        cv2.ellipse(img, (620, 200), (100, 70), 0, 0, 360, 0, 3)
        _letters(img, 560, 185, 4)
        boxes = scanlate_detect.detect_bubbles_cv(_save(tmp_path, img))
        assert len(boxes) == 1 and abs(boxes[0]["x"] - 520) < 10

    def test_thresholds_come_from_the_tuning_module(self, tmp_path, monkeypatch):
        path = _save(tmp_path, _white_panel_page())
        monkeypatch.setattr(scanlate_tuning, "CV_MIN_W", 400)
        assert scanlate_detect.detect_bubbles_cv(path) == []


class TestMlBoxMerge:
    def test_a_text_box_inside_its_balloon_box_merges_into_the_balloon(self):
        balloon = {"x": 280, "y": 1071, "w": 125, "h": 185, "confidence": 0.94, "label": "bubble"}
        text = {"x": 301, "y": 1097, "w": 82, "h": 136, "confidence": 0.95, "label": "text_bubble"}
        assert scanlate_detect._box_iou(balloon, text) < 0.5    # the case IoU alone missed
        assert scanlate_detect.dedupe_overlapping_boxes([text, balloon]) == [balloon]

    def test_neighbouring_balloons_stay_apart(self):
        a = {"x": 0, "y": 0, "w": 100, "h": 100, "label": "bubble"}
        b = {"x": 80, "y": 0, "w": 100, "h": 100, "label": "bubble"}
        assert len(scanlate_detect.dedupe_overlapping_boxes([a, b])) == 2


class TestOrientation:
    def _box_with(self, tmp_path, vertical, n):
        img = np.full((400, 400), 255, np.uint8)
        _letters(img, 150 if vertical else 60, 60 if vertical else 180, n, vertical=vertical)
        return cv2.imread(_save(tmp_path, img), cv2.IMREAD_GRAYSCALE)

    def test_one_horizontal_line_of_characters_is_horizontal(self, tmp_path):
        gray = self._box_with(tmp_path, False, 6)
        box = {"x": 40, "y": 150, "w": 230, "h": 90}
        assert scanlate_regions.estimate_text_orientation(gray, box, "ko") == "horizontal"

    def test_one_column_of_characters_is_vertical(self, tmp_path):
        gray = self._box_with(tmp_path, True, 6)
        box = {"x": 120, "y": 40, "w": 90, "h": 230}
        assert scanlate_regions.estimate_text_orientation(gray, box, "ja") == "vertical"

    def test_the_balloon_outline_in_the_corners_is_not_lettering(self, tmp_path):
        img = np.full((400, 400), 255, np.uint8)
        cv2.ellipse(img, (200, 200), (150, 60), 0, 0, 360, 0, 3)
        _letters(img, 110, 190, 6)
        gray = cv2.imread(_save(tmp_path, img), cv2.IMREAD_GRAYSCALE)
        box = {"x": 50, "y": 140, "w": 300, "h": 120}
        assert scanlate_regions.estimate_text_orientation(gray, box, "zh") == "horizontal"


class TestReadingOrder:
    LEFT_HIGH = {"x": 20, "y": 98, "w": 100, "h": 80}
    RIGHT_LOW = {"x": 300, "y": 110, "w": 100, "h": 80}    # same row, top 12 px lower

    def test_manga_reads_a_row_right_to_left(self):
        assert scanlate_regions.reading_sequence([self.LEFT_HIGH, self.RIGHT_LOW], True) == [1, 0]

    def test_manhua_and_manhwa_read_a_row_left_to_right(self):
        assert scanlate_regions.reading_sequence([self.RIGHT_LOW, self.LEFT_HIGH], False) == [1, 0]

    def test_a_lower_row_comes_after_a_higher_one_either_way(self):
        top_left = {"x": 20, "y": 20, "w": 100, "h": 80}
        low_right = {"x": 300, "y": 300, "w": 100, "h": 80}
        assert scanlate_regions.reading_sequence([low_right, top_left], True) == [1, 0]
        assert scanlate_regions.reading_sequence([low_right, top_left], False) == [1, 0]

    def test_stacked_regions_in_one_row_read_top_first(self):
        tall = {"x": 300, "y": 20, "w": 100, "h": 300}
        upper = {"x": 20, "y": 30, "w": 100, "h": 80}
        lower = {"x": 30, "y": 200, "w": 100, "h": 80}
        assert scanlate_regions.reading_sequence([lower, upper, tall], True) == [2, 1, 0]

    def test_chinese_page_reads_the_left_panel_first(self, tmp_path):
        img = np.full((600, 800, 3), 255, np.uint8)
        cv2.rectangle(img, (20, 20), (380, 580), (0, 0, 0), 4)
        cv2.rectangle(img, (420, 20), (780, 580), (0, 0, 0), 4)
        path = _save(tmp_path, img)
        left, right = {"x": 60, "y": 300, "w": 100, "h": 80}, {"x": 460, "y": 60, "w": 100, "h": 80}
        by_x = {r.x: r for r in scanlate_regions.analyze_page_regions(path, [right, left], "zh")}
        assert by_x[60].reading_order < by_x[460].reading_order
        by_x = {r.x: r for r in scanlate_regions.analyze_page_regions(path, [right, left], "ja")}
        assert by_x[460].reading_order < by_x[60].reading_order

    def test_panels_are_found_on_grey_scanned_paper(self, tmp_path):
        rng = np.random.default_rng(0)
        img = np.full((600, 800), 232, np.float32) + rng.normal(0, 6, (600, 800))
        img = np.clip(img, 0, 255).astype(np.uint8)
        cv2.rectangle(img, (20, 20), (380, 580), 0, 4)
        cv2.rectangle(img, (420, 20), (780, 580), 0, 4)
        assert len(scanlate_detect.detect_panels(_save(tmp_path, img))) == 2


class TestKind:
    def test_a_densely_lettered_balloon_on_a_white_panel_is_a_bubble(self, tmp_path):
        img = np.full((400, 400, 3), 255, np.uint8)
        cv2.ellipse(img, (200, 200), (120, 90), 0, 0, 360, (0, 0, 0), 3)
        for row in range(4):
            _letters(img, 120, 140 + row * 30, 6, size=26, step=28, gap=2)
        out = scanlate_detect.classify_text_regions(_save(tmp_path, img),
                                                    [{"x": 80, "y": 110, "w": 240, "h": 180}])
        assert out[0]["kind"] == "bubble"


class TestCleanup:
    def _toned_balloon_page(self, tmp_path):
        img = np.full((500, 500, 3), 255, np.uint8)
        for yy in range(0, 500, 6):
            cv2.line(img, (0, yy), (500, yy), (120, 120, 120), 1)
        cv2.ellipse(img, (250, 250), (150, 100), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(img, (250, 250), (150, 100), 0, 0, 360, (0, 0, 0), 3)
        gray = np.full((500, 500), 255, np.uint8)
        _letters(gray, 170, 235, 6)
        img[gray < 128] = 0
        return img, _save(tmp_path, img)

    BOX = {"x": 100, "y": 150, "w": 300, "h": 200, "kind": "bubble"}

    def test_erasing_inside_the_outline_keeps_the_outline_and_the_tone(self, tmp_path):
        img, path = self._toned_balloon_page(tmp_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        page = img.copy()
        scanlate_render.erase_lettering(path, dict(self.BOX), page, gray)
        after = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        assert (after[235:260, 170:350] < 128).sum() == 0          # lettering gone
        assert after[250, 101] < 128 and after[250, 398] < 128     # outline kept
        corner = (slice(152, 170), slice(102, 130))               # tone in the box's corner
        assert np.array_equal(after[corner], gray[corner])

    def test_a_box_erase_still_clears_its_whole_rectangle(self, tmp_path):
        img, path = self._toned_balloon_page(tmp_path)
        page = img.copy()
        inpaint_region(path, dict(self.BOX), image=page)
        after = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
        assert (after[235:260, 170:350] < 128).sum() == 0

    def test_a_render_keeps_the_outline(self, tmp_path):
        img, path = self._toned_balloon_page(tmp_path)
        out = str(tmp_path / "out.png")
        scanlate_render.process_page(path, [dict(self.BOX, translated_text="Hello there", skip=False,
                                                 font_size=20)], out)
        after = cv2.imread(out, cv2.IMREAD_GRAYSCALE)
        assert after[250, 101] < 128 and after[160, 110] == cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)[160, 110]


class TestFontSize:
    def _gray_with(self, size):
        img = np.full((400, 400), 255, np.uint8)
        _letters(img, 80, 180, 4, size=size, step=size + 6, gap=2)
        return img

    def test_bigger_source_lettering_starts_the_english_bigger(self):
        box = {"x": 50, "y": 140, "w": 300, "h": 120, "orientation": "horizontal"}
        small = scanlate.suggest_font_size(self._gray_with(16), box)
        big = scanlate.suggest_font_size(self._gray_with(34), box)
        assert big > small

    def test_no_lettering_keeps_the_default(self):
        box = {"x": 50, "y": 140, "w": 300, "h": 120, "orientation": "horizontal"}
        blank = np.full((400, 400), 255, np.uint8)
        assert scanlate.suggest_font_size(blank, box) == scanlate_tuning.TYPESET_DEFAULT_FONT_PX

    def test_the_start_size_stays_within_the_tuned_range(self):
        box = {"x": 10, "y": 10, "w": 380, "h": 380, "orientation": "horizontal"}
        img = np.full((400, 400), 255, np.uint8)
        cv2.rectangle(img, (40, 40), (360, 360), 0, 3)
        assert scanlate.suggest_font_size(img, box) <= scanlate_tuning.TYPESET_START_MAX_PX


class TestOcrCrop:
    def _crop(self, noise):
        from PIL import Image
        rng = np.random.default_rng(1)
        img = np.full((80, 160), 235, np.float32) + rng.normal(0, noise, (80, 160))
        img[30:50, 20:140] = 30
        return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))

    def test_a_clean_crop_is_left_alone(self):
        crop = self._crop(0)
        assert scanlate.clean_ocr_crop(crop) is crop

    def test_a_noisy_crop_is_smoothed_and_stretched(self):
        out = np.asarray(scanlate.clean_ocr_crop(self._crop(8)))
        assert out[:20].std() < 4 and out[:20].mean() > 245 and out[35:45, 30:130].max() < 15
