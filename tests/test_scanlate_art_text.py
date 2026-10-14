"""Captions drawn on the artwork (scanlate_art_text): grouping, detection,
painting out, style matching and drawing. PaddleOCR and LaMa are faked: the real
weights are never loaded here."""

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("PIL")

import cv2
from PIL import Image

import ocr
import scanlate
import scanlate_art_text as art
import scanlate_inpaint
import scanlate_render


def _line(x, y, w, h, text):
    return {"x": x, "y": y, "w": w, "h": h, "text": text}


def _dark_page_with_white_text(w=600, h=400):
    page = np.zeros((h, w, 3), np.uint8)
    cv2.putText(page, "BOOM", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 12)
    return page


class TestGroupLines:
    def test_stacked_lines_of_one_size_join_in_reading_order(self):
        out = art.group_lines([_line(10, 60, 300, 40, "BB"), _line(12, 10, 296, 42, "AA")])
        assert len(out) == 1 and out[0]["source_text"] == "AABB"
        assert (out[0]["x"], out[0]["y"], out[0]["h"]) == (10, 10, 90)

    def test_a_much_larger_line_stays_apart(self):
        out = art.group_lines([_line(10, 10, 300, 40, "small"), _line(10, 60, 300, 120, "BIG")])
        assert len(out) == 2

    def test_vertical_columns_never_merge(self):
        out = art.group_lines([_line(10, 10, 40, 200, "魔王"), _line(10, 220, 40, 200, "来了")])
        assert len(out) == 2

    def test_far_apart_lines_stay_apart(self):
        out = art.group_lines([_line(10, 10, 300, 40, "a"), _line(10, 400, 300, 40, "b")])
        assert len(out) == 2


class TestFindArtCaptions:
    def _fake(self, monkeypatch, lines):
        seen = {}

        def paddle_lines(path, lang, scale):
            seen["scale"] = scale
            return lines
        monkeypatch.setattr(ocr, "paddle_lines", paddle_lines)
        return seen

    def test_drops_low_confidence_tiny_and_already_covered_lines(self, monkeypatch):
        page = np.zeros((300, 400, 3), np.uint8)
        self._fake(monkeypatch, [
            ("keep", 0.9, (10, 10, 200, 60)),
            ("unsure", 0.3, (10, 100, 200, 60)),
            ("watermark", 0.9, (10, 200, 200, 10)),
            ("in a bubble", 0.9, (250, 200, 100, 50)),
        ])
        found = art.find_art_captions("p.png", "zh", [{"x": 240, "y": 190, "w": 150, "h": 80}],
                                      image=page)
        assert [c["source_text"] for c in found] == ["keep"]

    def test_tall_pages_are_detected_at_a_capped_size(self, monkeypatch):
        seen = self._fake(monkeypatch, [])
        art.find_art_captions("p.png", "zh", [], image=np.zeros((2560, 1280, 3), np.uint8))
        assert seen["scale"] == pytest.approx(0.5)


    def test_a_non_ascii_page_path_is_decoded_once_and_the_array_is_what_paddle_reads(
            self, tmp_path, monkeypatch):
        path = tmp_path / "页面_0001.png"
        Image.new("RGB", (200, 100), (10, 10, 10)).save(path)
        got = {}

        def paddle_lines(image, lang, scale):
            got["shape"] = image.shape
            return []
        monkeypatch.setattr(ocr, "paddle_lines", paddle_lines)
        art.find_art_captions(str(path), "zh", [])
        assert got["shape"] == (100, 200, 3)


