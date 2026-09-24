"""
tests/test_benchmark.py -- benchmark.py, the accuracy/regression tracking
harness. Real pipeline calls (core.transcribe_for_timing, an engine's
translate_batch, ocr.extract_text_from_images) are mocked at their exact
boundary -- this proves the harness's OWN logic (scoring, case
dispatch, error isolation, regression detection) works, the same way
run_transcribe_job's tests mock transcribe_for_timing rather than
running real Whisper.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import benchmark
import core
import ocr as ocr_module


class TestScoreTextSimilarity:
    def test_identical_text_scores_one(self):
        assert benchmark.score_text_similarity("你好世界", "你好世界") == 1.0

    def test_completely_different_text_scores_low(self):
        assert benchmark.score_text_similarity("你好", "再见世界不同") < 0.3

    def test_whitespace_differences_are_ignored(self):
        assert benchmark.score_text_similarity("hi  there\n", "hi there") == 1.0

    def test_both_empty_scores_one(self):
        assert benchmark.score_text_similarity("", "") == 1.0

    def test_one_empty_scores_zero(self):
        assert benchmark.score_text_similarity("something", "") == 0.0

    def test_partial_overlap_is_between_zero_and_one(self):
        score = benchmark.score_text_similarity("the cat sat on the mat", "the cat sat on a rug")
        assert 0.0 < score < 1.0


class TestRunTranscriptionCase:
    def test_scores_against_a_reference_transcript(self, monkeypatch):
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 0.0, "end": 1.0, "text": "你好"},
                                                  {"start": 1.0, "end": 2.0, "text": "世界"}])
        case = {"input_path": "/fake/audio.wav", "source_language": "zh",
                "reference_text": "你好世界"}
        result = benchmark.run_transcription_case(case)

        assert result["output_text"] == "你好世界"
        assert result["score"] == 1.0
        assert result["error"] is None
        assert result["duration_seconds"] >= 0

    def test_no_reference_text_gives_no_score(self, monkeypatch):
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        case = {"input_path": "/fake/audio.wav"}
        result = benchmark.run_transcription_case(case)

        assert result["output_text"] == "你好"
        assert result["score"] is None

    def test_a_transcription_failure_is_captured_not_raised(self, monkeypatch):
        def fake_transcribe(path, **kw):
            raise RuntimeError("model download failed")
        monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)

        case = {"input_path": "/fake/audio.wav", "reference_text": "x"}
        result = benchmark.run_transcription_case(case)

        assert result["error"] == "model download failed"
        assert result["output_text"] == ""
        assert result["score"] == 0.0  # scored against "x" -- correctly bad, not skipped

    def test_passes_through_whisper_size_and_language(self, monkeypatch):
        captured = {}
        def fake_transcribe(path, model_size=None, language=None, use_gpu=False, **kw):
            captured["model_size"] = model_size
            captured["language"] = language
            return []
        monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)

        case = {"input_path": "/fake/audio.wav", "source_language": "ja"}
        benchmark.run_transcription_case(case, whisper_size="large-v3")

        assert captured["model_size"] == "large-v3"
        assert captured["language"] == "ja"


class TestRunTranslationCase:
    class FakeEngine:
        def __init__(self, translation="EN:hi"):
            self.translation = translation

        def translate_batch(self, texts, context):
            return [self.translation]

    def test_scores_against_a_reference_translation(self):
        engine = self.FakeEngine(translation="Hello world")
        case = {"source_text": "你好世界", "reference_text": "Hello world"}
        result = benchmark.run_translation_case(case, engine)

        assert result["output_text"] == "Hello world"
        assert result["score"] == 1.0

    def test_a_translation_failure_is_captured_not_raised(self):
        class FailingEngine:
            def translate_batch(self, texts, context):
                raise RuntimeError("api down")

        case = {"source_text": "你好", "reference_text": "hi"}
        result = benchmark.run_translation_case(case, FailingEngine())

        assert result["error"] == "api down"
        assert result["output_text"] == ""

    def test_logs_cost_when_the_engine_reports_usage(self, monkeypatch):
        class UsageEngine:
            model = "claude-sonnet-5"
            last_usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}

            def translate_batch(self, texts, context):
                return ["translated"]

        import translate_engines
        monkeypatch.setattr(translate_engines, "estimate_cost", lambda model, i, o: 12.0)

        case = {"source_text": "x"}
        result = benchmark.run_translation_case(case, UsageEngine())
        assert result["cost_usd"] == 12.0

    def test_no_usage_attribute_costs_nothing(self):
        engine = self.FakeEngine()
        case = {"source_text": "x"}
        result = benchmark.run_translation_case(case, engine)
        assert result["cost_usd"] == 0.0


class TestRunOcrCase:
    def test_scores_against_reference_ocr_text(self, monkeypatch):
        monkeypatch.setattr(ocr_module, "extract_text_from_images",
                             lambda paths, backend="tesseract", source_language="zh": "你好")
        case = {"input_path": "/fake/page.png", "reference_text": "你好"}
        result = benchmark.run_ocr_case(case)
        assert result["score"] == 1.0

    def test_an_ocr_failure_is_captured_not_raised(self, monkeypatch):
        def fake_extract(paths, backend="tesseract", source_language="zh"):
            raise RuntimeError("tesseract not installed")
        monkeypatch.setattr(ocr_module, "extract_text_from_images", fake_extract)

        case = {"input_path": "/fake/page.png"}
        result = benchmark.run_ocr_case(case)
        assert result["error"] == "tesseract not installed"


class TestRunSuite:
    def test_dispatches_to_the_right_stage_runner(self, monkeypatch):
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 0, "end": 1, "text": "hi"}])
        cases = [{"id": 1, "input_path": "/fake/a.wav"}, {"id": 2, "input_path": "/fake/b.wav"}]
        results = benchmark.run_suite(cases, "transcription")

        assert len(results) == 2
        assert [r["case_id"] for r in results] == [1, 2]

    def test_unknown_stage_raises_a_clear_error(self):
        with pytest.raises(ValueError, match="Unknown benchmark stage"):
            benchmark.run_suite([], "not_a_real_stage")

    def test_one_cases_failure_does_not_stop_the_rest(self, monkeypatch):
        calls = {"n": 0}
        def fake_transcribe(path, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return [{"start": 0, "end": 1, "text": "ok"}]
        monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)

        cases = [{"id": 1, "input_path": "/a"}, {"id": 2, "input_path": "/b"}]
        results = benchmark.run_suite(cases, "transcription")

        assert results[0]["error"] == "boom"
        assert results[1]["error"] is None
        assert results[1]["output_text"] == "ok"


class TestDetectRegressions:
    def test_a_meaningful_score_drop_is_flagged(self):
        previous = [{"case_id": 1, "score": 0.9}]
        latest = [{"case_id": 1, "score": 0.6}]
        regressions = benchmark.detect_regressions(previous, latest, threshold=0.05)
        assert len(regressions) == 1
        assert regressions[0]["case_id"] == 1
        assert regressions[0]["drop"] == pytest.approx(0.3)

    def test_a_small_drop_under_the_threshold_is_not_flagged(self):
        previous = [{"case_id": 1, "score": 0.90}]
        latest = [{"case_id": 1, "score": 0.87}]
        assert benchmark.detect_regressions(previous, latest, threshold=0.05) == []

    def test_a_score_improvement_is_not_flagged(self):
        previous = [{"case_id": 1, "score": 0.5}]
        latest = [{"case_id": 1, "score": 0.9}]
        assert benchmark.detect_regressions(previous, latest, threshold=0.05) == []

    def test_a_case_with_no_score_in_either_run_is_skipped(self):
        previous = [{"case_id": 1, "score": None}]
        latest = [{"case_id": 1, "score": None}]
        assert benchmark.detect_regressions(previous, latest) == []

    def test_a_case_missing_from_the_previous_run_is_skipped_not_flagged(self):
        previous = []
        latest = [{"case_id": 1, "score": 0.5}]
        assert benchmark.detect_regressions(previous, latest) == []

    def test_multiple_regressions_are_sorted_worst_first(self):
        previous = [{"case_id": 1, "score": 0.9}, {"case_id": 2, "score": 0.9}]
        latest = [{"case_id": 1, "score": 0.8}, {"case_id": 2, "score": 0.3}]
        regressions = benchmark.detect_regressions(previous, latest, threshold=0.05)
        assert [r["case_id"] for r in regressions] == [2, 1]
