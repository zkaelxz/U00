"""
Tests for services/transcribe_service.py -- Migration Slice 20's
Transcript-stage config (read + a narrow, validated partial update) and
its "job does everything" start_transcribe_run action.

The job body (_run_transcribe_and_apply_job) is called directly, the same
way tests/test_workspace_tab.py exercises run_transcribe_job -- transcribe_
for_timing is mocked throughout, so no real model/GPU/audio is involved.
"""
import functools
import multiprocessing
import json
import os
import subprocess
import sys
import tempfile
import time

import pytest

import background_jobs
import core as core_module
from services import transcribe_service
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError, UnsupportedOperationError)


def _drama_with_audio(isolated_db, **fields):
    fields.setdefault("title_en", "D")
    fields.setdefault("audio_filename", "audio.wav")
    did = isolated_db.create_drama(**fields)
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, fields["audio_filename"]), "wb").close()
    return did, ddir


def _drama_with_video(isolated_db, with_audio=False, **fields):
    fields.setdefault("title_en", "D")
    fields.setdefault("source_video_filename", "source.mp4")
    fields.setdefault("transcript_mode", "hardsub_ocr")
    if with_audio:
        fields.setdefault("audio_filename", "audio.wav")
    did = isolated_db.create_drama(**fields)
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, fields["source_video_filename"]), "wb").close()
    if with_audio:
        open(os.path.join(ddir, fields["audio_filename"]), "wb").close()
    return did, ddir


def _clear(job_id):
    background_jobs.clear_job(job_id)


def _capture_worker_start(monkeypatch, started=True):
    """Fakes start_process_job for an audio transcribe run: records the
    worker's arguments by parameter name, the on_done hook's options and the
    start call itself (job_id, target, start_kwargs)."""
    import inspect
    captured = {}

    def fake_start_process_job(job_id, target, args=(), **k):
        captured.update(dict(zip(inspect.signature(target).parameters, args)))
        captured.update(k["on_done"].keywords)
        captured.update(job_id=job_id, target=target, start_kwargs=k)
        return started
    monkeypatch.setattr(background_jobs, "start_process_job", fake_start_process_job)
    return captured


def _seed_running_job(job_id):
    _clear(job_id)
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}


class TestGetTranscribeConfig:
    def test_unknown_drama_raises(self, isolated_db):
        with pytest.raises(NotFoundError):
            transcribe_service.get_transcribe_config(999999)

    def test_fresh_drama_defaults(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.get_transcribe_config(did)
        assert result == {
            "drama_id": did,
            "transcript_mode": "have_transcript",
            "has_audio_pipeline": True,
            "audio_available": False,
            "alignment_method": "whisper_diff",
            "asr_backend_choice": "whisper",
            "whisper_size": core_module.DEFAULT_WHISPER_SIZE,
            "whisper_model_cached": core_module.is_whisper_model_cached(core_module.DEFAULT_WHISPER_SIZE),
            "measured_speed": None,
            "measured_speed_runs": 0,
            "measured_stage_seconds": {}, "measured_diarize_speed": None, "measured_diarize_runs": 0,
            "whisper_installed": result["whisper_installed"],
            "beam_size": 5,
            "min_silence_ms": 300,
            "vad_threshold": 0.5,
            "hallucination_silence_sec": 2.0,
            "separate_vocals_first": False,
            "separation_backend": "auto",
            "realign_long_segments": False,
            "whisper_fast_mode": False,
            "use_groq": False,
            "has_video_source": False,
            "hardsub_ocr_backend": "paddle",
            "hardsub_interval_sec": 1.0,
            "auto_initial_prompt": "",
        }

    @pytest.mark.parametrize("installed", [True, False])
    def test_reports_whether_faster_whisper_is_installed(self, isolated_db, monkeypatch, installed):
        import diagnostics
        seen = []
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: seen.append(name) or installed)
        did = isolated_db.create_drama(title_en="D")
        assert transcribe_service.get_transcribe_config(did)["whisper_installed"] is installed
        assert seen == ["faster_whisper"]

    def test_reflects_persisted_values(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.update_drama(did, min_silence_ms=800, vad_threshold=0.3,
                                 separate_vocals_first=1, use_groq=1)
        result = transcribe_service.get_transcribe_config(did)
        assert result["min_silence_ms"] == 800
        assert result["vad_threshold"] == 0.3
        assert result["separate_vocals_first"] is True
        assert result["use_groq"] is True


class TestUpdateTranscribeConfig:
    def test_unknown_drama_raises(self, isolated_db):
        with pytest.raises(NotFoundError):
            transcribe_service.update_transcribe_config(999999, beam_size=8)

    def test_updates_only_passed_fields(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, min_silence_ms=1000)
        assert result["min_silence_ms"] == 1000
        assert result["vad_threshold"] == 0.5  # untouched, still the default

    def test_no_fields_is_a_no_op(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        before = transcribe_service.get_transcribe_config(did)
        after = transcribe_service.update_transcribe_config(did)
        assert before == after

    def test_unknown_alignment_method_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, alignment_method="nonsense")

    def test_unknown_asr_backend_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, asr_backend_choice="nonsense")

    def test_beam_size_out_of_range_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, beam_size=11)

    @pytest.mark.parametrize("value", [99, 3001])
    def test_min_silence_ms_out_of_range_raises(self, isolated_db, value):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, min_silence_ms=value)

    @pytest.mark.parametrize("value", [100, 3000])
    def test_min_silence_ms_bounds_accepted(self, isolated_db, value):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, min_silence_ms=value)
        assert result["min_silence_ms"] == value

    def test_vad_threshold_out_of_range_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, vad_threshold=1.0)

    def test_hallucination_silence_sec_zero_turns_it_off_and_is_kept(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, hallucination_silence_sec=0)
        assert result["hallucination_silence_sec"] == 0

    def test_hallucination_silence_sec_out_of_range_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        for bad in (0.2, 11):
            with pytest.raises(InvalidInputError):
                transcribe_service.update_transcribe_config(did, hallucination_silence_sec=bad)

    def test_unknown_separation_backend_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, separation_backend="nonsense")

    def test_bool_fields_persist_as_true_and_false(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, realign_long_segments=True)
        assert result["realign_long_segments"] is True
        result = transcribe_service.update_transcribe_config(did, realign_long_segments=False)
        assert result["realign_long_segments"] is False


