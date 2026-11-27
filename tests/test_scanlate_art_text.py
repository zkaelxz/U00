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
from scanlate import scanlate
from scanlate import scanlate_art_text as art
from scanlate import scanlate_inpaint
from scanlate import scanlate_lama
from scanlate import scanlate_render
def _line(x, y, w, h, text):
    return {"x": x, "y": y, "w": w, "h": h, "text": text}


def _dark_page_with_white_text(w=600, h=400):
    page = np.zeros((h, w, 3), np.uint8)
    cv2.putText(page, "BOOM", (100, 200), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 12)
    return page


_COLOUR_PAGE = np.full((300, 400, 3), (200, 80, 40), np.uint8)


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
        page = _COLOUR_PAGE.copy()
        self._fake(monkeypatch, [
            ("保留的字", 0.9, (10, 10, 200, 60)),
            ("unsure", 0.3, (10, 100, 200, 60)),
            ("watermark", 0.9, (10, 200, 200, 10)),
            ("in a bubble", 0.9, (250, 200, 100, 50)),
        ])
        found = art.find_art_captions("p.png", "zh", [{"x": 240, "y": 190, "w": 150, "h": 80}],
                                      image=page)
        assert [c["source_text"] for c in found] == ["保留的字"]

    def test_rapidocr_backend_reads_the_lines_and_paddle_adds_only_site_stamps(self, monkeypatch):
        monkeypatch.setattr(ocr, "paddle_lines", lambda page, lang, scale: [
            ("Paddle's own caption", 0.9, (300, 300, 200, 60)),
            ("가장른 미리보가", 0.65, (622, 500, 154, 26))])
        monkeypatch.setattr(ocr, "rapidocr_lines", lambda page, lang, scale: [
            ("保留的字", 0.9, (10, 10, 200, 60)), ("unsure", 0.3, (10, 100, 200, 60))])
        found = art.find_art_captions("p.png", "ko", [], image=_COLOUR_PAGE.copy(),
                                      backend="rapidocr")
        assert [c["source_text"] for c in found] == ["保留的字", "가장른 미리보가"]

    def test_a_stamp_only_paddle_sees_is_not_read_twice_when_rapidocr_sees_it_too(self, monkeypatch):
        stamp = ("구글검색 뉴토끼", 0.9, (620, 520, 160, 18))
        monkeypatch.setattr(ocr, "paddle_lines", lambda page, lang, scale: [stamp])
        monkeypatch.setattr(ocr, "rapidocr_lines", lambda page, lang, scale: [stamp])
        found = art.find_art_captions("p.png", "ko", [], image=_COLOUR_PAGE.copy(), backend="rapidocr")
        assert [c["source_text"] for c in found] == ["구글검색 뉴토끼"]

    def test_the_paddle_stamp_search_can_be_left_out(self, monkeypatch):
        def never(*a, **k):
            raise AssertionError("paddle must not run")
        monkeypatch.setattr(ocr, "paddle_lines", never)
        monkeypatch.setattr(ocr, "rapidocr_lines", lambda page, lang, scale: [
            ("保留的字", 0.9, (10, 10, 200, 60))])
        found = art.find_art_captions("p.png", "zh", [], image=_COLOUR_PAGE.copy(),
                                      backend="rapidocr", find_stamps=False)
        assert [c["source_text"] for c in found] == ["保留的字"]

    def test_without_paddle_installed_the_rapidocr_lines_are_still_used(self, monkeypatch):
        def missing(*a, **k):
            raise ImportError("paddleocr")
        monkeypatch.setattr(ocr, "paddle_lines", missing)
        monkeypatch.setattr(ocr, "rapidocr_lines", lambda page, lang, scale: [
            ("保留的字", 0.9, (10, 10, 200, 60))])
        found = art.find_art_captions("p.png", "zh", [], image=_COLOUR_PAGE.copy(), backend="rapidocr")
        assert [c["source_text"] for c in found] == ["保留的字"]

    def test_specks_and_already_english_text_are_not_captions(self, monkeypatch):
        self._fake(monkeypatch, [
            ("0", 0.9, (10, 10, 40, 40)),
            ("代", 0.9, (10, 100, 45, 45)),
            ("SCHOOL", 0.9, (10, 200, 200, 40)),
            ("魔王来了", 0.9, (200, 10, 150, 60)),
        ])
        found = art.find_art_captions("p.png", "zh", [], image=_COLOUR_PAGE.copy())
        assert [c["source_text"] for c in found] == ["魔王来了"]

    def test_a_small_site_stamp_is_found_so_it_can_be_skipped_and_painted_out(self, monkeypatch):
        self._fake(monkeypatch, [
            ("구글검색 뉴토끼", 0.9, (620, 520, 160, 18)),
            ("작은 글씨", 0.9, (10, 10, 160, 18)),
        ])
        found = art.find_art_captions("p.png", "ko", [], image=_COLOUR_PAGE.copy())
        assert [c["source_text"] for c in found] == ["구글검색 뉴토끼"]

    def test_tall_pages_are_detected_at_a_capped_size(self, monkeypatch):
        seen = self._fake(monkeypatch, [])
        art.find_art_captions("p.png", "zh", [], image=np.full((2560, 1280, 3), (200, 80, 40), np.uint8))
        assert seen["scale"] == pytest.approx(0.5)


    def test_a_non_ascii_page_path_is_decoded_once_and_the_array_is_what_paddle_reads(
            self, tmp_path, monkeypatch):
        path = tmp_path / "页面_0001.png"
        Image.new("RGB", (200, 100), (40, 80, 200)).save(path)
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


