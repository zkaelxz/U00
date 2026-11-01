"""
The Benchmark Lab's translation settings sweep (services/benchmark_translate_sweep_service.py):
coordinate descent over batch size, context, Reflect and style with the default repeated for noise,
the hold-out check and margin rule, resuming by skipping finished passes, the estimate and cap on a
paid engine, and the owner's approve/reject. Mocked: the engine is a fake whose output depends on
the batch and context it is given.
"""
import json

import pytest

import background_jobs
import db
from engine_backends import engine_registry, pricing
from engine_backends import local as engine_local
from services import benchmark_lab_service as lab
from services import benchmark_sweep_core as sweep_core
from services import benchmark_translate_sweep_service as tsweep
from services import translate_service
from lib.errors import (ConflictError, InvalidInputError, MissingKeyError,
                                     UnsupportedOperationError)
from tests.job_helpers import wait_for_job

ENGINE = "claude"


class FakeEngine:
    """Gets a line right only when its batch holds at most two lines; a longer batch garbles it."""
    name = "claude"
    supports_reference = True
    calls = []
    fail = False

    def __init__(self, api_key=None, model="claude-sonnet-5", **kw):
        self.model = model
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        type(self).calls.append(len(zh_lines))
        if type(self).fail:
            raise RuntimeError("engine down")
        self.last_usage = {"input_tokens": 500, "output_tokens": 500}
        return [f"EN {z}" if len(zh_lines) <= 2 else f"EN {z} garbled words" for z in zh_lines]


@pytest.fixture(autouse=True)
def _env(monkeypatch, isolated_db):
    background_jobs.clear_all_jobs()
    tsweep._store.active["id"] = None
    monkeypatch.setitem(engine_registry.ENGINES, ENGINE, FakeEngine)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: "sk-test-key-12345" if name == ENGINE else None)
    # Small ranges keep the sweep short; the winner is a batch of two.
    monkeypatch.setattr(tsweep, "KNOBS", (("batch_size", "Batch size", (2,)),
                                          ("context_window", "Context lines before", (0, 3))))
    # Ollama's model list is read from its server; no test may depend on one running here.
    monkeypatch.setattr(engine_local, "pulled_ollama_models", lambda url: [])
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda url: True)
    FakeEngine.calls, FakeEngine.fail = [], False
    yield
    background_jobs.clear_all_jobs()


def _cases(titles=("A", "B", "C", "D"), per_title=6, language="zh", set_name="s", tags=None):
    rows = []
    for t in titles:
        for i in range(1, per_title + 1):
            rows.append({"label": f"{t}{i}", "stage": "translation", "content_type": "audio_drama",
                         "source_language": language, "source_text": f"{t}{i}", "reference_text": f"EN {t}{i}",
                         "tier": "application", "set_name": set_name, "title": t, "scene_order": i,
                         "tags": json.dumps(tags or ["quiet"])})
    return db.create_benchmark_clip_cases(rows)


def _sweep(**kw):
    tsweep.start("s", "zh", ENGINE, **kw)
    wait_for_job(tsweep.JOB_ID, timeout=30)
    background_jobs.clear_all_jobs()
    return tsweep.get_sweep()["sweep"]


