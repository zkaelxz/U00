"""The per-title "Difficult audio" option: levelled temporary copy, the Sensitive
preset, and a relaxed Whisper skip rule. A title without it must run exactly as before."""
import inspect
import os
import queue
import sys
import tempfile

import pytest

import audio_preprocess
import background_jobs
import sensitivity_preset
from services import (difficult_audio_service, run_settings_service, series_defaults_service,
                      timing_settings_service, transcribe_pipeline, transcribe_service)
from services.service_errors import InvalidInputError


def _title(isolated_db, **fields):
    fields.setdefault("title_en", "D")
    fields.setdefault("audio_filename", "audio.wav")
    fields.setdefault("transcript_mode", "whisper")
    did = isolated_db.create_drama(**fields)
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, fields["audio_filename"]), "wb").close()
    return did


def _capture_worker_start(monkeypatch):
    captured = {}

    def fake_start_process_job(job_id, target, args=(), **k):
        captured.update(dict(zip(inspect.signature(target).parameters, args)))
        captured.update(k["on_done"].keywords)
        captured["run_settings"] = k["run_settings"]
        return True
    monkeypatch.setattr(background_jobs, "start_process_job", fake_start_process_job)
    return captured


# --- the stored option --------------------------------------------------------

class TestStoredOption:
    def test_off_by_default_and_saved_per_title(self, isolated_db):
        did = _title(isolated_db)
        other = _title(isolated_db)
        assert transcribe_service.get_transcribe_config(did)["difficult_audio"] is False
        out = transcribe_service.update_transcribe_config(did, difficult_audio=True)
        assert out["difficult_audio"] is True
        assert transcribe_service.get_transcribe_config(other)["difficult_audio"] is False
        assert isolated_db.get_drama(did)["difficult_audio"] == 1

    def test_validation_stores_a_flag_and_ignores_none(self):
        assert transcribe_service.check_transcribe_fields({"difficult_audio": True}, "zh") == {
            "difficult_audio": 1}
        assert transcribe_service.check_transcribe_fields({"difficult_audio": False}, "zh") == {
            "difficult_audio": 0}
        assert transcribe_service.check_transcribe_fields({"difficult_audio": None}, "zh") == {}

    def test_the_effective_threshold_shown_follows_the_option(self, isolated_db):
        did = _title(isolated_db)
        assert transcribe_service.get_transcribe_config(did)["effective_vad_threshold"] == 0.5
        transcribe_service.update_transcribe_config(did, difficult_audio=True)
        cfg = transcribe_service.get_transcribe_config(did)
        assert cfg["effective_vad_threshold"] == 0.35
        assert cfg["sensitivity_preset"] == "normal"      # the select still shows the stored choice

    def test_a_series_may_default_it(self):
        assert series_defaults_service.SETTINGS["difficult_audio"]["type"] == "bool"
        series_defaults_service._check_value("difficult_audio", True)
        with pytest.raises(InvalidInputError):
            series_defaults_service._check_value("difficult_audio", "yes")

    def test_the_whisper_numbers_are_developer_mode_rows(self, isolated_db):
        assert timing_settings_service.values()["difficult_no_speech_threshold"] == 0.8
        isolated_db.set_app_setting("pref.difficult_no_speech_threshold", 0.95)
        assert timing_settings_service.values()["difficult_no_speech_threshold"] == 0.8
        isolated_db.set_app_setting("assistant.developer_mode", True)
        assert timing_settings_service.values()["difficult_no_speech_threshold"] == 0.95


# --- starting a run -----------------------------------------------------------

