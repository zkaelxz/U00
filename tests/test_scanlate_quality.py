"""Scanlate quality fixes measured by tools/scanlate_bench.py: bubbles on white
panels are found, empty art and white panels are not, a balloon and its text
box are one region, text direction and reading order follow the lettering and
the language, a lettered balloon is a bubble (not a sign), cleanup keeps the
outline and the art around it, and the English starts at the source size.
Every page is drawn in the test."""
import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")  # requirements-media.txt, not core

from scanlate import scanlate
from scanlate import scanlate_detect
from scanlate import scanlate_inpaint
from scanlate import scanlate_balloons
from scanlate import scanlate_box_merge
from scanlate import scanlate_page_decode
from scanlate import scanlate_panels
from scanlate import scanlate_regions
from scanlate import scanlate_render
from scanlate import scanlate_tuning
from scanlate.scanlate_inpaint import inpaint_region


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

    @pytest.mark.parametrize("spokes", [40, 90, 180])
    def test_a_burst_balloon_with_a_ring_of_hairline_spikes_is_found(self, tmp_path, spokes):
        import math
        img = np.full((900, 700), 90, np.uint8)
        cx, cy, radius = 350, 450, 190
        cv2.circle(img, (cx, cy), radius, 255, -1)
        for k in range(spokes):
            a = 2 * math.pi * k / spokes
            cv2.line(img, (int(cx + math.cos(a) * radius * 0.8), int(cy + math.sin(a) * radius * 0.8)),
                     (int(cx + math.cos(a) * radius), int(cy + math.sin(a) * radius)), 0, 1)
        _letters(img, 300, 420, 5)
        _letters(img, 300, 480, 5)
        boxes = scanlate_detect.detect_bubbles_cv(_save(tmp_path, img))
        assert len(boxes) == 1 and abs(boxes[0]["w"] - 380) < 20

    def test_a_white_shape_with_one_deep_cut_is_still_a_ragged_outline(self, tmp_path):
        img = np.full((900, 700), 90, np.uint8)
        cv2.circle(img, (350, 450), 190, 255, -1)
        import math
        cv2.line(img, (350, 450), (int(350 + math.cos(0.3) * 200), int(450 + math.sin(0.3) * 200)), 0, 12)
        _letters(img, 300, 420, 5)
        boxes, rejected = scanlate_detect.detect_bubbles_cv(_save(tmp_path, img), debug=True)
        assert boxes == [] and any(r[4] == "ragged outline" for r in rejected)

    def test_thresholds_come_from_the_tuning_module(self, tmp_path, monkeypatch):
        path = _save(tmp_path, _white_panel_page())
        monkeypatch.setattr(scanlate_tuning, "CV_MIN_W", 400)
        assert scanlate_detect.detect_bubbles_cv(path) == []


