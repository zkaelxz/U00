"""
tests/test_benchmark.py -- benchmark.py, the accuracy/regression tracking
harness. Real pipeline calls (core.transcribe_for_timing, an engine's
translate_batch, ocr.extract_text_from_images) are mocked at their exact
boundary -- this proves the harness's OWN logic (scoring, case
dispatch, error isolation, regression detection) works, the same way
the transcription tests mock transcribe_for_timing rather than
running real Whisper.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import benchmark
import whisper_transcribe
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
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing",
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
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        case = {"input_path": "/fake/audio.wav"}
        result = benchmark.run_transcription_case(case)

        assert result["output_text"] == "你好"
        assert result["score"] is None

    def test_a_transcription_failure_is_captured_not_raised(self, monkeypatch):
        def fake_transcribe(path, **kw):
            raise RuntimeError("model download failed")
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake_transcribe)

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
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake_transcribe)

        case = {"input_path": "/fake/audio.wav", "source_language": "ja"}
        benchmark.run_transcription_case(case, whisper_size="large-v3")

        assert captured["model_size"] == "large-v3"
        assert captured["language"] == "ja"

    def test_local_model_path_reaches_transcribe(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing",
                             lambda path, **kw: captured.update(kw) or [])
        benchmark.run_transcription_case({"input_path": "/fake/a.wav"}, local_model_path="/models/ja")
        assert captured["local_model_path"] == "/models/ja"


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
