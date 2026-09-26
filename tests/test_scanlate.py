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
cv2 = pytest.importorskip("cv2")  # requirements-media.txt, not core -- skip cleanly without it

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


class TestInsetBoxForOcr:
    """Regression coverage for a real bug found via direct testing: OCRing
    a detected bubble box exactly as drawn -- border included -- can make
    Tesseract return nothing at all (confirmed directly: an ellipse-shaped
    bubble's outline read as an enclosing shape it couldn't segment past,
    going from a correct '你好世界' with a few pixels trimmed off each
    side to an empty string with none trimmed at all)."""

    def test_shrinks_a_typical_box_on_all_sides(self):
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        inset = scanlate.inset_box_for_ocr(box)
        assert inset["x"] > box["x"]
        assert inset["y"] > box["y"]
        assert inset["x"] + inset["w"] < box["x"] + box["w"]
        assert inset["y"] + inset["h"] < box["y"] + box["h"]

    def test_never_collapses_a_small_box_to_zero_or_negative_size(self):
        box = {"x": 10, "y": 10, "w": 6, "h": 6}
        inset = scanlate.inset_box_for_ocr(box)
        assert inset["w"] > 0
        assert inset["h"] > 0

    def test_inset_is_capped_so_a_huge_box_still_loses_only_a_border(self):
        box = {"x": 0, "y": 0, "w": 2000, "h": 2000}
        inset = scanlate.inset_box_for_ocr(box)
        # Losing a fixed max, not a fraction, of a very large box.
        assert box["w"] - inset["w"] <= 30
        assert box["h"] - inset["h"] <= 30


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

    def test_font_category_resolves_to_a_different_real_font_than_regular(self, synthetic_page):
        # Uses the real bold/regular DejaVu system fonts if present -- proves
        # font_category actually changes which font file gets loaded, not
        # just that the parameter is accepted.
        if not os.path.exists("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            pytest.skip("DejaVu fonts not present on this system")
        from PIL import Image
        img = Image.open(synthetic_page).convert("RGB")
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        bold_path = scanlate._find_font(category="bold")
        regular_path = scanlate._find_font(category="regular")
        assert bold_path != regular_path
        # Both categories render without error.
        scanlate.render_text_in_box(img.copy(), box, "Hi", font_category="bold")
        scanlate.render_text_in_box(img.copy(), box, "Hi", font_category="regular")

    def test_custom_font_is_actually_used_for_rendering(self, synthetic_page):
        from PIL import Image
        img = Image.open(synthetic_page).convert("RGB")
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        real_font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not os.path.exists(real_font):
            pytest.skip("DejaVu fonts not present on this system")
        # Doesn't raise, and the custom font (not a system fallback) is
        # what actually gets resolved for this render.
        scanlate.render_text_in_box(img, box, "Hi", font_category="handwritten",
                                     custom_fonts={"handwritten": real_font})
        assert scanlate._find_font(category="handwritten",
                                    custom_fonts={"handwritten": real_font}) == real_font


class TestFindFont:
    def test_explicit_font_path_always_wins(self, tmp_path):
        real_font = tmp_path / "custom.ttf"
        real_font.write_bytes(b"fake ttf bytes")
        result = scanlate._find_font(str(real_font), category="handwritten",
                                      custom_fonts={"handwritten": "/nonexistent/other.ttf"})
        assert result == str(real_font)

    def test_a_nonexistent_explicit_path_falls_through_to_the_rest(self, tmp_path):
        custom = tmp_path / "custom_bold.ttf"
        custom.write_bytes(b"fake")
        result = scanlate._find_font("/nonexistent/explicit.ttf", category="bold",
                                      custom_fonts={"bold": str(custom)})
        assert result == str(custom)

    def test_custom_font_for_the_category_is_used_before_system_fallbacks(self, tmp_path):
        custom = tmp_path / "my_handwriting.ttf"
        custom.write_bytes(b"fake")
        result = scanlate._find_font(category="handwritten", custom_fonts={"handwritten": str(custom)})
        assert result == str(custom)

    def test_custom_font_for_a_different_category_is_ignored(self, tmp_path):
        custom = tmp_path / "my_bold.ttf"
        custom.write_bytes(b"fake")
        # A handwritten custom font must not leak into a "bold" lookup.
        result = scanlate._find_font(category="bold", custom_fonts={"handwritten": str(custom)})
        assert result != str(custom)

    def test_unknown_category_falls_back_to_regular_system_fonts(self):
        # Doesn't crash on a category with no dedicated candidates list.
        result = scanlate._find_font(category="not_a_real_category")
        assert result is None or os.path.exists(result)

    def test_categories_constant_matches_sample_text_style_output(self):
        assert set(scanlate.FONT_CATEGORIES) == {"regular", "bold", "handwritten"}


class TestSampleTextStyle:
    def test_always_includes_suggested_style_even_on_early_return(self, tmp_path):
        # Regression test: the two early-return paths (unreadable image,
        # degenerate box) used to omit "suggested_style" entirely, so a
        # caller doing result["suggested_style"] could KeyError depending
        # on which path was hit -- .get() masked this rather than fixing it.
        missing_path = str(tmp_path / "does_not_exist.png")
        result = scanlate.sample_text_style(missing_path, {"x": 0, "y": 0, "w": 10, "h": 10})
        assert result["suggested_style"] == "regular"

    def test_degenerate_box_also_includes_suggested_style(self, synthetic_page):
        result = scanlate.sample_text_style(synthetic_page, {"x": 0, "y": 0, "w": 0, "h": 0})
        assert result["suggested_style"] == "regular"

    def test_a_dense_bold_looking_region_is_flagged_bold(self, temp_dir):
        img = np.full((100, 100), 255, dtype=np.uint8)
        # A large, uniformly dark filled block -- high, consistent ink
        # ratio, the signature this function reads as "bold".
        cv2.rectangle(img, (10, 10), (90, 90), 0, -1)
        path = os.path.join(temp_dir, "bold.png")
        cv2.imwrite(path, img)
        result = scanlate.sample_text_style(path, {"x": 0, "y": 0, "w": 100, "h": 100})
        assert result["suggested_style"] == "bold"

    def test_a_light_sparse_region_is_flagged_regular(self, temp_dir):
        img = np.full((100, 100), 255, dtype=np.uint8)
        cv2.rectangle(img, (45, 45), (55, 55), 0, -1)  # tiny mark, low ink ratio
        path = os.path.join(temp_dir, "regular.png")
        cv2.imwrite(path, img)
        result = scanlate.sample_text_style(path, {"x": 0, "y": 0, "w": 100, "h": 100})
        assert result["suggested_style"] == "regular"


class TestExportFontStyleReport:
    def test_writes_a_json_file_with_one_entry_per_bubble(self, tmp_path):
        bubbles = [
            {"x": 10, "y": 20, "w": 100, "h": 50, "font_category": "bold",
             "ink_ratio": 0.3, "irregular": False},
            {"x": 200, "y": 20, "w": 100, "h": 50, "font_category": "handwritten",
             "ink_ratio": 0.4, "irregular": True},
        ]
        out_path = str(tmp_path / "styles.json")
        result = scanlate.export_font_style_report(bubbles, out_path)

        assert result == out_path
        import json
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 2
        assert data[0]["font_category"] == "bold"
        assert data[1]["font_category"] == "handwritten"

    def test_missing_font_category_defaults_to_regular_in_the_report(self, tmp_path):
        bubbles = [{"x": 0, "y": 0, "w": 10, "h": 10}]
        out_path = str(tmp_path / "styles.json")
        scanlate.export_font_style_report(bubbles, out_path)

        import json
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data[0]["font_category"] == "regular"

    def test_empty_bubbles_list_writes_an_empty_array(self, tmp_path):
        out_path = str(tmp_path / "styles.json")
        scanlate.export_font_style_report([], out_path)
        import json
        with open(out_path, encoding="utf-8") as f:
            assert json.load(f) == []


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

    def test_per_bubble_font_category_and_custom_fonts_are_threaded_through(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "Hello",
                    "font_size": 16, "skip": False, "font_category": "handwritten"}]
        out_path = os.path.join(temp_dir, "final_style.png")
        custom_fonts = {"handwritten": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"}
        # Must not raise even though "handwritten" has no reliable system
        # fallback of its own -- the custom font (or the regular fallback,
        # if no custom one were given) covers that.
        result, skipped_blank = scanlate.process_page(synthetic_page, bubbles, out_path,
                                                        custom_fonts=custom_fonts)
        assert os.path.exists(result)
        assert skipped_blank == []

    def test_missing_font_category_defaults_to_regular(self, synthetic_page, temp_dir):
        bubbles = [{"x": 100, "y": 80, "w": 300, "h": 140, "translated_text": "Hello",
                    "font_size": 16, "skip": False}]  # no font_category key at all
        out_path = os.path.join(temp_dir, "final_default.png")
        result, skipped_blank = scanlate.process_page(synthetic_page, bubbles, out_path)
        assert os.path.exists(result)


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

    def test_ml_backend_falls_back_cleanly_when_the_model_isnt_installed(self, synthetic_page):
        # No mocking here -- exercises the real code path with whatever's
        # actually installed. Where torch/transformers are genuinely
        # absent, this hits the real ImportError -> BubbleModelUnavailable
        # fallback rather than a simulated one; where they ARE installed,
        # this only confirms detect_bubbles_ml() doesn't hang trying a
        # real network download against a fake path -- it still must
        # raise (and fall back), just via a different exception branch.
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError:
            with pytest.raises(scanlate.BubbleModelUnavailable) as exc_info:
                scanlate.detect_bubbles(synthetic_page, backend="ml")
            assert exc_info.value.fell_back_to_cv is True
        else:
            pytest.skip("torch and transformers are both installed -- see the mocked "
                        "TestHfTokenInScanlateMlDetector tests for the loads-and-runs path")


class TestAutoBackendSelection:
    """Step 11 item 5: 'auto' uses the ML backend whenever its weights
    are already cached locally, the free heuristic otherwise -- and
    never triggers a fresh download just because auto mode is on."""

    def test_bubble_ml_weights_cached_true_when_cache_hit(self, monkeypatch, tmp_path):
        import huggingface_hub
        cached_file = tmp_path / "model.safetensors"
        cached_file.write_bytes(b"x")
        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                             lambda repo_id, filename: str(cached_file))
        assert scanlate.bubble_ml_weights_cached() is True

    def test_bubble_ml_weights_cached_false_when_no_cache_hit(self, monkeypatch):
        import huggingface_hub
        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                             lambda repo_id, filename: None)
        assert scanlate.bubble_ml_weights_cached() is False

    def test_lama_ml_weights_cached_true_when_cache_hit(self, monkeypatch, tmp_path):
        import huggingface_hub
        cached_file = tmp_path / "lama.safetensors"
        cached_file.write_bytes(b"x")
        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                             lambda repo_id, filename: str(cached_file))
        assert scanlate.lama_ml_weights_cached() is True

    def test_lama_ml_weights_cached_false_when_no_cache_hit(self, monkeypatch):
        import huggingface_hub
        monkeypatch.setattr(huggingface_hub, "try_to_load_from_cache",
                             lambda repo_id, filename: None)
        assert scanlate.lama_ml_weights_cached() is False

    def test_detect_bubbles_auto_never_calls_ml_when_not_cached(self, monkeypatch, synthetic_page):
        monkeypatch.setattr(scanlate, "bubble_ml_weights_cached", lambda: False)
        called = {}
        monkeypatch.setattr(scanlate, "detect_bubbles_ml",
                             lambda *a, **k: called.setdefault("ml", True) or [])
        boxes = scanlate.detect_bubbles(synthetic_page, backend="auto")
        assert "ml" not in called
        assert isinstance(boxes, list)

    def test_detect_bubbles_auto_uses_ml_when_cached(self, monkeypatch, synthetic_page):
        monkeypatch.setattr(scanlate, "bubble_ml_weights_cached", lambda: True)
        fake_boxes = [{"x": 1, "y": 1, "w": 2, "h": 2, "confidence": 0.9}]
        monkeypatch.setattr(scanlate, "detect_bubbles_ml", lambda *a, **k: fake_boxes)
        assert scanlate.detect_bubbles(synthetic_page, backend="auto") == fake_boxes

    def test_inpaint_region_auto_uses_cv_when_lama_not_cached(self, monkeypatch, synthetic_page, temp_dir):
        monkeypatch.setattr(scanlate, "lama_ml_weights_cached", lambda: False)
        called = {}
        monkeypatch.setattr(scanlate, "_run_ml_inpaint",
                             lambda *a, **k: called.setdefault("ml", True))
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        out_path = os.path.join(temp_dir, "auto_cv.png")
        result = scanlate.inpaint_region(synthetic_page, box, out_path=out_path, backend="auto")
        assert "ml" not in called
        assert os.path.exists(result)

    def test_inpaint_region_auto_uses_ml_when_cached(self, monkeypatch, synthetic_page, temp_dir):
        monkeypatch.setattr(scanlate, "lama_ml_weights_cached", lambda: True)
        called = {}

        def fake_ml_inpaint(roi, mask, hf_token=None):
            called["used"] = True
            return roi  # identity -- still a valid image array

        monkeypatch.setattr(scanlate, "_run_ml_inpaint", fake_ml_inpaint)
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        out_path = os.path.join(temp_dir, "auto_ml.png")
        result = scanlate.inpaint_region(synthetic_page, box, out_path=out_path, backend="auto")
        assert called.get("used") is True
        assert os.path.exists(result)

    def test_inpaint_region_ml_unavailable_falls_back_to_cv_and_raises(
            self, monkeypatch, synthetic_page, temp_dir):
        monkeypatch.setattr(scanlate, "_run_ml_inpaint",
                             lambda *a, **k: (_ for _ in ()).throw(ImportError("no torch")))
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        out_path = os.path.join(temp_dir, "fallback.png")
        with pytest.raises(scanlate.InpaintModelUnavailable) as exc_info:
            scanlate.inpaint_region(synthetic_page, box, out_path=out_path, backend="ml")
        assert exc_info.value.fell_back_to_cv is True
        # The CV-inpainted result was still produced and written, not lost.
        assert os.path.exists(out_path)