class TestStrokeWeight:
    @staticmethod
    def _bars(width):
        ink = np.zeros((60, 200), bool)
        for x in range(10, 190, 30):
            ink[:, x:x + width] = True
        return ink

    def test_thin_strokes_are_regular_and_thick_strokes_bold(self):
        assert art._stroke_weight(self._bars(3), 60) == "regular"
        assert art._stroke_weight(self._bars(14), 60) == "bold"

    def test_caption_style_carries_the_weight_of_the_lettering(self):
        def style_of(width):
            page = np.full((300, 600, 3), 235, np.uint8)
            for x in range(100, 400, 60):
                page[120:200, x:x + width] = (20, 20, 20)
            return art.caption_style(page, {"x": 90, "y": 110, "w": 330, "h": 100})["weight"]
        assert (style_of(4), style_of(16)) == ("regular", "bold")


class TestPaintOut:
    def test_without_the_model_opencv_paints_and_reports_it(self, monkeypatch):
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "opencv")
        page = _dark_page_with_white_text()
        done_by_model = art.paint_out_captions(page, [{"x": 90, "y": 140, "w": 330, "h": 80}])
        assert done_by_model is False
        assert page[180, 130].max() < 100        # the letters are gone

    def test_a_caption_too_large_for_the_model_falls_back_alone(self, monkeypatch):
        monkeypatch.setattr(art, "_paint_out_over_dark_art", lambda page, box: False)
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "ml")

        def too_big(roi, mask, **kw):
            raise ValueError("too large")
        monkeypatch.setattr(scanlate_lama, "_run_ml_inpaint", too_big)
        page = _dark_page_with_white_text()
        assert art.paint_out_captions(page, [{"x": 90, "y": 140, "w": 330, "h": 80}]) is False
        assert page[180, 130].max() < 100

    def test_the_model_result_is_used_and_never_downloads(self, monkeypatch):
        monkeypatch.setattr(art, "_paint_out_over_dark_art", lambda page, box: False)
        monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda b: "ml")
        kwargs = {}

        def fake(roi, mask, **kw):
            kwargs.update(kw)
            return np.full_like(roi, 7)
        monkeypatch.setattr(scanlate_lama, "_run_ml_inpaint", fake)
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

    def test_regular_lettering_gets_the_regular_face(self):
        def ink_of(weight):
            img = Image.new("RGB", (600, 400), (0, 0, 0))
            art.draw_caption(img, {"x": 90, "y": 140, "w": 330, "h": 80}, "The Demon King",
                             {"fill": (255, 255, 255), "glow": None, "line_h": 60,
                              "align": "center", "weight": weight})
            return int((np.array(img).max(axis=2) > 200).sum())
        assert ink_of("bold") > ink_of("regular")

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

    def test_the_page_ocr_backend_picks_who_reads_the_captions(self, page_file, monkeypatch):
        seen = []
        monkeypatch.setattr(art, "find_art_captions",
                            lambda *a, **k: seen.append(k["backend"]) or [])
        scanlate.detect_and_ocr_page(page_file, "zh", ocr_backend="rapidocr", art_captions=True)
        scanlate.detect_and_ocr_page(page_file, "zh", ocr_backend="paddle", art_captions=True)
        assert seen == ["rapidocr", "paddle"]

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