class TestStartRun:
    def test_a_default_title_runs_exactly_as_before(self, isolated_db, monkeypatch):
        did = _title(isolated_db)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert difficult_audio_service.FLAG not in captured["timing"]
        assert captured["sensitivity_preset"] == "normal" and captured["vad_threshold"] == 0.5
        assert captured["run_settings"].get("difficult_audio") in (None, False)
        # The worker takes no argument of its own for the option.
        names = list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)
        assert names[-3:] == ["timing", "scratch_dir", "result_queue"]

    def test_the_option_uses_the_sensitive_preset_and_marks_the_run(self, isolated_db, monkeypatch):
        did = _title(isolated_db, difficult_audio=1)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["timing"][difficult_audio_service.FLAG] is True
        assert captured["sensitivity_preset"] == "sensitive" and captured["vad_threshold"] == 0.35
        assert captured["run_settings"]["difficult_audio"] is True

    def test_a_threshold_the_title_changed_still_wins(self, isolated_db, monkeypatch):
        did = _title(isolated_db, difficult_audio=1, vad_threshold=0.6)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["vad_threshold"] == 0.6 and captured["sensitivity_preset"] == "sensitive"

    def test_min_silence_is_left_alone(self, isolated_db, monkeypatch):
        did = _title(isolated_db, difficult_audio=1, min_silence_ms=700)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        assert captured["min_silence_ms"] == 700

    def test_run_settings_record_the_option(self):
        out = run_settings_service.for_transcribe(
            {"difficult_audio": 1}, "zh", None, "medium", 5, 300, 0.35, "sensitive", 0, 0.35,
            "auto", False, "whisper", "whisper_diff", "whisper", False, None, None)
        assert out["difficult_audio"] is True

    def test_the_scratch_folder_the_parent_removes_holds_the_copy(self, tmp_path):
        scratch = str(tmp_path / "scratch")
        os.makedirs(scratch)
        copy = os.path.join(scratch, difficult_audio_service.LEVELLED_NAME)
        open(copy, "wb").close()
        transcribe_pipeline._remove_scratch_dir(scratch)
        assert not os.path.exists(copy)


# --- the worker: the temporary copy ------------------------------------------