class TestPaddleLines:
    @pytest.fixture
    def fake_paddle(self, monkeypatch):
        import sys
        import types
        seen = {}

        class FakePaddle:
            result = []

            def __init__(self, **kw):
                pass

            def predict(self, source):
                seen["source"] = source
                return self.result
        mod = types.ModuleType("paddleocr")
        mod.PaddleOCR = FakePaddle
        monkeypatch.setitem(sys.modules, "paddleocr", mod)
        monkeypatch.setattr(ocr, "_paddle_line_instances", {}, raising=False)
        return FakePaddle, seen

    def test_reads_the_array_it_is_given_scaled_and_maps_boxes_back(self, fake_paddle):
        paddle, seen = fake_paddle
        paddle.result = [{"rec_texts": ["hi"], "rec_scores": [0.9],
                          "rec_polys": [[[10, 20], [60, 20], [60, 40], [10, 40]]]}]
        out = ocr.paddle_lines(np.zeros((200, 100, 3), np.uint8), "zh", 0.5)
        assert seen["source"].shape == (100, 50, 3)
        assert out == [("hi", 0.9, (20, 40, 100, 40))]

    def test_texts_are_not_paired_with_boxes_when_the_counts_differ(self, fake_paddle):
        paddle, _ = fake_paddle
        paddle.result = [{"rec_texts": ["a", "b"], "rec_scores": [0.9, 0.9],
                          "rec_polys": [[[0, 0], [5, 0], [5, 5], [0, 5]]],
                          "dt_polys": [[[0, 0], [5, 0], [5, 5], [0, 5]]] * 3}]
        assert ocr.paddle_lines(np.zeros((50, 50, 3), np.uint8)) == []


class TestMaskAndStyle:
    def test_mask_covers_the_lettering_and_a_margin_only(self):
        page = _dark_page_with_white_text()
        box = {"x": 90, "y": 140, "w": 330, "h": 80}
        mask = art.caption_mask(page, box)
        assert mask[180, 130] > 0 and mask[5, 5] == 0

    def test_light_lettering_on_dark_gets_its_colour_and_a_glow(self):
        page = _dark_page_with_white_text()
        page[page.max(axis=2) < 50] = (0, 0, 40)  # faint red haze around the letters
        style = art.caption_style(page, {"x": 90, "y": 140, "w": 330, "h": 80})
        assert min(style["fill"]) > 200
        assert style["glow"] is not None
        assert style["align"] == "center" and style["line_h"] > 0

    def test_dark_lettering_on_light_has_no_glow(self):
        page = np.full((300, 600, 3), 235, np.uint8)
        cv2.putText(page, "BOOM", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 4, (20, 20, 20), 12)
        style = art.caption_style(page, {"x": 90, "y": 120, "w": 330, "h": 100})
        assert style["glow"] is None and max(style["fill"]) < 100

    def test_line_layout_reads_left_alignment_and_line_height(self):
        ink = np.zeros((100, 200), bool)
        ink[10:30, 0:150] = True
        ink[50:70, 0:100] = True
        height, align = art._line_layout(ink, {"x": 0, "y": 0, "w": 200, "h": 100})
        assert (height, align) == (20, "left")

    def test_vertical_text_uses_its_column_width(self):
        ink = np.ones((300, 40), bool)
        assert art._line_layout(ink, {"x": 0, "y": 0, "w": 40, "h": 300}) == (40, "center")


class TestPaintOut:
    def test_without_the_model_opencv_paints_and_reports_it(self, monkeypatch):
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "opencv")
        page = _dark_page_with_white_text()
        done_by_model = art.paint_out_captions(page, [{"x": 90, "y": 140, "w": 330, "h": 80}])
        assert done_by_model is False
        assert page[180, 130].max() < 100        # the letters are gone

    def test_a_caption_too_large_for_the_model_falls_back_alone(self, monkeypatch):
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "ml")

        def too_big(roi, mask, **kw):
            raise ValueError("too large")
        monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint", too_big)
        page = _dark_page_with_white_text()
        assert art.paint_out_captions(page, [{"x": 90, "y": 140, "w": 330, "h": 80}]) is False
        assert page[180, 130].max() < 100

    def test_the_model_result_is_used_and_never_downloads(self, monkeypatch):
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "ml")
        kwargs = {}

        def fake(roi, mask, **kw):
            kwargs.update(kw)
            return np.full_like(roi, 7)
        monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint", fake)
        page = _dark_page_with_white_text()
        assert art.paint_out_captions(page, [{"x": 90, "y": 140, "w": 330, "h": 80}]) is True
        assert kwargs == {"allow_download": False}
        assert page[180, 130].tolist() == [7, 7, 7]


