"""
Benchmark Lab transcription at real settings (services/benchmark_case_service.py
run_transcription, transcribe_options_service.resolve_lab_options): a Lab arm
takes the per-title options with the app's defaults, can run Qwen3-ASR and
anime-whisper when they are already installed, and records what it ran with.
Mocked: no GPU, no models, no network.
"""
import json
import time

import pytest

import asr_backend
import audio_preprocess
import background_jobs
import benchmark
import db
import whisper_transcribe
from api.benchmark_schemas import BenchmarkRunRequest
from services import benchmark_case_service as cases_svc
from services import benchmark_lab_service as svc
from services import asr_options_service, model_readiness_service, transcribe_options_service as opts
from services.service_errors import DependencyUnavailableError, InvalidInputError
from tests.saved_settings import patch_setting


def _wait(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(svc.JOB_ID)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def _run(configs, **kw):
    started = svc.start_run("transcription", configs, **kw)
    assert _wait()["status"] == "done"
    return started


def _case(language="ja", name="clip"):
    return db.create_benchmark_case(name, "transcription", "audio_drama", input_filename=f"{name}.wav",
                                    reference_text="こんにちは", source_language=language)


@pytest.fixture(autouse=True)
def separation_downloaded(monkeypatch):
    monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: True)


@pytest.fixture
def whisper_calls(monkeypatch):
    """transcribe_for_timing as the Lab calls it, one dict of keyword arguments per case."""
    calls = []

    def fake(path, model_size=None, language=None, use_gpu=False, local_model_path=None, **kw):
        calls.append({"path": path, "model_size": model_size, "language": language, **kw})
        return [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]
    monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake)
    return calls


class TestResolveLabOptions:
    def test_nothing_given_is_the_apps_default_not_the_functions(self):
        resolved = opts.resolve_lab_options(None)
        assert resolved["min_silence_ms"] == 300       # transcribe_for_timing's own default is 2000
        assert resolved["beam_size"] == 5
        assert resolved["vad_threshold"] == 0.5
        assert resolved["sensitivity_preset"] == "normal"
        assert resolved["initial_prompt"] == ""
        assert not any(resolved[k] for k in ("whisper_repeat_guard", "difficult_audio", "separate_vocals_first"))
        assert resolved == opts.lab_option_defaults()

    def test_a_field_given_wins_and_the_rest_keep_the_default(self):
        resolved = opts.resolve_lab_options({"beam_size": 8, "difficult_audio": True, "initial_prompt": " 桜 "})
        assert (resolved["beam_size"], resolved["difficult_audio"], resolved["initial_prompt"]) == (8, True, "桜")
        assert resolved["min_silence_ms"] == 300

    @pytest.mark.parametrize("bad", [
        {"beam_size": 11}, {"min_silence_ms": 5}, {"vad_threshold": 0.95}, {"sensitivity_preset": "loud"},
        {"hallucination_silence_sec": 0.2}, {"beam_size": "5"}, {"beam_size": True},
        {"difficult_audio": 1}, {"nonsense": 1}, {"initial_prompt": "x" * 1001},
    ])
    def test_the_title_stages_ranges_apply(self, bad):
        with pytest.raises(InvalidInputError):
            opts.resolve_lab_options(bad)

    def test_hallucination_silence_zero_means_off_as_for_a_title(self):
        assert opts.resolve_lab_options({"hallucination_silence_sec": 0})["hallucination_silence_sec"] == 0


