"""
Tests for services/transcribe_service.py -- Migration Slice 20's
Transcript-stage config (read + a narrow, validated partial update) and
its "job does everything" start_transcribe_run action.

The job body (_run_transcribe_and_apply_job) is called directly, and
transcribe_for_timing is mocked throughout, so no real model/GPU/audio is involved.
"""
import functools
import multiprocessing
import json
import os
import subprocess
import sys
import types
import tempfile
import time

import pytest

import background_jobs
from jobs import job_launch, job_runner, job_settings, job_table, process_worker
import core as core_module
import whisper_transcribe
import qwen3_native
import whisper_models
import segment_splitting
from jobs import gpu_slots
from services import transcribe_options_service, transcribe_pipeline, transcribe_service
from services import settings_service
from lib.errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                     NotFoundError, UnsupportedOperationError)
from tests.saved_settings import patch_setting


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
    monkeypatch.setattr(job_launch, "start_process_job", fake_start_process_job)
    return captured


def _seed_running_job(job_id):
    _clear(job_id)
    job_table._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
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
            "asr_backend_notice": None,
            "transcript_mode": "have_transcript",
            "has_audio_pipeline": True,
            "audio_available": False,
            "alignment_method": "whisper_diff",
            "asr_backend_choice": "whisper",
            "whisper_size": whisper_models.DEFAULT_WHISPER_SIZE,
            "whisper_size_notice": None,
            "whisper_exclusions": whisper_models.whisper_language_exclusions(),
            "selectable_whisper_sizes": ["large-v3", "large-v3-turbo", "ja-1.5b", "kotoba-v2", "breeze-asr-25",
                                         "thonburian-large-v3"],
            "whisper_model_cached": whisper_models.is_whisper_model_cached(whisper_models.DEFAULT_WHISPER_SIZE),
            "measured_speed": None,
            "measured_speed_runs": 0,
            "measured_stage_seconds": {}, "measured_diarize_speed": None, "measured_diarize_runs": 0,
            "whisper_installed": result["whisper_installed"],
            "gpu_runtime": result["gpu_runtime"],
            "use_gpu": result["use_gpu"],
            "beam_size": 5,
            "min_silence_ms": 1000,
            "vad_threshold": 0.5,
            "sensitivity_preset": "normal",
            "effective_vad_threshold": 0.5,
            "hallucination_silence_sec": 0.0,
            "min_pause_sec": 0.35,
            "separate_vocals_first": False,
            "separation_backend": "auto",
            "realign_long_segments": False,
            "whisper_fast_mode": False,
            "whisper_repeat_guard": False,
            "split_by_sentences": False, "difficult_audio": False,
            "vocabulary_hint": False,
            "use_groq": False,
            "has_video_source": False,
            "hardsub_ocr_backend": "auto",
            "hardsub_interval_sec": 1.0,
            "auto_initial_prompt": "",
            "source_language": "zh",
            "chinese_script": "simplified",
            "run_start": {"run_diarize": False, "expected_speakers": None, "min_speakers": None,
                          "max_speakers": None, "extra_names": "", "initial_prompt": ""},
        }

    @pytest.mark.parametrize("language", ["zh", "ja", "ko"])
    def test_a_title_with_no_saved_backend_stays_on_whisper(self, isolated_db, monkeypatch, language):
        # transformers 5.15+, torch and faster-whisper are all present, which
        # a plain Diagnostics install provides; that must not pick Qwen3-ASR.
        monkeypatch.setattr(transcribe_options_service.importlib.util, "find_spec",
                            lambda name, *a: object())
        monkeypatch.setattr(qwen3_native, "installed_transformers_version", lambda: "5.19.0")
        did = isolated_db.create_drama(title_en="D", source_language=language)
        assert transcribe_service.get_transcribe_config(did)["asr_backend_choice"] == "whisper"

    def test_an_explicit_long_backend_still_wins_over_groq(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", source_language="zh", use_groq=1,
                                       asr_backend_choice="qwen3_asr_long")
        assert transcribe_service.get_transcribe_config(did)["asr_backend_choice"] == "qwen3_asr_long"

    def test_a_saved_backend_is_never_replaced_by_the_default(self, isolated_db, monkeypatch):
        monkeypatch.setattr(transcribe_options_service.importlib.util, "find_spec",
                            lambda name, *a: object())
        did = isolated_db.create_drama(title_en="D", source_language="zh",
                                       asr_backend_choice="whisper")
        assert transcribe_service.get_transcribe_config(did)["asr_backend_choice"] == "whisper"

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

    def test_saves_the_repeat_guard_sentence_split_and_long_window_backend(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(
            did, whisper_repeat_guard=True, split_by_sentences=True,
            asr_backend_choice="qwen3_asr_long")
        assert result["whisper_repeat_guard"] is True and result["split_by_sentences"] is True
        assert result["asr_backend_choice"] == "qwen3_asr_long"

    def test_unknown_asr_backend_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, asr_backend_choice="nonsense")

    @pytest.mark.parametrize("choice", ["tesseract", "paddle", "rapidocr", "auto"])
    def test_hardsub_ocr_backend_choices_are_saved(self, isolated_db, choice):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, hardsub_ocr_backend=choice)
        assert result["hardsub_ocr_backend"] == choice

    @pytest.mark.parametrize("choice", ["manga_ocr", "paddle_vl_manga", "nonsense"])
    def test_unknown_hardsub_ocr_backend_raises(self, isolated_db, choice):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, hardsub_ocr_backend=choice)

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

    def test_min_pause_sec_defaults_to_035_and_a_null_column_uses_it(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert transcribe_service.get_transcribe_config(did)["min_pause_sec"] == 0.35
        isolated_db.update_drama(did, min_pause_sec=None)
        assert transcribe_service.get_transcribe_config(did)["min_pause_sec"] == 0.35

    def test_min_pause_sec_range_is_enforced(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        for ok in (0.1, 0.35, 2.0, 1):
            result = transcribe_service.update_transcribe_config(did, min_pause_sec=ok)
            assert result["min_pause_sec"] == ok
        for bad in (0.09, 2.01, 0, -1, float("nan"), float("inf"), "0.5", True, False, [0.5]):
            with pytest.raises(InvalidInputError):
                transcribe_service.update_transcribe_config(did, min_pause_sec=bad)
        assert transcribe_service.get_transcribe_config(did)["min_pause_sec"] == 1

    def test_hallucination_silence_sec_zero_turns_it_off_and_is_kept(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, hallucination_silence_sec=0)
        assert result["hallucination_silence_sec"] == 0

    def test_hallucination_silence_sec_out_of_range_raises(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        for bad in (0.2, 11):
            with pytest.raises(InvalidInputError):
                transcribe_service.update_transcribe_config(did, hallucination_silence_sec=bad)

    @pytest.mark.parametrize("value", ["nonsense", "demucs"])
    def test_unknown_separation_backend_raises(self, isolated_db, value):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, separation_backend=value)

    def test_bool_fields_persist_as_true_and_false(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(did, realign_long_segments=True)
        assert result["realign_long_segments"] is True
        result = transcribe_service.update_transcribe_config(did, realign_long_segments=False)
        assert result["realign_long_segments"] is False


class TestTranscribeRunChoicesStayOnTheTitle:
    """The form's language, script and run choices are saved with the title, so another
    browser, the Queue and the next visit all see what was set."""

    def test_a_fresh_title_reports_the_form_defaults(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        config = transcribe_service.get_transcribe_config(did)
        assert config["run_start"] == {"run_diarize": False, "expected_speakers": None,
                                       "min_speakers": None, "max_speakers": None,
                                       "extra_names": "", "initial_prompt": ""}
        assert (config["source_language"], config["chinese_script"]) == ("zh", "simplified")

    def test_language_script_and_run_choices_are_saved_and_read_back(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        result = transcribe_service.update_transcribe_config(
            did, source_language="ja", chinese_script="traditional",
            run_start={"run_diarize": True, "min_speakers": 2, "max_speakers": 4,
                       "extra_names": "白鹤", "initial_prompt": "名前"})
        assert result["source_language"] == "ja" and result["chinese_script"] == "traditional"
        assert result["run_start"] == {"run_diarize": True, "expected_speakers": None,
                                       "min_speakers": 2, "max_speakers": 4,
                                       "extra_names": "白鹤", "initial_prompt": "名前"}
        assert transcribe_service.get_transcribe_config(did)["run_start"] == result["run_start"]
        assert isolated_db.get_drama(did)["source_language"] == "ja"

    def test_run_choices_are_replaced_as_a_whole(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        transcribe_service.update_transcribe_config(
            did, run_start={"run_diarize": True, "expected_speakers": 3})
        result = transcribe_service.update_transcribe_config(did, run_start={"run_diarize": True})
        assert result["run_start"]["expected_speakers"] is None

    def test_a_save_without_run_choices_leaves_them_alone(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        transcribe_service.update_transcribe_config(did, run_start={"run_diarize": True})
        result = transcribe_service.update_transcribe_config(did, beam_size=7)
        assert result["run_start"]["run_diarize"] is True

    @pytest.mark.parametrize("bad", [
        {"run_diarize": "yes"}, {"expected_speakers": 21}, {"expected_speakers": True},
        {"expected_speakers": 2, "min_speakers": 1}, {"min_speakers": 4, "max_speakers": 2},
        {"extra_names": "x" * 1001}, {"initial_prompt": 5}])
    def test_a_bad_run_choice_is_refused_and_nothing_is_saved(self, isolated_db, bad):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, beam_size=8, run_start=bad)
        assert transcribe_service.get_transcribe_config(did)["beam_size"] == 5

    def test_a_hand_edited_or_imported_column_is_bounded_when_read(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.update_drama(did, transcribe_start_json=json.dumps({
            "run_diarize": "yes", "expected_speakers": 99, "min_speakers": 2, "max_speakers": 3,
            "extra_names": "x" * 5000, "initial_prompt": "p" * 9000}))
        run = transcribe_service.get_transcribe_config(did)["run_start"]
        assert run["run_diarize"] is False and run["expected_speakers"] is None
        assert (run["min_speakers"], run["max_speakers"]) == (2, 3)
        assert len(run["extra_names"]) == 1000 and len(run["initial_prompt"]) == 4000
        isolated_db.update_drama(did, transcribe_start_json="not json")
        assert transcribe_service.get_transcribe_config(did)["run_start"]["extra_names"] == ""

    @pytest.mark.parametrize("counts", [{"min_speakers": 5, "max_speakers": 2},
                                        {"expected_speakers": 3, "min_speakers": 2}])
    def test_counts_the_run_would_refuse_read_as_no_counts(self, isolated_db, counts):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.update_drama(did, transcribe_start_json=json.dumps({"run_diarize": True, **counts}))
        run = transcribe_options_service.run_start(isolated_db.get_drama(did))
        assert run["run_diarize"] is True
        assert (run["expected_speakers"], run["min_speakers"], run["max_speakers"]) == (None, None, None)

    def test_a_title_with_nothing_saved_runs_as_before(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        assert transcribe_options_service.run_start(isolated_db.get_drama(did)) == {
            "run_diarize": False, "expected_speakers": None, "min_speakers": None,
            "max_speakers": None, "extra_names": "", "initial_prompt": ""}

    def test_a_model_is_checked_against_the_language_saved_with_it(self, isolated_db):
        did = isolated_db.create_drama(title_en="D", source_language="zh")
        size = whisper_models.DEFAULT_WHISPER_SIZE
        result = transcribe_service.update_transcribe_config(
            did, source_language="ko", whisper_size=size)
        assert result["source_language"] == "ko" and result["whisper_size"] == size

    def test_an_unknown_language_is_refused_before_anything_is_written(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, source_language="xx", beam_size=8)
        config = transcribe_service.get_transcribe_config(did)
        assert config["beam_size"] == 5 and config["source_language"] == "zh"


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
        monkeypatch.setattr(job_launch, "start_job", fake_start_job)

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
        monkeypatch.setattr(job_launch, "start_job", fake_start_job)

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
        monkeypatch.setattr(job_launch, "start_job", fake_start_job)

        transcribe_service.start_transcribe_run(did, run_diarize=True)

        assert captured["diarize_audio_path"] is None

    def test_starts_the_background_job(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)

        result = transcribe_service.start_transcribe_run(did)

        assert result == {"job_id": f"transcribe_{did}"}
        assert captured["job_id"] == f"transcribe_{did}"
        assert captured["target"] is transcribe_pipeline._transcribe_worker
        k = captured["start_kwargs"]
        assert k["gpu_touching"] is True and k["kill_whole_tree"] is True
        assert k["start_method"] == "spawn"
        assert callable(k["on_done"]) and callable(k["on_finish"])
        assert captured["drama_id"] == did

    def test_auto_language_runs_the_detected_language_on_large_v3_and_saves_it(self, isolated_db, monkeypatch):
        import inspect
        from services import language_detect_service
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.update_drama(did, source_language="zh", whisper_size="turbo")
        captured = _capture_worker_start(monkeypatch)
        monkeypatch.setattr(language_detect_service, "detect", lambda *a: "ko")
        transcribe_service.start_transcribe_run(did, detect_language=True)
        assert captured["source_language"] == "ko"
        assert captured["whisper_size"] == "large-v3" and captured["asr_backend_choice"] == "whisper"
        assert isolated_db.get_drama(did)["source_language"] == "ko"
        assert isolated_db.get_drama(did)["whisper_size"] == "turbo"   # the title's own model is untouched
        assert "beam_size" in inspect.signature(transcribe_pipeline._transcribe_worker).parameters
        assert captured["beam_size"] == 5

    def test_auto_language_refuses_a_title_that_does_not_transcribe_with_whisper(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        with pytest.raises(UnsupportedOperationError):
            transcribe_service.start_transcribe_run(did, detect_language=True)

    def test_a_gpu_run_carries_the_same_args_on_the_cpu_for_a_crash_retry(self, isolated_db,
                                                                            monkeypatch):
        import inspect
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        patch_setting(monkeypatch, "use_gpu", True)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
        retry = dict(zip(names, captured["start_kwargs"]["cpu_retry_args"]()))
        assert captured["use_gpu"] is True and retry.pop("use_gpu") is False
        assert retry.pop("checkpoint") == {**captured["checkpoint"], "record": False}
        assert retry == {n: captured[n] for n in retry}

    def test_the_crash_retry_continues_after_the_units_the_gpu_run_stored(self, isolated_db,
                                                                          monkeypatch):
        import inspect
        from services import job_checkpoint_service
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        patch_setting(monkeypatch, "use_gpu", True)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        scope = captured["checkpoint"]["scope"]
        assert captured["checkpoint"]["resumed_units"] == 0
        # The GPU run stores two units, then its worker dies.
        unit0 = [{"start": 0.0, "end": 121.0, "text": "a"}]
        unit1 = [{"start": 122.0, "end": 245.0, "text": "b"}]
        job_checkpoint_service.record_unit(scope, 0, unit0)
        job_checkpoint_service.record_unit(scope, 1, unit1)
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
        retry = dict(zip(names, captured["start_kwargs"]["cpu_retry_args"]()))
        assert retry["use_gpu"] is False
        assert retry["checkpoint"] == {"scope": scope, "segments": unit0 + unit1,
                                       "resumed_units": 2, "record": False}

    def test_the_crash_retry_keeps_the_settings_the_gpu_run_started_with(self, isolated_db,
                                                                         monkeypatch):
        import inspect
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        patch_setting(monkeypatch, "use_gpu", True)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        # Changed while the GPU run was going: a stitched result must not mix two models.
        patch_setting(monkeypatch, "whisper_model_path", "/models/other")
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
        retry = dict(zip(names, captured["start_kwargs"]["cpu_retry_args"]()))
        assert retry["local_model_path"] == captured["local_model_path"] != "/models/other"

    def test_a_crash_retry_that_cannot_read_the_units_continues_from_where_the_run_started(
            self, isolated_db, monkeypatch):
        import inspect
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        patch_setting(monkeypatch, "use_gpu", True)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)

        def locked(scope):
            raise OSError("database is locked")
        monkeypatch.setattr(transcribe_service.transcribe_checkpoint_service, "resume_from", locked)
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
        retry = dict(zip(names, captured["start_kwargs"]["cpu_retry_args"]()))
        assert retry["checkpoint"] == {**captured["checkpoint"], "record": False}

    def test_a_groq_run_has_no_crash_retry_so_its_paid_calls_are_not_repeated(
            self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", use_groq=1)
        patch_setting(monkeypatch, "use_gpu", True)
        monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                            lambda key, env_path=None: "gsk_test_groq_key_value")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["use_groq"] is True and captured["use_gpu"] is True
        assert captured["start_kwargs"]["cpu_retry_args"] is None

    def test_a_cpu_run_has_no_crash_retry(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        patch_setting(monkeypatch, "use_gpu", False)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["start_kwargs"]["cpu_retry_args"] is None

    def test_overrides_are_this_runs_settings_and_never_saved_to_the_title(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", beam_size=5,
                                   whisper_size="large-v3", separate_vocals_first=0)
        captured = _capture_worker_start(monkeypatch)

        transcribe_service.start_transcribe_run(
            did, overrides={"beam_size": 9, "whisper_size": "large-v3-turbo", "separate_vocals_first": True})

        assert (captured["beam_size"], captured["whisper_size"]) == (9, "large-v3-turbo")
        assert captured["separate_vocals_first"] is True
        saved = isolated_db.get_drama(did)
        assert (saved["beam_size"], saved["whisper_size"], saved["separate_vocals_first"]) == (5, "large-v3", 0)

    def test_overrides_are_checked_like_the_title_options(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)
        with pytest.raises(InvalidInputError):
            transcribe_service.start_transcribe_run(did, overrides={"beam_size": 99})
        assert "job_id" not in captured

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
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
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
        settings_service.set_settings({"use_gpu": False})   # these assert the CPU path
        did, ddir = _drama_with_audio(
            isolated_db, transcript_mode="whisper", whisper_size="small", beam_size=7,
            min_silence_ms=900, vad_threshold=0.4, separate_vocals_first=1,
            separation_backend="audio_separator", realign_long_segments=1, whisper_fast_mode=1)
        captured = _capture_worker_start(monkeypatch)

        transcribe_service.start_transcribe_run(did, initial_prompt="names")

        assert captured["whisper_size"] == "small"
        assert captured["beam_size"] == 7
        assert captured["min_silence_ms"] == 900
        assert captured["vad_threshold"] == 0.4
        assert captured["hallucination_silence_sec"] == 0.0
        assert captured["min_pause_sec"] == 0.35
        assert captured["separate_vocals_first"] is True
        assert captured["separation_backend"] == "audio_separator"
        assert captured["realign_long_segments"] is True
        assert captured["whisper_fast_mode"] is True
        assert captured["use_groq"] is False
        assert captured["initial_prompt"] == "names"
        assert captured["use_gpu"] is False
        assert captured["asr_backend_choice"] == "whisper"
        assert captured["alignment_method"] == "whisper_diff"
        # Every worker parameter but the queue is passed.
        import inspect
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
        assert names[-1] == "result_queue" and all(n in captured for n in names[:-1])


@pytest.fixture(autouse=True)
def _no_real_whisper_load(monkeypatch):
    """The job body pre-loads the Whisper model to report stage/device;
    tests never load a real model."""
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: object())


class TestRunTranscribeAndApplyJob:
    def test_model_loading_message_and_gpu_device_reported(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        messages = []
        monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: False)
        monkeypatch.setattr(whisper_models, "get_whisper_device_info", lambda *a, **k: {
            "device": "cpu", "compute_type": "int8", "gpu_error": "RuntimeError: no cublas64_12.dll"})
        real_update = background_jobs.update_progress
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda j, f, m="": (messages.append(m), real_update(j, f, m)))
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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

    def test_the_titles_chinese_script_reaches_whisper(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        seen = {}
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: (
            seen.update(k) or [{"start": 0.0, "end": 1.0, "text": "hi"}]))

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "traditional",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert seen["chinese_script"] == "traditional"
        _clear(job_id)

    def test_runtime_gpu_fallback_reason_lands_in_result(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def fake_transcribe(*a, on_gpu_fallback=None, **k):
            on_gpu_fallback(RuntimeError("cuDNN failed"))
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_transcribe)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            use_gpu=True)

        result = background_jobs.get_status(job_id)["result"]
        assert result["gpu_fallback"] == "RuntimeError: cuDNN failed"
        assert result["device"] == "GPU unavailable (RuntimeError: cuDNN failed); using CPU"
        assert "ran on the CPU" in result["device_notice"] and "slower" in result["device_notice"]
        _clear(job_id)

    def test_a_cpu_run_after_a_gpu_worker_crash_says_it_ran_on_the_cpu(self, isolated_db,
                                                                         monkeypatch):
        from jobs import crash_retry
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        real_pipeline = transcribe_pipeline._transcribe_pipeline
        reason = crash_retry.crash_reason(3221225477)
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", lambda *a, **k: {
            **real_pipeline(*a, **k), crash_retry.REASON_KEY: reason})
        speeds = []
        monkeypatch.setattr(transcribe_service, "_record_speed",
                            lambda outcome, size, use_gpu: speeds.append(use_gpu))

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None,
            use_gpu=True)

        result = background_jobs.get_status(job_id)["result"]
        assert result["gpu_fallback"] == reason
        assert result["device_notice"] == whisper_models.gpu_fallback_notice("Transcription", reason)
        assert crash_retry.REASON_KEY not in result
        assert speeds == [False]
        _clear(job_id)

    def test_a_failed_cpu_run_after_a_crash_still_names_the_crash(self, isolated_db):
        from jobs import crash_retry
        reason = crash_retry.crash_reason(3221225477)
        outcome = {**transcribe_pipeline.missing_package_outcome(), crash_retry.REASON_KEY: reason}
        result = transcribe_service._apply_on_done(
            "transcribe_x", dict(outcome), drama_id=7, source_language="zh", whisper_size="small",
            use_gpu=True, transcript_mode="whisper", alignment_method="whisper_diff",
            hf_token=None, diarize_audio_path=None, expected_speakers=None)
        assert result["failed_reason"] == "dependency_missing"
        assert result["gpu_fallback"] == reason and crash_retry.REASON_KEY not in result

    def test_whisper_mode_success_saves_lines(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        fake_segments = [{"start": 0.0, "end": 1.0, "text": "hi"},
                         {"start": 1.0, "end": 2.0, "text": "there"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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

    @pytest.mark.parametrize("split_by_sentences", [False, True])
    def test_sentence_split_hears_long_chunks_and_cuts_lines_at_sentences(
            self, tmp_path, monkeypatch, split_by_sentences):
        seen = {}

        def fake(*a, min_silence_duration_ms=None, repeat_guard=None, **k):
            seen.update(min_silence=min_silence_duration_ms, repeat_guard=repeat_guard)
            return [{"start": 0.0, "end": 6.0, "text": "今天天气很好。我们出去走走吧。"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake)
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: None)
        monkeypatch.setattr(whisper_models, "get_whisper_device_info", lambda *a, **k: {})
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: None)
        out = transcribe_pipeline._transcribe_pipeline(
            transcribe_pipeline._ThreadReporter(None), str(tmp_path / "a.wav"), "whisper", None,
            "zh", "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False, None,
            "", False, "whisper", "whisper_diff", repeat_guard=True,
            split_by_sentences=split_by_sentences)
        assert seen == {"min_silence": 2000 if split_by_sentences else 300, "repeat_guard": True}
        assert [ln.zh for ln in out["lines"]] == (
            ["今天天气很好。", "我们出去走走吧。"] if split_by_sentences
            else ["今天天气很好。我们出去走走吧。"])
        assert out["run_config"]["split_by_sentences"] is split_by_sentences

    def test_have_transcript_mode_aligns_supplied_text(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="have_transcript")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        fake_segments = [{"start": 0.0, "end": 2.0, "text": "hi there"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: [])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "no_lines"
        _clear(job_id)

    def test_empty_result_keeps_existing_lines(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        from core import Line
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="existing")])
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        # An empty transcript (segments with only whitespace text) with
        # existing lines already saved must not wipe them out.
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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
        job_table._jobs[job_id]["cancel_requested"] = True

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
            raise whisper_models.ModelDownloadError("no network")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", raise_download_error)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        result = background_jobs.get_status(job_id)["result"]
        assert result["failed_reason"] == "model_download"
        _clear(job_id)

    def test_an_unexpected_whisper_error_is_raised_not_recorded(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def boom(*a, **k):
            raise RuntimeError("something genuinely broke")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", boom)

        with pytest.raises(RuntimeError, match="genuinely broke"):
            transcribe_service._run_transcribe_and_apply_job(
                job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
                "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)
        _clear(job_id)

    def test_chains_diarization_when_hf_token_given(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        patch_setting(monkeypatch, "use_gpu", True)
        diarize_calls = []
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **k: diarize_calls.append(
                                (job_id, args)) or True)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", 3,
            diarize_audio_path=os.path.join(ddir, "audio.wav"))

        # Step 101: the chained run carries the persisted use_gpu setting.
        assert diarize_calls == [(f"diarize_{did}", (
            os.path.join(ddir, "audio.wav"), "hf-token", 3,
            {"use_gpu": True, "backend": "nemotron", "min_speakers": None, "max_speakers": None}))]
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
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "new"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        assert cancelled == [did]
        assert os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["new"]
        assert len(isolated_db.list_line_history(did)) == 1
        _clear(job_id)

    def test_raw_transcript_keeps_what_the_hallucination_filters_dropped(self, isolated_db, monkeypatch):
        import raw_transcript
        from whisper_hallucination import filter_hallucinated_segments
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        loop = [{"start": 10.0 + i, "end": 10.8 + i, "text": "谢谢"} for i in range(5)]
        # The real decode filters its own output, inside the run.
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: filter_hallucinated_segments(
            [{"start": 0.0, "end": 1.0, "text": "new"}, *loop]))

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, None)

        raw = raw_transcript.load_latest(ddir)
        assert [s["text"] for s in raw["segments"]] == ["new", "谢谢"]
        assert [(d["start"], d["text"], d["reason"]) for d in raw["dropped"]] == [
            (11.0, "谢谢", "repeat"), (12.0, "谢谢", "repeat"), (13.0, "谢谢", "repeat"), (14.0, "谢谢", "repeat")]
        _clear(job_id)

    def test_raw_transcript_saves_the_settings_the_run_started_with(self, isolated_db, monkeypatch):
        import raw_transcript
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def edit_everything_mid_run(*a, **k):
            isolated_db.update_drama(did, beam_size=99, min_silence_ms=9999)
            job_settings.set_gpu_max_parallel(4)
            return [{"start": 0.0, "end": 1.0, "text": "new"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", edit_everything_mid_run)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, None, 2,
            initial_prompt="names", gpu_app_settings={"gpu_max_parallel": 1, "gpu_limit_enabled": True})

        s = raw_transcript.load_latest(ddir)["settings"]
        assert set(s) == set(raw_transcript.SETTINGS_KEYS)
        assert (s["whisper_size"], s["beam_size"], s["min_silence_ms"]) == ("medium", 5, 300)
        assert s["gpu_max_parallel"] == 1 and s["gpu_limit_enabled"] is True
        assert s["expected_speakers"] == 2 and s["language"] == "zh"
        assert s["initial_prompt_chars"] == 5 and "names" not in json.dumps(s)
        _clear(job_id)

    def test_start_freezes_the_gpu_app_settings_for_the_apply_step(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_settings.set_gpu_max_parallel(3)
        captured = _capture_worker_start(monkeypatch)

        transcribe_service.start_transcribe_run(did)

        assert captured["gpu_app_settings"] == {"gpu_max_parallel": 3, "gpu_limit_enabled": True}

    def test_groq_path_uses_groq_and_reports_failure(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def boom(*a, **k):
            raise whisper_transcribe.GroqTranscriptionError("bad key")
        monkeypatch.setattr(whisper_transcribe, "transcribe_with_groq", boom)

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
            raise audio_preprocess.VocalSeparationError("no audio-separator")
        monkeypatch.setattr(audio_preprocess, "separate_vocals", boom)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"]["failed_reason"] == "vocal_separation"
        _clear(job_id)

    def _preload_run(self, isolated_db, monkeypatch, fits, separation_fails=False, gpu_error=None,
                     separation_raises=None, worker_process=True, raise_once=False, events=None):
        """Runs a separated Whisper job with the model on disk; returns the order of events.
        worker_process: run it as the GPU worker process does (the job here runs in a thread)."""
        import threading
        import audio_preprocess
        import memory_headroom
        from services import transcribe_reporters
        if worker_process:
            monkeypatch.setattr(transcribe_pipeline, "_ProcessReporter", transcribe_reporters._ThreadReporter)
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        events, loaded = ([] if events is None else events), threading.Event()
        monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda size: True)
        monkeypatch.setattr(memory_headroom, "fits_beside_separation", lambda *a: fits)
        monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
        monkeypatch.setattr(whisper_models, "get_whisper_device_info",
                            lambda *a, **k: {"device": "cuda", "gpu_error": gpu_error})
        monkeypatch.setattr(whisper_models, "forget_whisper_model", lambda *a, **k: events.append("forget"))
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: events.append("release"))

        def load(*a, **k):
            events.append(("load", threading.current_thread().name))
            loaded.set()
        monkeypatch.setattr(whisper_models, "load_whisper_model", load)

        def separate(audio_path, out_path, **k):
            if fits and worker_process:
                assert loaded.wait(5), "Whisper did not start loading during the separation"
            events.append("separated")
            events.append(("separate", threading.current_thread().name))
            if separation_raises is not None and not (raise_once and events.count("separated") > 1):
                raise separation_raises
            if separation_fails:
                raise audio_preprocess.VocalSeparationError("no audio-separator")
            with open(out_path, "wb") as f:
                f.write(b"RIFF")
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", separate)
        monkeypatch.setattr(transcribe_pipeline, "_to_speech_format", lambda path: path)
        def transcribe(*a, **k):
            events.append(("transcribe", threading.current_thread().name))
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", transcribe)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "large-v3", 5, 300, 0.5, True, "auto", False, False, False, None, None, None, use_gpu=True)
        result = background_jobs.get_status(job_id)["result"]
        _clear(job_id)
        return events, result

    def test_whisper_loads_while_the_vocals_are_separated_on_the_thread_that_transcribes(
            self, isolated_db, monkeypatch):
        events, result = self._preload_run(isolated_db, monkeypatch, fits=True)
        threads = dict(e for e in events if isinstance(e, tuple))
        assert events[0][0] == "load" and events.index("separated") > 0 and "forget" not in events
        # A CTranslate2 model used from another thread than the one that built it, once that
        # thread is gone, crashes the process; the separation is the one moved aside.
        assert threads["load"] == threads["transcribe"] != threads["separate"] == "vocal-separation"
        assert result.get("failed_reason") is None

    def test_whisper_waits_for_the_separation_when_memory_would_not_hold_both(self, isolated_db, monkeypatch):
        events, _ = self._preload_run(isolated_db, monkeypatch, fits=False)
        assert events[0] == "separated"
        assert ("separate", "vocal-separation") not in events

    def test_a_preload_that_fell_back_to_the_cpu_is_dropped(self, isolated_db, monkeypatch):
        events, _ = self._preload_run(isolated_db, monkeypatch, fits=True, gpu_error="out of memory")
        assert "forget" in events

    def test_a_failed_separation_frees_the_early_model_and_tries_once_more_alone(self, isolated_db, monkeypatch):
        events, result = self._preload_run(isolated_db, monkeypatch, fits=True, separation_fails=True)
        assert result["failed_reason"] == "vocal_separation"
        assert events.count("separated") == 2
        assert events.index("release") < events.index("separated", events.index("separated") + 1)

    def test_an_unexpected_separation_error_still_frees_the_early_model(self, isolated_db, monkeypatch):
        events = []
        real = transcribe_pipeline._PipelineRun._separate_beside_whisper_load

        def watched(run_self):
            try:
                return real(run_self)
            except RuntimeError:
                events.append("raised")
                raise
        monkeypatch.setattr(transcribe_pipeline._PipelineRun, "_separate_beside_whisper_load", watched)
        with pytest.raises(RuntimeError, match="driver crashed"):
            self._preload_run(isolated_db, monkeypatch, fits=True, events=events,
                              separation_raises=RuntimeError("driver crashed"))
        assert events.index("release") < events.index("raised")

    def test_a_memory_refusal_beside_the_early_model_frees_it_and_separates_alone(self, isolated_db, monkeypatch):
        import memory_headroom
        events, result = self._preload_run(isolated_db, monkeypatch, fits=True, raise_once=True,
                                           separation_raises=memory_headroom.HeadroomError("low memory"))
        assert result.get("failed_reason") is None
        assert events.count("separated") == 2
        assert events.index("release") < events.index("separated", events.index("separated") + 1)

    def test_a_thread_job_separates_before_loading_as_before(self, isolated_db, monkeypatch):
        events, _ = self._preload_run(isolated_db, monkeypatch, fits=True, worker_process=False)
        assert events[0] == "separated"
        assert ("separate", "vocal-separation") not in events

    def test_reused_vocals_load_whisper_after_as_before(self, isolated_db, monkeypatch):
        monkeypatch.setattr(transcribe_pipeline, "_reusable_vocals", lambda *a: True)
        events, _ = self._preload_run(isolated_db, monkeypatch, fits=True)
        assert "separated" not in events
        assert ("separate", "vocal-separation") not in events

    def test_vocal_separation_cancel_never_starts_transcription(self, isolated_db, monkeypatch):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def stopped(*a, **k):
            raise audio_preprocess.VocalSeparationCancelled("stopped")
        monkeypatch.setattr(audio_preprocess, "separate_vocals", stopped)

        def never(*a, **k):
            raise AssertionError("transcription started after a cancel")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", never)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        _clear(job_id)

    def test_a_cancel_as_vocal_separation_finishes_stops_before_transcription(
            self, isolated_db, monkeypatch):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def separate_then_cancel(audio_path, out_path, **k):
            # separate_vocals only checks for a cancel between its chunks, so
            # one arriving at its tail must be caught before Whisper starts.
            open(out_path, "wb").close()
            job_table._jobs[job_id]["cancel_requested"] = True
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", separate_then_cancel)

        def never(*a, **k):
            raise AssertionError("transcription started after a cancel")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", never)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        _clear(job_id)

    def test_vocal_separation_status_names_stage_device_and_percent(self, isolated_db, monkeypatch):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        seen = []

        def fake_separate(in_path, out_path, backend="auto", progress_cb=None,
                          cancel_check_cb=None, use_gpu=None, event_cb=None):
            event_cb("loading", "audio_separator")
            seen.append(background_jobs.get_status(job_id)["message"])
            event_cb("device", "cpu")
            seen.append(background_jobs.get_status(job_id)["message"])
            progress_cb(0.4)
            seen.append(background_jobs.get_status(job_id)["message"])
            seen.append(use_gpu)
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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

    @pytest.mark.parametrize("vocals_seconds,vocals_age,separates", [
        (600.0, 10, False),     # a finished separation of this audio: reused
        (600.4, 10, False),     # within the length slack
        (312.0, 10, True),      # cut short (killed while writing): separated again
        (600.0, -10, True),     # older than the audio (media since replaced)
    ])
    def test_a_rerun_reuses_the_vocals_an_earlier_run_separated(
            self, isolated_db, monkeypatch, vocals_seconds, vocals_age, separates):
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        audio = os.path.join(ddir, "audio.wav")
        vocals = os.path.join(ddir, "vocals.wav")
        with open(vocals, "wb") as fh:
            fh.write(b"earlier vocals")
        later = max(os.stat(audio).st_mtime, os.stat(audio).st_ctime) + vocals_age
        os.utime(vocals, (later, later))
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds",
                            lambda path: vocals_seconds if path == vocals else 600.0)
        separated = []

        def fake_separate(in_path, out_path, **k):
            separated.append(in_path)
            with open(out_path, "wb") as fh:
                fh.write(b"new vocals")
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)
        heard = []
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda path, *a, **k: heard.append(path) or [
                                {"start": 0.0, "end": 1.0, "text": "hi"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, audio, "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        assert separated == ([audio] if separates else [])
        assert heard == [vocals]
        with open(vocals, "rb") as fh:
            assert fh.read() == (b"new vocals" if separates else b"earlier vocals")
        _clear(job_id)

    def test_separated_vocals_are_kept_as_16k_mono_speech_audio(self, isolated_db, monkeypatch):
        sf = pytest.importorskip("soundfile")
        import shutil
        if not shutil.which("ffmpeg"):
            pytest.skip("needs ffmpeg")
        import numpy as np
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def fake_separate(in_path, out_path, **k):
            sf.write(out_path, np.zeros((44100 * 2, 2), dtype="float32"), 44100)
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)
        heard = []
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda path, *a, **k: heard.append(sf.info(path)) or [
                                {"start": 0.0, "end": 1.0, "text": "hi"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        (info,) = heard
        assert (info.samplerate, info.channels) == (16000, 1)
        assert info.duration == pytest.approx(2.0, abs=0.05)
        assert sorted(os.listdir(ddir)) == sorted(set(os.listdir(ddir)) - {".speech-vocals.wav"})
        assert os.path.exists(os.path.join(ddir, "vocals.wav"))
        _clear(job_id)

    def test_a_failed_speech_conversion_keeps_the_separated_vocals(self, isolated_db, monkeypatch):
        import audio_extract
        import audio_preprocess
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)

        def fake_separate(in_path, out_path, **k):
            with open(out_path, "wb") as fh:
                fh.write(b"separated")
            return out_path
        monkeypatch.setattr(audio_preprocess, "separate_vocals", fake_separate)

        def broken(src, dst):
            open(dst, "wb").close()
            raise audio_extract.FfmpegError(1, ["ffmpeg"])
        monkeypatch.setattr(audio_extract, "extract_audio_from_video", broken)
        heard = []
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda path, *a, **k: heard.append(path) or [
                                {"start": 0.0, "end": 1.0, "text": "hi"}])

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, None, None)

        vocals = os.path.join(ddir, "vocals.wav")
        assert heard == [vocals]
        with open(vocals, "rb") as fh:
            assert fh.read() == b"separated"
        assert ".speech-vocals.wav" not in os.listdir(ddir)
        _clear(job_id)

    def test_model_load_stage_says_no_progress_is_available(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        seen = []
        monkeypatch.setattr(whisper_models, "load_whisper_model",
                            lambda *a, **k: seen.append(background_jobs.get_status(job_id)["message"]))
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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
        assert transcribe_pipeline.REALIGN_START * transcribe_pipeline.RUNNING_MAX < progress < transcribe_pipeline.RUNNING_MAX
        assert background_jobs.get_status(job_id)["result"] == {"failed_reason": "cancelled"}
        _clear(job_id)

    @pytest.mark.parametrize("gpu_available", [True, False])
    def test_realign_runs_on_the_device_the_gpu_choice_selects(
            self, isolated_db, monkeypatch, gpu_available):
        import word_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 20.0, "text": "hi"}])
        monkeypatch.setattr(transcribe_pipeline, "select_device",
                            lambda use_gpu: "cuda" if use_gpu and gpu_available else "cpu")
        devices = []

        def fake_realign(segments, *a, device=None, **k):
            devices.append(device)
            return segments
        monkeypatch.setattr(word_align, "realign_oversized_segments", fake_realign)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", True, False, False, None, None, None,
            use_gpu=True)

        assert devices == ["cuda" if gpu_available else "cpu"]
        _clear(job_id)

    def test_realign_error_is_reported_but_lines_still_saved(self, isolated_db, monkeypatch):
        import word_align
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
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
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake)

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
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        seen = []
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **k: seen.append(args) or True)

        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, original, "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, True, "auto", False, False, False, None, "hf-token", None,
            diarize_audio_path=original)

        assert seen[0][0] == original
        _clear(job_id)


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
    whisper_models.load_whisper_model = lambda *a, **k: object()
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
        transcribe_pipeline.transcribe_for_timing = two_lines
    elif kind == "vocals":
        audio_preprocess.separate_vocals = fake_separate
        transcribe_pipeline.transcribe_for_timing = (
            lambda path, *a, **k: [{"start": 0.0, "end": 1.0, "text": path}])
    elif kind == "stuck":
        transcribe_pipeline.transcribe_for_timing = stuck_whisper
    transcribe_pipeline._transcribe_worker(*args)