class TestSweep:
    def test_coordinate_descent_finds_the_winner_and_the_hold_out_confirms_it(self):
        _cases()
        rec = _sweep()
        assert rec["status"] == "done", rec["note"]
        r = rec["recommendation"]
        assert r["state"] == "adopt"
        assert r["winner"]["changes"] == {"batch_size": 2} and r["noise"] == 0.0
        assert r["margin"] == tsweep.MIN_SCORE_MARGIN and "1.5 chrF points" in r["margin_rule"]
        assert r["holdout"]["winner_score"] > r["holdout"]["default_score"]
        noise = [s for s in r["steps"] if s["step"] == "noise"]
        assert len(noise) == 2 and noise[0]["run_id"] != noise[1]["run_id"]
        assert [s["kept"] for s in r["steps"] if s["step"] == "batch_size"] == [True]

    def test_only_tuning_cases_pick_and_the_hold_out_only_confirms(self):
        _cases()
        rec = _sweep()
        holdout_ids = {c["id"] for c in sweep_core.split_by_title(tsweep._sweep_cases("s", "zh"))[1]}
        for step in rec["recommendation"]["steps"]:
            ids = {r["case_id"] for r in db.list_benchmark_results(step["run_id"])}
            assert (ids <= holdout_ids) == (step["half"] == "holdout")

    def test_every_pass_is_a_scene_run_that_records_its_options(self):
        _cases()
        rec = _sweep()
        run = db.get_benchmark_session(rec["recommendation"]["steps"][0]["run_id"])
        ctx = json.loads(run["context_settings"])
        assert ctx["translate_scenes"] is True and ctx["translate_options"]["series_id"] is None

    def test_nothing_better_keeps_the_default_and_runs_no_hold_out(self, monkeypatch):
        monkeypatch.setattr(FakeEngine, "translate_batch",
                            lambda self, zh, ctx: [f"EN {z}" for z in zh])
        _cases()
        r = _sweep()["recommendation"]
        assert r["state"] == "keep_default" and r["holdout"] is None
        assert not any(s["half"] == "holdout" for s in r["steps"])

    def test_a_gain_inside_the_noise_is_not_taken(self, monkeypatch):
        turn = {"n": 0}

        def noisy(self, zh, ctx):
            # Every other batch is garbled, whatever the settings: the same setup scores differently twice.
            turn["n"] += 1
            return [f"EN {z}" if turn["n"] % 2 else "garbled" for z in zh]
        monkeypatch.setattr(FakeEngine, "translate_batch", noisy)
        _cases()
        r = _sweep()["recommendation"]
        assert r["noise"] > 0 and r["state"] in ("keep_default", "not_confirmed")
        assert r["margin"] == pytest.approx(max(tsweep.MIN_SCORE_MARGIN, 2 * r["noise"]))

    def test_reflect_is_swept_only_for_an_engine_that_follows_instructions(self, monkeypatch):
        monkeypatch.setattr(tsweep, "KNOBS", tsweep.KNOBS + (("reflect", "Reflect", (False, True)),))
        _cases()
        claude = tsweep.estimate("s", "zh", ENGINE)
        assert "reflect" in [k["key"] for k in claude["knobs"]]
        fake = tsweep.estimate("s", "zh", "fake")
        assert "reflect" not in [k["key"] for k in fake["knobs"]]

    def test_examples_need_a_series_and_a_temperature_needs_an_engine_that_takes_one(self, monkeypatch):
        monkeypatch.setattr(tsweep, "KNOBS", tsweep.KNOBS + (
            ("memory_examples", "Translation-memory examples", (False, True)), ("temperature", "Temperature", (None, 0.3))))
        _cases()
        keys = lambda est: [k["key"] for k in est["knobs"]]
        assert "memory_examples" not in keys(tsweep.estimate("s", "zh", ENGINE))
        assert "temperature" not in keys(tsweep.estimate("s", "zh", ENGINE))
        series_id = db.create_series("Saga")
        assert "memory_examples" in keys(tsweep.estimate("s", "zh", ENGINE, series_id=series_id))
        assert "temperature" in keys(tsweep.estimate("s", "zh", "ollama"))

    def test_a_winner_a_profile_cannot_hold_is_not_offered_for_approval(self, monkeypatch):
        _cases()
        plan = tsweep._Plan("s", "zh", ENGINE, None, None)
        base, win = {"id": 1, "aggregate_score": 0.5}, {"id": 2, "aggregate_score": 0.9}

        def recommend(changes, model=None):
            outcome = {"noise": 0.0, "default_score": 0.5, "winner_score": 0.9, "steps": [],
                       "winner": {"engine": ENGINE, "model": model, "changes": changes}, "holdout": (base, win)}
            return tsweep._recommend(plan, outcome)["state"]
        assert recommend({"temperature": 0.3}) == "not_confirmed"
        assert recommend({"temperature": 0.3, "batch_size": 15}) == "adopt"
        assert recommend({"temperature": 0.3}, model="claude-haiku-5") == "adopt"

    def test_a_winner_that_held_up_but_a_profile_cannot_hold_says_so(self):
        _cases()
        plan = tsweep._Plan("s", "zh", ENGINE, None, None)
        outcome = {"noise": 0.0, "default_score": 0.5, "winner_score": 0.9, "steps": [],
                   "winner": {"engine": ENGINE, "model": None, "changes": {"temperature": 0.3}},
                   "holdout": ({"id": 1, "aggregate_score": 0.5}, {"id": 2, "aggregate_score": 0.9})}
        text = tsweep._recommend(plan, outcome)["text"]
        assert "smaller than the margin" not in text and "a profile can't save" in text

    def test_on_ollama_only_pulled_local_models_are_tried(self, monkeypatch):
        _cases()
        monkeypatch.setattr(engine_local, "pulled_ollama_models", lambda url: ["qwen3:8b", "local-a"])
        monkeypatch.setattr(translate_service, "list_engines", lambda **k: [
            {"name": "ollama", "models": ["qwen3:8b", "local-a", "not-pulled:7b", "gemma4:31b-cloud"]}])
        monkeypatch.setattr(tsweep.lab, "check_config", lambda stage, cfg: {**cfg, "options": cfg["options"] or {}})
        monkeypatch.setattr(tsweep.lab, "run_context", lambda *a, **k: {"app_version": "1"})
        plan = tsweep._Plan("s", "zh", "ollama", "qwen3:8b", None)
        by_model = {m["model"]: m for m in plan.models}
        assert by_model["local-a"]["included"]
        assert not by_model["not-pulled:7b"]["included"] and "isn't pulled" in by_model["not-pulled:7b"]["reason"]
        assert not by_model["gemma4:31b-cloud"]["included"] and "off this PC" in by_model["gemma4:31b-cloud"]["reason"]
        tried = []
        sweep_core.descend(plan, lambda setup, half, repeat, step: tried.append(setup["model"]))
        assert "gemma4:31b-cloud" not in tried and "not-pulled:7b" not in tried and "local-a" in tried

    def test_with_ollama_down_its_models_are_left_out_for_that_reason(self, monkeypatch):
        _cases()
        monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda url: False)
        assert all(m["reason"] == "Could not reach Ollama." for m in tsweep._model_choices("ollama", ["local-a"]))

    def test_a_series_is_part_of_every_pass_and_never_swept(self):
        series_id = db.create_series("Saga")
        _cases()
        rec = _sweep(series_id=series_id)
        for step in rec["recommendation"]["steps"]:
            ctx = json.loads(db.get_benchmark_session(step["run_id"])["context_settings"])
            assert ctx["translate_options"]["series_id"] == series_id
        assert "series_id" not in rec["recommendation"]["winner"]["changes"]

    def test_a_translation_sweep_never_clears_models_another_job_loaded(self, monkeypatch):
        released = []
        monkeypatch.setattr("whisper_models.release_gpu_models", lambda: released.append(1))
        _cases()
        _sweep()
        assert not released

    def test_every_pass_is_held_to_a_job_cap_above_the_estimate(self, monkeypatch):
        caps = []
        real = lab._run_plan
        monkeypatch.setattr(lab, "_run_plan", lambda *a, **k: caps.append(a[5]) or real(*a, **k))
        _cases()
        est = tsweep.estimate("s", "zh", ENGINE)
        _sweep()
        assert caps and all(c is not None and c > 0 for c in caps)
        assert caps[0] == pytest.approx(est["estimated_cost_usd"] * tsweep.COST_MARGIN + tsweep.COST_MARGIN_FLOOR_USD)
        assert caps == sorted(caps, reverse=True)

    def test_a_pass_with_failed_cases_stops_the_sweep_instead_of_being_scored(self, monkeypatch):
        def flaky(self, zh, ctx):
            return ["" if z == "A1" else f"EN {z}" for z in zh]
        monkeypatch.setattr(FakeEngine, "translate_batch", flaky)
        _cases()
        rec = _sweep()
        assert rec["status"] == "failed" and "failed cases" in rec["note"]
        assert rec["recommendation"] is None

    def test_one_title_cannot_be_split(self):
        _cases(titles=("A",))
        with pytest.raises(UnsupportedOperationError, match="at least 2 titles"):
            tsweep.estimate("s", "zh", ENGINE)

    def test_only_the_sweeps_language_counts(self):
        _cases()
        _cases(titles=("J",), language="ja")
        assert {c["source_language"] for c in tsweep._sweep_cases("s", "zh")} == {"zh"}

    def test_an_unknown_engine_or_model_is_refused_like_a_lab_run(self):
        _cases()
        with pytest.raises(InvalidInputError):
            tsweep.estimate("s", "zh", "nope")
        with pytest.raises(InvalidInputError):
            tsweep.estimate("s", "zh", ENGINE, model="not-a-model")