@pytest.fixture
def worker(monkeypatch, tmp_path):
    """Runs _transcribe_worker with a fake pipeline; records what it was given."""
    monkeypatch.setattr(background_jobs, "start_own_process_group", lambda: None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    state = {"levelled": [], "pipeline": [], "scratch": str(tmp_path / "scratch")}

    def fake_level(src, out, cancel=None, **k):
        state["levelled"].append((src, out, cancel, k.get("own_group")))
        with open(out, "wb") as f:
            f.write(b"x")
        return out
    monkeypatch.setattr(audio_preprocess, "level_audio", fake_level)

    def fake_pipeline(rep, audio_path, *a, **k):
        state["pipeline"].append((audio_path, k, os.path.exists(audio_path)))
        return {"failed_reason": "empty"} if state.get("fail") else {"lines": [], "coverage_warning": state.get("warning")}
    monkeypatch.setattr(transcribe_pipeline, "_transcribe_pipeline", fake_pipeline)

    def run(difficult, mode="whisper", backend="whisper", groq=False, mixed=False, timing=None):
        q = queue.Queue()
        timing = {**(timing or {}), **({difficult_audio_service.FLAG: True} if difficult else {})}
        transcribe_pipeline._transcribe_worker(
            str(tmp_path / "stored.wav"), mode, None, "zh", "simplified", "medium", 5, 300, 0.5,
            False, "auto", False, False, groq, "", False, backend, "whisper_diff", None, 1,
            False, mixed, 2.0, 0.35, False, False, "normal", "standard", None, timing,
            state["scratch"], q)
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    state["run"] = run
    return state


class TestWorker:
    def test_a_default_run_never_touches_ffmpeg_and_hands_the_pipeline_its_timing_unchanged(self, worker, tmp_path):
        timing = {"split_max_seconds": 8.0}
        worker["run"](False, timing=timing)
        assert worker["levelled"] == []
        audio_path, kwargs, _ = worker["pipeline"][0]
        assert audio_path == str(tmp_path / "stored.wav") and kwargs["timing"] == timing
        assert difficult_audio_service.whisper_kwargs(kwargs["timing"]) == {}

    def test_the_pipeline_gets_the_levelled_copy_not_the_stored_file(self, worker, tmp_path):
        worker["run"](True, timing={"difficult_no_speech_threshold": 0.9})
        (src, out, cancel, _group), = worker["levelled"]
        assert src == str(tmp_path / "stored.wav")
        assert os.path.dirname(out) == worker["scratch"] and out != src and callable(cancel)
        audio_path, kwargs, existed = worker["pipeline"][0]
        assert audio_path == out and existed
        assert difficult_audio_service.whisper_kwargs(kwargs["timing"]) == {
            "difficult_audio": True, "no_speech_threshold": 0.9,
            "log_prob_threshold": sensitivity_preset.DIFFICULT_LOG_PROB_THRESHOLD}

    def test_without_ffmpeg_the_original_is_used_and_the_result_says_so(self, worker, monkeypatch, tmp_path):
        def missing(src, out, cancel=None, **k):
            raise audio_preprocess.AudioLevelingUnavailable("ffmpeg was not found, so the audio was not levelled.")
        monkeypatch.setattr(audio_preprocess, "level_audio", missing)
        worker["warning"] = "Speech coverage looks low."
        items = worker["run"](True)
        assert worker["pipeline"][0][0] == str(tmp_path / "stored.wav")
        # The other two parts still apply.
        assert difficult_audio_service.FLAG in worker["pipeline"][0][1]["timing"]
        assert items[-1][1]["coverage_warning"] == (
            "ffmpeg was not found, so the audio was not levelled. Speech coverage looks low.")

    @pytest.mark.parametrize("backend", ["qwen3_asr_vad", "qwen3_asr_long", "anime_whisper"])
    def test_detector_backends_get_levelling_only(self, worker, backend):
        items = worker["run"](True, backend=backend)
        assert len(worker["levelled"]) == 1
        assert difficult_audio_service.whisper_kwargs(worker["pipeline"][0][1]["timing"]) == {}
        assert "levelled the sound only" in items[-1][1]["coverage_warning"]

    def test_qwen3_asr_on_whisper_segments_gets_all_three(self, worker):
        worker["run"](True, backend="qwen3_asr")
        assert len(worker["levelled"]) == 1
        assert difficult_audio_service.whisper_kwargs(worker["pipeline"][0][1]["timing"])

    def test_mixed_language_keeps_whispers_own_skip_rule(self, worker):
        items = worker["run"](True, mixed=True)
        assert len(worker["levelled"]) == 1
        assert difficult_audio_service.whisper_kwargs(worker["pipeline"][0][1]["timing"]) == {}
        assert "skip rule is unchanged" in items[-1][1]["coverage_warning"]

    @pytest.mark.parametrize("kwargs", [{"groq": True}, {"mode": "have_transcript"}])
    def test_groq_and_a_supplied_transcript_ignore_it_with_a_note(self, worker, kwargs):
        items = worker["run"](True, **kwargs)
        assert worker["levelled"] == []
        assert difficult_audio_service.whisper_kwargs(worker["pipeline"][0][1]["timing"]) == {}
        assert "it was ignored" in items[-1][1]["coverage_warning"]

    def test_a_failed_run_carries_no_note(self, worker):
        worker["fail"] = True
        items = worker["run"](True, groq=True)
        assert items[-1] == ("ok", {"failed_reason": "empty"})


class TestWhisperKwargs:
    def test_none_without_the_flag(self):
        assert difficult_audio_service.whisper_kwargs(None) == {}
        assert difficult_audio_service.whisper_kwargs({"difficult_no_speech_threshold": 0.9}) == {}

    def test_the_developer_numbers_are_used(self):
        assert difficult_audio_service.whisper_kwargs({
            difficult_audio_service.FLAG: True, "difficult_no_speech_threshold": 0.9,
            "difficult_log_prob_threshold": -2.0}) == {
            "difficult_audio": True, "no_speech_threshold": 0.9, "log_prob_threshold": -2.0}

    def test_mark_only_touches_a_title_with_the_option_on(self):
        timing = {"a": 1}
        assert difficult_audio_service.mark(timing, {"difficult_audio": 0}) is timing
        assert difficult_audio_service.mark(timing, {"difficult_audio": 1}) == {
            "a": 1, difficult_audio_service.FLAG: True, difficult_audio_service.NO_LEVELLING: False}
        assert timing == {"a": 1}


# --- the pipeline: what reaches faster-whisper --------------------------------

class TestPipeline:
    def _run(self, tmp_path, monkeypatch, **extra):
        from services import transcribe_pipeline as tp
        import whisper_models
        seen = {}
        monkeypatch.setattr(tp, "transcribe_for_timing",
                            lambda *a, **k: seen.update(k) or [{"start": 0.0, "end": 1.0, "text": "啊"}])
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: None)
        monkeypatch.setattr(whisper_models, "get_whisper_device_info", lambda *a, **k: {})
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: None)
        tp._transcribe_pipeline(
            tp._ThreadReporter(None), str(tmp_path / "a.wav"), "whisper", None, "zh",
            "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False, None, "",
            False, "whisper", "whisper_diff", **extra)
        return seen

    def test_a_default_run_passes_no_difficult_keywords(self, tmp_path, monkeypatch):
        seen = self._run(tmp_path, monkeypatch)
        assert not {"difficult_audio", "no_speech_threshold", "log_prob_threshold"} & set(seen)
        seen = self._run(tmp_path, monkeypatch, timing=timing_settings_service.defaults())
        assert not {"difficult_audio", "no_speech_threshold", "log_prob_threshold"} & set(seen)

    def test_difficult_numbers_reach_transcribe_for_timing(self, tmp_path, monkeypatch):
        timing = {**timing_settings_service.defaults(), difficult_audio_service.FLAG: True,
                  "difficult_no_speech_threshold": 0.9, "difficult_log_prob_threshold": -2.0}
        seen = self._run(tmp_path, monkeypatch, timing=timing)
        assert seen["difficult_audio"] is True
        assert (seen["no_speech_threshold"], seen["log_prob_threshold"]) == (0.9, -2.0)