class TestCaptionAcrossPages:
    UPPER = {"x": 100, "y": 940, "w": 400, "h": 60}
    LOWER = {"x": 120, "y": 0, "w": 380, "h": 40}

    def test_a_caption_cut_by_the_page_break_continues_on_the_next_page(self):
        assert art.continues_across_pages(self.UPPER, self.LOWER, 1000)

    def test_captions_away_from_the_edges_or_apart_sideways_do_not_join(self):
        assert not art.continues_across_pages({**self.UPPER, "y": 500}, self.LOWER, 1000)
        assert not art.continues_across_pages(self.UPPER, {**self.LOWER, "y": 200}, 1000)
        assert not art.continues_across_pages(self.UPPER, {**self.LOWER, "x": 700}, 1000)

    def test_vertical_columns_do_not_join(self):
        column = {"x": 100, "y": 700, "w": 40, "h": 300}
        assert not art.continues_across_pages(column, self.LOWER, 1000)

    def test_the_english_is_cut_at_a_word_by_the_share_of_source(self):
        assert art.split_by_source_share("one two three four", "abc", "abc") == ("one two", "three four")
        assert art.split_by_source_share("one two three four", "abcdef", "ab") == ("one two three", "four")

    def test_a_single_word_cannot_be_split(self):
        assert art.split_by_source_share("Hello", "ab", "ab") is None


def _outlined_red_lettering_on_dark_art():
    page = np.full((400, 600, 3), (15, 20, 45), np.uint8)
    fill = np.zeros(page.shape[:2], np.uint8)
    cv2.putText(fill, "FIRE", (90, 230), cv2.FONT_HERSHEY_SIMPLEX, 3, 255, 8)
    outline = cv2.dilate(fill, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)))
    page[outline > 0] = (255, 255, 255)
    page[fill > 0] = (40, 20, 150)
    return page


def test_outlined_lettering_on_dark_art_is_erased_to_the_art_tone():
    # The brightness cut-off only finds the white outline, so the red fill and the
    # glow used to survive as ghost letters under the English.
    page = _outlined_red_lettering_on_dark_art()
    box = {"x": 70, "y": 120, "w": 460, "h": 140}
    scanlate_inpaint.inpaint_region(None, box, image=page, spare_art=True, backend="cv")
    erased = page[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
    assert int(erased.max()) < 90
    assert abs(float(erased.mean()) - 30) < 15


def test_a_sign_region_on_dark_art_is_painted_out_without_spare_art():
    page = _outlined_red_lettering_on_dark_art()
    box = {"x": 70, "y": 120, "w": 460, "h": 140}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="cv", on_art=True)
    erased = page[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
    assert int(erased.max()) < 90


def test_a_sign_with_pale_lettering_on_coloured_art_is_erased_without_blanking_the_art():
    page = np.full((300, 400, 3), (30, 110, 230), np.uint8)
    cv2.putText(page, "HELLO", (60, 160), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 8)
    box = {"x": 40, "y": 90, "w": 320, "h": 100}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="cv", on_art=True)
    assert int(page[90:190, 40:360].min(axis=2).max()) < 150
    assert abs(int(page[250, 20, 0]) - 30) < 5


def test_a_box_full_of_coloured_bright_art_is_not_painted_out_as_lettering():
    page = np.full((300, 400, 3), (15, 20, 45), np.uint8)
    page[40:260, 60:340] = (0, 140, 255)
    assert scanlate_inpaint._dark_art_fill(page, 50, 30, 300, 240) is None


def test_a_balloon_with_an_outline_does_not_use_the_art_fill(monkeypatch):
    page = _outlined_red_lettering_on_dark_art()
    called = []
    monkeypatch.setattr(scanlate_inpaint, "_dark_art_fill",
                        lambda *a, **k: called.append(1))
    inside = np.ones((140, 460), bool)
    scanlate_inpaint.inpaint_region(None, {"x": 70, "y": 120, "w": 460, "h": 140}, image=page,
                                    inside=inside, spare_art=True, backend="cv")
    assert not called