@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="checks the worker's processes through /proc")
class TestTranscribeProcessJob:
    """An audio transcription runs in its own spawned process
    (start_process_job, start_method="spawn"), with a fake Whisper installed
    in the child (_spawned_worker); nothing loads a real model."""

    def _use_spawned_fakes(self, monkeypatch, isolated_db, kind, marker=None):
        monkeypatch.setattr(sys.modules[__name__], "_PARENT_ONLY_PROBE", True)
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_worker", functools.partial(
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
        for module in (background_jobs, job_runner):
            monkeypatch.setattr(module, "update_progress",
                                lambda j, f, m="": (messages.append((f, m)), real_update(j, f, m)))
        scratch = self._track_scratch(monkeypatch)

        transcribe_service.start_transcribe_run(did)
        status = _wait_finished(job_id, timeout=60)

        assert status["status"] == "done", status
        assert status["result"]["line_count"] == 2
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["hi", "there"]
        assert isolated_db.get_drama(did)["status"] == "aligned"
        assert os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        assert messages[0][1].startswith(f"Loading Whisper model {whisper_models.DEFAULT_WHISPER_SIZE}")
        assert background_jobs.stage_ticker.NOTE in messages[0][1]
        assert any(f == pytest.approx(0.5 * transcribe_pipeline.RUNNING_MAX)
                   and m.startswith("Transcribing... 50%") for f, m in messages)
        assert max(f for f, _ in messages) < 1.0
        assert _wait_until(lambda: not os.path.exists(scratch[0]))
        assert _wait_until(lambda: gpu_slots.holder_count() == 0)
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
        assert gpu_slots.holder_count() == 1

        started = time.monotonic()
        background_jobs.request_cancel(job_id)
        status = _wait_finished(job_id, timeout=10)

        assert status["status"] == "cancelled"
        assert time.monotonic() - started < 5
        assert status["message"] == job_runner.CANCELLED_MESSAGE
        assert _wait_until(lambda: _process_gone(info["worker"]) and _process_gone(info["helper"]))
        assert _wait_until(lambda: not os.path.exists(info["temp"]))
        assert [r["zh"] for r in isolated_db.load_lines(did)] == ["旧的"]
        assert not os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        assert _wait_until(lambda: gpu_slots.holder_count() == 0)
        _clear(job_id)