class TestStartTranscribeRun:
    def test_unknown_drama_raises(self, isolated_db):
        with pytest.raises(NotFoundError):
            transcribe_service.start_transcribe_run(999999)

    def test_missing_audio_raises_unsupported_operation(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_hardsub_ocr_mode_with_audio_but_no_video_still_raises(self, isolated_db):
        # hardsub_ocr checks for a video source specifically -- having
        # audio (but no video) must not be treated as satisfying it.
        did, _ = _drama_with_audio(isolated_db, transcript_mode="hardsub_ocr")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_have_transcript_mode_without_text_raises(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_hardsub_ocr_without_video_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", transcript_mode="hardsub_ocr")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_hardsub_ocr_with_video_starts_the_job(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_video(isolated_db)
        captured = {}

        def fake_start_job(job_id, target, *a, **k):
            import inspect
            names = list(inspect.signature(target).parameters)
            captured.update(dict(zip(names, a)))
            return True
        monkeypatch.setattr(background_jobs, "start_job", fake_start_job)

        result = transcribe_service.start_transcribe_run(did)

        assert result == {"job_id": f"transcribe_{did}"}
        assert captured["video_path"] == os.path.join(ddir, "source.mp4")
        assert captured["audio_path"] is None
        assert captured["transcript_mode"] == "hardsub_ocr"

    def test_hardsub_ocr_resolves_the_dramas_audio_for_diarization_independently(
            self, isolated_db, monkeypatch):
        # Regression test: a hardsub_ocr drama commonly also has real audio
        # on disk (the video-upload flow extracts one alongside the video),
        # and diarization always needs that real audio regardless of where
        # the transcript text came from -- this must not stay None just
        # because hardsub_ocr's own transcribe-time audio_path is None.
        did, ddir = _drama_with_video(isolated_db, with_audio=True)
        captured = {}

        def fake_start_job(job_id, target, *a, **k):
            import inspect
            names = list(inspect.signature(target).parameters)
            captured.update(dict(zip(names, a)))
            return True
        monkeypatch.setattr(background_jobs, "start_job", fake_start_job)

        transcribe_service.start_transcribe_run(did, run_diarize=True)

        assert captured["audio_path"] is None  # no transcribe-time audio for hardsub_ocr
        assert captured["diarize_audio_path"] == os.path.join(ddir, "audio.wav")

    def test_hardsub_ocr_with_no_audio_leaves_diarize_audio_path_none(
            self, isolated_db, monkeypatch):
        did, ddir = _drama_with_video(isolated_db, with_audio=False)
        captured = {}

        def fake_start_job(job_id, target, *a, **k):
            import inspect
            names = list(inspect.signature(target).parameters)
            captured.update(dict(zip(names, a)))
            return True
        monkeypatch.setattr(background_jobs, "start_job", fake_start_job)

        transcribe_service.start_transcribe_run(did, run_diarize=True)

        assert captured["diarize_audio_path"] is None

    def test_starts_the_background_job(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)

        result = transcribe_service.start_transcribe_run(did)

        assert result == {"job_id": f"transcribe_{did}"}
        assert captured["job_id"] == f"transcribe_{did}"
        assert captured["target"] is transcribe_service._transcribe_worker
        k = captured["start_kwargs"]
        assert k["gpu_touching"] is True and k["kill_whole_tree"] is True
        assert k["start_method"] == "spawn"
        assert callable(k["on_done"]) and callable(k["on_finish"])
        assert captured["drama_id"] == did

    def test_on_finish_removes_the_scratch_folder_and_a_leftover_part_file(self, isolated_db,
                                                                         monkeypatch):
        """A worker killed during the cross-volume move of vocals.wav leaves
        a .part- file beside it; the finish hook removes that too."""
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        folder = os.path.dirname(captured["audio_path"])
        leftover = os.path.join(folder, ".part-abc123.wav")
        open(leftover, "wb").close()
        kept = [os.path.join(folder, name) for name in ("vocals.wav", ".part-notes.txt")]
        for path in kept:
            open(path, "wb").close()
        scratch = captured["scratch_dir"]
        assert os.path.isdir(scratch)

        captured["start_kwargs"]["on_finish"](f"transcribe_{did}")

        assert not os.path.exists(scratch) and not os.path.exists(leftover)
        assert all(os.path.exists(path) for path in kept)
        assert os.path.exists(captured["audio_path"])

    def test_no_key_is_passed_to_the_worker_process(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", use_groq=1)
        keys = {"groq": "gsk_test_groq_key_value", "hf_token": "hf_test_token_value"}
        monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                            lambda key, env_path=None: keys.get(key))
        captured = _capture_worker_start(monkeypatch)

        transcribe_service.start_transcribe_run(did, run_diarize=True)

        import inspect
        names = list(inspect.signature(transcribe_service._transcribe_worker).parameters)
        worker_args = [captured[n] for n in names[:-1]]
        assert not set(keys.values()) & {a for a in worker_args if isinstance(a, str)}
        assert captured["use_groq"] is True
        # The token stays in this process, for the chained speaker detection.
        assert captured["hf_token"] == "hf_test_token_value"

    def test_already_running_raises_conflict(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch, started=False)
        with pytest.raises(ConflictError):
            transcribe_service.start_transcribe_run(did)
        assert not os.path.exists(captured["scratch_dir"])

    def test_language_and_script_default_to_the_dramas_own(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                      source_language="ja", chinese_script="traditional")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["source_language"] == "ja" and captured["chinese_script"] == "traditional"

    def test_explicit_language_overrides_the_dramas_own(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", source_language="ja")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did, source_language="ko")
        assert captured["source_language"] == "ko"

    def test_unknown_language_raises_invalid_input(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        with pytest.raises(InvalidInputError):
            transcribe_service.start_transcribe_run(did, source_language="xx")

    def test_unknown_script_raises_invalid_input(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        with pytest.raises(InvalidInputError):
            transcribe_service.start_transcribe_run(did, chinese_script="xx")

    def test_novel_narration_drama_is_rejected(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   content_mode="novel_narration")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_use_groq_without_a_key_raises_dependency_unavailable(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", use_groq=1)
        monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                            lambda key, env_path=None: None)
        with pytest.raises(DependencyUnavailableError):
            transcribe_service.start_transcribe_run(did)

    def test_whitespace_only_transcript_text_raises(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did, transcript_text="  \n ")

    def test_positional_job_args_are_mapped_in_signature_order(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(
            isolated_db, transcript_mode="whisper", whisper_size="small", beam_size=7,
            min_silence_ms=900, vad_threshold=0.4, separate_vocals_first=1,
            separation_backend="demucs", realign_long_segments=1, whisper_fast_mode=1)
        captured = _capture_worker_start(monkeypatch)

        transcribe_service.start_transcribe_run(did, initial_prompt="names")

        assert captured["whisper_size"] == "small"
        assert captured["beam_size"] == 7
        assert captured["min_silence_ms"] == 900
        assert captured["vad_threshold"] == 0.4
        assert captured["hallucination_silence_sec"] == 2.0
        assert captured["separate_vocals_first"] is True
        assert captured["separation_backend"] == "demucs"
        assert captured["realign_long_segments"] is True
        assert captured["whisper_fast_mode"] is True
        assert captured["use_groq"] is False
        assert captured["initial_prompt"] == "names"
        assert captured["use_gpu"] is False
        assert captured["asr_backend_choice"] == "whisper"
        assert captured["alignment_method"] == "whisper_diff"
        # Every worker parameter but the queue is passed.
        import inspect
        names = list(inspect.signature(transcribe_service._transcribe_worker).parameters)
        assert names[-1] == "result_queue" and all(n in captured for n in names[:-1])


@pytest.fixture(autouse=True)
def _no_real_whisper_load(monkeypatch):
    """The job body pre-loads the Whisper model to report stage/device;
    tests never load a real model."""
    monkeypatch.setattr(core_module, "load_whisper_model", lambda *a, **k: object())


class TestRunTranscribeAndApplyJob:
    def test_model_loading_message_and_gpu_device_reported(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        messages = []
        monkeypatch.setattr(core_module, "is_whisper_model_cached", lambda size: False)
        monkeypatch.setattr(core_module, "get_whisper_device_info", lambda *a, **k: {
            "device": "cpu", "compute_type": "int8", "gpu_error": "RuntimeError: no cublas64_12.dll"})
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (messages.append(m), real_update(j, f, m)))
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "large-v3", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            use_gpu=True)

        assert messages[0].startswith("Loading Whisper model large-v3 (downloading on first use, ~3 GB) (elapsed ")
        assert "GPU unavailable (RuntimeError: no cublas64_12.dll); using CPU" in messages[1]
        result = background_jobs.get_status(job_id)["result"]
        assert result["device"] == "GPU unavailable (RuntimeError: no cublas64_12.dll); using CPU"
        # A fallback at model load is as loud as one during inference.
        assert result["gpu_fallback"] == "RuntimeError: no cublas64_12.dll"
        assert result["device_notice"] == (
            "Transcription ran on the CPU because the GPU couldn't be used "
            "(RuntimeError: no cublas64_12.dll). This was slower than on the GPU.")
        _clear(job_id)

    def test_runtime_gpu_fallback_reason_lands_in_result(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def fake_transcribe(*a, on_gpu_fallback=None, **k):
            on_gpu_fallback(RuntimeError("cuDNN failed"))
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", fake_transcribe)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            use_gpu=True)

        result = background_jobs.get_status(job_id)["result"]
        assert result["gpu_fallback"] == "RuntimeError: cuDNN failed"
        assert result["device"] == "GPU unavailable (RuntimeError: cuDNN failed); using CPU"
        assert "ran on the CPU" in result["device_notice"] and "slower" in result["device_notice"]
        _clear(job_id)

    def test_whisper_mode_success_saves_lines(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        fake_segments = [{"start": 0.0, "end": 1.0, "text": "hi"},
                         {"start": 1.0, "end": 2.0, "text": "there"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: fake_segments)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        status = background_jobs.get_status(job_id)
        assert status["result"]["line_count"] == 2
        assert status["result"]["diarize_started"] is False
        saved = isolated_db.load_lines(did)
        assert [r["zh"] for r in saved] == ["hi", "there"]
        assert isolated_db.get_drama(did)["status"] == "aligned"
        _clear(job_id)

    def test_have_transcript_mode_aligns_supplied_text(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        fake_segments = [{"start": 0.0, "end": 2.0, "text": "hi there"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: fake_segments)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "have_transcript", "hi there", "zh",
            "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        status = background_jobs.get_status(job_id)
        assert status["result"]["line_count"] == 1
        saved = isolated_db.load_lines(did)
        assert saved[0]["zh"] == "hi there"
        _clear(job_id)

    def test_empty_segments_records_failed_reason(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", lambda *a, **k: [])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "empty"}
        _clear(job_id)

    def test_empty_result_keeps_existing_lines(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        from core import Line
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="existing")])
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        # An empty transcript (segments with only whitespace text) with
        # existing lines already saved must not wipe them out.
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "   "}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "empty_kept_existing"
        assert result["existing_line_count"] == 1
        saved = isolated_db.load_lines(did)
        assert [r["zh"] for r in saved] == ["existing"]
        _clear(job_id)

    def test_cancelled_before_transcription_records_failed_reason(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        background_jobs._jobs[job_id]["cancel_requested"] = True

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        _clear(job_id)

    def test_model_download_error_records_failed_reason(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def raise_download_error(*a, **k):
            raise core_module.ModelDownloadError("no network")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", raise_download_error)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "model_download"
        _clear(job_id)

    def test_chains_diarization_when_hf_token_given(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        from services import settings_service
        monkeypatch.setattr(settings_service, "get_use_gpu", lambda: True)
        diarize_calls = []
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **k: diarize_calls.append(
                                (job_id, args)) or True)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", 3,
            diarize_audio_path=os.path.join(ddir, "audio.wav"))

        # Step 101: the chained run carries the persisted use_gpu setting.
        assert diarize_calls == [(f"diarize_{did}", (
            os.path.join(ddir, "audio.wav"), "hf-token", 3,
            {"use_gpu": True, "min_speakers": None, "max_speakers": None}))]
        assert background_jobs.get_status(job_id)["result"]["diarize_started"] is True
        _clear(job_id)

    def test_success_writes_snapshot_and_raw_transcript(self, isolated_db, monkeypatch):
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="old")])
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        cancelled = []
        monkeypatch.setattr(background_jobs, "cancel_line_jobs", lambda d: cancelled.append(d))
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "new"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert cancelled == [did]
        assert os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["new"]
        assert len(isolated_db.list_line_history(did)) == 1
        _clear(job_id)

    def test_raw_transcript_saves_the_settings_the_run_started_with(self, isolated_db, monkeypatch):
        import raw_transcript
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def edit_everything_mid_run(*a, **k):
            isolated_db.update_drama(did, beam_size=99, min_silence_ms=9999)
            background_jobs.set_gpu_max_parallel(4)
            return [{"start": 0.0, "end": 1.0, "text": "new"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", edit_everything_mid_run)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, 2,
            initial_prompt="names")

        s = raw_transcript.load_latest(ddir)["settings"]
        assert set(s) == set(raw_transcript.SETTINGS_KEYS)
        assert (s["whisper_size"], s["beam_size"], s["min_silence_ms"]) == ("medium", 5, 300)
        assert s["gpu_max_parallel"] == 1
        assert s["expected_speakers"] == 2 and s["language"] == "zh"
        assert s["initial_prompt_chars"] == 5 and "names" not in json.dumps(s)
        _clear(job_id)

    def test_groq_path_uses_groq_and_reports_failure(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def boom(*a, **k):
            raise core_module.GroqTranscriptionError("bad key")
        monkeypatch.setattr(core_module, "transcribe_with_groq", boom)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, True, "key", None, None)

        assert background_jobs.get_status(job_id)["result"]["failed_reason"] == "groq"
        _clear(job_id)

    def test_vocal_separation_error_records_failed_reason(self, isolated_db, monkeypatch):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def boom(*a, **k):
            raise audio_preprocess.VocalSeparationError("no demucs")
        monkeypatch.setattr(audio_preprocess, "separate_vocals", boom)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"]["failed_reason"] == "vocal_separation"
        _clear(job_id)

    def test_vocal_separation_status_names_stage_device_and_percent(self, isolated_db, monkeypatch):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        seen = []

        def fake_separate(in_path, out_path, backend="auto", progress_cb=None,
                          cancel_check_cb=None, use_gpu=None, event_cb=None):
            event_cb("loading", "demucs")
            seen.append(background_jobs.get_status(job_id)["message"])
            event_cb("device", "cpu")
            seen.append(background_jobs.get_status(job_id)["message"])
            progress_cb(0.4)
            seen.append(background_jobs.get_status(job_id)["message"])
            seen.append(use_gpu)
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None,
            use_gpu=False)

        assert seen[0].startswith("Loading the vocal separation model")
        assert "no progress is available" in seen[0]
        assert seen[1] == "Separating vocals on CPU (slow), 0%"
        assert seen[2] == "Separating vocals on CPU (slow), 40%"
        assert seen[3] is False
        _clear(job_id)

    def test_model_load_stage_says_no_progress_is_available(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        seen = []
        monkeypatch.setattr(transcribe_service.core_module, "load_whisper_model",
                            lambda *a, **k: seen.append(background_jobs.get_status(job_id)["message"]))
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert seen and seen[0].startswith("Loading Whisper model medium")
        assert "elapsed" in seen[0] and "no progress is available" in seen[0]
        _clear(job_id)

    def test_realign_reports_progress_and_cancel_ends_the_job_cancelled(
            self, isolated_db, monkeypatch):
        import word_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 20.0, "text": "hi"}])
        seen = []

        def fake_realign(segments, *a, progress_cb=None, cancel_check=None, **k):
            progress_cb(1, 3)
            seen.append((background_jobs.get_status(job_id)["progress"],
                         background_jobs.get_status(job_id)["message"]))
            background_jobs.request_cancel(job_id)
            assert cancel_check()
            return segments
        monkeypatch.setattr(word_align, "realign_oversized_segments", fake_realign)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", True, False, False, None, None, None)

        progress, message = seen[0]
        assert message == "Splitting long merged lines: 1 of 3"
        assert transcribe_service.REALIGN_START * transcribe_service.RUNNING_MAX < progress < transcribe_service.RUNNING_MAX
        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        _clear(job_id)

    def test_realign_error_is_reported_but_lines_still_saved(self, isolated_db, monkeypatch):
        import word_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        def boom(*a, **k):
            raise word_align.WordAlignError("no torchaudio")
        monkeypatch.setattr(word_align, "realign_oversized_segments", boom)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", True, False, False, None, None, None)

        result = background_jobs.get_status(job_id)["result"]
        assert result["word_align_error"] == "no torchaudio"
        assert result["line_count"] == 1
        _clear(job_id)

    def test_gpu_fallback_message_is_reported(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def fake(*a, **k):
            k["on_gpu_fallback"]("CUDA oom")
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", fake)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"]["gpu_fallback"] == "CUDA oom"
        _clear(job_id)

    def test_diarization_runs_on_the_original_audio_not_the_vocals(self, isolated_db, monkeypatch):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        original = os.path.join(ddir, "audio.wav")
        monkeypatch.setattr(audio_preprocess, "separate_vocals",
                            lambda *a, **k: os.path.join(ddir, "vocals.wav"))
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        seen = []
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **k: seen.append(args) or True)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, original, "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, "hf-token", None,
            diarize_audio_path=original)

        assert seen[0][0] == original
        _clear(job_id)


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

        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "empty"}
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

    def test_diarization_chains_off_the_dramas_own_audio_not_the_video(
            self, isolated_db, monkeypatch):
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
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **k: seen.append(args) or True)
        audio_path = os.path.join(ddir, "audio.wav")

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", None,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0, diarize_audio_path=audio_path)

        assert seen == [(audio_path, "hf-token", None,
                         {"use_gpu": False, "min_speakers": None, "max_speakers": None})]
        assert background_jobs.get_status(job_id)["result"]["diarize_started"] is True
        _clear(job_id)

    def test_diarization_skipped_gracefully_with_no_drama_audio(self, isolated_db, monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db, with_audio=False)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        seen = []
        monkeypatch.setattr(background_jobs, "start_process_job",
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



class TestQwen3Backends:
    """Slice 34: the stored qwen3_asr / qwen3_forced_align choices are honoured
    by the run. Fake backend classes only -- no model, GPU or network."""

    _AUDIO_ARGS = ("simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False,
                   None, None, None)

    def _run(self, did, ddir, mode, text=None, **kw):
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), mode, text, "zh", *self._AUDIO_ARGS, **kw)
        return job_id

    def _raw(self, ddir):
        import json
        with open(os.path.join(ddir, "raw_transcript.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_qwen3_asr_choice_replaces_text_keeps_timing_and_records_backend(
            self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.5, "text": "whisper text"}])
        calls = []

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                calls.append((language, whisper_segments, use_gpu, batch_size))
                return [{"start": s["start"], "end": s["end"], "text": "qwen text"}
                        for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)
        # Step 103: the saved batch size reaches the backend.
        from services import asr_options_service
        asr_options_service.set_asr_options(qwen_asr_batch_size=4)
        messages = []
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (messages.append(m), real_update(j, f, m)))

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr", use_gpu=True)

        assert len(calls) == 1 and calls[0][0] == "zh" and calls[0][2] is True
        assert calls[0][3] == 4
        saved = isolated_db.load_lines(did)
        assert [(r["zh"], r["start"], r["end"]) for r in saved] == [("qwen text", 0.0, 1.5)]
        raw = self._raw(ddir)
        assert (raw["backend"], raw["model"]) == ("qwen3_asr", "Qwen3-ASR")
        assert any("Qwen3-ASR" in m for m in messages)
        assert background_jobs.get_status(job_id)["result"]["asr_backend"] == "qwen3_asr"
        _clear(job_id)

    def test_long_qwen3_segment_becomes_several_subtitle_lines(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 175.0, "end": 210.0, "text": "w"}])
        text = "好,你刚讲不要讲。哇,我先离开一下。好,OK。重来。哇,大家好哦!" * 3

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                return [{"start": s["start"], "end": s["end"], "text": text}
                        for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        saved = isolated_db.load_lines(did)
        assert len(saved) > 3
        assert "".join(r["zh"] for r in saved) == text
        assert saved[0]["start"] == 175.0 and saved[-1]["end"] == 210.0
        assert all(a["end"] == b["start"] for a, b in zip(saved, saved[1:]))
        assert len(self._raw(ddir)["segments"]) == 1
        _clear(job_id)

    def test_progress_never_hits_100_before_done_and_never_goes_back(
            self, isolated_db, monkeypatch):
        """Whisper fills 0-85%, the Qwen3 step 85-99% (no percent until its
        first batch), and only completion reports 100%."""
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        seen = []
        real_update = background_jobs.update_progress

        def record(j, f, m=""):
            seen.append((f, m))
            real_update(j, f, m)
        monkeypatch.setattr(background_jobs, "update_progress", record)

        def fake_whisper(*a, progress_cb=None, **k):
            for f in (0.25, 1.0):
                progress_cb(f)
            return [{"start": 0.0, "end": 1.5, "text": "w"}, {"start": 2.0, "end": 3.0, "text": "w2"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", fake_whisper)

        class FakeQwen3ASR:
            def transcribe(self, audio_path, language, whisper_segments, use_gpu=False,
                           batch_size=1, progress_cb=None, **_device_callbacks):
                progress_cb(0.5)
                progress_cb(1.0)
                return [{"start": s["start"], "end": s["end"], "text": "q"} for s in whisper_segments]
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", FakeQwen3ASR)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        fracs = [f for f, m in seen if m]
        assert max(fracs) < 1.0
        whisper_fracs = [f for f, m in seen if m.startswith("Transcribing (step 1 of 2)")]
        assert whisper_fracs and max(whisper_fracs) == pytest.approx(0.85)
        qwen_fracs = [f for f, m in seen if "step 2 of 2)... " in m]
        assert qwen_fracs and min(qwen_fracs) > 0.85 and max(qwen_fracs) <= 0.99
        # Monotonic from the first Whisper percent onward (the ticker's own
        # no-percent message holds the value at the split).
        later = [f for f, m in seen if m.startswith(("Transcribing", "Re-transcribing"))]
        assert later == sorted(later)
        _clear(job_id)

    def test_whisper_only_run_stays_below_100_until_done(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        seen = []
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (seen.append(f), real_update(j, f, m)))

        def fake_whisper(*a, progress_cb=None, **k):
            progress_cb(1.0)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", fake_whisper)
        job_id = self._run(did, ddir, "whisper")
        assert max(seen) < 1.0
        _clear(job_id)

    def test_song_with_one_whisper_segment_gives_one_qwen3_line_and_warns(
            self, isolated_db, monkeypatch):
        """Qwen3-ASR only sees the slices Whisper's speech detector found, so a
        song where Whisper finds one segment yields one line however long the
        audio is; the run reports the low coverage instead of looking fine."""
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 6.0, "text": "first line"}])
        monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda p: 240.0)
        monkeypatch.setattr(asr_backend, "load_qwen3_asr", lambda **k: type(
            "M", (), {"transcribe": lambda self, audio, language: [
                type("R", (), {"text": "qwen first line"})()]})())
        monkeypatch.setattr(asr_backend, "extract_audio_slice",
                            lambda a, s, e, out: open(out, "wb").close())

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["qwen first line"]
        warning = background_jobs.get_status(job_id)["result"]["coverage_warning"]
        assert warning.startswith("Only 2% of the audio has text")
        assert "Qwen3-ASR only re-transcribes" in warning
        _clear(job_id)

    def test_default_whisper_path_never_touches_qwen3(self, isolated_db, monkeypatch):
        import asr_backend
        import forced_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        def boom(*a, **k):
            raise AssertionError("Qwen3 must not be used")
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", boom)
        monkeypatch.setattr(forced_align, "align_with_qwen3", boom)

        job_id = self._run(did, ddir, "whisper")

        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["hi"]
        assert self._raw(ddir)["backend"] == "whisper"
        result = background_jobs.get_status(job_id)["result"]
        assert result["asr_backend"] == "whisper" and result["alignment_method"] == "whisper_diff"
        _clear(job_id)

    def test_qwen3_asr_import_error_fails_job_without_saving(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])

        class Broken:
            def transcribe(self, *a, **k):
                raise ImportError("No module named 'qwen_asr'")
        monkeypatch.setattr(asr_backend, "Qwen3ASRBackend", Broken)

        job_id = self._run(did, ddir, "whisper", asr_backend_choice="qwen3_asr")

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "dependency_missing"
        assert "pip install qwen-asr torch" in result["detail"]
        assert isolated_db.load_lines(did) == []
        assert not os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        _clear(job_id)

    def test_forced_align_choice_uses_true_alignment(self, isolated_db, monkeypatch):
        import forced_align
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "x"}])
        calls = []

        def fake_align(audio_path, user_lines, segments, language, use_gpu=False, **_device_callbacks):
            calls.append((language, use_gpu, list(user_lines)))
            return [Line(idx=0, start=0.25, end=1.75, zh="hi there")]
        monkeypatch.setattr(forced_align, "align_with_qwen3", fake_align)

        job_id = self._run(did, ddir, "have_transcript", "hi there",
                           alignment_method="qwen3_forced_align")

        assert len(calls) == 1 and calls[0][0] == "zh"
        saved = isolated_db.load_lines(did)
        assert [(r["start"], r["end"]) for r in saved] == [(0.25, 1.75)]
        assert self._raw(ddir)["mode"] == "aligned_transcript"
        result = background_jobs.get_status(job_id)["result"]
        assert result["alignment_method"] == "qwen3_forced_align"
        assert result["forced_align_error"] is None
        _clear(job_id)

    def test_forced_align_cancel_ends_cancelled_releases_gpu_and_keeps_lines(
            self, isolated_db, monkeypatch):
        import forced_align
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="old")])
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "x"}])
        released = []
        monkeypatch.setattr(transcribe_service.core_module, "release_gpu_models",
                            lambda: released.append(1))
        job_id = f"transcribe_{did}"

        def fake_align(*a, cancel_check=None, **k):
            background_jobs.request_cancel(job_id)
            cancel_check()
            raise AssertionError("cancel_check must raise once cancel is requested")
        monkeypatch.setattr(forced_align, "align_with_qwen3", fake_align)

        # _spawn turns this exception into a "cancelled" job.
        with pytest.raises(background_jobs.JobCancelled):
            self._run(did, ddir, "have_transcript", "hi there",
                      alignment_method="qwen3_forced_align")

        assert released
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["old"]
        _clear(job_id)

    def test_forced_align_value_error_falls_back_and_is_reported(self, isolated_db, monkeypatch):
        import forced_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 2.0, "text": "hi there"}])

        def too_long(*a, **k):
            raise ValueError("Line 0 spans 400s on its own")
        monkeypatch.setattr(forced_align, "align_with_qwen3", too_long)

        job_id = self._run(did, ddir, "have_transcript", "hi there",
                           alignment_method="qwen3_forced_align")

        result = background_jobs.get_status(job_id)["result"]
        assert result["line_count"] == 1
        assert "spans 400s" in result["forced_align_error"]
        assert result["alignment_method"] == "whisper_diff"
        _clear(job_id)

    def test_start_forced_align_without_transcript_is_invalid_input(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   alignment_method="qwen3_forced_align")
        with pytest.raises(InvalidInputError, match="needs a transcript"):
            transcribe_service.start_transcribe_run(did)

    def test_start_missing_package_is_dependency_unavailable(self, isolated_db, monkeypatch):
        import importlib.util
        real_find = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, *a, **k: None if name == "qwen_asr" else real_find(name, *a, **k))
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   asr_backend_choice="qwen3_asr")
        with pytest.raises(DependencyUnavailableError, match="pip install qwen-asr torch"):
            transcribe_service.start_transcribe_run(did)
        did2, _ = _drama_with_audio(isolated_db, transcript_mode="have_transcript",
                                    alignment_method="qwen3_forced_align")
        with pytest.raises(DependencyUnavailableError, match="qwen-asr"):
            transcribe_service.start_transcribe_run(did2, transcript_text="hi")

    def test_start_passes_choices_to_the_job_when_packages_present(self, isolated_db, monkeypatch):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec", lambda *a, **k: object())
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                   asr_backend_choice="qwen3_asr")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert (captured["asr_backend_choice"], captured["alignment_method"]) == (
            "qwen3_asr", "whisper_diff")


