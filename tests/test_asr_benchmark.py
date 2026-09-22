"""
tests/test_asr_benchmark.py -- asr_benchmark.py's stage-sequencing and
failure-isolation logic, exercised against fake asr_backend/forced_align
classes (the real ones need a GPU/network this sandbox doesn't have).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asr_backend
import forced_align
import asr_benchmark


class TestSimilarity:
    def test_identical_strings_score_one(self):
        assert asr_benchmark._similarity("你好世界", "你好世界") == 1.0

    def test_completely_different_strings_score_low(self):
        assert asr_benchmark._similarity("你好", "xyz") < 0.5

    def test_empty_strings_do_not_crash(self):
        assert asr_benchmark._similarity("", "") == 1.0


class TestRunStageIsolatesFailures:
    def test_successful_stage_records_timing_and_data(self):
        stage = asr_benchmark._run_stage("ok_stage", lambda: {"value": 42})
        assert stage.ok is True
        assert stage.error is None
        assert stage.data == {"value": 42}
        assert stage.seconds >= 0.0

    def test_failing_stage_is_captured_not_raised(self):
        def boom():
            raise RuntimeError("simulated model crash")
        stage = asr_benchmark._run_stage("bad_stage", boom)
        assert stage.ok is False
        assert "simulated model crash" in stage.error
        assert stage.data == {}

    def test_vram_helpers_degrade_gracefully_without_torch(self, monkeypatch):
        # This sandbox has no torch installed -- confirms the real,
        # unmocked behavior on a CPU-only machine reports None rather
        # than crashing, which is exactly the case this exists to handle.
        assert asr_benchmark._peak_vram_mb() is None
        asr_benchmark._reset_vram_counter()  # must not raise


class TestRunBenchmarkSequencing:
    def test_whisper_failure_short_circuits_the_rest(self, monkeypatch):
        def exploding_transcribe(self, audio_path, language, **kwargs):
            raise RuntimeError("no audio device")
        monkeypatch.setattr(asr_backend.WhisperBackend, "transcribe", exploding_transcribe)

        results = asr_benchmark.run_benchmark("/fake.wav", "zh")
        assert len(results["stages"]) == 1
        assert results["stages"][0].name == "whisper_transcribe"
        assert results["stages"][0].ok is False

    def test_no_transcript_runs_whisper_and_qwen3_asr_only(self, monkeypatch):
        monkeypatch.setattr(
            asr_backend.WhisperBackend, "transcribe",
            lambda self, audio_path, language, **kwargs: [{"start": 0.0, "end": 1.0, "text": "w"}],
        )
        monkeypatch.setattr(
            asr_backend.Qwen3ASRBackend, "transcribe",
            lambda self, audio_path, language, whisper_segments, use_gpu=False:
                [{"start": 0.0, "end": 1.0, "text": "q"}],
        )
        results = asr_benchmark.run_benchmark("/fake.wav", "ja")
        stage_names = [s.name for s in results["stages"]]
        assert stage_names == ["whisper_transcribe", "qwen3_asr_transcribe"]
        assert "whisper_similarity_to_reference" not in results

    def test_with_transcript_runs_all_four_stages_and_similarity(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            asr_backend.WhisperBackend, "transcribe",
            lambda self, audio_path, language, **kwargs:
                [{"start": 0.0, "end": 2.0, "text": "你好世界"}],
        )
        monkeypatch.setattr(
            asr_backend.Qwen3ASRBackend, "transcribe",
            lambda self, audio_path, language, whisper_segments, use_gpu=False:
                [{"start": 0.0, "end": 2.0, "text": "你好世界"}],
        )

        from core import Line
        monkeypatch.setattr(
            "core.align_transcript_to_timing",
            lambda user_lines, whisper_segments: [Line(idx=0, start=0.0, end=2.0, zh="你好世界")],
        )
        monkeypatch.setattr(
            forced_align, "align_with_qwen3",
            lambda audio_path, user_lines, whisper_segments, language, use_gpu=False:
                [Line(idx=0, start=0.1, end=1.9, zh="你好世界")],
        )

        transcript_path = tmp_path / "ref.txt"
        transcript_path.write_text("你好世界", encoding="utf-8")

        results = asr_benchmark.run_benchmark(
            "/fake.wav", "zh", transcript_path=str(transcript_path))

        stage_names = [s.name for s in results["stages"]]
        assert stage_names == ["whisper_transcribe", "qwen3_asr_transcribe",
                                "whisper_diff_align", "qwen3_forced_align"]
        assert results["whisper_similarity_to_reference"] == 1.0
        assert results["qwen3_asr_similarity_to_reference"] == 1.0
        assert results["stages"][2].data["lines"][0]["zh"] == "你好世界"
        assert results["stages"][3].data["lines"][0]["start"] == 0.1

    def test_qwen3_asr_failure_does_not_block_alignment_stages(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            asr_backend.WhisperBackend, "transcribe",
            lambda self, audio_path, language, **kwargs:
                [{"start": 0.0, "end": 2.0, "text": "你好"}],
        )

        def exploding_qwen3_asr(self, audio_path, language, whisper_segments, use_gpu=False):
            raise ImportError("qwen-asr not installed")
        monkeypatch.setattr(asr_backend.Qwen3ASRBackend, "transcribe", exploding_qwen3_asr)

        from core import Line
        monkeypatch.setattr(
            "core.align_transcript_to_timing",
            lambda user_lines, whisper_segments: [Line(idx=0, start=0.0, end=2.0, zh="你好")],
        )
        monkeypatch.setattr(
            forced_align, "align_with_qwen3",
            lambda audio_path, user_lines, whisper_segments, language, use_gpu=False:
                [Line(idx=0, start=0.0, end=2.0, zh="你好")],
        )

        transcript_path = tmp_path / "ref.txt"
        transcript_path.write_text("你好", encoding="utf-8")

        results = asr_benchmark.run_benchmark(
            "/fake.wav", "zh", transcript_path=str(transcript_path))

        assert results["stages"][1].ok is False  # qwen3_asr_transcribe
        assert "whisper_similarity_to_reference" in results
        assert "qwen3_asr_similarity_to_reference" not in results
        # The alignment stages still ran despite the ASR-backend failure --
        # they only need whisper_segments, not qwen3_asr_stage's output.
        assert results["stages"][2].ok is True
        assert results["stages"][3].ok is True


class TestPrintSummaryDoesNotCrash:
    def test_prints_without_error_for_a_minimal_result_set(self, capsys):
        stage = asr_benchmark.StageResult(name="whisper_transcribe", ok=True, seconds=1.5,
                                           peak_vram_mb=None, data={})
        results = {"audio_path": "/fake.wav", "language": "zh", "stages": [stage]}
        asr_benchmark.print_summary(results)
        out = capsys.readouterr().out
        assert "whisper_transcribe" in out
        assert "N/A" in out