class TestStopAndResume:
    def test_starting_again_reuses_every_finished_pass(self):
        _cases()
        first = _sweep()
        assert first["passes_reused"] == 0
        calls = len(FakeEngine.calls)
        again = _sweep()
        assert again["passes_run"] == 0 and again["passes_reused"] == first["passes_run"]
        assert len(FakeEngine.calls) == calls
        assert again["recommendation"]["winner"] == first["recommendation"]["winner"]

    def test_a_cancelled_sweep_resumes_without_redoing_finished_passes(self, monkeypatch):
        _cases()
        real = background_jobs.is_cancel_requested
        monkeypatch.setattr(background_jobs, "is_cancel_requested",
                            lambda job_id: len(db.list_benchmark_sessions(100, "translation")) >= 3)
        stopped = _sweep()
        assert stopped["status"] == "cancelled" and stopped["passes_run"] == 2
        monkeypatch.setattr(background_jobs, "is_cancel_requested", real)
        resumed = _sweep()
        assert resumed["status"] == "done" and resumed["passes_reused"] == 2

    def test_a_failed_pass_stops_the_sweep_and_keeps_the_finished_ones(self):
        _cases()
        FakeEngine.fail = True
        out = _sweep()
        assert out["status"] == "failed" and "did not finish" in out["note"]
        assert out["passes_run"] == 0

    def test_an_edited_reference_is_not_skipped(self):
        ids = _cases()
        first = _sweep()
        db.update_benchmark_case_texts(ids[0], "A1", "EN A1 reworded")
        again = _sweep()
        assert again["passes_run"] > 0 and again["passes_reused"] < first["passes_run"] + 1

    def test_a_refused_start_leaves_the_waiting_recommendation_alone(self, monkeypatch):
        _cases()
        _sweep()
        before = tsweep._store.read()
        monkeypatch.setattr(tsweep.lab, "_job_active", lambda: True)
        with pytest.raises(ConflictError):
            tsweep.start("s", "zh", ENGINE)
        assert tsweep._store.read() == before