class TestMossBackend:
    """Step 104: the experimental MOSS-Transcribe-Diarize backend -- off by
    default, refused unless its Settings toggle is on and the package is
    installed, and when run it keeps MOSS's own speaker labels. Fake backend
    only -- no model, GPU or network."""

    _AUDIO_ARGS = ("simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False,
                   None, "hf-token", None)

    def _enable(self, monkeypatch, installed=True):
        from services import asr_options_service
        asr_options_service.set_asr_options(moss_experimental=True)
        monkeypatch.setattr(asr_options_service, "moss_installed", lambda: installed)

    def test_choice_is_refused_while_the_toggle_is_off(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        with pytest.raises(InvalidInputError, match="experimental"):
            transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")
        assert isolated_db.get_drama(did).get("asr_backend_choice") in (None, "whisper")

    def test_choice_is_saved_once_the_toggle_is_on(self, isolated_db, monkeypatch):
        self._enable(monkeypatch)
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")
        assert isolated_db.get_drama(did)["asr_backend_choice"] == "moss_td"

    def test_start_is_refused_when_the_toggle_was_turned_off_later(self, isolated_db, monkeypatch):
        self._enable(monkeypatch)
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")
        from services import asr_options_service
        asr_options_service.set_asr_options(moss_experimental=False)
        with pytest.raises(InvalidInputError, match="experimental"):
            transcribe_service.start_transcribe_run(did)
        with pytest.raises(InvalidInputError, match="experimental"):
            transcribe_service.validate_transcribe_options(did)

    def test_start_names_the_missing_package(self, isolated_db, monkeypatch):
        self._enable(monkeypatch, installed=False)
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")
        with pytest.raises(DependencyUnavailableError, match="MOSS"):
            transcribe_service.start_transcribe_run(did)

    def _run(self, did, ddir, **kw):
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh",
            *self._AUDIO_ARGS, diarize_audio_path=os.path.join(ddir, "audio.wav"),
            asr_backend_choice="moss_td", **kw)
        return job_id

    def _fake_moss(self, monkeypatch, segments, truncated=False, calls=None):
        import asr_backend

        class FakeMoss:
            name = "moss_td"

            def transcribe(self, audio_path, language=None, use_gpu=False, run_info=None):
                if calls is not None:
                    calls.append((language, use_gpu))
                run_info.update({"device": "cuda" if use_gpu else "cpu", "truncated": truncated})
                return segments
        monkeypatch.setitem(asr_backend.BACKENDS, "moss_td", FakeMoss)

    def test_run_keeps_moss_speakers_and_skips_whisper_and_pyannote(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")

        def boom(*a, **k):
            raise AssertionError("Whisper must not run for MOSS")
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", boom)
        monkeypatch.setattr(transcribe_service.core_module, "load_whisper_model", boom)
        diarize_calls = []
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda *a, **k: diarize_calls.append(a) or True)
        calls = []
        self._fake_moss(monkeypatch, [
            {"start": 0.0, "end": 1.0, "text": "你好", "speaker": "S01"},
            {"start": 1.0, "end": 2.0, "text": "再见", "speaker": "S02"},
            {"start": 2.0, "end": 2.5, "text": "  ", "speaker": "S01"},
        ], calls=calls)

        job_id = self._run(did, ddir, use_gpu=True)

        assert calls == [("zh", True)]
        saved = isolated_db.load_lines(did)
        assert [(r["zh"], r["speaker"]) for r in saved] == [("你好", "S01"), ("再见", "S02")]
        assert [c["speaker_label"] for c in isolated_db.list_characters(did)] == ["S01", "S02"]
        assert diarize_calls == []
        result = background_jobs.get_status(job_id)["result"]
        assert result["asr_backend"] == "moss_td" and result["device"] == "GPU"
        assert result["diarize_started"] is False and not result.get("partial")
        import json
        with open(os.path.join(ddir, "raw_transcript.json"), encoding="utf-8") as f:
            assert json.load(f)["backend"] == "moss_td"
        _clear(job_id)

    def test_a_truncated_run_is_reported_as_partial(self, isolated_db, monkeypatch):
        from services import jobs_service
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        self._fake_moss(monkeypatch, [{"start": 0.0, "end": 1.0, "text": "x", "speaker": "S01"}],
                        truncated=True)
        job_id = self._run(did, ddir)
        result = background_jobs.get_status(job_id)["result"]
        assert result["partial"] is True
        outcome, message = jobs_service.derive_outcome("done", None,
                                                       jobs_service.project_result(result))
        assert outcome == "partial" and "output limit" in message
        _clear(job_id)

    def test_a_moss_failure_fails_the_job_without_saving(self, isolated_db, monkeypatch):
        import asr_backend
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")

        class Broken:
            def transcribe(self, *a, **k):
                raise RuntimeError("CUDA out of memory, token sk-abcdefghijklmnopqrstuvwx")
        monkeypatch.setitem(asr_backend.BACKENDS, "moss_td", Broken)
        job_id = self._run(did, ddir)
        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "moss_td"
        assert "sk-abcdefghijklmnopqrstuvwx" not in result["detail"]
        assert isolated_db.load_lines(did) == []
        _clear(job_id)

    def test_a_cancel_during_the_moss_call_keeps_the_existing_lines(self, isolated_db, monkeypatch):
        import asr_backend
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧的")])
        job_id = f"transcribe_{did}"

        class SlowMoss:
            def transcribe(self, audio_path, language=None, use_gpu=False, run_info=None):
                background_jobs._jobs[job_id]["cancel_requested"] = True
                return [{"start": 0.0, "end": 1.0, "text": "新的", "speaker": "S01"}]
        monkeypatch.setitem(asr_backend.BACKENDS, "moss_td", SlowMoss)
        self._run(did, ddir)
        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["旧的"]
        _clear(job_id)

    def test_other_options_still_save_on_a_moss_drama_after_the_toggle_is_off(
            self, isolated_db, monkeypatch):
        self._enable(monkeypatch)
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")
        from services import asr_options_service
        asr_options_service.set_asr_options(moss_experimental=False)
        # The form re-sends the stored moss_td with every save.
        transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td", beam_size=7)
        drama = isolated_db.get_drama(did)
        assert drama["beam_size"] == 7 and drama["asr_backend_choice"] == "moss_td"
        transcribe_service.update_transcribe_config(did, asr_backend_choice="whisper")
        with pytest.raises(InvalidInputError, match="experimental"):
            transcribe_service.update_transcribe_config(did, asr_backend_choice="moss_td")


