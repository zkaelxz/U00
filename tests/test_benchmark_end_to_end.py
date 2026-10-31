"""
Benchmark Lab end-to-end arms (services/benchmark_lab_service.py `transcribe`): a translation arm
that translates what a recogniser hears in each clip, not the reference transcript, and is scored
against the reference translation. Mocked recogniser and engine: no GPU, models or network.
"""
import json

import pytest

import background_jobs
import db
import translate_engines
import whisper_transcribe
from api.benchmark_schemas import BenchmarkRunRequest
from services import benchmark_lab_service as lab
from services import benchmark_runs_service as runs
from services import model_readiness_service, translate_service
from lib.errors import InvalidInputError, UnsupportedOperationError
from tests.job_helpers import wait_for_job

HEARD = {"a.wav": "你好吗", "b.wav": "再见"}


class EchoEngine:
    """Answers `EN <source>` and remembers every source it was asked to translate."""
    name = "claude"
    sources = []

    def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
        self.model = model
        self.last_usage = {"input_tokens": 10, "output_tokens": 10}

    def translate_batch(self, zh_lines, context):
        type(self).sources.extend(zh_lines)
        return [f"EN {z}" for z in zh_lines]


@pytest.fixture(autouse=True)
def mocks(monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "claude", EchoEngine)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: "sk-test" if name == "claude" else "offline")
    monkeypatch.setattr(model_readiness_service, "separation_ready", lambda *a, **k: True)
    EchoEngine.sources = []
    calls = []

    def fake(path, model_size=None, language=None, use_gpu=False, local_model_path=None, **kw):
        calls.append({"path": path, "model_size": model_size, **kw})
        text = HEARD.get(path.rsplit("/", 1)[-1])
        if text is None:
            raise RuntimeError("cannot read audio")
        return [{"start": 0.0, "end": 1.0, "text": text}]
    monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", fake)
    background_jobs.clear_all_jobs()
    yield calls
    background_jobs.clear_all_jobs()


def _clip(name="a.wav", heard_as=None, key="k1", reference="EN 你好吗", transcript="你好嗎", order=1):
    """What a clip import makes: an audio case and a translation case sharing a clip_key."""
    common = {"content_type": "audio_drama", "source_language": "zh", "tier": "application", "set_name": "s",
              "title": "T", "scene_order": order, "clip_key": key, "label": name}
    return db.create_benchmark_clip_cases([
        {**common, "stage": "transcription", "input_filename": name, "reference_text": transcript},
        {**common, "stage": "translation", "source_text": transcript, "reference_text": reference}])


def _run(configs, **kw):
    started = lab.start_run("translation", configs, **kw)
    wait_for_job(lab.JOB_ID, timeout=15)
    return started


WHISPER = {"engine": "whisper", "model": "large-v3"}


class TestEndToEndRun:
    def test_translates_what_was_heard_not_the_reference_transcript(self, isolated_db):
        _clip()
        started = _run([{"engine": "claude", "transcribe": WHISPER}])
        assert EchoEngine.sources == ["你好吗"]
        (result,) = db.list_benchmark_results(started["session_ids"][0])
        assert result["output_text"] == "EN 你好吗"
        assert result["recognised_text"] == "你好吗"
        assert result["score"] == 1.0

    def test_a_misheard_clip_scores_lower_than_the_ceiling_arm_on_the_same_cases(self, isolated_db):
        _clip(reference="EN 你好嗎")
        started = _run([{"engine": "claude"}, {"engine": "claude", "transcribe": WHISPER}])
        plain, e2e = (db.list_benchmark_results(sid)[0] for sid in started["session_ids"])
        assert plain["recognised_text"] is None
        assert plain["score"] == 1.0
        assert e2e["score"] < plain["score"]

    def test_recognition_failure_is_that_cases_error_scored_zero_and_never_stops_the_arm(self, isolated_db):
        for i in range(8):
            _clip(name=f"bad{i}.wav", key=f"bad{i}", order=i)
        _clip(name="a.wav", key="good", order=9)
        started = _run([{"engine": "claude", "transcribe": WHISPER}])
        session = db.get_benchmark_session(started["session_ids"][0])
        results = db.list_benchmark_results(started["session_ids"][0])
        assert session["status"] == "done"
        assert (session["case_count"], session["error_count"], len(results)) == (9, 8, 9)
        assert [r["score"] for r in results].count(0.0) == 8
        assert EchoEngine.sources == ["你好吗"]

    def test_nothing_heard_costs_no_translation_call(self, isolated_db, monkeypatch):
        _clip()
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", lambda *a, **k: [])
        started = _run([{"engine": "claude", "transcribe": WHISPER}])
        (result,) = db.list_benchmark_results(started["session_ids"][0])
        assert result["error"] == "Nothing was recognised in this clip."
        assert EchoEngine.sources == []

    def test_the_recognisers_options_reach_it_and_are_recorded(self, isolated_db, mocks):
        _clip()
        started = _run([{"engine": "claude", "transcribe": {**WHISPER, "options": {"beam_size": 1}}}])
        assert mocks[0]["beam_size"] == 1 and mocks[0]["model_size"] == "large-v3"
        context = json.loads(db.get_benchmark_session(started["session_ids"][0])["context_settings"])
        e2e = context["end_to_end"]
        assert (e2e["engine"], e2e["model"], e2e["changes"]) == ("whisper", "large-v3", {"beam_size": 1})
        assert e2e["options"]["beam_size"] == 1 and "model_identity" in e2e
        detail = runs.get_run(started["session_ids"][0])
        assert detail["results"][0]["recognised_text"] == "你好吗"