class TestWhisperArmRunsAtRealSettings:
    def test_default_arm_hands_whisper_what_a_title_would(self, isolated_db, whisper_calls):
        _case()
        _run([{"engine": "whisper", "model": "large-v3"}])
        (call,) = whisper_calls
        assert call["beam_size"] == 5
        assert call["min_silence_duration_ms"] == 300
        assert call["vad_threshold"] == 0.5
        assert call["initial_prompt"] == ""
        assert call["repeat_guard"] is False
        assert call["sensitivity_preset"] == "normal"
        assert "difficult_audio" not in call

    def test_changed_options_reach_whisper(self, isolated_db, whisper_calls):
        _case()
        _run([{"engine": "whisper", "model": "large-v3",
               "options": {"beam_size": 1, "min_silence_ms": 600, "vad_threshold": 0.3,
                           "whisper_repeat_guard": True, "hallucination_silence_sec": 3,
                           "initial_prompt": "桜"}}])
        (call,) = whisper_calls
        assert (call["beam_size"], call["min_silence_duration_ms"], call["vad_threshold"]) == (1, 600, 0.3)
        assert call["repeat_guard"] is True
        assert call["hallucination_silence_sec"] == 3
        assert call["initial_prompt"] == "桜"

    def test_difficult_audio_uses_the_sensitive_preset_and_skip_rule(self, isolated_db, whisper_calls, monkeypatch):
        _case()
        levelled = []
        monkeypatch.setattr(audio_preprocess, "level_audio",
                            lambda src, out, **kw: levelled.append(src) or out)
        _run([{"engine": "whisper", "model": "large-v3", "options": {"difficult_audio": True}}])
        (call,) = whisper_calls
        assert call["sensitivity_preset"] == "sensitive"
        assert call["difficult_audio"] is True
        assert call["vad_threshold"] < 0.5          # the Sensitive preset's own threshold
        assert levelled and call["path"].endswith("leveled.wav")

    def test_separate_vocals_first_transcribes_the_vocals(self, isolated_db, whisper_calls, monkeypatch):
        _case()
        monkeypatch.setattr(audio_preprocess, "separate_vocals",
                            lambda src, out, **kw: out)
        _run([{"engine": "whisper", "model": "large-v3", "options": {"separate_vocals_first": True}}])
        assert whisper_calls[0]["path"].endswith("vocals.wav")

    def test_a_failed_separation_is_the_cases_error_not_the_runs(self, isolated_db, whisper_calls, monkeypatch):
        _case()

        def boom(*a, **k):
            raise audio_preprocess.VocalSeparationError("separation needs audio-separator")
        monkeypatch.setattr(audio_preprocess, "separate_vocals", boom)
        (sid,) = _run([{"engine": "whisper", "model": "large-v3",
                        "options": {"separate_vocals_first": True}}])["session_ids"]
        (res,) = svc.get_run(sid)["results"]
        assert "audio-separator" in res["error"]
        assert whisper_calls == []

    def test_the_scratch_audio_is_removed_afterwards(self, isolated_db, whisper_calls, monkeypatch):
        import os
        _case()
        monkeypatch.setattr(audio_preprocess, "separate_vocals", lambda src, out, **kw: open(out, "wb").close() or out)
        _run([{"engine": "whisper", "model": "large-v3", "options": {"separate_vocals_first": True}}])
        assert not os.path.exists(whisper_calls[0]["path"])


class TestRunRecordsWhatItRan:
    def test_session_context_has_options_version_identity_and_the_scoring_marker(self, isolated_db, whisper_calls, monkeypatch):
        _case()
        monkeypatch.setattr(cases_svc, "app_version", lambda: "0.1.0-test")
        monkeypatch.setattr(svc, "app_version", lambda: "0.1.0-test")
        (sid,) = _run([{"engine": "whisper", "model": "large-v3", "options": {"beam_size": 2}}])["session_ids"]
        ctx = svc.get_run(sid)["run"]["context_settings"]
        assert ctx["transcribe_options"]["beam_size"] == 2
        assert ctx["transcribe_options"]["min_silence_ms"] == 300
        assert ctx["app_version"] == "0.1.0-test"
        assert ctx["model_identity"] == {"model": "large-v3"}
        assert ctx["text_normaliser"] == 2
        assert ctx["transcribe_options_ignored"] == []
        assert ctx["transcribe_changes"] == {"beam_size": 2}

    def test_each_arm_records_its_own_options(self, isolated_db, whisper_calls):
        _case()
        sids = _run([{"engine": "whisper", "model": "large-v3", "options": {"beam_size": 1}},
                     {"engine": "whisper", "model": "large-v3", "options": {"beam_size": 5}}])["session_ids"]
        beams = [svc.get_run(s)["run"]["context_settings"]["transcribe_options"]["beam_size"] for s in sids]
        assert beams == [1, 5]
        assert sorted(c["beam_size"] for c in whisper_calls) == [1, 5]

    def test_offline_folder_is_named_not_pathed(self, isolated_db, whisper_calls, tmp_path, monkeypatch):
        folder = tmp_path / "private-ja-finetune"
        folder.mkdir()
        patch_setting(monkeypatch, "whisper_model_path", str(folder))
        _case()
        (sid,) = _run([{"engine": "whisper", "model": benchmark.LOCAL_FOLDER_MODEL}])["session_ids"]
        detail = svc.get_run(sid)
        assert detail["run"]["context_settings"]["model_identity"] == {"model": "offline model folder"}
        assert "private-ja-finetune" not in json.dumps(detail)

    def test_same_model_with_different_options_is_not_a_duplicate(self, isolated_db, whisper_calls):
        _case()
        configs = [{"engine": "whisper", "model": "small", "options": {"beam_size": 1}},
                   {"engine": "whisper", "model": "small", "options": {"beam_size": 2}}]
        assert svc.estimate("transcription", configs)["case_count"] == 1

    def test_the_same_model_and_options_twice_is_refused(self, isolated_db, whisper_calls):
        _case()
        with pytest.raises(InvalidInputError, match="twice"):
            svc.estimate("transcription", [{"engine": "whisper", "model": "small"},
                                           {"engine": "whisper", "model": "small", "options": {"beam_size": 5}}])

    def test_api_and_ocr_identity_is_the_model_or_backend_name(self):
        assert cases_svc.model_identity("translation", {"engine": "claude", "model": "claude-sonnet-5"}) == {
            "model": "claude-sonnet-5"}
        assert cases_svc.model_identity("ocr", {"engine": "tesseract", "model": None}) == {"model": "tesseract"}

    def test_ollama_identity_carries_the_digest_and_survives_a_dead_server(self, monkeypatch):
        from lib import http

        class Reply:
            status = 200
            body = json.dumps({"models": [{"name": "qwen3:8b", "digest": "abc123"}]})
        monkeypatch.setattr(http, "get", lambda url, **kw: Reply())
        assert cases_svc.model_identity("translation", {"engine": "ollama", "model": "qwen3:8b"}) == {
            "model": "qwen3:8b", "digest": "abc123"}

        def dead(url, **kw):
            raise OSError("refused")
        monkeypatch.setattr(http, "get", dead)
        assert cases_svc.model_identity("translation", {"engine": "ollama", "model": "qwen3:8b"}) == {
            "model": "qwen3:8b", "digest": None}


