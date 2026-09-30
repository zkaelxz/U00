"""
Step 40b: scheduled model re-evaluation and promotion
(services/model_reeval_service.py). Mocked engines only (test_offline,
libretranslate patched to an offline fake).
"""
import datetime
import time

import pytest

import background_jobs
import db
import translate_engines
from services import benchmark_lab_service as lab
from services import model_reeval_service as svc
from services import settings_service, translate_service
from services.service_errors import (ConflictError, InvalidInputError,
                                     UnsupportedOperationError)


def _wait(timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        st = background_jobs.get_status(lab.JOB_ID)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


class GoodEngine:
    """Stands in for a 'better' candidate (as nllb): answers with the reference."""
    name = "nllb"
    answers = {"你好": "Hello", "谢谢": "Thanks"}

    def __init__(self, api_key=None, model="facebook/nllb-200-distilled-600M", **kw):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        return [self.answers.get(z, "") for z in zh_lines]


class WeakEngine(GoodEngine):
    """The production model (as libretranslate): answers badly."""
    name = "libretranslate"

    def translate_batch(self, zh_lines, context):
        return ["Hmm" for _ in zh_lines]


@pytest.fixture
def world(isolated_db, monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "nllb", GoodEngine)
    monkeypatch.setitem(translate_engines.ENGINES, "libretranslate", WeakEngine)
    settings_service.set_settings({"default_engine": "libretranslate"})
    lab.import_golden_set("g", "你好\tHello\n谢谢\tThanks\n", "tsv", "public")
    svc.set_settings(False, 30, tier="public", set_name="g")
    return monkeypatch


def _run_now(**kw):
    out = svc.run_now(**kw)
    assert _wait()["status"] == "done"
    return out


class TestCandidates:
    def test_production_defaults_to_settings_engine(self, world):
        prod = svc.get_production()
        assert (prod["engine"], prod["source"]) == ("libretranslate", "settings")

    def test_add_and_reject_is_recorded_and_surfaced(self, world):
        c = svc.add_candidate("nllb", note="try it")["candidate"]
        assert c["status"] == "candidate" and c["last_decision"] is None
        svc.reject(c["id"], reason="too literal")
        again = svc.add_candidate("nllb")
        assert again["already_registered"] is True
        d = again["candidate"]["last_decision"]
        assert d["decision"] == "rejected" and d["reason"] == "too literal"
        assert d["summary"].startswith("Already evaluated on ") and "rejected: too literal" in d["summary"]
        assert len(db.list_model_candidates("translation")) == 1

    def test_rejected_candidate_is_not_rerun(self, world):
        c = svc.add_candidate("nllb")["candidate"]
        svc.reject(c["id"], "no")
        with pytest.raises(UnsupportedOperationError):
            svc.run_now()

    def test_reopen_puts_it_back_but_keeps_the_decision(self, world):
        c = svc.add_candidate("nllb")["candidate"]
        svc.reject(c["id"], "no")
        out = svc.reopen_candidate(c["id"])
        assert out["status"] == "candidate" and out["last_decision"]["decision"] == "rejected"

    def test_production_model_is_not_a_candidate(self, world):
        with pytest.raises(InvalidInputError):
            svc.add_candidate("libretranslate")


class TestRunAndReport:
    def test_run_uses_the_benchmark_lab_database(self, world):
        c = svc.add_candidate("nllb")["candidate"]
        out = _run_now()
        assert out["arena_group"] and out["candidate_ids"] == [c["id"]]
        prod_run, cand_run = (db.get_benchmark_session(s) for s in out["session_ids"])
        assert prod_run["engine"] == "libretranslate" and cand_run["engine"] == "nllb"
        assert prod_run["arena_group"] == cand_run["arena_group"]
        assert db.list_benchmark_results(cand_run["id"])  # rows in benchmark_results
        rep = svc.report()
        (row,) = rep["rows"]
        assert row["candidate"]["id"] == c["id"]
        assert row["quality_delta"] > 0          # the candidate matched the references
        assert row["cost_delta_usd"] == 0

    def test_running_never_promotes(self, world):
        svc.add_candidate("nllb")
        _run_now()
        assert svc.get_production()["engine"] == "libretranslate"
        assert settings_service.get_default_engine() == "libretranslate"
        assert db.list_model_candidates("translation")[0]["status"] == "candidate"

    def test_promotion_needs_explicit_confirm(self, world):
        c = svc.add_candidate("nllb")["candidate"]
        _run_now()
        with pytest.raises(InvalidInputError):
            svc.promote(c["id"], confirm=False)
        assert svc.get_production()["engine"] == "libretranslate"
        out = svc.promote(c["id"], confirm=True, reason="scored higher")
        assert out["production"]["engine"] == "nllb"
        assert out["default_engine_changed"] is True
        assert settings_service.get_default_engine() == "nllb"
        (d,) = svc.list_decisions()["decisions"]
        assert d["decision"] == "promoted" and d["scores"]["aggregate_score"] is not None
        with pytest.raises(ConflictError):
            svc.promote(c["id"], confirm=True)


class TestSchedule:
    def test_not_due_when_schedule_off(self, world):
        svc.add_candidate("nllb")
        assert svc.is_due() is False
        assert svc.run_if_due() is False

    def test_due_then_not_due_after_run(self, world):
        svc.add_candidate("nllb")
        svc.set_settings(True, 30, tier="public", set_name="g")
        assert svc.is_due() is True
        assert svc.run_if_due() is True
        _wait()
        assert svc.is_due() is False
        later = datetime.datetime.utcnow() + datetime.timedelta(days=31)
        assert svc.is_due(now=later) is True

    def test_scheduled_refusal_is_recorded_not_retried(self, world, monkeypatch):
        svc.add_candidate("nllb")
        svc.set_settings(True, 30, tier="public", set_name="nope")
        assert svc.run_if_due() is False
        assert "No benchmark cases" in svc.report()["error"]
        assert svc.is_due() is False

    def test_not_due_without_candidates(self, world):
        svc.set_settings(True, 30)
        assert svc.is_due() is False

    @pytest.mark.parametrize("days", [0, 366, "30"])
    def test_bad_interval(self, world, days):
        with pytest.raises(InvalidInputError):
            svc.set_settings(True, days)


class TestScheduler:
    def test_loop_calls_run_if_due_and_stops(self, monkeypatch):
        from api import background
        calls = []
        monkeypatch.setattr(svc, "run_if_due", lambda: calls.append(1) or False)
        assert background.start_reeval_scheduler(interval=0.02) is True
        try:
            assert background.start_reeval_scheduler(interval=0.02) is False
            end = time.time() + 2
            while not calls and time.time() < end:
                time.sleep(0.02)
        finally:
            background.stop_reeval_scheduler()
        assert calls


class TestReviewFixes:
    def test_failed_attempt_keeps_last_good_report(self, world):
        c = svc.add_candidate("nllb")["candidate"]
        _run_now()
        svc.set_settings(True, 1, tier="public", set_name="nope")
        db.set_app_setting(svc._LAST_RUN_KEY, __import__("json").dumps({
            **svc._last_run(), "started_at": "2000-01-01T00:00:00"}))
        assert svc.run_if_due() is False
        rep = svc.report()
        assert rep["error"] and rep["rows"] and rep["rows"][0]["candidate"]["id"] == c["id"]
        svc.reject(c["id"], "no")
        assert svc.list_decisions()["decisions"][0]["scores"]["aggregate_score"] is not None

    def test_tick_while_a_run_is_going_is_not_an_attempt(self, world):
        svc.add_candidate("nllb")
        svc.set_settings(True, 30, tier="public", set_name="g")
        with background_jobs._lock:
            background_jobs._jobs[lab.JOB_ID] = {"status": "running"}
        try:
            assert svc.run_if_due() is False
        finally:
            with background_jobs._lock:
                background_jobs._jobs.pop(lab.JOB_ID, None)
        assert svc.report()["error"] is None
        assert svc.is_due() is True

    def test_scheduled_cost_limit(self, world, monkeypatch):
        svc.add_candidate("nllb")
        svc.set_settings(True, 30, tier="public", set_name="g", max_cost_usd=0.0)
        monkeypatch.setattr(svc, "estimate_run", lambda capability="translation": {"estimated_cost_usd": 1.0})
        assert svc.run_if_due() is False
        assert "limit set for scheduled runs" in svc.report()["error"]

    def test_settings_change_wins_over_old_promotion(self, world):
        c = svc.add_candidate("nllb")["candidate"]
        _run_now()
        svc.promote(c["id"], confirm=True)
        assert svc.get_production()["source"] == "promoted"
        settings_service.set_settings({"default_engine": "libretranslate"})
        prod = svc.get_production()
        assert (prod["engine"], prod["source"]) == ("libretranslate", "settings")

    def test_candidate_equal_to_new_production_is_left_out(self, world):
        svc.add_candidate("nllb")
        settings_service.set_settings({"default_engine": "nllb"})
        with pytest.raises(UnsupportedOperationError):
            svc.estimate_run()

    def test_promoting_b_supersedes_a_which_can_be_reopened(self, world, monkeypatch):
        a = svc.add_candidate("nllb")["candidate"]
        _run_now()
        svc.promote(a["id"], confirm=True)
        monkeypatch.setitem(translate_engines.ENGINES, "ollama", GoodEngine)
        b = svc.add_candidate("ollama", "qwen3:8b")["candidate"]
        _run_now()
        svc.promote(b["id"], confirm=True)
        assert db.get_model_candidate(a["id"])["status"] == "superseded"
        assert svc.reopen_candidate(a["id"])["status"] == "candidate"

    def test_engine_without_model_picker_as_production(self, world):
        settings_service.set_settings({"default_engine": "deepseek"})
        prod = svc.get_production()
        cfg = lab._check_config("translation", {"engine": "deepseek", "model": prod["model"]})
        assert cfg == {"engine": "deepseek", "model": None}

    def test_first_scheduler_check_is_soon(self, monkeypatch):
        from api import background
        calls = []
        monkeypatch.setattr(svc, "run_if_due", lambda: calls.append(1) or False)
        monkeypatch.setattr(background, "REEVAL_FIRST_CHECK_SECONDS", 0.02)
        background.start_reeval_scheduler(interval=3600)
        try:
            end = time.time() + 2
            while not calls and time.time() < end:
                time.sleep(0.02)
        finally:
            background.stop_reeval_scheduler()
        assert calls