class TestMlBoxMerge:
    def test_a_text_box_inside_its_balloon_box_merges_into_the_balloon(self):
        balloon = {"x": 280, "y": 1071, "w": 125, "h": 185, "confidence": 0.94, "label": "bubble"}
        text = {"x": 301, "y": 1097, "w": 82, "h": 136, "confidence": 0.95, "label": "text_bubble"}
        assert scanlate_box_merge._box_iou(balloon, text) < 0.5    # the case IoU alone missed
        assert scanlate_box_merge.dedupe_overlapping_boxes([text, balloon]) == [
            dict(balloon, text_boxes=[{"x": 301, "y": 1097, "w": 82, "h": 136}])]

    def test_merged_fragments_keep_every_text_box(self):
        a = {"x": 0, "y": 0, "w": 100, "h": 100, "label": "bubble", "text_boxes": [{"x": 10, "y": 10, "w": 5, "h": 5}]}
        b = {"x": 2, "y": 2, "w": 96, "h": 96, "label": "bubble", "text_boxes": [{"x": 60, "y": 60, "w": 5, "h": 5}]}
        merged = scanlate_box_merge.merge_fragment_boxes([a, b])
        assert len(merged) == 1 and len(merged[0]["text_boxes"]) == 2

    def test_a_small_balloon_inside_a_big_balloon_s_box_stays(self):
        big = {"x": 0, "y": 0, "w": 300, "h": 300, "label": "bubble"}
        small = {"x": 200, "y": 200, "w": 80, "h": 80, "label": "bubble"}
        assert len(scanlate_box_merge.dedupe_overlapping_boxes([big, small])) == 2

    def test_neighbouring_balloons_stay_apart(self):
        a = {"x": 0, "y": 0, "w": 100, "h": 100, "label": "bubble"}
        b = {"x": 80, "y": 0, "w": 100, "h": 100, "label": "bubble"}
        assert len(scanlate_box_merge.dedupe_overlapping_boxes([a, b])) == 2


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

    def test_columns_with_furigana_between_them_are_vertical(self):
        # The reading aids beside each column filled the gap between the columns, so a
        # narration box of two columns was read as horizontal and cut into pieces.
        gray = np.full((500, 400), 255, np.uint8)
        for col_x in (110, 180, 250):
            for k in range(4):
                y = 80 + k * 52
                cv2.rectangle(gray, (col_x, y), (col_x + 34, y + 34), 0, 3)
                cv2.line(gray, (col_x + 4, y + 17), (col_x + 30, y + 17), 0, 3)
            for k in (0, 2):
                y = 85 + k * 52
                cv2.rectangle(gray, (col_x - 24, y), (col_x - 12, y + 12), 0, 2)
                cv2.rectangle(gray, (col_x - 24, y + 16), (col_x - 12, y + 28), 0, 2)
        box = {"x": 60, "y": 50, "w": 260, "h": 300}
        assert scanlate_regions.estimate_text_orientation(gray, box, "ja") == "vertical"

    def test_specks_of_tone_above_vertical_columns_do_not_make_them_horizontal(self):
        # A speck of the art's tone above the lettering opened the widest gap between rows.
        gray = np.full((500, 400), 255, np.uint8)
        for col_x in (110, 180, 250):
            for k in range(4):
                y = 120 + k * 52
                cv2.rectangle(gray, (col_x, y), (col_x + 34, y + 34), 0, 3)
                cv2.line(gray, (col_x + 4, y + 17), (col_x + 30, y + 17), 0, 3)
        for x in (150, 220):
            cv2.circle(gray, (x, 60), 3, 0, -1)
        box = {"x": 60, "y": 40, "w": 260, "h": 340}
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

    def test_a_box_under_a_wide_one_is_read_after_it(self):
        right = {"x": 208, "y": 24, "w": 70, "h": 100}
        wide = {"x": 111, "y": 24, "w": 141, "h": 100}
        under = {"x": 102, "y": 48, "w": 20, "h": 100}        # below the wide box's left end
        order = scanlate_regions.reading_sequence([right, wide, under], False)
        assert order.index(1) < order.index(2)

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
        assert len(scanlate_panels.detect_panels(_save(tmp_path, img))) == 2