def test_the_worker_reads_the_groq_key_from_its_environment(isolated_db, monkeypatch, tmp_path):
    """Run in this process: the key comes from settings_service.resolve_key
    inside the worker, as for a thread job, not from its arguments."""
    import queue
    seen = []
    monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                        lambda key, env_path=None: "gsk_env_key" if key == "groq" else None)
    monkeypatch.setattr(whisper_transcribe, "transcribe_with_groq",
                        lambda path, lang, key, progress_cb=None: seen.append(key) or [])
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    result_queue = queue.Queue()
    transcribe_pipeline._transcribe_worker(
        str(tmp_path / "audio.wav"), "whisper", None, "zh", "simplified", "medium", 5, 300, 0.5,
        False, "auto", False, False, True, "", False, "whisper", "whisper_diff", None, 1,
        False, False, 2.0, 0.35, False, False, "normal", "standard", None, None, None, str(tmp_path / "scratch"), result_queue)
    items = []
    while not result_queue.empty():
        items.append(result_queue.get_nowait())
    assert seen == ["gsk_env_key"]
    assert items[-1][1]["failed_reason"] == "no_lines"


def test_the_run_uses_the_pause_saved_when_it_started(isolated_db, monkeypatch, tmp_path):
    """The pause reaches split_long_segments from the run's frozen copy, not from
    the title read again later."""
    import queue
    did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper", use_groq=1, min_pause_sec=0.8)
    monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                        lambda key, env_path=None: "gsk_env_key" if key == "groq" else None)
    captured = _capture_worker_start(monkeypatch)
    transcribe_service.start_transcribe_run(did)
    isolated_db.update_drama(did, min_pause_sec=0.2)
    assert captured["min_pause_sec"] == 0.8

    seen = []
    segments = [{"start": 0.0, "end": 1.0, "text": "你好"}]
    monkeypatch.setattr(whisper_transcribe, "transcribe_with_groq",
                        lambda path, lang, key, progress_cb=None: segments)
    monkeypatch.setattr(segment_splitting, "split_long_segments",
                        lambda segs, **kw: seen.append(kw) or segs)
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    result_queue = queue.Queue()
    import inspect
    names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)[:-1]
    worker_args = [captured[n] for n in names]
    worker_args[names.index("scratch_dir")] = str(tmp_path / "scratch")
    transcribe_pipeline._transcribe_worker(*worker_args, result_queue)
    assert seen and seen[0]["min_pause"] == 0.8


