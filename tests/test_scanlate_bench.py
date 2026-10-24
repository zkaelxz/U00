"""tools/scanlate_bench.py: the scoring it rests on, the synthetic pages, the
override switch, and one small end-to-end run (OpenCV and a CJK font needed)."""
import json

import pytest

import scanlate_tuning
from tools import scanlate_bench as bench


def _box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def _balloon(x=100, y=100, w=120, h=200, text_box=(130, 140, 60, 120)):
    return bench.Balloon(x, y, w, h, "今日は", True, 24, text_box, "ellipse")


class TestScoring:
    def test_cer_ignores_punctuation_spacing_and_width(self):
        assert bench.cer("今日は、雨\n！", "今日は雨") == 0.0
        assert bench.cer("ＡＢ", "AB") == 0.0

    def test_cer_counts_edits_over_the_reference(self):
        assert bench.cer("今日", "今日は雨") == pytest.approx(0.5)
        assert bench.cer("", "今日") == 1.0
        assert bench.cer("ずっと長い読み違えの文字列", "今") == 1.0     # capped

    def test_a_box_hugging_the_lettering_counts_as_the_balloon(self):
        b = _balloon()
        assert bench.match([_box(128, 138, 64, 124)], [b]) == [(0, 0)]

    def test_a_second_box_on_one_balloon_is_left_unmatched(self):
        b = _balloon()
        preds = [_box(100, 100, 120, 200), _box(128, 138, 64, 124)]
        assert bench.match(preds, [b]) == [(0, 0)]

    def test_a_box_elsewhere_matches_nothing(self):
        assert bench.match([_box(400, 400, 100, 100)], [_balloon()]) == []

    def test_pair_order_accuracy(self):
        assert bench.pair_order_accuracy([0, 1, 2]) == 1.0
        assert bench.pair_order_accuracy([2, 1, 0]) == 0.0
        assert bench.pair_order_accuracy([1, 0, 2]) == pytest.approx(2 / 3)
        assert bench.pair_order_accuracy([5]) == 1.0


class TestOverrides:
    def test_a_value_is_converted_to_the_type_it_already_has(self, monkeypatch):
        for name in ("CV_MIN_W", "ML_MERGE_IOU", "RIGHT_TO_LEFT_LANGUAGES"):
            monkeypatch.setattr(scanlate_tuning, name, getattr(scanlate_tuning, name))
        scanlate_tuning.apply_overrides({"CV_MIN_W": "40", "ML_MERGE_IOU": "0.6",
                                         "RIGHT_TO_LEFT_LANGUAGES": "ja,zh"})
        assert scanlate_tuning.CV_MIN_W == 40
        assert scanlate_tuning.ML_MERGE_IOU == 0.6
        assert scanlate_tuning.reads_right_to_left("zh")

    @pytest.mark.parametrize("values", [{"NOT_A_VALUE": "1"}, {"apply_overrides": "1"},
                                        {"CV_MIN_W": "wide"}])
    def test_unknown_names_and_bad_values_are_refused(self, values):
        with pytest.raises(ValueError):
            scanlate_tuning.apply_overrides(values)

    def test_the_command_line_needs_name_equals_value(self):
        assert bench.parse_overrides(["A=1", "B = x"]) == {"A": "1", "B": "x"}
        with pytest.raises(bench.BenchError):
            bench.parse_overrides(["A"])


class TestPages:
    @pytest.fixture
    def fonts(self):
        try:
            return {lang: bench.find_font(lang) for lang in bench.LANGS}
        except bench.BenchError:
            pytest.skip("no CJK font on this machine")

    def test_the_same_seed_draws_the_same_page(self, fonts):
        a, b = bench.make_page(1, "ja", fonts), bench.make_page(1, "ja", fonts)
        assert a.image.tobytes() == b.image.tobytes()
        assert [(x.x, x.y, x.text) for x in a.balloons] == [(x.x, x.y, x.text) for x in b.balloons]

    def test_balloons_sit_on_the_page_in_reading_order(self, fonts):
        for lang in bench.LANGS:
            page = bench.make_page(0, lang, fonts)
            assert page.balloons
            for i, b in enumerate(page.balloons):
                assert b.order == i
                assert 0 < b.x and b.x + b.w < bench.PAGE_W and 0 < b.y and b.y + b.h < bench.PAGE_H

    def test_a_small_run_writes_the_report(self, fonts, tmp_path):
        pytest.importorskip("cv2")
        code = bench.main(["--pages", "1", "--langs", "ko", "--skip-ocr", "--out", str(tmp_path),
                           "--set", "CV_MIN_W=25"])
        assert code == 0
        report = (tmp_path / bench.REPORT_NAME).read_text(encoding="utf-8")
        assert "Bubble recall" in report and "CV_MIN_W=25" in report
        summary = json.loads((tmp_path / bench.JSON_NAME).read_text(encoding="utf-8"))["summary"]
        assert summary["ko"]["pages"] == 1 and summary["ko"]["recall"] is not None
        assert summary["ko"]["cer_oracle"] is None          # OCR was skipped

    def test_an_unknown_override_stops_before_any_page(self, tmp_path, capsys):
        assert bench.main(["--set", "NOPE=1", "--out", str(tmp_path)]) == 2
        assert "Unknown scanlate_tuning value" in capsys.readouterr().err
        assert not (tmp_path / bench.REPORT_NAME).exists()
