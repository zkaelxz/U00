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
            "has_video_source": False,
            "hardsub_ocr_backend": "paddle",
            "hardsub_interval_sec": 1.0,
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

    def test_language_and_script_default_to_the_dramas_own(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper",
                                      source_language="ja", chinese_script="traditional")
        calls = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **k: calls.append(a) or True)
        transcribe_service.start_transcribe_run(did)
        args = calls[0]
        # (job_id, drama_id, audio_path, mode, text, language, script, ...)
        assert args[5] == "ja" and args[6] == "traditional"

    def test_explicit_language_overrides_the_dramas_own(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", source_language="ja")
        calls = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **k: calls.append(a) or True)
        transcribe_service.start_transcribe_run(did, source_language="ko")
        assert calls[0][5] == "ko"

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
        captured = {}

        def fake_start_job(job_id, target, *a, **k):
            import inspect
            names = list(inspect.signature(target).parameters)
            captured.update(dict(zip(names, a)))
            return True
        monkeypatch.setattr(background_jobs, "start_job", fake_start_job)

        transcribe_service.start_transcribe_run(did, initial_prompt="names")

        assert captured["whisper_size"] == "small"
        assert captured["beam_size"] == 7
        assert captured["min_silence_ms"] == 900
        assert captured["vad_threshold"] == 0.4
        assert captured["separate_vocals_first"] is True
        assert captured["separation_backend"] == "demucs"
        assert captured["realign_long_segments"] is True
        assert captured["whisper_fast_mode"] is True
        assert captured["use_groq"] is False
        assert captured["initial_prompt"] == "names"
        assert captured["use_gpu"] is False
        assert list(captured)[-1] == "use_gpu"


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
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", 3,
            diarize_audio_path=os.path.join(ddir, "audio.wav"))

        assert diarize_calls == [(f"diarize_{did}", (os.path.join(ddir, "audio.wav"), "hf-token", 3))]
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

        assert seen == [(audio_path, "hf-token", None)]
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