class TestMoney:
    def test_the_estimate_counts_the_passes_and_prices_a_paid_engine(self):
        _cases()
        est = tsweep.estimate("s", "zh", ENGINE)
        assert est["passes_to_run"] > 0 and est["estimated_cost_usd"] > 0 and est["cap_applies"]
        assert est["tuning_cases"] + est["holdout_cases"] == 24
        done = _sweep()
        assert tsweep.estimate("s", "zh", ENGINE)["passes_to_run"] == 0 and done["status"] == "done"

    def test_a_free_engine_costs_nothing_and_has_no_cap(self):
        _cases()
        est = tsweep.estimate("s", "zh", "fake")
        assert est["estimated_cost_usd"] == 0 and not est["cap_applies"]

    def test_a_sweep_over_the_monthly_cap_is_refused_whatever_the_client_showed(self, monkeypatch):
        _cases()
        monkeypatch.setattr(tsweep.settings_service, "get_monthly_cap_usd", lambda: 0.0000001)
        with pytest.raises(UnsupportedOperationError, match="more than the"):
            tsweep.start("s", "zh", ENGINE)
        assert FakeEngine.calls == [] and tsweep.get_sweep() == {"sweep": None}

    def test_a_used_up_month_is_refused(self, monkeypatch):
        _cases()
        monkeypatch.setattr(tsweep.settings_service, "get_monthly_cap_usd", lambda: 1.0)
        monkeypatch.setattr(db, "get_month_spend", lambda **k: 5.0)
        with pytest.raises(UnsupportedOperationError):
            tsweep.start("s", "zh", ENGINE)

    def test_a_missing_key_is_refused_before_anything_runs(self, monkeypatch):
        _cases()
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: None)
        with pytest.raises(MissingKeyError):
            tsweep.start("s", "zh", ENGINE)

    def test_spend_counts_toward_the_monthly_cap(self):
        _cases()
        before = db.get_month_spend()
        _sweep()
        assert db.get_month_spend() > before

    def test_the_cap_stops_a_pass_and_the_sweep_says_so(self, monkeypatch):
        _cases()
        calls = {"n": 0}
        real = pricing.resolve_cost_cap

        def tight(job_left, monthly, spend):
            calls["n"] += 1
            return (0.0000001, None) if calls["n"] > 1 else real(job_left, monthly, spend)
        monkeypatch.setattr(pricing, "resolve_cost_cap", tight)
        out = _sweep()
        assert out["status"] == "failed" and "did not finish" in out["note"]