def test_outlined_caption_on_dark_art_is_painted_out_with_the_art_tone_without_the_model(monkeypatch):
    # Captions never pass through inpaint_region, so they need the same art fill
    # or the model smears a grey patch over the art.
    def no_model(*a, **k):
        raise AssertionError("the art fill should have handled this caption")
    monkeypatch.setattr(scanlate_lama, "_run_ml_inpaint", no_model)
    page = _outlined_red_lettering_on_dark_art()
    box = {"x": 70, "y": 120, "w": 460, "h": 140}
    art.paint_out_captions(page, [box])
    erased = page[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
    assert int(erased.max()) < 90
    assert abs(float(erased.mean()) - 30) < 15


def test_a_large_sign_on_mid_tone_art_is_not_filled_with_black():
    # The lettered block is wider than the fill's blur reaches, which used to fade
    # the fill to black in its middle.
    page = np.full((420, 700, 3), (70, 80, 90), np.uint8)
    for row, y in enumerate((130, 210, 290)):
        cv2.putText(page, "WWWWWWWWWWWW", (60, y), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (255, 255, 255), 14)
    box = {"x": 40, "y": 60, "w": 620, "h": 270}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="cv", on_art=True)
    erased = page[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
    assert float(erased.mean()) > 60
    assert int(erased.min()) > 45


def test_the_art_fill_fades_in_from_the_box_edge_instead_of_leaving_a_seam():
    page = np.full((420, 700, 3), (70, 80, 90), np.uint8)
    page[:, :350] = (40, 70, 60)
    before = page.copy()
    for y in (130, 210, 290):
        cv2.putText(page, "WWWWWWWWWWWW", (60, y), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (255, 255, 255), 14)
    box = {"x": 40, "y": 60, "w": 620, "h": 270}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="cv", on_art=True)
    edge = page[box["y"] - 4, 100:600].astype(int)
    assert np.abs(edge - before[box["y"] - 4, 100:600]).max() == 0


def test_a_dark_art_sign_goes_through_lama_when_the_model_is_usable(monkeypatch):
    page = _outlined_red_lettering_on_dark_art()
    seen = {}

    def fake_lama(roi, mask, hf_token=None, allow_download=True):
        seen["mask_area"] = int((mask > 0).sum())
        return np.full_like(roi, 25)

    monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint", fake_lama)
    box = {"x": 70, "y": 120, "w": 460, "h": 140}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="ml", on_art=True)
    assert seen["mask_area"] > 0
    erased = page[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
    assert int(erased.max()) < 90


@pytest.mark.parametrize("backend", ["cv", "ml"])
def test_coloured_lettering_on_a_plain_black_notice_is_erased_to_black(monkeypatch, backend):
    # A yellow headline over white lines on a black disclaimer page: the yellow is too
    # saturated to pass as pale lettering, so it stayed under the English, and LaMa left
    # grey blocks where the white lines were.
    monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint",
                        lambda roi, mask, hf_token=None, allow_download=True: np.full_like(roi, 60))
    page = np.zeros((500, 600, 3), np.uint8)
    cv2.putText(page, "NOTICE", (120, 150), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (40, 220, 250), 10)
    for y in (240, 300, 360):
        cv2.putText(page, "lorem ipsum", (130, y), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    box = {"x": 80, "y": 60, "w": 440, "h": 340}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend=backend, on_art=True)
    assert int(page.max()) < 25


def test_a_dark_art_sign_falls_back_to_the_tone_fill_when_lama_fails(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("no weights")
    monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint", broken)
    page = _outlined_red_lettering_on_dark_art()
    box = {"x": 70, "y": 120, "w": 460, "h": 140}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="ml", spare_art=True)
    erased = page[box["y"]:box["y"] + box["h"], box["x"]:box["x"] + box["w"]]
    assert int(erased.max()) < 90


def test_dark_art_lama_mask_covers_the_letters_but_not_pale_art_beside_them():
    page = np.full((300, 500, 3), (45, 30, 20), np.uint8)
    page[:, 200:300] = (120, 160, 190)
    cv2.putText(page, "HELLO", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 6)
    mask = scanlate_inpaint._dark_art_stroke_mask(page)
    assert mask[:, 230:270].max() == 0 or mask[:, 230:270].mean() < 5
    assert mask[120:170, 20:190].mean() > 40


def _region(kind, text, x=0, y=0, w=100, h=100):
    return {"kind": kind, "source_text": text, "x": x, "y": y, "w": w, "h": h}