class TestMoveIntoPlace:
    def test_replaces_an_existing_file_with_os_replace(self, tmp_path, monkeypatch):
        src, dst = tmp_path / "scratch.wav", tmp_path / "vocals.wav"
        src.write_bytes(b"new")
        dst.write_bytes(b"old")
        monkeypatch.setattr(transcribe_pipeline.shutil, "move",
                            lambda *a, **k: pytest.fail("shutil.move copies then deletes"))
        transcribe_pipeline._move_into_place(str(src), str(dst))
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
        monkeypatch.setattr(transcribe_pipeline.os, "replace", replace)
        transcribe_pipeline._move_into_place(str(src), str(dst))
        assert dst.read_bytes() == b"new" and not src.exists()
        assert calls[-1] == (str(dst_dir), str(dst))
        assert sorted(p.name for p in dst_dir.iterdir()) == ["vocals.wav"]

    def test_another_os_error_is_raised(self, tmp_path, monkeypatch):
        import errno

        def replace(a, b):
            raise OSError(errno.EACCES, "Permission denied")
        monkeypatch.setattr(transcribe_service.os, "replace", replace)
        with pytest.raises(PermissionError):
            transcribe_pipeline._move_into_place(str(tmp_path / "a"), str(tmp_path / "b"))


def test_the_workers_stage_checks_end_it_once_its_parent_is_gone(monkeypatch):
    exits = []
    monkeypatch.setattr(process_worker, "exit_if_parent_gone", lambda: exits.append(True))
    rep = transcribe_pipeline._ProcessReporter(None)
    assert rep.cancelled() is False
    rep.raise_if_cancelled()
    assert exits == [True, True]


