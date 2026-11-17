"""Tests for a transcription run that reads burned-in subtitles with OCR instead of hearing the audio."""

import os

import pytest

import background_jobs
from jobs import job_launch, job_runner, job_table
from services import transcribe_service
from services import settings_service
from lib.errors import ConflictError
from tests.test_transcribe_service import (  # noqa: F401  (_no_real_whisper_load is the autouse fixture)
    _clear, _drama_with_video, _no_real_whisper_load, _seed_running_job,
)


class TestRunTranscribeAndApplyJobHardsubOcr:
    """hardsub_ocr.py hard-imports cv2 at module level, an optional
    dependency -- importorskip it here so a core-only install still gets
    a clean run of the rest of this file (per CLAUDE.md's own rule)."""

    def test_success_saves_lines_from_ocr_cues(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        cues = [{"start": 0.0, "end": 1.0, "text": "hello"},
                {"start": 1.0, "end": 2.0, "text": "world"}]
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", lambda *a, **k: cues)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0)

        status = background_jobs.get_status(job_id)
        assert status["result"]["line_count"] == 2
        assert status["result"]["diarize_started"] is False
        saved = isolated_db.load_lines(did)
        assert [r["zh"] for r in saved] == ["hello", "world"]
        assert isolated_db.get_drama(did)["status"] == "aligned"
        _clear(job_id)

    def test_empty_cues_records_failed_reason(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", lambda *a, **k: [])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0)

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "no_text"
        _clear(job_id)

    def test_caption_reading_progress_reaches_the_job(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        messages = []
        real_update = background_jobs.update_progress
        for module in (background_jobs, job_runner):
            monkeypatch.setattr(module, "update_progress",
                                lambda j, f, m="": (messages.append(m), real_update(j, f, m)))

        def fake_extract(video_path, progress_cb=None, **kwargs):
            progress_cb(0.4)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", fake_extract)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0)

        assert any(m.startswith("Reading captions from video... 40%") for m in messages)
        _clear(job_id)

    def test_an_unexpected_caption_reading_error_is_raised_not_recorded(
            self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def boom(*a, **k):
            raise RuntimeError("ffmpeg not found")
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", boom)

        with pytest.raises(RuntimeError, match="ffmpeg not found"):
            transcribe_service._run_transcribe_and_apply_job(
                job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
                "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
                video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
                hardsub_interval=1.0)
        _clear(job_id)

    def test_passes_backend_interval_and_tesseract_cmd_through(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        captured = {}

        def fake_extract(video_path, **kwargs):
            captured["video_path"] = video_path
            captured.update(kwargs)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", fake_extract)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "ja", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="paddle",
            hardsub_interval=2.5, tesseract_cmd="/usr/bin/tesseract")

        assert captured["video_path"] == os.path.join(ddir, "source.mp4")
        assert captured["language"] == "ja"
        assert captured["ocr_backend"] == "paddle"
        assert captured["sample_interval"] == 2.5
        assert captured["tesseract_cmd"] == "/usr/bin/tesseract"
        _clear(job_id)

    def test_frames_dropped_by_a_tesseract_timeout_are_reported(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def fake_extract(video_path, **kwargs):
            kwargs["info"].update(backend="tesseract", note=None, dropped_frames=3)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", fake_extract)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0)

        warning = background_jobs.get_status(job_id)["result"]["coverage_warning"]
        assert warning == "3 sampled frames timed out while reading and may be missing captions."
        _clear(job_id)

    def test_auto_fallback_records_the_engine_that_ran_and_surfaces_the_note(
            self, isolated_db, monkeypatch):
        import raw_transcript
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        note = "PaddleOCR isn't installed, so Tesseract was used."

        def fake_extract(video_path, **kwargs):
            assert kwargs["ocr_backend"] == "auto"
            kwargs["info"].update(backend="tesseract", note=note)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles", fake_extract)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "ko", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="auto",
            hardsub_interval=1.0)

        assert background_jobs.get_status(job_id)["result"]["coverage_warning"] == note
        raw = raw_transcript.load_latest(ddir)
        assert (raw["backend"], raw["model"]) == ("hardsub_ocr", "tesseract")
        _clear(job_id)

    def test_diarization_chains_off_the_dramas_own_audio_not_the_video(
            self, isolated_db, monkeypatch):
        settings_service.set_settings({"use_gpu": False})   # these assert the CPU path
        # Regression test for a real bug found by code review: hardsub_ocr
        # has no transcribe-time audio_path (only video), so the diarize
        # chain-start must use the separately-resolved diarize_audio_path,
        # never None and never the video file.
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db, with_audio=True)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        seen = []
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **k: seen.append(args) or True)
        audio_path = os.path.join(ddir, "audio.wav")

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0, diarize_audio_path=audio_path)

        assert seen == [(audio_path, "hf-token", None,
                         {"use_gpu": False, "backend": "nemotron", "min_speakers": None, "max_speakers": None})]
        assert background_jobs.get_status(job_id)["result"]["diarize_started"] is True
        _clear(job_id)

    def _run_with_refusing_chain_start(self, isolated_db, monkeypatch, cancel, seen,
                                       stopping=False):
        """Runs the apply step with a chain start that raises; `seen` gets
        the launch kwargs and the saved line count."""
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db, with_audio=True)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        def refuse(_diarize_id, target, args=(), **k):
            seen["launch"] = k
            if stopping:
                # The stop refuses new jobs before it cancels existing ones.
                job_launch._stopping = True
            if cancel:
                # The clean stop cancels every job, after the lines were saved.
                job_table._jobs[job_id]["cancel_requested"] = True
            raise ConflictError("Baihe is stopping, so no new job can start.")
        monkeypatch.setattr(job_launch, "start_process_job", refuse)
        try:
            transcribe_service._run_transcribe_and_apply_job(
                job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
                "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", None,
                video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
                hardsub_interval=1.0, diarize_audio_path=os.path.join(ddir, "audio.wav"))
        finally:
            seen["line_count"] = len(isolated_db.load_lines(did))
            _clear(job_id)

    def test_a_stop_that_began_after_the_save_cancels_and_keeps_the_lines(
            self, isolated_db, monkeypatch):
        seen = {}
        with pytest.raises(background_jobs.JobCancelled):
            self._run_with_refusing_chain_start(isolated_db, monkeypatch, True, seen)
        assert seen["line_count"] == 1

    def test_a_refusal_between_the_stop_and_the_cancel_cancels_too(
            self, isolated_db, monkeypatch):
        seen = {}
        with pytest.raises(background_jobs.JobCancelled):
            self._run_with_refusing_chain_start(
                isolated_db, monkeypatch, False, seen, stopping=True)
        assert seen["line_count"] == 1

    def test_a_refused_chain_start_without_a_stop_is_still_an_error(
            self, isolated_db, monkeypatch):
        seen = {}
        with pytest.raises(ConflictError):
            self._run_with_refusing_chain_start(isolated_db, monkeypatch, False, seen)

    def test_the_diarization_chain_runs_spawned_and_is_killed_as_a_tree(
            self, isolated_db, monkeypatch):
        seen = {}
        with pytest.raises(ConflictError):
            self._run_with_refusing_chain_start(isolated_db, monkeypatch, False, seen)
        assert seen["launch"]["kill_whole_tree"] is True
        assert seen["launch"]["start_method"] == "spawn"

    def test_diarization_skipped_gracefully_with_no_drama_audio(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db, with_audio=False)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        seen = []
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **k: seen.append(args) or True)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0, diarize_audio_path=None)

        assert seen == []
        assert background_jobs.get_status(job_id)["result"]["diarize_started"] is False
        # The transcript itself must still succeed even though diarization
        # was skipped -- a missing audio file for diarization purposes is
        # not a reason to fail the whole job.
        assert background_jobs.get_status(job_id)["result"]["line_count"] == 1
        _clear(job_id)