def test_a_balloon_box_on_the_titles_own_glyph_is_dropped_when_the_caption_covers_it():
    # The detector boxed one glyph of a logo title as a balloon; the caption holds the whole title.
    glyph = _region("bubble", "的", 10, 10, 30, 30)
    caption = _region("caption", "重生佣兵王的复仇", 0, 0, 200, 200)
    assert scanlate.drop_parts_of_captions([glyph], [caption]) == []


def test_a_real_balloon_inside_a_caption_keeps_its_own_lettering():
    balloon = _region("bubble", "快跑！", 10, 10, 30, 30)
    caption = _region("caption", "重生佣兵王的复仇", 0, 0, 200, 200)
    assert scanlate.drop_parts_of_captions([balloon], [caption]) == [balloon]


def test_a_blank_balloon_inside_a_caption_is_kept():
    blank = _region("bubble", "", 10, 10, 30, 30)
    caption = _region("caption", "重生佣兵王的复仇", 0, 0, 200, 200)
    assert scanlate.drop_parts_of_captions([blank], [caption]) == [blank]


def _page_with_black_title_and_red_tag():
    page = np.full((300, 600, 3), (240, 200, 180), np.uint8)             # pale blue sky (BGR)
    cv2.putText(page, "TITLE", (20, 150), cv2.FONT_HERSHEY_SIMPLEX, 3, (10, 10, 10), 14)
    cv2.rectangle(page, (500, 40), (560, 160), (40, 40, 200), -1)         # solid red tag
    return page


def test_a_solid_coloured_tag_in_the_title_box_is_found_and_the_english_stays_clear_of_it():
    page = _page_with_black_title_and_red_tag()
    box = {"x": 10, "y": 30, "w": 570, "h": 200}
    assert art.decoration_blocks(page, box) == [(500, 40, 561, 161)]
    room = art.clear_of_decoration(page, box)
    assert room["x"] == 10 and room["x"] + room["w"] <= 500 - 8


def test_the_tag_is_not_painted_out_with_the_title():
    page = _page_with_black_title_and_red_tag()
    mask = art.caption_mask(page, {"x": 10, "y": 30, "w": 570, "h": 200})
    assert not mask[40:160, 500:560].any()
    assert mask[110:150, 40:120].any()                                    # the title still is


def test_a_balloon_box_on_a_glyph_with_a_stray_ocr_digit_is_dropped():
    glyph = {"kind": "bubble", "source_text": "的\n7", "x": 10, "y": 10, "w": 30, "h": 30}
    caption = {"kind": "caption", "source_text": "重生佣兵王的复仇", "x": 0, "y": 0, "w": 200, "h": 200}
    assert scanlate.drop_parts_of_captions([glyph], [caption]) == []


def test_dark_caption_lettering_gets_a_light_thin_outline_not_a_fat_dark_one():
    base = Image.new("RGB", (300, 120), (200, 200, 200))
    dark, light = base.copy(), base.copy()
    box = {"x": 10, "y": 10, "w": 280, "h": 100}
    art.draw_caption(dark, box, "TITLE", {"fill": (20, 20, 20), "glow": None})
    art.draw_caption(light, box, "TITLE", {"fill": (240, 240, 240), "glow": None})
    inked = lambda im: int((np.asarray(im.convert("L")) < 100).sum())
    assert inked(dark) < inked(light)                                       # no dark outline fattening the dark fill


def _title_caption():
    caption = _region("caption", "超視覚警察", 0, 0, 400, 200)
    caption["line_boxes"] = [{"x": 0, "y": 0, "w": 400, "h": 90}]
    return caption


def test_a_misread_glyph_on_the_titles_own_line_is_dropped():
    # 察 of the title read as 祭, or manga-ocr's "そんな": not in the caption's text, yet only a
    # piece of the title, and its English was set over the title's.
    for text in ("祭", "そんな", "超視\nちょう\nてい", "．．．"):
        glyph = _region("bubble", text, 10, 10, 60, 70)
        assert scanlate.drop_parts_of_captions([glyph], [_title_caption()]) == [], text


def test_a_balloon_inside_the_caption_box_but_off_its_lines_is_kept():
    balloon = _region("bubble", "快跑！", 300, 120, 60, 60)
    assert scanlate.drop_parts_of_captions([balloon], [_title_caption()]) == [balloon]


def test_the_caption_lines_are_not_kept_on_the_saved_region():
    caption = _title_caption()
    assert "line_boxes" not in scanlate.with_captions([], [caption])[0]