@pytest.fixture
def installed(monkeypatch):
    """Packages present and models on disk for both extra recognisers."""
    from services import qwen3_requirements_service as qwen_req
    monkeypatch.setattr(qwen_req, "require_qwen3_packages", lambda *a, **k: None)
    monkeypatch.setattr(opts, "_require_vad_packages", lambda: None)
    monkeypatch.setattr(opts, "anime_whisper_package_problem", lambda: None)
    monkeypatch.setattr(model_readiness_service, "qwen_asr_ready", lambda: True)
    monkeypatch.setattr(model_readiness_service, "whisper_ready", lambda *a, **k: True)


class _FakeBackend:
    calls = []

    def __init__(self, name):
        self.name = name

    def transcribe(self, audio_path, language, **kw):
        self.calls.append({"backend": self.name, "language": language, **kw})
        return [{"start": 0.0, "end": 1.0, "text": "こんにちは"}]


@pytest.fixture
def backends(monkeypatch):
    _FakeBackend.calls = []
    monkeypatch.setattr(asr_backend, "get_backend", lambda name: _FakeBackend(name))
    return _FakeBackend.calls


class TestExtraRecognisers:
    def test_offered_as_lab_models(self, isolated_db):
        offered = {b["name"]: b["label"] for b in svc.get_options()["transcription_backends"]}
        assert offered == {"qwen3_asr_vad": "Qwen3-ASR", "anime_whisper": "anime-whisper"}

    @pytest.mark.parametrize("engine, ready, sentence", [
        ("qwen3_asr_vad", "qwen_asr_ready", "Qwen3-ASR isn't downloaded yet"),
        ("anime_whisper", "whisper_ready", "anime-whisper isn't downloaded yet"),
    ])
    def test_a_model_that_is_not_downloaded_is_refused_and_never_fetched(
            self, isolated_db, installed, monkeypatch, backends, engine, ready, sentence):
        _case()
        monkeypatch.setattr(model_readiness_service, ready, lambda *a, **k: False)
        with pytest.raises(DependencyUnavailableError, match=sentence) as err:
            svc.start_run("transcription", [{"engine": engine}])
        assert "does not download" in str(err.value)
        assert backends == []

    def test_missing_packages_use_the_apps_plain_sentence(self, isolated_db, monkeypatch):
        from services import qwen3_requirements_service as qwen_req

        def missing(*a, **k):
            raise DependencyUnavailableError(qwen_req.MISSING_QWEN_MESSAGE)
        monkeypatch.setattr(qwen_req, "require_vad_backend_packages", missing)
        _case()
        with pytest.raises(DependencyUnavailableError, match="Open Diagnostics"):
            svc.start_run("transcription", [{"engine": "qwen3_asr_vad"}])

    def test_timing_refinement_needs_the_forced_aligner_downloaded_too(
            self, isolated_db, installed, monkeypatch, backends):
        monkeypatch.setattr(asr_options_service, "get_vad_refine_timing", lambda: True)
        monkeypatch.setattr(model_readiness_service, "aligner_ready", lambda: False)
        _case()
        with pytest.raises(DependencyUnavailableError, match="forced aligner"):
            svc.start_run("transcription", [{"engine": "qwen3_asr_vad"}])
        assert backends == []

    def test_options_list_says_what_a_recogniser_still_needs(self, isolated_db, installed, monkeypatch):
        monkeypatch.setattr(model_readiness_service, "qwen_asr_ready", lambda: False)
        offered = {b["name"]: b["problem"] for b in svc.get_options()["transcription_backends"]}
        assert "isn't downloaded" in offered["qwen3_asr_vad"]
        assert offered["anime_whisper"] is None

    def test_anime_whisper_is_refused_on_a_chinese_case(self, isolated_db, installed, backends):
        _case(language="zh")
        with pytest.raises(InvalidInputError, match="Japanese"):
            svc.start_run("transcription", [{"engine": "anime_whisper"}])

    def test_qwen_runs_with_the_apps_saved_choices_and_gets_no_whisper_options(
            self, isolated_db, installed, backends, monkeypatch):
        patch_setting(monkeypatch, "qwen_asr_batch_size", 4)
        _case()
        (sid,) = _run([{"engine": "qwen3_asr_vad",
                        "options": {"beam_size": 1}}])["session_ids"]
        (call,) = backends
        assert call["backend"] == "qwen3_asr_vad"
        assert call["language"] == "ja"
        assert {"batch_size", "detector", "tuning"} <= set(call)
        assert "beam_size" not in call and "initial_prompt" not in call
        ctx = svc.get_run(sid)["run"]["context_settings"]
        assert "beam_size" in ctx["transcribe_options_ignored"]
        import qwen3_native
        assert ctx["model_identity"] == {"model": qwen3_native.asr_repo_id("1.7B")}
        assert svc.get_run(sid)["run"]["engine"] == "qwen3_asr_vad"

    def test_anime_whisper_takes_the_repeat_guard_and_records_its_pinned_revision(
            self, isolated_db, installed, backends):
        _case()
        (sid,) = _run([{"engine": "anime_whisper",
                        "options": {"whisper_repeat_guard": True}}])["session_ids"]
        assert backends[0]["repeat_guard"] is True
        identity = svc.get_run(sid)["run"]["context_settings"]["model_identity"]
        assert identity["revision"] and "anime-whisper" in identity["model"]

    def test_a_result_is_scored_like_any_other_transcription(self, isolated_db, installed, backends):
        _case()
        (sid,) = _run([{"engine": "anime_whisper"}])["session_ids"]
        (res,) = svc.get_run(sid)["results"]
        assert (res["metric"], res["score"]) == ("cer", 1.0)