class TestSelection:
    def test_a_case_without_a_clip_is_refused_before_anything_runs(self, isolated_db):
        db.create_benchmark_clip_cases([{"label": "typed", "stage": "translation", "content_type": "novel",
                                         "source_language": "zh", "source_text": "你好", "reference_text": "Hi",
                                         "tier": "application", "set_name": "s"}])
        with pytest.raises(UnsupportedOperationError, match="no usable audio clip"):
            lab.start_run("translation", [{"engine": "claude", "transcribe": WHISPER}])
        assert db.list_benchmark_sessions() == []

    def test_an_excluded_audio_case_counts_as_missing(self, isolated_db):
        audio_id, _ = _clip()
        db.set_benchmark_case_excluded(audio_id, True)
        with pytest.raises(UnsupportedOperationError, match="no usable audio clip"):
            lab.estimate("translation", [{"engine": "claude", "transcribe": WHISPER}])

    def test_two_recognisers_under_one_engine_are_a_real_comparison(self, isolated_db):
        _clip()
        started = _run([{"engine": "claude", "transcribe": WHISPER},
                        {"engine": "claude", "transcribe": {**WHISPER, "options": {"beam_size": 1}}}])
        assert len(started["session_ids"]) == 2

    def test_the_same_pair_twice_is_refused(self, isolated_db):
        _clip()
        with pytest.raises(InvalidInputError, match="picked twice"):
            lab.estimate("translation", [{"engine": "claude", "transcribe": WHISPER},
                                         {"engine": "claude", "transcribe": WHISPER}])

    def test_batch_options_cannot_be_combined_yet(self, isolated_db):
        with pytest.raises(InvalidInputError, match="can't also take"):
            lab.check_config("translation", {"engine": "claude", "options": {"batch_size": 5}, "transcribe": WHISPER})

    def test_only_a_translation_run_takes_a_recogniser_arm(self, isolated_db):
        with pytest.raises(InvalidInputError, match="Only a translation run"):
            lab.check_config("transcription", {"engine": "whisper", "model": "large-v3", "transcribe": WHISPER})

    def test_a_judge_pass_is_refused_for_an_end_to_end_arm(self, isolated_db):
        _clip()
        with pytest.raises(UnsupportedOperationError, match="end-to-end"):
            lab.start_run("translation", [{"engine": "claude", "transcribe": WHISPER}],
                          judge={"engine": "claude", "model": None})

    def test_an_unknown_whisper_size_is_refused(self, isolated_db):
        with pytest.raises(InvalidInputError, match="Unknown Whisper size"):
            lab.check_config("translation", {"engine": "claude", "transcribe": {"engine": "whisper", "model": "huge"}})


class TestRequestShape:
    def test_the_run_request_accepts_a_recogniser_arm_and_refuses_unknown_fields(self):
        body = BenchmarkRunRequest(configs=[{"engine": "claude", "transcribe": {
            "engine": "whisper", "model": "large-v3", "options": {"beam_size": 1}}}])
        assert body.configs[0].model_dump(exclude_none=True)["transcribe"] == {
            "engine": "whisper", "model": "large-v3", "options": {"beam_size": 1}}
        with pytest.raises(ValueError):
            BenchmarkRunRequest(configs=[{"engine": "claude", "transcribe": {"engine": "whisper", "key": "x"}}])


class TestEdges:
    def test_a_cancel_after_recognition_makes_no_translation_call(self, isolated_db, monkeypatch):
        from services import benchmark_end_to_end_service as e2e
        audio_id, case_id = _clip()
        case = db.get_benchmark_case(case_id)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda job_id: True)
        r = e2e.translate_recognised(EchoEngine(), lab.check_config("transcription", WHISPER), case, None, False, None, "j")
        assert r["recognition_failed"] and r["cost_usd"] == 0.0 and EchoEngine.sources == []

    def test_a_clip_that_vanished_is_that_cases_error_not_the_jobs(self, isolated_db, monkeypatch):
        from services import benchmark_end_to_end_service as e2e
        _, case_id = _clip()
        case = db.get_benchmark_case(case_id)
        monkeypatch.setattr(e2e, "audio_cases", lambda cases: ({}, cases))
        r = e2e.translate_recognised(EchoEngine(), lab.check_config("transcription", WHISPER), case, None, False, None, "j")
        assert r["error"] == "The clip audio is missing." and r["recognition_failed"]

    def test_a_key_in_a_recogniser_error_is_not_stored(self, isolated_db, monkeypatch):
        _clip()

        def boom(*a, **k):
            raise RuntimeError("401 for key sk-ant-abcdefghijklmnopqrstuvwxyz0123456789")
        monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", boom)
        started = _run([{"engine": "claude", "transcribe": WHISPER}])
        (result,) = db.list_benchmark_results(started["session_ids"][0])
        assert "sk-ant-abcdefghijklmnop" not in result["error"]