def test_title_glyphs_boxed_as_balloons_do_not_count_toward_the_caption_limit():
    regions = [_region("bubble", t) for t in ("祭", "三", "覚", "え", "")] + [_region("sign", "新連載")]
    assert scanlate._balloons_with_lines(regions) == 0
    assert scanlate._balloons_with_lines([_region("bubble", "あれ？"), _region("thought", "糸が")]) == 2


def _mid_grey_wall_with_dark_and_white_title():
    page = np.full((400, 700, 3), 165, np.uint8)
    cv2.line(page, (0, 120), (699, 120), (60, 60, 60), 6)                     # a rail across the wall
    cv2.putText(page, "DARK", (80, 140), cv2.FONT_HERSHEY_SIMPLEX, 3, (10, 10, 10), 14)
    cv2.putText(page, "WHITE", (80, 280), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 14)
    cv2.line(page, (0, 330), (699, 230), (250, 250, 250), 1)                  # a light ray across it
    return page


def test_white_and_dark_title_words_on_a_mid_grey_wall_are_both_painted_out():
    page = _mid_grey_wall_with_dark_and_white_title()
    mask = art.caption_mask(page, {"x": 60, "y": 50, "w": 520, "h": 260})
    gray = cv2.cvtColor(page, cv2.COLOR_BGR2GRAY)
    assert mask[gray < 30].mean() > 0.9 * 255                                 # the dark word
    assert mask[gray > 240][:2000].mean() > 0.6 * 255                         # the white word
    assert not mask[120, 650]                                                 # the rail past the title


def test_the_paper_of_a_box_beside_a_title_on_a_mid_grey_wall_is_kept():
    page = _mid_grey_wall_with_dark_and_white_title()
    page[0:400, 600:700] = 255                                                # a narration box's paper
    mask = art.caption_mask(page, {"x": 60, "y": 50, "w": 560, "h": 260})
    assert not mask[100:300, 640:700].any()


def test_heavy_black_lettering_that_fills_its_box_on_pale_art_is_erased(monkeypatch):
    # The black strokes are most of the box, so the box read as a dark ground: the pale art
    # between the letters was painted out instead, and the letters stayed as black blocks.
    monkeypatch.setattr(scanlate_inpaint, "_run_ml_inpaint",
                        lambda roi, mask, hf_token=None, allow_download=True: np.full_like(roi, 170))
    page = np.full((300, 700, 3), 170, np.uint8)
    for x in range(0, 700, 37):
        cv2.line(page, (x, 0), (x + 60, 299), (90, 90, 90), 1)                 # the art's own lines
    cv2.putText(page, "NEW", (60, 220), cv2.FONT_HERSHEY_SIMPLEX, 5.5, (25, 25, 25), 45)
    box = {"x": 60, "y": 70, "w": 500, "h": 170}
    scanlate_inpaint.inpaint_region(None, box, image=page, backend="ml", on_art=True)
    erased = cv2.cvtColor(page[70:240, 60:560], cv2.COLOR_BGR2GRAY)
    assert (erased < 50).mean() < 0.02


def test_a_black_strip_with_white_lettering_is_not_taken_for_heavy_black_lettering():
    page = np.full((300, 200, 3), 170, np.uint8)
    page[20:280, 80:120] = 0
    assert scanlate_inpaint._heavy_dark_lettering(page, 80, 20, 40, 260) is None


def test_a_caption_is_repainted_with_art_round_it_and_only_its_lettering_changes(monkeypatch):
    seen = {}

    def model(roi, mask, **kwargs):
        seen["shape"], seen["masked"] = roi.shape[:2], int((mask > 0).sum())
        return np.full_like(roi, 255)

    monkeypatch.setattr(scanlate_lama, "_run_ml_inpaint", model)
    monkeypatch.setattr(scanlate_inpaint, "_resolve_inpaint_backend", lambda backend: "ml")
    page = np.full((400, 600, 3), (200, 150, 120), np.uint8)
    cv2.putText(page, "TITLE", (120, 220), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 10)
    before = page.copy()
    art.paint_out_captions(page, [{"x": 100, "y": 130, "w": 400, "h": 140}])
    changed = (page != before).any(axis=2)
    ys, xs = np.nonzero(changed)
    assert seen["masked"] and seen["shape"][0] > ys.max() - ys.min() + 1
    assert seen["shape"][1] > xs.max() - xs.min() + 1
    assert changed.sum() <= seen["masked"]