class TestDecision:
    def test_nothing_is_applied_before_the_owner_decides(self):
        _cases()
        rec = _sweep()
        assert db.list_presets() == [] and db.list_model_decisions(tsweep.DECISION_CAPABILITY) == []
        by_key = {a["key"]: a["value"] for a in rec["applies"]}
        assert by_key["batch_size"] == "2" and by_key["engine"] == ENGINE

    def test_approve_saves_a_profile_and_records_the_decision(self):
        _cases()
        _sweep()
        out = tsweep.decide("approve", "smaller batches read better")["sweep"]["decision"]
        assert out["decision"] == "promoted"
        preset = next(p for p in db.list_presets() if p["id"] == out["profile"]["id"])
        options = json.loads(preset["options_json"])
        assert options["batch_size"] == 2 and options["reflect"] is False
        (d,) = db.list_model_decisions(tsweep.DECISION_CAPABILITY)
        assert d["decision"] == "promoted" and json.loads(d["scores"])["holdout"]["winner_score"] > 0
        # Model re-evaluation's own list stays its own.
        assert db.list_model_decisions("translation") == []
        assert tsweep.list_decisions()["decisions"][0]["setup"].endswith("Batch size 2")

    def test_reject_records_and_saves_nothing(self):
        _cases()
        _sweep()
        assert tsweep.decide("reject")["sweep"]["decision"]["decision"] == "rejected"
        assert db.list_presets() == []

    def test_a_decision_is_made_once(self):
        _cases()
        _sweep()
        tsweep.decide("reject")
        with pytest.raises(ConflictError):
            tsweep.decide("approve")

    def test_a_winner_that_did_not_hold_up_cannot_be_approved(self):
        _cases()
        _sweep()
        record = tsweep._store.read()
        record["recommendation"]["state"] = "not_confirmed"
        tsweep._store.save(record)
        with pytest.raises(UnsupportedOperationError, match="did not hold up"):
            tsweep.decide("approve")

    def test_nothing_is_decided_before_a_sweep_finishes(self):
        with pytest.raises(UnsupportedOperationError):
            tsweep.decide("approve")

    def test_a_winner_with_memory_examples_can_be_approved(self):
        series_id = db.create_series("Saga")
        _cases()
        _sweep(series_id=series_id)
        record = tsweep._store.read()
        record["recommendation"]["winner"]["changes"] = {"batch_size": 2, "memory_examples": True}
        tsweep._store.save(record)
        applies = tsweep.get_sweep()["sweep"]["applies"]
        assert isinstance(applies, list) and {a["key"]: a["value"] for a in applies}["batch_size"] == "2"
        out = tsweep.decide("approve")["sweep"]["decision"]
        preset = next(p for p in db.list_presets() if p["id"] == out["profile"]["id"])
        assert json.loads(preset["options_json"])["batch_size"] == 2

    def test_a_stored_winner_the_profile_rules_no_longer_accept_shows_no_list(self):
        _cases()
        _sweep()
        record = tsweep._store.read()
        record["recommendation"]["winner"]["changes"]["gone_setting"] = 1
        tsweep._store.save(record)
        assert tsweep.get_sweep()["sweep"]["applies"] is None