class TestKind:
    def test_a_densely_lettered_balloon_on_a_white_panel_is_a_bubble(self, tmp_path):
        img = np.full((400, 400, 3), 255, np.uint8)
        cv2.ellipse(img, (200, 200), (120, 90), 0, 0, 360, (0, 0, 0), 3)
        for row in range(4):
            _letters(img, 120, 140 + row * 30, 6, size=26, step=28, gap=2)
        out = scanlate_balloons.classify_text_regions(_save(tmp_path, img),
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

    def test_lettering_touching_the_outline_is_erased_and_the_outline_kept(self, tmp_path):
        img = np.full((460, 460, 3), 255, np.uint8)
        cv2.rectangle(img, (0, 0), (60, 460), (0, 0, 0), -1)               # art beside the balloon
        cv2.ellipse(img, (230, 230), (150, 100), 0, 0, 360, (0, 0, 0), 3)
        cv2.line(img, (230, 132), (230, 180), (0, 0, 0), 4)                 # a stroke joined to it
        ring = np.zeros((460, 460), np.uint8)
        cv2.ellipse(ring, (230, 230), (150, 100), 0, 0, 360, 1, 3)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for pad in (0, 30):                                                 # tight and generous boxes
            page = img.copy()
            box = {"x": 79 - pad, "y": 129 - pad, "w": 303 + 2 * pad, "h": 203 + 2 * pad, "kind": "bubble"}
            scanlate_render.erase_lettering(_save(tmp_path, img), box, page, gray)
            after = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
            assert (after[140:185, 220:240] < 128).sum() == 0
            assert (after[ring > 0] < 128).all()
            assert np.array_equal(after[:, :61], gray[:, :61])

    @pytest.mark.parametrize("art", [122, 60])                         # grey tone, dark jacket
    def test_art_in_the_dent_above_a_tail_is_not_erased(self, art):
        # The shape of a tall vertical balloon on a real page: its tail runs to the
        # box edge, and art fills the dent between tail and outline above it.
        img = np.full((1000, 450), 225, np.uint8)                          # light sky
        cv2.rectangle(img, (0, 170), (120, 1000), art, -1)
        cv2.ellipse(img, (250, 500), (190, 480), 0, 0, 360, 0, -1)
        cv2.fillPoly(img, [np.array([[0, 340], [80, 300], [80, 380]], np.int32)], 0)
        cv2.ellipse(img, (250, 500), (186, 476), 0, 0, 360, 255, -1)
        cv2.fillPoly(img, [np.array([[5, 340], [84, 306], [84, 374]], np.int32)], 255)
        for left in (200, 290):                                            # two columns
            for k in range(7):
                cv2.rectangle(img, (left, 150 + k * 100), (left + 50, 210 + k * 100), 0, -1)
        box = {"x": 0, "y": 0, "w": 450, "h": 1000}
        mask = scanlate_balloons.bubble_erase_mask("unused", box, image=img)
        assert mask[150:210, 200:250].all() and mask[750:810, 290:340].all()
        assert not (mask & (img == art)).any()

    def test_a_generous_box_still_finds_the_oval(self):
        img = np.full((800, 800), 255, np.uint8)
        cv2.ellipse(img, (400, 400), (150, 100), 0, 0, 360, 0, 3)
        box = {"x": 210, "y": 260, "w": 380, "h": 280}
        mask = scanlate_balloons.bubble_shape_mask("unused", box, margin=0, image=img)
        assert mask is not None and not mask[0, 0] and mask[140, 190]

    def test_a_box_on_fine_screentone_is_quick(self):
        import time
        tone = np.full((1600, 1600), 255, np.uint8)
        tone[::4, :] = 0
        tone[:, ::4] = 0
        start = time.monotonic()
        scanlate_balloons.bubble_shape_mask("unused", {"x": 0, "y": 0, "w": 1500, "h": 1500}, image=tone)
        assert time.monotonic() - start < 10

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

    def test_short_lettering_in_a_large_balloon_starts_bigger_than_the_same_lettering_in_a_snug_one(self):
        gray = self._gray_with(16)
        snug = {"x": 70, "y": 170, "w": 4 * 22 + 20, "h": 40, "orientation": "horizontal"}
        roomy = {"x": 10, "y": 10, "w": 380, "h": 380, "orientation": "horizontal"}
        assert scanlate.suggest_font_size(gray, roomy) > scanlate.suggest_font_size(gray, snug)

    def test_the_growth_never_passes_the_tuned_maximum(self):
        gray = self._gray_with(30)
        roomy = {"x": 10, "y": 10, "w": 380, "h": 380, "orientation": "horizontal"}
        assert scanlate.suggest_font_size(gray, roomy) <= scanlate_tuning.TYPESET_START_MAX_PX

    def test_no_lettering_keeps_the_default(self):
        box = {"x": 50, "y": 140, "w": 300, "h": 120, "orientation": "horizontal"}
        blank = np.full((400, 400), 255, np.uint8)
        assert scanlate.suggest_font_size(blank, box) == scanlate_tuning.TYPESET_DEFAULT_FONT_PX

    def test_unmeasurable_lettering_in_a_big_balloon_starts_bigger_than_the_default(self):
        pale = np.full((400, 400), 255, np.uint8)
        _letters(pale, 120, 180, 4, size=24, step=30, gap=2)
        pale[pale < 128] = 200  # ink the dark-ink threshold misses
        box = {"x": 10, "y": 10, "w": 380, "h": 300, "orientation": "horizontal"}
        size = scanlate.suggest_font_size(pale, box)
        assert scanlate_tuning.TYPESET_DEFAULT_FONT_PX < size <= scanlate_tuning.TYPESET_START_MAX_PX

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


def _hatched_balloon(letter_bgr, hatch_bgr=(200, 160, 150)):
    """A balloon drawn on 360x300 with hatching over its lower right and lettering on the left."""
    page = np.full((300, 360, 3), 255, np.uint8)
    cv2.ellipse(page, (180, 150), (160, 130), 0, 0, 360, (20, 20, 20), 4)
    yy, xx = np.mgrid[:300, :360]
    hatched = (((xx - 180) / 155.0) ** 2 + ((yy - 150) / 125.0) ** 2 < 1) & (xx + yy > 330)
    lines = ((xx + yy) % 8 == 0) | ((xx - yy) % 8 == 0)
    page[hatched & lines] = hatch_bgr
    for x0 in range(90, 150, 14):
        cv2.rectangle(page, (x0, 120), (x0 + 6, 150), letter_bgr, -1)
    return page


def _erase_mask(tmp_path, page, monkeypatch):
    """The mask inpaint_region would erase, captured instead of inpainted."""
    seen = {}

    def fake_inpaint(roi, mask, *args, **kwargs):
        seen["mask"] = mask.copy()
        return roi

    monkeypatch.setattr(cv2, "inpaint", fake_inpaint)
    path = _save(tmp_path, page)
    gray = scanlate_page_decode.page_gray(path, None)
    box = {"x": 0, "y": 0, "w": 360, "h": 300}
    inside = scanlate_balloons.bubble_erase_mask(path, box, image=gray)
    inpaint_region(path, box, image=page.copy(), inside=inside, backend="cv")
    return seen["mask"] > 0


def test_hatching_inside_a_balloon_is_not_erased_with_dark_lettering(tmp_path, monkeypatch):
    erased = _erase_mask(tmp_path, _hatched_balloon((15, 15, 15)), monkeypatch)
    assert erased[120:150, 90:150].any()
    assert not erased[200:240, 215:255].any()


def test_hatching_as_dark_as_the_lettering_is_erased_as_before(tmp_path, monkeypatch):
    erased = _erase_mask(tmp_path, _hatched_balloon((60, 100, 180), (60, 100, 180)), monkeypatch)
    assert erased[120:150, 90:150].any()
    assert erased[200:240, 215:255].any()


def test_a_balloon_without_hatching_is_erased_as_before(tmp_path, monkeypatch):
    plain = _hatched_balloon((15, 15, 15), (255, 255, 255))
    from scanlate import scanlate_inpaint
    gray = cv2.cvtColor(plain, cv2.COLOR_BGR2GRAY)
    keep = np.ones(gray.shape, bool)
    assert (scanlate_inpaint._without_hatching(gray, keep) == gray).all()


class TestRimAndCaptionCleanup:
    def test_a_ring_of_rim_shading_is_not_erased_but_a_joined_stroke_is(self):
        keep = np.zeros((120, 120), bool)
        cv2.circle(keep.view(np.uint8), (60, 60), 50, 1, -1)
        mask = np.zeros((120, 120), np.uint8)
        cv2.circle(mask, (60, 60), 50, 255, 3)           # the rim's shading as one ring
        cv2.line(mask, (60, 12), (60, 50), 255, 4)        # a stroke joined to it
        scanlate_inpaint._drop_rim_rings(mask, keep)
        assert not mask[60, 8:14].any()                   # ring gone
        assert mask[30:48, 58:62].any()                   # stroke kept

    def test_white_lettering_on_mid_tone_art_is_masked_without_the_flat_art_round_it(self):
        gray = np.full((120, 80), 150, np.uint8)
        cv2.putText(gray, "H", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 2, 255, 5)
        mask = scanlate_inpaint._bright_strokes(gray)
        assert mask is not None and mask[50, 5] == 0 and mask.any() and (mask > 0).mean() < 0.5

    def test_dark_lettering_on_paper_is_not_a_bright_stroke_case(self):
        gray = np.full((120, 80), 245, np.uint8)
        gray[30:44, 10:70] = 0
        gray[60:74, 10:70] = 0
        assert scanlate_inpaint._bright_strokes(gray) is None

    def test_pale_art_spilled_over_by_the_balloon_mask_keeps_its_colour(self):
        roi = np.full((60, 60, 3), 255, np.uint8)
        roi[:, 30:] = (225, 215, 240)                      # pale pink art inside the mask
        inside = np.ones((60, 60), bool)
        mask = np.zeros((60, 60), np.uint8)
        mask[5:10, 5:10] = 255                              # lettering far from the art
        out = scanlate_inpaint._flatten_plain_paper(roi, roi.copy(), mask, inside)
        assert (out[10:50, 40:55] == (225, 215, 240)).all()


class TestDecorationAndBanners:
    def test_a_thin_coloured_rule_is_taken_out_of_the_mask_but_a_coloured_glyph_is_not(self):
        roi = np.full((60, 200, 3), 240, np.uint8)
        cv2.line(roi, (20, 30), (110, 30), (40, 40, 200), 3)             # red rule
        cv2.rectangle(roi, (140, 10), (160, 40), (40, 40, 200), -1)       # red glyph-sized block
        mask = np.full((60, 200), 255, np.uint8)                           # a mask joined across both
        scanlate_inpaint.drop_colour_rules(mask, roi)
        assert not mask[30, 40:100].any()
        assert mask[20:30, 145:155].all()

    def test_a_coloured_dash_beside_coloured_glyphs_is_lettering_not_a_rule(self):
        roi = np.full((60, 200, 3), 240, np.uint8)
        cv2.line(roi, (20, 30), (80, 30), (200, 120, 40), 3)              # a glow-blue dash
        cv2.rectangle(roi, (86, 10), (106, 40), (200, 120, 40), -1)       # its first glyph, beside it
        mask = np.full((60, 200), 255, np.uint8)
        scanlate_inpaint.drop_colour_rules(mask, roi)
        assert mask[29:32, 30:70].all()

    def test_a_coloured_ornament_off_a_rules_line_does_not_save_the_rule(self):
        roi = np.full((80, 200, 3), 240, np.uint8)
        cv2.line(roi, (20, 60), (80, 60), (40, 40, 200), 3)
        cv2.rectangle(roi, (86, 10), (106, 30), (40, 40, 200), -1)       # an ornament above its line
        mask = np.full((80, 200), 255, np.uint8)
        scanlate_inpaint.drop_colour_rules(mask, roi)
        assert not mask[59:62, 30:70].any()

    def test_a_small_end_cap_on_a_rules_line_does_not_save_the_rule(self):
        roi = np.full((80, 200, 3), 240, np.uint8)
        cv2.line(roi, (20, 40), (80, 40), (40, 40, 200), 3)
        cv2.rectangle(roi, (84, 35), (94, 45), (40, 40, 200), -1)         # a 10 px cap on its line
        mask = np.full((80, 200), 255, np.uint8)
        scanlate_inpaint.drop_colour_rules(mask, roi)
        assert not mask[39:42, 30:70].any()

    def test_a_black_strip_with_olive_stripes_over_art_counts_as_a_plain_dark_ground(self):
        strip = np.zeros((40, 400, 3), np.uint8)
        strip[::4] = (10, 30, 25)                                          # olive stripe rows
        gray = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
        assert scanlate_inpaint._on_plain_dark_ground(gray, scanlate_inpaint._channel_spread(strip))
        yellow = strip.copy()
        cv2.putText(yellow, "AB", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 230, 255), 3)
        assert scanlate_inpaint._pale_pixels(yellow).any()                 # yellow lettering is masked

    def test_the_glow_reach_shrinks_for_a_thin_strip(self):
        strip = np.zeros((36, 400, 3), np.uint8)
        cv2.putText(strip, "AB", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 3)
        mask = scanlate_inpaint._dark_art_lettering_mask(strip)
        assert not mask[:, 300:].any() and not mask.all()