class TestApiShapes:
    def test_run_request_accepts_options_and_refuses_unknown_ones(self):
        body = BenchmarkRunRequest(stage="transcription", configs=[
            {"engine": "whisper", "model": "small", "options": {"beam_size": 2}}])
        assert body.configs[0].options.beam_size == 2
        with pytest.raises(ValueError):
            BenchmarkRunRequest(stage="transcription", configs=[
                {"engine": "whisper", "options": {"path": "/etc/passwd"}}])

    def test_options_endpoint_lists_the_app_defaults(self, isolated_db):
        got = svc.get_options()
        assert got["transcribe_defaults"] == opts.lab_option_defaults()
        assert got["transcribe_defaults"]["min_silence_ms"] == 300


class TestRunSafety:
    def test_a_module_without_a_spec_does_not_break_the_options_list(self, isolated_db, monkeypatch):
        import sys
        import types
        monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
        monkeypatch.setattr(model_readiness_service, "qwen_asr_ready", lambda: True)
        monkeypatch.setattr(model_readiness_service, "whisper_ready", lambda *a, **k: True)
        assert len(svc.get_options()["transcription_backends"]) == 2

    def test_separation_model_missing_is_refused_before_anything_runs(self, isolated_db, whisper_calls, monkeypatch):
        monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: False)
        _case()
        with pytest.raises(DependencyUnavailableError, match="separation model"):
            svc.start_run("transcription", [{"engine": "whisper", "model": "small",
                                             "options": {"separate_vocals_first": True}}])
        assert whisper_calls == []

    def test_any_preparation_failure_is_that_cases_error_and_the_run_goes_on(
            self, isolated_db, whisper_calls, monkeypatch):
        _case(name="bad")
        _case(name="good")
        real = audio_preprocess.separate_vocals

        def flaky(src, out, **kw):
            if "bad" in src:
                raise RuntimeError("Error opening 'bad.wav': Format not recognised.")
            return real(src, out, **kw) if False else out
        monkeypatch.setattr(audio_preprocess, "separate_vocals", flaky)
        (sid,) = _run([{"engine": "whisper", "model": "small",
                        "options": {"separate_vocals_first": True}}])["session_ids"]
        detail = svc.get_run(sid)
        assert detail["run"]["status"] == "done"
        errors = sorted((r["case_label"], bool(r["error"])) for r in detail["results"])
        assert errors == [("bad", True), ("good", False)]

    def test_gpu_models_are_released_when_a_transcription_run_ends(self, isolated_db, whisper_calls, monkeypatch):
        import whisper_models
        released = []
        monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: released.append(1))
        _case()
        _run([{"engine": "whisper", "model": "small"}])
        assert released == [1]

    def test_cancel_stops_the_run_and_the_interrupted_case_is_not_saved(self, isolated_db, monkeypatch):
        _case(name="a")
        _case(name="b")
        seen = []

        def fake(path, **kw):
            seen.append(path)
            background_jobs.request_cancel(svc.JOB_ID)
            kw["progress_cb"](0.5)        # the poll a real decode makes between segments
            return [{"start": 0, "end": 1, "text": "こんにちは"}]
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake)
        started = svc.start_run("transcription", [{"engine": "whisper", "model": "small"}])
        _wait()
        detail = svc.get_run(started["session_ids"][0])
        assert detail["run"]["status"] == "cancelled"
        assert detail["results"] == [] and len(seen) == 1

    def test_levelling_is_cancellable_in_this_process(self, isolated_db, whisper_calls, monkeypatch):
        seen = {}

        def level(src, out, cancel=None, own_group=True, **kw):
            seen.update(cancel=cancel, own_group=own_group)
            return out
        monkeypatch.setattr(audio_preprocess, "level_audio", level)
        _case()
        _run([{"engine": "whisper", "model": "small", "options": {"difficult_audio": True}}])
        assert seen["own_group"] is True and callable(seen["cancel"])

    def test_qwen_and_anime_get_a_cancel_check(self, isolated_db, installed, backends):
        _case()
        _run([{"engine": "anime_whisper"}])
        assert callable(backends[0]["cancel_check"])