def test_a_sweep_the_app_stopped_during_reads_interrupted():
    tsweep._store.save({"status": "running", "set_name": "s", "language": "zh"})
    assert tsweep.get_sweep()["sweep"]["status"] == "interrupted"


def test_a_sweep_cancelled_while_queued_reads_cancelled(monkeypatch):
    tsweep._store.save({"id": "x", "status": "queued", "set_name": "s", "language": "zh"})
    tsweep._store.active["id"] = "x"
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {"status": "cancelled"})
    out = tsweep.get_sweep()["sweep"]
    assert out["status"] == "cancelled" and "app stopped" not in out["note"]


def test_a_sweep_whose_job_ended_without_finishing_reads_interrupted(monkeypatch):
    tsweep._store.save({"id": "x", "status": "running", "set_name": "s", "language": "zh"})
    tsweep._store.active["id"] = "x"
    monkeypatch.setattr(background_jobs, "get_status", lambda job_id: {"status": "done"})
    out = tsweep.get_sweep()["sweep"]
    assert out["status"] == "interrupted" and "job ended" in out["note"]


def test_a_crash_marks_the_sweep_failed_and_its_unfinished_run_stopped(monkeypatch):
    _cases()

    def crash(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(lab, "_run_plan", crash)
    tsweep.start("s", "zh", ENGINE)
    st = wait_for_job(tsweep.JOB_ID, timeout=30)
    assert st["status"] == "error"
    (sid,) = st["result"]["session_ids"]
    run = db.get_benchmark_session(sid)
    # The unfinished run keeps the reason it stopped, not only that it did.
    assert run["status"] == "failed" and run["note"] == "The run stopped: boom"
    rec = tsweep.get_sweep()["sweep"]
    assert rec["status"] == "failed" and "boom" in rec["note"] and rec["finished_at"]
    assert tsweep._store.active["id"] is None


def test_a_missing_key_at_run_time_fails_the_sweep_and_runs_nothing(monkeypatch):
    _cases()
    keys = iter(["sk-test-key-12345"])
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: next(keys, None))
    tsweep.start("s", "zh", ENGINE)
    st = wait_for_job(tsweep.JOB_ID, timeout=30)
    assert st["result"]["session_ids"] == []
    assert tsweep.get_sweep()["sweep"]["status"] == "failed"


def test_earlier_decisions_name_the_setup_that_was_decided(monkeypatch):
    _cases()
    rec = _sweep()
    assert rec["recommendation"]["state"] == "adopt"
    tsweep.decide("reject", "not now")
    (d,) = tsweep.list_decisions()["decisions"]
    assert d["decision"] == "rejected" and d["reason"] == "not now" and d["language"] == "zh"
    # A sweep on the engine's default model stores no model, so the engine names it.
    assert d["setup"] == "claude, Batch size 2"