# --- the other parts of a run that is not Whisper-on-the-audio -----------------

class TestOtherRuns:
    @pytest.mark.parametrize("fields", [{"transcript_mode": "have_transcript"}, {"use_groq": 1}])
    def test_a_run_that_ignores_the_option_keeps_the_normal_detector(self, isolated_db, monkeypatch, fields):
        monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                            lambda key, env_path=None: "gsk_x" if key == "groq" else None)
        did = _title(isolated_db, difficult_audio=1, **fields)
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did, transcript_text="hello")
        assert captured["sensitivity_preset"] == "normal" and captured["vad_threshold"] == 0.5
        # The worker still says it was ignored.
        assert captured["timing"][difficult_audio_service.FLAG] is True

    def test_separate_vocals_first_keeps_the_separation_input_original(self, worker, isolated_db):
        timing = difficult_audio_service.mark({}, {"difficult_audio": 1, "separate_vocals_first": 1})
        items = worker["run"](True, timing=timing)
        assert worker["levelled"] == []
        audio_path, kwargs, _ = worker["pipeline"][0]
        assert difficult_audio_service.whisper_kwargs(kwargs["timing"])      # the other parts still apply
        assert "Separate vocals first is on" in items[-1][1]["coverage_warning"]

    def test_levelling_runs_in_the_workers_process_group(self, worker):
        worker["run"](True)
        assert worker["levelled"][0][3] is False

    def test_a_levelling_failure_never_fails_the_job(self, worker, monkeypatch):
        def broken(src, out, cancel=None, **k):
            raise audio_preprocess.AudioLevelingUnavailable("ffmpeg could not be started, so the audio was not levelled.")
        monkeypatch.setattr(audio_preprocess, "level_audio", broken)
        items = worker["run"](True)
        assert items[-1][0] == "ok" and "could not be started" in items[-1][1]["coverage_warning"]