class TestGlowSpillAndRules:
    def test_glow_beyond_takes_the_coloured_halo_near_the_mask_only(self):
        roi = np.full((120, 200, 3), 20, np.uint8)
        roi[:, 60:75] = (20, 20, 160)                                      # red halo just outside the mask
        roi[:, 180:195] = (20, 20, 160)                                    # red far away
        mask = np.zeros((120, 200), np.uint8)
        mask[40:80, 76:120] = 255
        spill = scanlate_inpaint._glow_beyond(roi, mask)
        assert spill[60, 65] and not spill[60, 185]

    def test_glow_beyond_skips_art_that_is_coloured_throughout(self):
        roi = np.full((60, 60, 3), (20, 20, 160), np.uint8)
        mask = np.zeros((60, 60), np.uint8)
        mask[20:40, 20:40] = 255
        assert not scanlate_inpaint._glow_beyond(roi, mask).any()

    def test_between_rules_keeps_the_tall_stretch_under_the_upper_rule(self):
        from scanlate import scanlate_art_text as art
        page = np.full((300, 300, 3), 20, np.uint8)
        cv2.line(page, (20, 40), (280, 40), (30, 30, 220), 3)
        cv2.line(page, (20, 220), (280, 220), (30, 30, 220), 3)
        box = {"x": 0, "y": 0, "w": 300, "h": 300}
        room = art.between_rules(page, box)
        assert room["y"] > 40 and room["y"] + room["h"] < 220
        assert art.between_rules(np.full((300, 300, 3), 20, np.uint8), box) == box