def _wait_until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return bool(predicate())


def _wait_finished(job_id, timeout=20.0):
    _wait_until(lambda: (background_jobs.get_status(job_id) or {}).get("status")
                not in ("queued", "running"), timeout)
    return background_jobs.get_status(job_id)


def _process_gone(pid):
    """True once pid has exited (a zombie counts: it runs nothing)."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (FileNotFoundError, ProcessLookupError):
        # A process that exits between the open and the read fails the read
        # with ESRCH, not ENOENT; either way it is gone.
        return True


# Set in the parent by TestTranscribeProcessJob: a forked child would inherit
# it, a spawned one (a fresh interpreter) never does.
_PARENT_ONLY_PROBE = False


def _spawned_worker(scenario, *args):
    """The transcribe worker as TestTranscribeProcessJob's spawned child runs
    it. Under spawn the child imports every module fresh, so the parent's
    monkeypatches never reach it: the scenario's fakes are installed here,
    then the real worker runs. args end with (scratch_dir, result_queue)."""
    import json
    import audio_preprocess
    import db
    kind, library, marker = scenario
    if _PARENT_ONLY_PROBE:
        raise AssertionError("the worker was forked, not spawned")
    db.configure_library_dir(library)
    core_module.load_whisper_model = lambda *a, **k: object()
    scratch_dir = args[-2]

    def two_lines(*a, progress_cb=None, **k):
        progress_cb(0.5)
        return [{"start": 0.0, "end": 1.0, "text": "hi"},
                {"start": 1.0, "end": 2.0, "text": "there"}]

    def fake_separate(in_path, out_path, **k):
        if os.path.dirname(out_path) != scratch_dir:
            raise AssertionError("vocals not written in the scratch folder")
        with open(out_path, "wb") as f:
            f.write(b"vocals")
        return out_path

    def stuck_whisper(*a, progress_cb=None, **k):
        # One long model call with no cancel point, which has started a
        # helper process and written a temp file of its own.
        progress_cb(0.1)
        helper = subprocess.Popen(["sleep", "120"])
        fd, temp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(marker + ".tmp", "w") as f:
            json.dump({"worker": os.getpid(), "helper": helper.pid, "temp": temp_path}, f)
        os.replace(marker + ".tmp", marker)
        time.sleep(120)
        return [{"start": 0.0, "end": 1.0, "text": "新的"}]

    if kind == "two_lines":
        transcribe_service.transcribe_for_timing = two_lines
    elif kind == "vocals":
        audio_preprocess.separate_vocals = fake_separate
        transcribe_service.transcribe_for_timing = (
            lambda path, *a, **k: [{"start": 0.0, "end": 1.0, "text": path}])
    elif kind == "stuck":
        transcribe_service.transcribe_for_timing = stuck_whisper
    transcribe_service._transcribe_worker(*args)


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="checks the worker's processes through /proc")
class TestTranscribeProcessJob:
    """An audio transcription runs in its own spawned process
    (start_process_job, start_method="spawn"), with a fake Whisper installed
    in the child (_spawned_worker); nothing loads a real model."""

    def _use_spawned_fakes(self, monkeypatch, isolated_db, kind, marker=None):
        monkeypatch.setattr(sys.modules[__name__], "_PARENT_ONLY_PROBE", True)
        monkeypatch.setattr(transcribe_service, "_transcribe_worker", functools.partial(
            _spawned_worker, (kind, isolated_db.LIBRARY_DIR, marker)))

    def _track_scratch(self, monkeypatch):
        made = []
        real_new = transcribe_service.storage.new_workdir
        monkeypatch.setattr(transcribe_service.storage, "new_workdir",
                            lambda job_id=None: made.append(real_new(job_id)) or made[-1])
        return made

    def test_a_run_applies_lines_and_reports_progress(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _clear(job_id)
        self._use_spawned_fakes(monkeypatch, isolated_db, "two_lines")
        messages = []
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (messages.append((f, m)), real_update(j, f, m)))
        scratch = self._track_scratch(monkeypatch)

        transcribe_service.start_transcribe_run(did)
        status = _wait_finished(job_id, timeout=60)

        assert status["status"] == "done", status
        assert status["result"]["line_count"] == 2
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["hi", "there"]
        assert isolated_db.get_drama(did)["status"] == "aligned"
        assert os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        assert messages[0][1].startswith(f"Loading Whisper model {core_module.DEFAULT_WHISPER_SIZE}")
        assert background_jobs.stage_ticker.NOTE in messages[0][1]
        assert any(f == pytest.approx(0.5 * transcribe_service.RUNNING_MAX)
                   and m.startswith("Transcribing... 50%") for f, m in messages)
        assert max(f for f, _ in messages) < 1.0
        assert _wait_until(lambda: not os.path.exists(scratch[0]))
        assert _wait_until(lambda: isolated_db.gpu_lock_holder_count() == 0)
        _clear(job_id)

    def test_vocals_are_separated_in_the_scratch_folder_then_kept(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                      separate_vocals_first=1)
        job_id = f"transcribe_{did}"
        _clear(job_id)
        with open(os.path.join(ddir, "vocals.wav"), "wb") as f:
            f.write(b"an earlier run's vocals")
        self._use_spawned_fakes(monkeypatch, isolated_db, "vocals")
        scratch = self._track_scratch(monkeypatch)

        transcribe_service.start_transcribe_run(did)
        status = _wait_finished(job_id, timeout=60)

        assert status["status"] == "done", status
        vocals = os.path.join(ddir, "vocals.wav")
        assert [r["zh"] for r in isolated_db.load_lines(did)] == [vocals]
        with open(vocals, "rb") as f:
            assert f.read() == b"vocals"
        assert _wait_until(lambda: not os.path.exists(scratch[0]))
        _clear(job_id)

    def test_cancel_kills_the_run_and_writes_nothing(self, isolated_db, monkeypatch, tmp_path):
        import json
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧的")])
        job_id = f"transcribe_{did}"
        _clear(job_id)
        marker = str(tmp_path / "worker.json")
        self._use_spawned_fakes(monkeypatch, isolated_db, "stuck", marker)

        transcribe_service.start_transcribe_run(did)
        assert _wait_until(lambda: os.path.exists(marker), timeout=60)
        with open(marker) as f:
            info = json.load(f)
        assert info["temp"].startswith(transcribe_service.storage.temp_root())
        assert background_jobs.get_status(job_id)["status"] == "running"
        assert isolated_db.gpu_lock_holder_count() == 1

        started = time.monotonic()
        background_jobs.request_cancel(job_id)
        status = _wait_finished(job_id, timeout=10)

        assert status["status"] == "cancelled"
        assert time.monotonic() - started < 5
        assert status["message"] == background_jobs.CANCELLED_MESSAGE
        assert _wait_until(lambda: _process_gone(info["worker"]) and _process_gone(info["helper"]))
        assert _wait_until(lambda: not os.path.exists(info["temp"]))
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["旧的"]
        assert not os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        assert _wait_until(lambda: isolated_db.gpu_lock_holder_count() == 0)
        _clear(job_id)


def test_the_worker_reads_the_groq_key_from_its_environment(isolated_db, monkeypatch, tmp_path):
    """Run in this process: the key comes from settings_service.resolve_key
    inside the worker, as for a thread job, not from its arguments."""
    import queue
    seen = []
    monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                        lambda key, env_path=None: "gsk_env_key" if key == "groq" else None)
    monkeypatch.setattr(core_module, "transcribe_with_groq",
                        lambda path, lang, key, progress_cb=None: seen.append(key) or [])
    monkeypatch.setattr(background_jobs, "start_own_process_group", lambda: None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    result_queue = queue.Queue()
    transcribe_service._transcribe_worker(
        str(tmp_path / "audio.wav"), "whisper", None, "zh", "simplified", "medium", 5, 300, 0.5,
        False, "auto", False, False, True, "", False, "whisper", "whisper_diff", None, 1,
        False, False, 2.0, str(tmp_path / "scratch"), result_queue)
    items = []
    while not result_queue.empty():
        items.append(result_queue.get_nowait())
    assert seen == ["gsk_env_key"]
    assert items[-1] == ("ok", {"failed_reason": "empty"})


class TestMoveIntoPlace:
    def test_replaces_an_existing_file_with_os_replace(self, tmp_path, monkeypatch):
        src, dst = tmp_path / "scratch.wav", tmp_path / "vocals.wav"
        src.write_bytes(b"new")
        dst.write_bytes(b"old")
        monkeypatch.setattr(transcribe_service.shutil, "move",
                            lambda *a, **k: pytest.fail("shutil.move copies then deletes"))
        transcribe_service._move_into_place(str(src), str(dst))
        assert dst.read_bytes() == b"new" and not src.exists()

    def test_across_volumes_copies_beside_the_target_then_replaces(self, tmp_path, monkeypatch):
        import errno
        src_dir, dst_dir = tmp_path / "scratch", tmp_path / "drama"
        src_dir.mkdir()
        dst_dir.mkdir()
        src, dst = src_dir / "vocals.wav", dst_dir / "vocals.wav"
        src.write_bytes(b"new")
        dst.write_bytes(b"old")
        real_replace, calls = os.replace, []

        def replace(a, b):
            calls.append((os.path.dirname(a), b))
            if os.path.dirname(a) == str(src_dir):
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            real_replace(a, b)
        monkeypatch.setattr(transcribe_service.os, "replace", replace)
        transcribe_service._move_into_place(str(src), str(dst))
        assert dst.read_bytes() == b"new" and not src.exists()
        assert calls[-1] == (str(dst_dir), str(dst))
        assert sorted(p.name for p in dst_dir.iterdir()) == ["vocals.wav"]

    def test_another_os_error_is_raised(self, tmp_path, monkeypatch):
        import errno

        def replace(a, b):
            raise OSError(errno.EACCES, "Permission denied")
        monkeypatch.setattr(transcribe_service.os, "replace", replace)
        with pytest.raises(PermissionError):
            transcribe_service._move_into_place(str(tmp_path / "a"), str(tmp_path / "b"))


def test_the_workers_stage_checks_end_it_once_its_parent_is_gone(monkeypatch):
    exits = []
    monkeypatch.setattr(background_jobs, "exit_if_parent_gone", lambda: exits.append(True))
    rep = transcribe_service._ProcessReporter(None)
    assert rep.cancelled() is False
    rep.raise_if_cancelled()
    assert exits == [True, True]


def test_the_worker_pickles_and_runs_in_a_spawned_process(tmp_path):
    """Windows starts process jobs with spawn: the worker and its arguments
    must cross a fresh interpreter. An unknown separation backend fails
    before any model or network is touched."""
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(target=transcribe_service._transcribe_worker, daemon=True, args=(
        str(tmp_path / "audio.wav"), "whisper", None, "zh", "simplified", "medium", 5, 300, 0.5,
        True, "no_such_backend", False, False, False, "", False, "whisper", "whisper_diff", None,
        1, False, False, 2.0, str(tmp_path / "scratch"), result_queue))
    proc.start()
    items = [result_queue.get(timeout=60)]
    while items[-1][0] == "progress":
        items.append(result_queue.get(timeout=60))
    proc.join(timeout=10)
    assert items[0] == ("progress", 0.0, "Separating vocals from background music...")
    assert items[-1] == ("error", "KeyError", "'no_such_backend'")


class TestCoverageWarning:
    SEGS = [{"start": 0.0, "end": 6.0, "text": "a"}, {"start": 4.0, "end": 8.0, "text": "b"},
            {"start": 20.0, "end": 30.0, "text": "  "}]

    def test_fraction_counts_overlap_once_and_skips_blank_text(self):
        assert transcribe_service.audio_coverage_fraction(self.SEGS, 80.0) == pytest.approx(0.1)
        assert transcribe_service.audio_coverage_fraction(self.SEGS, None) is None

    def test_warns_when_little_audio_has_text(self):
        msg = transcribe_service.coverage_warning(self.SEGS, 80.0)
        assert msg.startswith("Only 10% of the audio has text")
        assert "vocal separation" in msg and "Qwen3" not in msg

    def test_no_warning_for_good_coverage_short_or_unknown_audio(self):
        assert transcribe_service.coverage_warning(self.SEGS, 40.0) is None
        assert transcribe_service.coverage_warning(self.SEGS, 20.0) is None
        assert transcribe_service.coverage_warning(self.SEGS, None) is None


class TestCancelReachesWhisper:
    """Owner report: Cancel did nothing on a running transcription. The
    Whisper path never checked the cancel flag: a cancel during the
    transcription was ignored and the drama's lines were replaced anyway."""

    def test_a_cancel_during_whisper_keeps_the_existing_lines(self, isolated_db, monkeypatch):
        from core import Line
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="旧的")])
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def whisper(*a, **k):
            background_jobs._jobs[job_id]["cancel_requested"] = True
            return [{"start": 0.0, "end": 1.0, "text": "新的"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", whisper)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["旧的"]
        _clear(job_id)

    def test_a_cancel_stops_whisper_at_its_next_segment(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        seen = []

        def whisper(*a, progress_cb=None, **k):
            for i in range(100):
                seen.append(i)
                if i == 3:
                    background_jobs._jobs[job_id]["cancel_requested"] = True
                progress_cb(i / 100)
            return [{"start": 0.0, "end": 1.0, "text": "新的"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", whisper)

        with pytest.raises(background_jobs.JobCancelled):
            transcribe_service._run_transcribe_and_apply_job(
                job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
                "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)
        assert seen == [0, 1, 2, 3]
        assert isolated_db.load_lines(did) == []
        _clear(job_id)


class TestSplitPiecesGetOwnSpeaker:
    def test_saved_turns_relabel_split_lines_and_reassign_keeps_manual(self, isolated_db):
        import diarize
        from services import diarization_service
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [
            transcribe_service.Line(idx=0, start=0.0, end=5.0, zh="a", speaker="X"),
            transcribe_service.Line(idx=1, start=5.0, end=10.0, zh="b", speaker="X"),
            transcribe_service.Line(idx=2, start=10.0, end=15.0, zh="c", speaker="X",
                                    speaker_manual=True)])
        with pytest.raises(Exception):
            diarization_service.reassign_speakers_from_saved_turns(did)
        diarize.save_turns(ddir, [{"start": 0.0, "end": 6.0, "speaker": "S1"},
                                  {"start": 6.0, "end": 15.0, "speaker": "S2"}])
        diarization_service.reassign_speakers_from_saved_turns(did)
        rows = isolated_db.load_lines(did)
        assert [r["speaker"] for r in rows] == ["S1", "S2", "X"]

    def test_reassign_refuses_while_a_job_runs(self, isolated_db, monkeypatch):
        import diarize
        from services import diarization_service
        from services.service_errors import ConflictError
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [
            transcribe_service.Line(idx=0, start=0.0, end=5.0, zh="a", speaker="X")])
        diarize.save_turns(ddir, [{"start": 0.0, "end": 6.0, "speaker": "S1"}])
        monkeypatch.setattr(transcribe_service.background_jobs, "any_job_running_for_drama",
                            lambda drama_id: True)
        with pytest.raises(ConflictError):
            diarization_service.reassign_speakers_from_saved_turns(did)
        assert isolated_db.load_lines(did)[0]["speaker"] == "X"


class TestTranscribeSpeedCalibration:
    def test_record_then_read_per_model_and_device(self, isolated_db):
        assert transcribe_service.measured_transcribe_speed("large-v3", False) is None
        transcribe_service.record_transcribe_speed("large-v3", False, 600, 300)
        assert transcribe_service.measured_transcribe_speed("large-v3", False) == 2.0
        assert transcribe_service.measured_transcribe_speed("large-v3", True) is None
        assert transcribe_service.measured_transcribe_speed("small", False) is None

    def test_two_readings_give_their_midpoint(self, isolated_db):
        transcribe_service.record_transcribe_speed("small", True, 600, 300)   # 2.0
        transcribe_service.record_transcribe_speed("small", True, 600, 100)   # 6.0
        assert transcribe_service.measured_transcribe_speed("small", True) == 4.0

    def test_the_median_of_the_last_five_runs_is_used(self, isolated_db):
        # One slow outlier (a busy PC) doesn't move the estimate; the oldest ages out.
        for work in (600, 300, 300, 3000, 300):   # 1, 2, 2, 0.2, 2 audio s per s
            transcribe_service.record_transcribe_speed("small", False, 600, work)
        assert transcribe_service.measured_transcribe_speed("small", False) == 2.0
        assert transcribe_service.measured_transcribe_runs("small", False) == 5
        transcribe_service.record_transcribe_speed("small", False, 600, 100)   # 6.0 pushes out the 1.0
        assert transcribe_service.measured_transcribe_runs("small", False) == 5
        assert transcribe_service.measured_transcribe_speed("small", False) == 2.0
        assert [r["speed"] for r in transcribe_service._recorded_runs("small", False)] == [
            2.0, 2.0, 0.2, 2.0, 6.0]

    def test_a_single_value_from_an_older_version_still_counts(self, isolated_db):
        isolated_db.set_app_setting("transcribe_speed", {"base|cpu": 3.0, "tiny|cpu": "bad"})
        assert transcribe_service.measured_transcribe_speed("base", False) == 3.0
        assert transcribe_service.measured_transcribe_runs("base", False) == 1
        assert transcribe_service.measured_transcribe_speed("tiny", False) is None
        transcribe_service.record_transcribe_speed("base", False, 600, 100)   # 6.0
        assert transcribe_service.measured_transcribe_speed("base", False) == 4.5

    def test_stage_seconds_are_kept_per_run_and_summarised_by_median(self, isolated_db):
        for decode, work in ((10, 300), (30, 300), (20, 300)):
            transcribe_service.record_transcribe_speed(
                "small", False, 600, work,
                stage_seconds={"decode_vad": decode, "transcribe": work, "bogus": 5, "align": -1})
        assert transcribe_service.measured_stage_seconds("small", False) == {
            "decode_vad": 20.0, "transcribe": 300.0}
        assert transcribe_service.measured_stage_seconds("small", True) == {}

    def test_diarize_speed_needs_three_runs_and_is_kept_per_device(self, isolated_db):
        for work in (300, 100):                                   # 2.0 and 6.0 audio s per s
            transcribe_service.record_diarize_speed(False, 600, work)
        assert transcribe_service.measured_diarize_speed(False) is None
        assert transcribe_service.measured_diarize_runs(False) == 2
        transcribe_service.record_diarize_speed(False, 600, 200)  # 3.0
        assert transcribe_service.measured_diarize_speed(False) == 3.0
        assert transcribe_service.measured_diarize_speed(True) is None
        # It does not mix with a Whisper model's own history.
        assert transcribe_service.measured_transcribe_speed("small", False) is None

    def test_old_records_without_diarize_or_stages_still_read(self, isolated_db):
        isolated_db.set_app_setting("transcribe_speed", {
            "base|cpu": 3.0, "small|cpu": {"runs": [{"speed": 2.0}]}})
        assert transcribe_service.measured_transcribe_speed("base", False) == 3.0
        assert transcribe_service.measured_stage_seconds("small", False) == {}
        assert transcribe_service.measured_diarize_speed(False) is None
        did = isolated_db.create_drama(title_en="D")
        cfg = transcribe_service.get_transcribe_config(did)
        assert cfg["measured_stage_seconds"] == {} and cfg["measured_diarize_speed"] is None
        assert cfg["measured_diarize_runs"] == 0

    def test_config_reports_stage_medians_and_diarize_speed(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        model = core_module.DEFAULT_WHISPER_SIZE
        transcribe_service.record_transcribe_speed(model, False, 600, 300, stage_seconds={"load": 12})
        for work in (300, 300, 300):
            transcribe_service.record_diarize_speed(False, 600, work)
        cfg = transcribe_service.get_transcribe_config(did)
        assert cfg["measured_stage_seconds"] == {"load": 12.0}
        assert cfg["measured_diarize_speed"] == 2.0 and cfg["measured_diarize_runs"] == 3

    @pytest.mark.parametrize("audio,work", [
        (None, 100), (100, None), ("100", 50), (100, "50"), (True, 50), (100, True),
        (0, 100), (-5, 100), (100, 0), (100, 1.0),          # too little work to trust
        (float("nan"), 100), (100, float("inf")),
        (1.0, 1000.0), (1e9, 10.0),                         # outside the speed bounds
    ])
    def test_bad_readings_are_ignored(self, isolated_db, audio, work):
        transcribe_service.record_transcribe_speed("base", False, audio, work)
        assert transcribe_service.measured_transcribe_speed("base", False) is None

    def test_bad_stored_values_read_as_none(self, isolated_db):
        for stored in ("fast", {"base|cpu": "x"}, {"base|cpu": -3}, {"base|cpu": True}, [1]):
            isolated_db.set_app_setting("transcribe_speed", stored)
            assert transcribe_service.measured_transcribe_speed("base", False) is None
        # A bad stored blob does not stop the next good reading from being kept.
        transcribe_service.record_transcribe_speed("base", False, 600, 300)
        assert transcribe_service.measured_transcribe_speed("base", False) == 2.0

    def test_a_settings_failure_never_raises(self, isolated_db, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("db locked")
        monkeypatch.setattr(isolated_db, "get_app_setting", boom)
        assert transcribe_service.measured_transcribe_speed("base", False) is None
        transcribe_service.record_transcribe_speed("base", False, 600, 300)

    def test_config_reports_the_speed_for_the_stored_model_and_device(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        # An unsaved drama's model follows the GPU setting, so each device has its own default.
        transcribe_service.record_transcribe_speed(core_module.DEFAULT_WHISPER_SIZE, False, 600, 300)
        assert transcribe_service.get_transcribe_config(did)["measured_speed"] == 2.0
        assert transcribe_service.get_transcribe_config(did)["measured_speed_runs"] == 1
        transcribe_service.record_transcribe_speed(core_module.DEFAULT_WHISPER_SIZE, True, 3000, 100)
        isolated_db.set_app_setting("use_gpu", True)
        assert transcribe_service.get_transcribe_config(did)["measured_speed"] == 30.0

    def test_a_finished_whisper_job_records_its_speed(self, isolated_db, monkeypatch):
        # Only this module's clock is faked: first percent at t=100, done at t=220.
        now = {"t": 100.0}
        monkeypatch.setattr(transcribe_service, "time", type("T", (), {"monotonic": staticmethod(lambda: now["t"])}))
        monkeypatch.setattr(transcribe_service, "_audio_duration_seconds", lambda p: 600.0)
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")

        def fake_whisper(*a, progress_cb=None, **k):
            progress_cb(0.0)
            progress_cb(0.1)   # the clock starts here, at 10%
            now["t"] = 220.0
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_service, "transcribe_for_timing", fake_whisper)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh",
            *TestQwen3Backends._AUDIO_ARGS)
        # 90% of 600 s of audio in 120 s of work.
        assert transcribe_service.measured_transcribe_speed("medium", False) == pytest.approx(4.5)
        # The wait for the first percent is decode and VAD; the rest is the Whisper pass.
        assert transcribe_service.measured_stage_seconds("medium", False)["decode_vad"] == 0.0
        assert transcribe_service.measured_stage_seconds("medium", False)["transcribe"] == 120.0
        _clear(job_id)