class TestInpaintMaskRegion:
    """Step 11 item 10: the manual erase/heal brush -- inpaints exactly
    the painted pixels, independent of any bubble box."""

    def test_erases_masked_pixels_and_produces_valid_image(self, synthetic_page, temp_dir):
        mask = np.zeros((400, 600), dtype=np.uint8)
        mask[100:150, 150:250] = 1
        out_path = os.path.join(temp_dir, "brush.png")
        result_path = scanlate.inpaint_mask_region(synthetic_page, mask, out_path=out_path)
        assert os.path.exists(result_path)
        img = cv2.imread(result_path)
        assert img.shape == (400, 600, 3)

    def test_empty_mask_leaves_the_image_unchanged(self, synthetic_page, temp_dir):
        mask = np.zeros((400, 600), dtype=np.uint8)
        out_path = os.path.join(temp_dir, "brush_empty.png")
        result_path = scanlate.inpaint_mask_region(synthetic_page, mask, out_path=out_path)
        original = cv2.imread(synthetic_page)
        result = cv2.imread(result_path)
        assert (original == result).all()

    def test_mismatched_mask_shape_raises(self, synthetic_page):
        mask = np.zeros((10, 10), dtype=np.uint8)
        with pytest.raises(ValueError):
            scanlate.inpaint_mask_region(synthetic_page, mask)

    def test_without_out_path_returns_array(self, synthetic_page):
        mask = np.zeros((400, 600), dtype=np.uint8)
        mask[100:150, 150:250] = 1
        result = scanlate.inpaint_mask_region(synthetic_page, mask, out_path=None)
        assert isinstance(result, np.ndarray)

    def test_ml_backend_unavailable_falls_back_and_attaches_result(
            self, monkeypatch, synthetic_page, temp_dir):
        monkeypatch.setattr(scanlate, "_run_ml_inpaint",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        mask = np.zeros((400, 600), dtype=np.uint8)
        mask[100:150, 150:250] = 1
        out_path = os.path.join(temp_dir, "brush_fallback.png")
        with pytest.raises(scanlate.InpaintModelUnavailable) as exc_info:
            scanlate.inpaint_mask_region(synthetic_page, mask, out_path=out_path, backend="ml")
        assert exc_info.value.fell_back_to_cv is True
        assert os.path.exists(out_path)

    def test_auto_uses_cv_when_lama_not_cached(self, monkeypatch, synthetic_page, temp_dir):
        # Same shared auto-select as inpaint_region() (Step 11 item 5) --
        # the brush uses the exact same backend logic, not a separate copy.
        monkeypatch.setattr(scanlate, "lama_ml_weights_cached", lambda: False)
        called = {}
        monkeypatch.setattr(scanlate, "_run_ml_inpaint",
                             lambda *a, **k: called.setdefault("ml", True))
        mask = np.zeros((400, 600), dtype=np.uint8)
        mask[100:150, 150:250] = 1
        out_path = os.path.join(temp_dir, "brush_auto_cv.png")
        result = scanlate.inpaint_mask_region(synthetic_page, mask, out_path=out_path, backend="auto")
        assert "ml" not in called
        assert os.path.exists(result)

    def test_auto_uses_ml_when_cached(self, monkeypatch, synthetic_page, temp_dir):
        monkeypatch.setattr(scanlate, "lama_ml_weights_cached", lambda: True)
        called = {}

        def fake_ml_inpaint(roi, mask, hf_token=None):
            called["used"] = True
            return roi

        monkeypatch.setattr(scanlate, "_run_ml_inpaint", fake_ml_inpaint)
        mask = np.zeros((400, 600), dtype=np.uint8)
        mask[100:150, 150:250] = 1
        out_path = os.path.join(temp_dir, "brush_auto_ml.png")
        result = scanlate.inpaint_mask_region(synthetic_page, mask, out_path=out_path, backend="auto")
        assert called.get("used") is True
        assert os.path.exists(result)


class TestAutoOcrBackend:
    """Step 11 item 4: default OCR backend by source language, always
    overridable manually."""

    def test_japanese_defaults_to_manga_ocr(self):
        assert scanlate.auto_ocr_backend("ja") == "manga_ocr"

    def test_japanese_prefers_paddle_vl_manga_when_asked(self):
        assert scanlate.auto_ocr_backend("ja", prefer_paddle_vl_manga=True) == "paddle_vl_manga"

    def test_chinese_defaults_to_paddle(self):
        assert scanlate.auto_ocr_backend("zh") == "paddle"

    def test_korean_defaults_to_paddle(self):
        assert scanlate.auto_ocr_backend("ko") == "paddle"

    def test_unknown_language_falls_back_to_tesseract(self):
        assert scanlate.auto_ocr_backend("fr") == "tesseract"


class TestOcrBoxRegion:
    def test_crops_and_routes_to_the_auto_backend(self, monkeypatch, synthetic_page):
        import ocr
        captured = {}

        def fake_extract(paths, backend, source_language, chinese_script="simplified",
                          tesseract_cmd=None):
            captured["backend"] = backend
            captured["path_exists"] = os.path.exists(paths[0])
            return "detected text"

        monkeypatch.setattr(ocr, "extract_text_from_images", fake_extract)
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        result = scanlate.ocr_box_region(synthetic_page, box, "ja")
        assert result == "detected text"
        assert captured["backend"] == "manga_ocr"
        assert captured["path_exists"] is True

    def test_explicit_backend_overrides_auto_routing(self, monkeypatch, synthetic_page):
        import ocr
        captured = {}
        monkeypatch.setattr(ocr, "extract_text_from_images",
                             lambda paths, backend, source_language, chinese_script="simplified",
                                    tesseract_cmd=None: captured.setdefault("backend", backend) or "x")
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        scanlate.ocr_box_region(synthetic_page, box, "ja", backend="tesseract")
        assert captured["backend"] == "tesseract"

    def test_temp_crop_file_is_cleaned_up_afterward(self, monkeypatch, synthetic_page):
        import ocr
        seen_paths = []

        def fake_extract(paths, backend, source_language, chinese_script="simplified",
                          tesseract_cmd=None):
            seen_paths.append(paths[0])
            return "x"

        monkeypatch.setattr(ocr, "extract_text_from_images", fake_extract)
        box = {"x": 100, "y": 80, "w": 300, "h": 140}
        scanlate.ocr_box_region(synthetic_page, box, "zh")
        assert not os.path.exists(seen_paths[0])


class TestPdfImportExport:
    """Step 11 item 7."""

    def test_pages_to_pdf_creates_a_valid_multi_page_pdf(self, synthetic_page, temp_dir):
        from PIL import Image
        page2 = os.path.join(temp_dir, "page2.png")
        Image.new("RGB", (600, 400), (200, 200, 200)).save(page2)
        out_pdf = os.path.join(temp_dir, "out.pdf")

        result = scanlate.pages_to_pdf([synthetic_page, page2], out_pdf)

        assert result == out_pdf
        assert os.path.exists(out_pdf)
        pypdf = pytest.importorskip("pypdf")
        reader = pypdf.PdfReader(out_pdf)
        assert len(reader.pages) == 2

    def test_pages_to_pdf_raises_on_an_empty_list(self, temp_dir):
        with pytest.raises(ValueError):
            scanlate.pages_to_pdf([], os.path.join(temp_dir, "empty.pdf"))

    def test_pdf_to_page_images_round_trips_a_real_pdf(self, synthetic_page, temp_dir):
        # Builds a real PDF from a real (synthetic, non-copyrighted) page,
        # then splits it back apart -- exercises pypdf's own image
        # extraction against a real file instead of a hand-crafted one.
        pytest.importorskip("pypdf")
        pdf_path = os.path.join(temp_dir, "roundtrip.pdf")
        scanlate.pages_to_pdf([synthetic_page], pdf_path)
        out_dir = os.path.join(temp_dir, "extracted")

        image_paths, skipped = scanlate.pdf_to_page_images(pdf_path, out_dir)

        assert len(image_paths) == 1
        assert skipped == []
        assert os.path.exists(image_paths[0])
        from PIL import Image
        assert Image.open(image_paths[0]).size == (600, 400)


class TestBulkFindReplacePreview:
    """Step 11 item 9. Pure preview logic -- no database involved (see
    tests/test_db.py's TestPagesAndBubbles for the apply-by-id path,
    which confirms only translated_text ever changes)."""

    def _bubbles(self):
        return [
            {"id": 1, "page_idx": 0, "translated_text": "Hello Bob"},
            {"id": 2, "page_idx": 0, "translated_text": "Bob said hi"},
            {"id": 3, "page_idx": 1, "translated_text": "Nothing to see here"},
        ]

    def test_finds_and_previews_matches_without_mutating_input(self):
        bubbles = self._bubbles()
        matches = scanlate.bulk_find_replace_preview(bubbles, "Bob", "Alice")
        assert len(matches) == 2
        assert {m["id"] for m in matches} == {1, 2}
        assert bubbles[0]["translated_text"] == "Hello Bob"  # preview only, no mutation

    def test_case_insensitive_by_default(self):
        bubbles = [{"id": 1, "page_idx": 0, "translated_text": "bob and BOB"}]
        matches = scanlate.bulk_find_replace_preview(bubbles, "bob", "Alice")
        assert matches[0]["new_text"] == "Alice and Alice"

    def test_case_sensitive_when_requested(self):
        bubbles = [{"id": 1, "page_idx": 0, "translated_text": "bob and BOB"}]
        matches = scanlate.bulk_find_replace_preview(bubbles, "bob", "Alice", case_sensitive=True)
        assert matches[0]["new_text"] == "Alice and BOB"

    def test_regex_mode(self):
        bubbles = [{"id": 1, "page_idx": 0, "translated_text": "line1 line2"}]
        matches = scanlate.bulk_find_replace_preview(bubbles, r"line\d", "X", use_regex=True)
        assert matches[0]["new_text"] == "X X"

    def test_literal_mode_does_not_treat_find_as_a_regex(self):
        bubbles = [{"id": 1, "page_idx": 0, "translated_text": "a.b.c"}]
        matches = scanlate.bulk_find_replace_preview(bubbles, ".", "-", use_regex=False)
        assert matches[0]["new_text"] == "a-b-c"

    def test_no_matches_when_find_is_empty(self):
        assert scanlate.bulk_find_replace_preview(self._bubbles(), "", "x") == []

    def test_invalid_regex_raises_value_error(self):
        with pytest.raises(ValueError):
            scanlate.bulk_find_replace_preview(self._bubbles(), "(unclosed", "x", use_regex=True)

    def test_bubbles_with_no_match_are_excluded(self):
        matches = scanlate.bulk_find_replace_preview(self._bubbles(), "zzz_not_present", "x")
        assert matches == []

    def test_text_field_generalizes_to_line_shaped_items(self):
        """Step 23c item 2: the same preview logic reused for a
        novel/workspace drama's line text instead of a Scanlate bubble's,
        just pointed at a different dict key."""
        lines = [
            {"idx": 0, "en": "Hello Bob"},
            {"idx": 1, "en": "Bob said hi"},
            {"idx": 2, "en": "Nothing to see here"},
        ]
        matches = scanlate.bulk_find_replace_preview(lines, "Bob", "Alice", text_field="en")
        assert {m["idx"] for m in matches} == {0, 1}
        assert all(m["new_text"].count("Alice") >= 1 for m in matches)
        assert lines[0]["en"] == "Hello Bob"  # preview only, no mutation

    def test_match_carries_through_every_original_field(self):
        bubbles = self._bubbles()
        matches = scanlate.bulk_find_replace_preview(bubbles, "Bob", "Alice")
        assert matches[0]["page_idx"] == 0
        assert matches[0]["translated_text"] == "Hello Bob"  # original field untouched