class TestStripWideningAndRulesOnSky:
    def test_a_box_that_stops_short_of_a_banner_strip_is_stretched_to_its_ends(self):
        page = np.full((120, 400, 3), (180, 190, 230), np.uint8)
        page[30:60, 20:300] = (10, 0, 0)                                   # the black strip
        page[40:50, 200:280] = (255, 255, 255)                             # lettering the box missed
        box = {"x": 20, "y": 35, "w": 150, "h": 20}
        wide = scanlate_inpaint.widen_to_dark_strip(page, box)
        assert wide["x"] == 20 and wide["x"] + wide["w"] >= 298

    def test_a_tall_box_on_a_vertical_strip_is_stretched_up_and_down(self):
        page = np.full((400, 120, 3), (180, 190, 230), np.uint8)
        page[20:300, 30:60] = (10, 0, 0)
        box = {"x": 35, "y": 20, "w": 20, "h": 150}
        tall = scanlate_inpaint.widen_to_dark_strip(page, box)
        assert (tall["x"], tall["w"]) == (35, 20) and tall["y"] == 20 and tall["y"] + tall["h"] >= 298

    def test_a_box_on_light_paper_or_a_tall_box_is_left_alone(self):
        page = np.full((200, 400, 3), 240, np.uint8)
        page[:, 100:300] = (10, 0, 0)
        assert scanlate_inpaint.widen_to_dark_strip(page, {"x": 120, "y": 20, "w": 60, "h": 60}) == \
            {"x": 120, "y": 20, "w": 60, "h": 60}
        assert scanlate_inpaint.widen_to_dark_strip(np.full((80, 400, 3), 240, np.uint8),
                                                    {"x": 10, "y": 10, "w": 300, "h": 30}) == \
            {"x": 10, "y": 10, "w": 300, "h": 30}

    def test_a_rule_over_a_blue_tinted_sky_still_bounds_the_credit(self):
        from scanlate import scanlate_art_text as art
        page = np.full((120, 300, 3), (230, 200, 190), np.uint8)           # lavender sky (BGR)
        cv2.line(page, (100, 20), (180, 20), (30, 30, 220), 3)
        box = {"x": 0, "y": 0, "w": 300, "h": 120}
        room = art.between_rules(page, box)
        assert room["y"] > 22