# --- killing the job takes the levelling ffmpeg with it ------------------------

@pytest.mark.skipif(not sys.platform.startswith("linux"),
                    reason="checks the worker's processes through /proc")
class TestKillingTheJob:
    def _fake_ffmpeg(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        marker = tmp_path / "ffmpeg.pid"
        script = bin_dir / "ffmpeg"
        # exec: the recorded pid is the process ffmpeg would be, with no shell left over.
        script.write_text(f'#!/bin/sh\necho $$ > "{marker}.tmp"\nmv "{marker}.tmp" "{marker}"\nexec sleep 120\n')
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return marker

    def test_cancel_kills_ffmpeg_and_the_scratch_folder_goes(self, isolated_db, monkeypatch, tmp_path):
        from tests import test_transcribe_service as tts
        marker = self._fake_ffmpeg(tmp_path, monkeypatch)
        did = _title(isolated_db, difficult_audio=1)
        job_id = f"transcribe_{did}"
        tts._clear(job_id)
        monkeypatch.setattr(sys.modules[tts.__name__], "_PARENT_ONLY_PROBE", True)
        monkeypatch.setattr(transcribe_pipeline, "_transcribe_worker", __import__("functools").partial(
            tts._spawned_worker, ("two_lines", isolated_db.LIBRARY_DIR, None)))
        made = []
        real_new = transcribe_service.storage.new_workdir
        monkeypatch.setattr(transcribe_service.storage, "new_workdir",
                            lambda job_id=None: made.append(real_new(job_id)) or made[-1])

        transcribe_service.start_transcribe_run(did)
        assert tts._wait_until(lambda: marker.exists(), timeout=60)
        ffmpeg_pid = int(marker.read_text())
        assert not tts._process_gone(ffmpeg_pid)

        background_jobs.request_cancel(job_id)
        assert tts._wait_finished(job_id, timeout=10)["status"] == "cancelled"
        assert tts._wait_until(lambda: tts._process_gone(ffmpeg_pid))
        assert tts._wait_until(lambda: not os.path.exists(made[0]))
        tts._clear(job_id)

    def test_an_abandoned_levelling_folder_is_swept_at_startup(self, isolated_db):
        import storage
        scratch = storage.new_workdir("transcribe_999")
        copy = os.path.join(scratch, difficult_audio_service.LEVELLED_NAME)
        open(copy, "wb").write(b"x")
        old = os.stat(scratch).st_mtime - 2 * 24 * 3600
        os.utime(scratch, (old, old))
        assert storage.sweep_library_temp()["removed"] >= 1
        assert not os.path.exists(scratch)


class TestProcOwnGroup:
    @pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
    def test_the_child_stays_in_the_callers_group_and_is_still_killed_on_timeout(self):
        import sys as _sys
        from lib import proc
        code = "import os,sys;print(os.getpgid(0));sys.stdout.flush();import time;time.sleep(30)"
        start = __import__("time").monotonic()
        run = proc.run_captured([_sys.executable, "-c", code], timeout=1.0, own_group=False)
        assert run.timed_out and __import__("time").monotonic() - start < 10
        assert int(run.stdout.split()[0]) == os.getpgid(0)

    @pytest.mark.skipif(os.name == "nt", reason="process groups are POSIX")
    def test_the_default_still_gets_a_group_of_its_own(self):
        import sys as _sys
        from lib import proc
        run = proc.run_captured([_sys.executable, "-c", "import os;print(os.getpgid(0))"], timeout=20)
        assert int(run.stdout.split()[0]) != os.getpgid(0)