class TestReviewTightening:
    def test_bad_types_are_refused_not_coerced(self):
        for bad in ({"beam_size": "5"}, {"whisper_repeat_guard": "yes"}, {"vad_threshold": "0.5"}, {"beam_size": 5.5}):
            with pytest.raises(ValueError):
                BenchmarkRunRequest(stage="transcription", configs=[{"engine": "whisper", "options": bad}])
        ok = BenchmarkRunRequest(stage="transcription", configs=[
            {"engine": "whisper", "options": {"vad_threshold": 1 // 2 + 0.4, "hallucination_silence_sec": 3}}])
        assert ok.configs[0].options.hallucination_silence_sec == 3

    def test_an_odd_ollama_digest_is_dropped(self, monkeypatch):
        from lib import http

        class Reply:
            status = 200
            body = json.dumps({"models": [{"name": "m:latest", "digest": "<script>" + "x" * 500}]})
        monkeypatch.setattr(http, "get", lambda url, **kw: Reply())
        assert cases_svc.model_identity("translation", {"engine": "ollama", "model": "m"})["digest"] is None

    def test_options_an_arm_ignores_do_not_make_two_arms_different(self, isolated_db, installed):
        _case()
        with pytest.raises(InvalidInputError, match="twice"):
            svc.estimate("transcription", [{"engine": "qwen3_asr_vad", "options": {"beam_size": 1}},
                                           {"engine": "qwen3_asr_vad", "options": {"beam_size": 2}}])

    @pytest.mark.parametrize("stage, engine", [("translation", "fake"), ("ocr", "tesseract")])
    def test_options_are_refused_off_the_transcription_stage(self, isolated_db, stage, engine):
        with pytest.raises(InvalidInputError, match="transcription runs only"):
            svc.check_config(stage, {"engine": engine, "options": {"beam_size": 1}})
