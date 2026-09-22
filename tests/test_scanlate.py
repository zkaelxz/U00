"""
tests/test_scanlate.py -- tests for scanlate.py using real synthetic
manga-page-like images built with OpenCV, not mocks. This exercises
the actual image processing (detection, inpainting, rendering).
"""

import sys
import os
import tempfile
import shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import numpy as np
import cv2

import scanlate


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="baihe_scanlate_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def synthetic_page(temp_dir):
    """A synthetic 'manga page': dark background, one white speech
    bubble with scribble marks simulating text -- entirely generated
    shapes, no copyrighted content of any kind."""
    img = np.full((400, 600, 3), 50, dtype=np.uint8)
    cv2.rectangle(img, (100, 80), (400, 220), (255, 255, 255), -1)
    cv2.rectangle(img, (100, 80), (400, 220), (0, 0, 0), 3)
    for i in range(6):
        y = 110 + i * 15
        cv2.line(img, (130, y), (350, y), (10, 10, 10), 4)
    path = os.path.join(temp_dir, "page.png")
    cv2.imwrite(path, img)
    return path


class TestDetectBubblesCv:
    def test_detects_the_synthetic_bubble(self, synthetic_page):
        boxes = scanlate.detect_bubbles_cv(synthetic_page)
        assert len(boxes) >= 1

    def test_detected_box_roughly_matches_drawn_bubble(self, synthetic_page):
        boxes = scanlate.detect_bubbles_cv(synthetic_page)
        box = boxes[0]
        # bubble was drawn at (100,80)-(400,220): 300 wide, 140 tall
        assert 250 < box["w"] < 320
        assert 100 < box["h"] < 160

    def test_blank_image_finds_nothing(self, temp_dir):
        blank = np.full((200, 200, 3), 128, dtype=np.uint8)
        path = os.path.join(temp_dir, "blank.png")
        cv2.imwrite(path, blank)
        boxes = scanlate.detect_bubbles_cv(path)
        assert boxes == []

    def test_missing_file_raises_clear_error(self):
        with pytest.raises(ValueError):
            scanlate.detect_bubbles_cv("/nonexistent/path/does_not_exist.png")


class TestInpaintRegion:
    def test_inpaint_produces_valid_image(self, synthetic_page, temp_dir):
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        out_path = os.path.join(temp_dir, "cleaned.png")
        result_path = scanlate.inpaint_region(synthetic_page, box, out_path=out_path)
        assert os.path.exists(result_path)
        result_img = cv2.imread(result_path)
        assert result_img is not None
        assert result_img.shape == (400, 600, 3)

    def test_inpaint_without_out_path_returns_array(self, synthetic_page):
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        result = scanlate.inpaint_region(synthetic_page, box, out_path=None)
        assert isinstance(result, np.ndarray)


class TestRenderTextInBox:
    def test_render_produces_correctly_sized_image(self, synthetic_page):
        from PIL import Image
        img = Image.open(synthetic_page).convert("RGB")
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        result = scanlate.render_text_in_box(img, box, "A reasonably short test line.")
        assert result.size == (600, 400)  # unchanged canvas size

    def test_render_handles_empty_text_without_crashing(self, synthetic_page):
        from PIL import Image
        img = Image.open(synthetic_page).convert("RGB")
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        # should not raise even with nothing to draw
        scanlate.render_text_in_box(img, box, "")

    def test_render_shrinks_font_for_long_text_in_small_box(self, synthetic_page):
        from PIL import Image
        img = Image.open(synthetic_page).convert("RGB")
        small_box = {"x": 100, "y": 80, "w": 80, "h": 30}
        long_text = "This is a much longer sentence than the tiny box can comfortably fit."
        # should not raise -- font auto-shrink should handle it gracefully
        scanlate.render_text_in_box(img, small_box, long_text, font_size=24)


class TestProcessPage:
    # process_page returns (out_path, skipped_blank) -- skipped_blank lists
    # bubbles that had no translated text and were left untouched rather
    # than erased-with-nothing-put-back. These tests went stale when that
    # signature changed (it used to return a bare path) and weren't
    # updated at the time -- caught only by actually running the full
    # suite, not by the fix's own standalone verification.
    def test_full_pipeline_produces_output_file(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "Hello world",
                    "font_size": 16, "skip": False}]
        out_path = os.path.join(temp_dir, "final.png")
        result, skipped_blank = scanlate.process_page(synthetic_page, bubbles, out_path)
        assert os.path.exists(result)
        assert skipped_blank == []  # real translated text -- nothing should be reported blank

    def test_skipped_bubble_is_not_rendered(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "Should not appear",
                    "font_size": 16, "skip": True}]
        out_path = os.path.join(temp_dir, "final_skip.png")
        # should complete without error even though the only bubble is skipped
        result, skipped_blank = scanlate.process_page(synthetic_page, bubbles, out_path)
        assert os.path.exists(result)
        assert skipped_blank == []  # explicitly skipped, not blank -- different category

    def test_empty_bubbles_list_still_produces_output(self, synthetic_page, temp_dir):
        out_path = os.path.join(temp_dir, "final_empty.png")
        result, skipped_blank = scanlate.process_page(synthetic_page, [], out_path)
        assert os.path.exists(result)
        assert skipped_blank == []

    def test_blank_translated_text_is_reported_and_left_untouched(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "",
                    "font_size": 16, "skip": False}]
        out_path = os.path.join(temp_dir, "final_blank.png")
        result, skipped_blank = scanlate.process_page(synthetic_page, bubbles, out_path)
        assert os.path.exists(result)
        assert len(skipped_blank) == 1

    def test_cleans_up_temp_working_file(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "Hi",
                    "font_size": 16, "skip": False}]
        out_path = os.path.join(temp_dir, "final.png")
        scanlate.process_page(synthetic_page, bubbles, out_path)
        assert not os.path.exists(out_path + ".tmp.png")  # working file cleaned up


