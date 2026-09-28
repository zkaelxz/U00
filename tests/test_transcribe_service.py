"""
Tests for services/transcribe_service.py -- Migration Slice 20's
Transcript-stage config (read + a narrow, validated partial update) and
its "job does everything" start_transcribe_run action.

The job body (_run_transcribe_and_apply_job) is called directly, the same
way tests/test_workspace_tab.py exercises run_transcribe_job -- transcribe_
for_timing is mocked throughout, so no real model/GPU/audio is involved.
"""
import os

import pytest

import background_jobs
import core as core_module
from services import transcribe_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError


def _drama_with_audio(isolated_db, **fields):
    fields.setdefault("title_en", "D")
    fields.setdefault("audio_filename", "audio.wav")
    did = isolated_db.create_drama(**fields)
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, fields["audio_filename"]), "wb").close()
    return did, ddir


def _clear(job_id):
    background_jobs.clear_job(job_id)


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
            "beam_size": 5,
            "min_silence_ms": 300,
            "vad_threshold": 0.5,
            "separate_vocals_first": False,
            "separation_backend": "auto",
            "realign_long_segments": False,
            "whisper_fast_mode": False,
            "use_groq": False,
        }

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

    def test_min_silence_ms_out_of_range_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, min_silence_ms=100)

    def test_vad_threshold_out_of_range_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, vad_threshold=1.0)

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

    def test_hardsub_ocr_mode_raises_unsupported_operation(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="hardsub_ocr")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_have_transcript_mode_without_text_raises(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did)

    def test_starts_the_background_job(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        calls = []
        monkeypatch.setattr(
            background_jobs, "start_job",
            lambda job_id, target, *a, **k: calls.append((job_id, target, a, k)) or True)

        result = transcribe_service.start_transcribe_run(did)

        assert result == {"job_id": f"transcribe_{did}"}
        assert len(calls) == 1
        assert calls[0][0] == f"transcribe_{did}"
        assert calls[0][1] is transcribe_service._run_transcribe_and_apply_job

    def test_already_running_raises_conflict(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            transcribe_service.start_transcribe_run(did)


class TestRunTranscribeAndApplyJob:
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
        diarize_calls = []
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **k: diarize_calls.append(
                                (job_id, args)) or True)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", 3)

        assert diarize_calls == [(f"diarize_{did}", (os.path.join(ddir, "audio.wav"), "hf-token", 3))]
        assert background_jobs.get_status(job_id)["result"]["diarize_started"] is True
        _clear(job_id)