class TestDarkStripErase:
    def test_lettering_on_a_dark_strip_is_painted_out_with_the_strip_kept_dark(self):
        roi = np.full((40, 400, 3), (200, 190, 230), np.uint8)             # pink art round the strip
        roi[4:36] = (12, 0, 0)
        cv2.putText(roi, "ABCDEFG", (20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        out = scanlate_inpaint._dark_strip_inpaint(roi)
        assert (cv2.cvtColor(out[4:36], cv2.COLOR_BGR2GRAY) < 60).all()
        assert (out[:3] == roi[:3]).all()                                   # art above the strip untouched

    def test_a_tall_strip_of_vertical_lettering_keeps_its_dark_ground(self):
        roi = np.full((400, 40, 3), (200, 190, 230), np.uint8)
        roi[:, 4:36] = (12, 0, 0)
        for y in range(20, 380, 40):
            for dy in (0, 10, 20):                                         # a glyph's strokes
                cv2.line(roi, (12, y + dy), (28, y + dy), (255, 255, 255), 2)
        out = scanlate_inpaint._dark_strip_inpaint(roi)
        assert (cv2.cvtColor(out[:, 4:36], cv2.COLOR_BGR2GRAY) < 60).all()
        assert (out[:, :3] == roi[:, :3]).all()

    def test_a_crop_that_is_not_a_dark_strip_is_left_to_the_other_erasers(self):
        assert scanlate_inpaint._dark_strip_inpaint(np.full((40, 400, 3), 220, np.uint8)) is None
        assert scanlate_inpaint._dark_strip_inpaint(np.zeros((200, 200, 3), np.uint8)) is None


def test_light_lettering_mask_reaches_past_a_coloured_edge():
    import cv2
    import numpy as np
    from scanlate import scanlate_inpaint
    page = np.full((160, 200), 40, np.uint8)
    cv2.rectangle(page, (80, 60), (120, 100), 255, -1)
    mask = scanlate_inpaint._lettering_mask(page)
    assert mask[80, 76] > 0 and mask[80, 124] > 0 and mask[80, 40] == 0


def test_mid_tone_balloon_with_white_lettering_is_not_read_as_paper_or_art():
    import numpy as np
    from scanlate import scanlate_inpaint
    box = {"x": 0, "y": 0, "w": 100, "h": 100, "kind": "sign"}
    balloon = np.full((100, 100), 105, np.uint8)
    balloon[30:60, 20:80] = 255
    assert scanlate_inpaint._has_light_lettering_on_midtone(balloon)
    assert not scanlate_inpaint.is_art_mistaken_for_sign(balloon, box)
    assert (scanlate_inpaint._lettering_mask(balloon) > 0).mean() < 0.5
    assert not scanlate_inpaint._has_light_lettering_on_midtone(np.full((100, 100), 105, np.uint8))
    bright_corners = balloon.copy()
    bright_corners[:, :20] = 255
    assert not scanlate_inpaint._has_light_lettering_on_midtone(bright_corners)