def test_the_worker_is_importable_by_its_qualified_name_in_a_fresh_interpreter():
    """A spawned child unpickles the worker by module and name in a fresh
    interpreter, so the module must import on its own and expose it at top level."""
    code = ("import pickle, services.transcribe_pipeline as m; "
            "assert pickle.loads(pickle.dumps(m._transcribe_worker)) is m._transcribe_worker")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert proc.returncode == 0, proc.stderr


def test_the_worker_pickles_and_runs_in_a_spawned_process(tmp_path):
    """Windows starts process jobs with spawn: the worker and its arguments
    must cross a fresh interpreter. An unknown separation backend fails
    before any model or network is touched."""
    ctx = multiprocessing.get_context("spawn")
    result_queue = ctx.Queue()
    proc = ctx.Process(target=transcribe_pipeline._transcribe_worker, daemon=True, args=(
        str(tmp_path / "audio.wav"), "whisper", None, "zh", "simplified", "medium", 5, 300, 0.5,
        True, "no_such_backend", False, False, False, "", False, "whisper", "whisper_diff", None,
        1, False, False, 2.0, 0.35, False, False, "normal", "standard", None, None, None, str(tmp_path / "scratch"), result_queue))
    proc.start()
    items = [result_queue.get(timeout=60)]
    while items[-1][0] == "progress":
        items.append(result_queue.get(timeout=60))
    proc.join(timeout=10)
    assert items[0] == ("progress", 0.0, "Separating vocals from background music...")
    assert items[-1][:3] == ("error", "KeyError", "'no_such_backend'")
    # The worker's traceback travels with it for the server log.
    assert "Traceback" in items[-1][3] and "KeyError" in items[-1][3]


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
            job_table._jobs[job_id]["cancel_requested"] = True
            return [{"start": 0.0, "end": 1.0, "text": "新的"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", whisper)

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
                    job_table._jobs[job_id]["cancel_requested"] = True
                progress_cb(i / 100)
            return [{"start": 0.0, "end": 1.0, "text": "新的"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", whisper)

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
            core_module.Line(idx=0, start=0.0, end=5.0, zh="a", speaker="X"),
            core_module.Line(idx=1, start=5.0, end=10.0, zh="b", speaker="X"),
            core_module.Line(idx=2, start=10.0, end=15.0, zh="c", speaker="X",
                                    speaker_manual=True)])
        with pytest.raises(ConflictError):
            diarization_service.reassign_speakers_from_saved_turns(did)
        diarize.save_turns(ddir, [{"start": 0.0, "end": 6.0, "speaker": "S1"},
                                  {"start": 6.0, "end": 15.0, "speaker": "S2"}])
        diarization_service.reassign_speakers_from_saved_turns(did)
        rows = isolated_db.load_lines(did)
        assert [r["speaker"] for r in rows] == ["S1", "S2", "X"]

    def test_reassign_refuses_while_a_job_runs(self, isolated_db, monkeypatch):
        import diarize
        from services import diarization_service
        from lib.errors import ConflictError
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        isolated_db.save_lines(did, [
            core_module.Line(idx=0, start=0.0, end=5.0, zh="a", speaker="X")])
        diarize.save_turns(ddir, [{"start": 0.0, "end": 6.0, "speaker": "S1"}])
        monkeypatch.setattr(transcribe_service.background_jobs, "any_job_running_for_drama",
                            lambda drama_id: True)
        with pytest.raises(ConflictError):
            diarization_service.reassign_speakers_from_saved_turns(did)
        assert isolated_db.load_lines(did)[0]["speaker"] == "X"


class TestRunSettings:
    def test_resolved_values_and_boolean_toggles(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(
            isolated_db, transcript_mode="whisper", source_language="ja", whisper_fast_mode=1,
            separate_vocals_first=1, realign_long_segments=0, use_groq=0, whisper_repeat_guard=1,
            split_by_sentences=1, sensitivity_preset="sensitive")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did, source_language="ko")
        settings = captured["start_kwargs"]["run_settings"]
        assert settings["source_language"] == "ko"
        assert settings["chinese_script"] == "simplified"
        assert settings["sensitivity_preset"] == "sensitive"
        assert isinstance(settings["vad_threshold"], float)
        assert {k: settings[k] for k in (
            "whisper_fast_mode", "separate_vocals_first", "realign_long_segments", "use_groq",
            "whisper_repeat_guard", "split_by_sentences")} == {
            "whisper_fast_mode": True, "separate_vocals_first": True,
            "realign_long_segments": False, "use_groq": False,
            "whisper_repeat_guard": True, "split_by_sentences": True}

    def test_a_refused_start_leaves_no_settings_to_borrow(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        _capture_worker_start(monkeypatch, started=False)
        with pytest.raises(ConflictError):
            transcribe_service.start_transcribe_run(did)
        assert background_jobs.get_status(f"transcribe_{did}") is None

    def test_the_auto_diarize_after_transcription_carries_its_own_settings(self, isolated_db,
                                                                         monkeypatch):
        hardsub_ocr = pytest.importorskip("hardsub_ocr")
        did, ddir = _drama_with_video(isolated_db, with_audio=True)
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        monkeypatch.setattr(hardsub_ocr, "extract_hardsub_subtitles",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        seen = []
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **k: seen.append(k) or True)
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, None, "hardsub_ocr", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "hf-token", 2,
            video_path=os.path.join(ddir, "source.mp4"), hardsub_ocr_backend="tesseract",
            hardsub_interval=1.0, diarize_audio_path=os.path.join(ddir, "audio.wav"))
        assert seen[0]["run_settings"]["expected_speakers"] == 2
        _clear(job_id)


def _bound_worker_args(args):
    import inspect
    return inspect.signature(transcribe_pipeline._transcribe_worker).bind(*args, None).arguments


def test_worker_args_for_settings_use_the_settings_without_reading_the_library(isolated_db):
    # isolated_db has no titles at all, so a lookup of the title would raise NotFoundError.
    settings = {"source_language": "zh", "transcript_mode": "whisper", "min_silence_ms": 300, "beam_size": 7,
                "sensitivity_preset": "sensitive", "separate_vocals_first": 1, "split_by_sentences": 1}
    args = transcribe_service.worker_args_for_settings(settings, "a.wav", "scratch", False, "名字。")
    bound = _bound_worker_args(args)
    assert (bound["audio_path"], bound["transcript_mode"], bound["scratch_dir"]) == ("a.wav", "whisper", "scratch")
    assert (bound["min_silence_ms"], bound["beam_size"], bound["sensitivity_preset"]) == (300, 7, "sensitive")
    assert bound["separate_vocals_first"] is True and bound["split_by_sentences"] is True
    assert bound["initial_prompt"] == "名字。" and bound["use_gpu"] is False and bound["checkpoint"] is None


def test_worker_args_for_settings_prompt_falls_back_like_a_run(isolated_db):
    plain = {"source_language": "zh", "transcript_mode": "whisper"}
    assert _bound_worker_args(transcribe_service.worker_args_for_settings(
        plain, "a.wav", "s", False))["initial_prompt"] == ""
    assert _bound_worker_args(transcribe_service.worker_args_for_settings(
        {**plain, "difficult_audio": 1}, "a.wav", "s", False))["initial_prompt"] == "以下是普通話的句子。"


def test_worker_args_for_settings_refuses_a_supplied_transcript_or_groq(isolated_db):
    with pytest.raises(InvalidInputError):
        transcribe_service.worker_args_for_settings(
            {"source_language": "zh", "transcript_mode": "hardsub_ocr"}, "a.wav", "s", False)
    with pytest.raises((InvalidInputError, DependencyUnavailableError)):
        transcribe_service.worker_args_for_settings(
            {"source_language": "zh", "transcript_mode": "whisper", "use_groq": 1}, "a.wav", "s", False)