class TestDrawCaption:
    def test_english_lands_inside_the_original_box_in_the_original_colour(self):
        img = Image.new("RGB", (600, 400), (0, 0, 0))
        box = {"x": 90, "y": 140, "w": 330, "h": 80}
        art.draw_caption(img, box, "The Demon King",
                         {"fill": (255, 40, 40), "glow": None, "line_h": 60, "align": "center"})
        arr = np.array(img)
        inked = np.argwhere(arr[:, :, 0] > 200)
        assert len(inked)
        assert inked[:, 1].min() >= box["x"] - 40 and inked[:, 1].max() <= box["x"] + box["w"] + 40
        assert arr[:, :, 1].max() < 100         # only the fill colour, no white

    def test_vertical_caption_reads_across_its_centre(self):
        img = Image.new("RGB", (800, 600), (0, 0, 0))
        box = {"x": 300, "y": 100, "w": 100, "h": 400}
        art.draw_caption(img, box, "Demon King", {"fill": (255, 255, 255), "glow": None,
                                                  "line_h": 100, "align": "center"})
        cols = np.argwhere(np.array(img).max(axis=2) > 200)[:, 1]
        assert cols.min() < box["x"] and cols.max() > box["x"] + box["w"]


class TestDetectAndOcrWithCaptions:
    @pytest.fixture
    def page_file(self, tmp_path):
        path = tmp_path / "p.png"
        Image.new("RGB", (300, 300), (30, 30, 30)).save(path)
        return str(path)

    @pytest.fixture(autouse=True)
    def no_bubbles(self, monkeypatch):
        monkeypatch.setattr(scanlate, "detect_bubbles", lambda *a, **k: [])

    def test_off_by_default_it_never_looks_for_captions(self, page_file, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("captions searched")
        monkeypatch.setattr(art, "find_art_captions", boom)
        bubbles, notes = scanlate.detect_and_ocr_page(page_file, "zh")
        assert bubbles == []

    def test_on_it_returns_caption_regions_that_need_translating(self, page_file, monkeypatch):
        monkeypatch.setattr(art, "find_art_captions", lambda *a, **k: [
            {"x": 10, "y": 20, "w": 200, "h": 50, "source_text": "世界破裂"},
            {"x": 10, "y": 100, "w": 40, "h": 150, "source_text": "魔王"}])
        bubbles, notes = scanlate.detect_and_ocr_page(page_file, "zh", art_captions=True)
        assert [b["kind"] for b in bubbles] == ["caption", "caption"]
        assert [b["source_text"] for b in bubbles] == ["世界破裂", "魔王"]
        assert [b["orientation"] for b in bubbles] == ["horizontal", "vertical"]
        assert all(b["translated_text"] == "" and not b["skip"] for b in bubbles)

    def test_a_detector_failure_keeps_the_page_and_adds_a_note(self, page_file, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("paddle broke")
        monkeypatch.setattr(art, "find_art_captions", boom)
        bubbles, notes = scanlate.detect_and_ocr_page(page_file, "zh", art_captions=True)
        assert bubbles == [] and any("Captions on the art" in m for _, m in notes)
        assert not any("paddle broke" in m for _, m in notes)


class TestProcessPageWithCaptions:
    def test_caption_is_painted_out_and_english_drawn(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "opencv")
        src = tmp_path / "p.png"
        cv2.imwrite(str(src), _dark_page_with_white_text())
        box = {"x": 90, "y": 140, "w": 330, "h": 80, "kind": "caption", "skip": False,
               "translated_text": "BANG", "font_size": 18, "source_text": "砰"}
        notes = []
        out = tmp_path / "out.png"
        scanlate_render.process_page(str(src), [box], str(out), notes=notes)
        assert out.exists()
        assert any(code == "inpaint_fallback" for _, code, _ in
                   [n if len(n) == 3 else (n[0], "", n[1]) for n in notes])

    def test_a_caption_without_english_is_left_alone(self, tmp_path):
        src = tmp_path / "p.png"
        page = _dark_page_with_white_text()
        cv2.imwrite(str(src), page)
        box = {"x": 90, "y": 140, "w": 330, "h": 80, "kind": "caption", "skip": False,
               "translated_text": "", "font_size": 18, "source_text": "砰"}
        out = tmp_path / "out.png"
        scanlate_render.process_page(str(src), [box], str(out))
        assert np.array_equal(cv2.imread(str(out))[180, 130], page[180, 130])