class TestTranslatePageWithContext:
    def test_pure_mt_engine_falls_back_without_context(self):
        class PureMT:
            supports_reference = False
            def translate_batch(self, texts, context):
                return [f"MT:{t}" for t in texts]

        translations, new_context = scanlate.translate_page_with_context(
            ["你好"], PureMT(), {}, previous_context="")
        assert translations == ["MT:你好"]
        assert new_context == ""  # unchanged, since pure MT can't summarize


class TestBulkRenderPages:
    # bulk_render_pages returns (zip_path, errors, blank_text_report) --
    # blank_text_report maps filename -> count of bubbles left untouched
    # for having no translated text. Same staleness issue as TestProcessPage:
    # these predate that third return value and weren't updated when it
    # was added, caught only by running the full suite.
    def test_renders_multiple_pages_and_zips(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "Page text",
                    "font_size": 16, "skip": False}]
        pages = [(synthetic_page, bubbles, "out1.png"), (synthetic_page, bubbles, "out2.png")]
        out_dir = os.path.join(temp_dir, "bulk_out")
        zip_path, errors, blank_report = scanlate.bulk_render_pages(pages, out_dir)
        assert os.path.exists(zip_path)
        assert errors == []
        assert blank_report == {}  # real translated text on both pages

        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert "out1.png" in names
            assert "out2.png" in names

    def test_one_bad_page_does_not_stop_the_rest(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "OK",
                    "font_size": 16, "skip": False}]
        pages = [
            ("/nonexistent/bad_page.png", bubbles, "bad.png"),  # will fail
            (synthetic_page, bubbles, "good.png"),               # should still succeed
        ]
        out_dir = os.path.join(temp_dir, "bulk_out2")
        zip_path, errors, blank_report = scanlate.bulk_render_pages(pages, out_dir)
        assert len(errors) == 1
        assert errors[0]["file"] == "bad.png"
        import zipfile
        with zipfile.ZipFile(zip_path) as zf:
            assert "good.png" in zf.namelist()
            assert "bad.png" not in zf.namelist()

    def test_blank_text_bubbles_reported_per_page(self, synthetic_page, temp_dir):
        blank_bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "",
                          "font_size": 16, "skip": False}]
        pages = [(synthetic_page, blank_bubbles, "blank_page.png")]
        out_dir = os.path.join(temp_dir, "bulk_out3")
        _, errors, blank_report = scanlate.bulk_render_pages(pages, out_dir)
        assert errors == []
        assert blank_report == {"blank_page.png": 1}


class TestRealisticPageDetection:
    """Regression cover for a real failure: the detector was only ever
    validated against a white-bubble-on-dark-background synthetic image.
    Actual manga pages are mostly white, so thresholding for 'bright'
    grabbed the whole page as one blob and discarded it -- zero bubbles
    found on every real page."""

    def _realistic_page(self, d):
        import numpy as np, cv2, os
        page = np.full((1200, 850, 3), 250, dtype=np.uint8)
        cv2.rectangle(page, (40, 40), (810, 560), (30, 30, 30), 3)
        cv2.rectangle(page, (40, 600), (810, 1160), (30, 30, 30), 3)
        cv2.rectangle(page, (60, 60), (790, 540), (170, 170, 170), -1)
        cv2.rectangle(page, (60, 620), (790, 1140), (150, 150, 150), -1)
        for cx, cy, rx, ry in [(250, 170, 150, 80), (560, 760, 170, 95)]:
            cv2.ellipse(page, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(page, (cx, cy), (rx, ry), 0, 0, 360, (20, 20, 20), 3)
        p = os.path.join(d, "real.png")
        cv2.imwrite(p, page)
        return p

    def test_finds_bubbles_on_a_white_background_page(self, temp_dir):
        boxes = scanlate.detect_bubbles_cv(self._realistic_page(temp_dir))
        assert len(boxes) == 2, f"expected 2 bubbles, got {len(boxes)}"

    def test_page_background_is_not_returned_as_a_bubble(self, temp_dir):
        for b in scanlate.detect_bubbles_cv(self._realistic_page(temp_dir)):
            assert b["x"] > 1 and b["y"] > 1        # never the page itself
            assert b["w"] < 850 and b["h"] < 1200

    def test_still_works_on_dark_background_pages(self, synthetic_page):
        assert len(scanlate.detect_bubbles_cv(synthetic_page)) >= 1

    def test_blank_page_finds_nothing(self, temp_dir):
        import numpy as np, cv2, os
        p = os.path.join(temp_dir, "blank.png")
        cv2.imwrite(p, np.full((400, 400, 3), 250, dtype=np.uint8))
        assert scanlate.detect_bubbles_cv(p) == []

    def test_debug_mode_returns_rejection_reasons(self, temp_dir):
        boxes, rejected = scanlate.detect_bubbles_cv(
            self._realistic_page(temp_dir), debug=True)
        assert isinstance(boxes, list) and isinstance(rejected, list)
        for r in rejected:
            assert isinstance(r[4], str) and r[4]


class TestMlBackendFallback:
    def test_unavailable_model_raises_typed_error(self):
        assert issubclass(scanlate.BubbleModelUnavailable, RuntimeError)

    def test_error_records_that_fallback_happened(self):
        exc = scanlate.BubbleModelUnavailable("nope")
        assert exc.fell_back_to_cv is True

    def test_cv_backend_never_raises_model_error(self, synthetic_page):
        assert isinstance(scanlate.detect_bubbles(synthetic_page, backend="cv"), list)
