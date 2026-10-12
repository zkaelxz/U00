"""Money limits for every paid loop that is not a translate run, and the one
usage-row helper behind them: a request that fails three times in a row, a
rate limit that outlasts its wait budget, a spent monthly cap, and unattended
work with no cap all end the work with a plain reason. Fakes only."""
import contextlib
import datetime
import threading
import time
from types import SimpleNamespace

import pytest

import background_jobs
import bulk_providers
import bulk_translate as bt
import db
import translate_engines as te
from core import Line
from engine_backends import llm_tasks, paid_calls, run_limits
from engine_backends.run_limits import TranslateRunStopped
from services import (benchmark_judge_service, benchmark_lab_service, live_follow_poller,
                      paid_job_service, review_jobs_service, settings_service,
                      translate_run_service, translate_service)
from services import workspace_job_service as wjs
from services.service_errors import UnsupportedOperationError

THREE_FAILED = "Stopped: 3 requests failed in a row"
CAP_REACHED = "Stopped: monthly cap reached"


class _Http(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


def _reply(text="[]", tokens=(10, 5)):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1]))


class FakeClaude:
    """Claude-shaped engine whose requests all fail (`fail`), are all
    rate-limited (`rate`), or succeed with an empty JSON array."""
    name = "claude"
    model = "claude-sonnet-5-5"
    supports_reference = True

    def __init__(self, fail=False, rate=False, reply="[]"):
        self.fail, self.rate, self.reply = fail, rate, reply
        self.requests = 0
        self.client = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **_kw):
        self.requests += 1
        if self.rate:
            raise _Http("429 slow down", 429)
        if self.fail:
            raise RuntimeError("the engine is down")
        return _reply(self.reply)


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda *a, **k: 50.0)
    llm_tasks._abandoned.clear()
    llm_tasks._abandoned_jobless.clear()
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


def _usage_rows():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM usage_log ORDER BY id")]


def _lines(n, translated=True):
    return [Line(idx=i, start=i, end=i + 1, zh=f"第{i}句", en=f"Line {i}." if translated else "",
                 id=100 + i) for i in range(n)]


def _drama():
    return db.create_drama(title_en="D", title_zh="D", status="translated")


def _run_job(job_id, target, *args):
    assert background_jobs.start_job(job_id, target, job_id, *args)
    assert background_jobs.wait_for_job_threads(30, [job_id])
    return background_jobs.get_status(job_id)


class TestRecordCall:
    def test_a_paid_call_is_logged_with_its_tokens_and_cost(self):
        engine = FakeClaude()
        assert paid_calls.record_call(engine, "claude", "op", {"input_tokens": 1000,
                                                              "output_tokens": 500}, drama_id=None)
        (row,) = _usage_rows()
        assert (row["engine"], row["model"], row["operation"]) == ("claude", engine.model, "op")
        assert (row["input_tokens"], row["output_tokens"]) == (1000, 500)
        assert row["estimated_cost_usd"] > 0

    def test_a_failed_paid_call_is_logged_with_no_tokens(self):
        assert paid_calls.record_call(FakeClaude(), "claude", "op", None)
        (row,) = _usage_rows()
        assert row["input_tokens"] == 0 and row["estimated_cost_usd"] == 0

    def test_a_free_engine_with_no_tokens_is_not_logged(self):
        local = SimpleNamespace(name="ollama", model="qwen3:8b")
        assert not paid_calls.record_call(local, "ollama", "op", {})
        assert _usage_rows() == []

    def test_a_free_engine_that_reports_tokens_is_logged(self):
        local = SimpleNamespace(name="ollama", model="qwen3:8b")
        assert paid_calls.record_call(local, "ollama", "op", {"input_tokens": 5, "output_tokens": 5})

    def test_a_hosted_ollama_model_is_logged_even_with_no_tokens(self):
        hosted = SimpleNamespace(name="ollama", model="gpt-oss:120b-cloud")
        assert paid_calls.record_call(hosted, "ollama", "op", {})


class TestCallLimits:
    def _call(self, engine, **kw):
        return llm_tasks.call_llm_json(engine, "p", fallback="[]", expect="json_array", **kw)

    def test_three_failed_requests_in_a_row_end_the_job_before_a_fourth_is_sent(self):
        engine = FakeClaude(fail=True)
        with paid_calls.call_limits(engine, "claude", "op") as limits:
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    self._call(engine)
            sent = engine.requests
            with pytest.raises(TranslateRunStopped, match="3 requests failed in a row"):
                self._call(engine)
        assert engine.requests == sent
        assert limits.stop_reason == THREE_FAILED

    def test_a_success_in_between_resets_the_streak(self):
        engine = FakeClaude(fail=True)
        with paid_calls.call_limits(engine, "claude", "op") as limits:
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    self._call(engine)
            engine.fail = False
            self._call(engine)
            engine.fail = True
            for _ in range(2):
                with pytest.raises(RuntimeError):
                    self._call(engine)
        assert limits.stop_reason is None

    def test_a_spent_cap_stops_the_job_before_any_request(self):
        engine = FakeClaude()
        with paid_calls.call_limits(engine, "claude", "op", cap_spent=lambda: True) as limits:
            with pytest.raises(TranslateRunStopped):
                self._call(engine)
        assert engine.requests == 0 and limits.stop_reason == CAP_REACHED

    def test_a_rate_limit_that_outlasts_the_wait_budget_ends_the_job(self, monkeypatch):
        monkeypatch.setattr(paid_calls, "RateLimitWaitBudget", lambda: run_limits.RateLimitWaitBudget(3.0))
        engine = FakeClaude(rate=True)
        with paid_calls.call_limits(engine, "claude", "op") as limits:
            with pytest.raises(TranslateRunStopped):
                self._call(engine)
            sent = engine.requests
            with pytest.raises(TranslateRunStopped):
                self._call(engine)
        assert engine.requests == sent
        assert limits.stop_reason.startswith("Stopped: the provider keeps rate-limiting you")
        assert "kept" not in limits.stop_reason  # the translate wording promises kept lines

    def test_log_usage_writes_a_row_per_call_failed_ones_too(self):
        good, bad = FakeClaude(), FakeClaude(fail=True)
        with paid_calls.call_limits(good, "claude", "reader_ask", drama_id=None, log_usage=True):
            self._call(good)
            with pytest.raises(RuntimeError):
                self._call(bad)
        rows = _usage_rows()
        assert [r["operation"] for r in rows] == ["reader_ask", "reader_ask"]
        assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (10, 5)
        assert rows[1]["input_tokens"] == 0

    def test_a_caller_with_its_own_usage_cb_is_not_logged_twice(self):
        engine, own = FakeClaude(), []
        with paid_calls.call_limits(engine, "claude", "op", log_usage=True):
            self._call(engine, usage_cb=lambda i, o, *_: own.append((i, o)))
        assert own == [(10, 5)] and _usage_rows() == []

    def test_logged_calls_inside_a_job_scope_uses_the_jobs_limits(self):
        engine = FakeClaude()
        with paid_calls.call_limits(engine, "claude", "job") as outer:
            with paid_calls.logged_calls(engine, "inner") as inner:
                assert inner is outer
        assert _usage_rows() == []

    def test_a_free_engine_with_no_tokens_writes_nothing(self):
        engine = FakeClaude()
        engine.name, engine.model = "ollama", "qwen3:8b"
        engine.client.messages.create = lambda **kw: SimpleNamespace(
            content=[SimpleNamespace(type="text", text="[]")], usage=None)
        with paid_calls.call_limits(engine, None, "op", log_usage=True):
            self._call(engine)
        assert _usage_rows() == []


class TestReviewJobsStop:
    """Each loop is run through its real job runner; a failing engine costs
    three failed requests (two attempts each), not one per batch."""

    @pytest.mark.parametrize("runner,batch", [(wjs.run_flag_job, 30), (wjs.run_consistency_job, 60)])
    def test_three_failed_requests_stop_a_loop_that_skips_failed_batches(self, runner, batch):
        did, engine = _drama(), FakeClaude(fail=True)
        status = _run_job("review_t", runner, did, _lines(batch * 5), engine, "claude")
        assert status["status"] == "done", status
        assert status["result"]["stopped"] == THREE_FAILED
        assert engine.requests == 6  # three calls, each tried twice; not five batches

    @pytest.mark.parametrize("name,args", [
        ("run_emotion_job", (False, "claude")), ("run_translation_notes_job", ("claude",))])
    def test_a_loop_that_gives_up_on_its_first_failure_sends_no_more(self, name, args):
        did, engine = _drama(), FakeClaude(fail=True)
        status = _run_job("first_fail", getattr(wjs, name), did, _lines(200), engine, *args)
        assert status["status"] == "error" and engine.requests == 2

    @pytest.mark.parametrize("name,args,field", [
        ("run_emotion_job", (False, "claude"), "emotions"),
        ("run_translation_notes_job", ("claude",), "note_count")])
    def test_a_rate_limit_or_spent_cap_ends_these_loops_with_a_reason(self, name, args, field,
                                                                       monkeypatch):
        monkeypatch.setattr(paid_calls, "RateLimitWaitBudget", lambda: run_limits.RateLimitWaitBudget(3.0))
        did, engine = _drama(), FakeClaude(rate=True)
        status = _run_job("rate_t", getattr(wjs, name), did, _lines(200), engine, *args)
        assert status["status"] == "done" and field in status["result"]
        assert status["result"]["stopped"].startswith("Stopped: the provider keeps rate-limiting")
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda *a, **k: 1.0)
        db.log_usage(None, "claude", engine.model, "earlier", 1, 1, 5.0)
        engine.requests = 0
        status = _run_job("cap_t", getattr(wjs, name), did, _lines(200), engine, *args)
        assert status["result"]["stopped"] == CAP_REACHED and engine.requests == 0

    def test_a_rate_limited_engine_stops_the_flag_pass_by_its_wait_budget(self, monkeypatch):
        monkeypatch.setattr(paid_calls, "RateLimitWaitBudget", lambda: run_limits.RateLimitWaitBudget(3.0))
        did, engine = _drama(), FakeClaude(rate=True)
        status = _run_job("flag_rate", wjs.run_flag_job, did, _lines(150), engine, "claude")
        assert status["result"]["stopped"].startswith("Stopped: the provider keeps rate-limiting")
        assert engine.requests <= 3

    def test_a_cap_spent_mid_run_stops_the_pass(self, monkeypatch):
        did, engine = _drama(), FakeClaude()
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda *a, **k: 1.0)
        db.log_usage(None, "claude", engine.model, "earlier", 1, 1, 5.0)
        status = _run_job("flag_cap", wjs.run_flag_job, did, _lines(90), engine, "claude")
        assert status["result"]["stopped"] == CAP_REACHED
        assert engine.requests == 0

    def test_the_flags_found_before_the_stop_are_kept(self):
        did, engine = _drama(), FakeClaude(reply='[{"line_idx": 0, "reason": "uncertain_translation", "note": "x"}]')
        lines = _lines(60)
        db.save_lines(did, lines)
        lines = db.load_line_objects(did)
        first = engine._create

        def flaky(**kw):
            if engine.requests >= 1:
                engine.fail = True
            return first(**kw)
        engine.client.messages.create = flaky
        status = _run_job("flag_partial", wjs.run_flag_job, did, lines, engine, "claude")
        assert status["status"] == "done"
        assert any(r["flag"] for r in db.load_lines(did))

    def test_a_clean_run_has_no_stopped_field(self):
        did, engine = _drama(), FakeClaude()
        status = _run_job("flag_ok", wjs.run_flag_job, did, _lines(30), engine, "claude")
        assert "stopped" not in status["result"]


class TestUnattendedNeedsACap:
    @pytest.fixture
    def no_cap(self, monkeypatch):
        monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda *a, **k: 0.0)

    def test_a_capped_engine_is_refused_without_a_cap(self, no_cap):
        for engine in ("claude", "deepseek", "gemini", "openai"):
            with pytest.raises(UnsupportedOperationError, match="Settings"):
                paid_job_service.require_cap_for_unattended(engine)

    def test_free_engines_and_gemini_free_tier_need_no_cap(self, no_cap):
        paid_job_service.require_cap_for_unattended("ollama")
        paid_job_service.require_cap_for_unattended("gemini", gemini_free_tier=True)

    def test_a_set_cap_lets_it_through(self):
        assert paid_job_service.require_monthly_cap(["claude", "ollama"]) == 50.0

    def test_a_bulk_batch_start_is_refused(self, no_cap, monkeypatch):
        did = db.create_drama(title_zh="D")
        db.save_lines(did, _lines(2))
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a, **k: "k")
        monkeypatch.setattr(te, "get_engine", lambda *a, **k: FakeClaude())
        with pytest.raises(UnsupportedOperationError, match="spending cap"):
            review_jobs_service.start_flag_review(did, engine_name="claude", bulk=True)
        assert db.list_bulk_jobs(did) == []

    def test_a_bulk_translate_start_is_refused(self, no_cap, monkeypatch):
        did = db.create_drama(title_zh="D", translation_engine="claude")
        db.save_lines(did, _lines(2, translated=False))
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a, **k: "k")
        monkeypatch.setattr(te, "get_engine", lambda *a, **k: FakeClaude())
        with pytest.raises(UnsupportedOperationError, match="spending cap"):
            translate_run_service.start_translate_run(did, engine_name="claude", bulk=True)
        assert db.list_bulk_jobs(did) == []

    def test_a_bulk_series_start_is_refused(self, no_cap):
        from services import library_admin_service as las
        did = db.create_drama(title_zh="D", status="aligned", translation_engine="claude")
        db.save_lines(did, _lines(2, translated=False))
        with pytest.raises(UnsupportedOperationError, match="spending cap"):
            las.start_bulk_translate([did])

    def test_the_series_job_itself_skips_a_paid_title_when_the_cap_was_removed(self, monkeypatch):
        did = db.create_drama(title_zh="D", status="aligned", translation_engine="claude")
        db.save_lines(did, _lines(2, translated=False))
        monkeypatch.setattr(wjs.translate_engines, "get_engine", lambda *a, **k: FakeClaude())
        started, results = [], {}
        monkeypatch.setattr(wjs.background_jobs, "start_job", lambda *a, **k: started.append(a) or False)
        monkeypatch.setattr(wjs.background_jobs, "set_result", lambda j, r: results.update(r))
        wjs.run_bulk_series_translate_job("series_t", [did], {"claude": "k"}, monthly_cap=0)
        assert results["skipped_cap"] == [did] and started == []

    def test_a_series_that_keeps_failing_stops_after_three_titles(self, monkeypatch):
        ids = []
        for _ in range(5):
            did = db.create_drama(title_zh="D", status="aligned", translation_engine="claude")
            db.save_lines(did, _lines(2, translated=False))
            ids.append(did)
        monkeypatch.setattr(wjs.translate_engines, "get_engine", lambda *a, **k: FakeClaude())
        monkeypatch.setattr(wjs.db, "get_month_spend", lambda: 0.0)
        seen = []

        def fake_start(job_id, target, *a, **kw):
            seen.append(job_id)
            background_jobs.set_result(job_id, {"errors": [{"error": "boom"}]})
            return True
        monkeypatch.setattr(wjs.background_jobs, "start_job", fake_start)
        monkeypatch.setattr(wjs.background_jobs, "get_status",
                            lambda j: {"status": "done", "result": {"errors": [{"error": "boom"}]}}
                            if j in seen else None)
        results = {}
        monkeypatch.setattr(wjs.background_jobs, "set_result", lambda j, r: results.update(r))
        wjs.run_bulk_series_translate_job("series_stop", ids, {"claude": "k"}, monthly_cap=50.0)
        assert len(seen) == 3 and results["stopped"] == "Stopped: 3 titles in a row failed"

    def test_an_automatic_live_start_is_refused(self, no_cap, monkeypatch):
        monkeypatch.setattr("services.live_service.list_sessions", lambda principal: [])
        assert "monthly spending cap" in live_follow_poller._gate_refusal("claude")
        assert live_follow_poller._gate_refusal("ollama") == ""


def _bulk_job(status="submitted", engine="claude"):
    did = _drama()
    bulk_id = db.create_bulk_job(did, engine, "m", status, [])
    db.update_bulk_job(bulk_id, provider_batch_id="batch_1")
    return did, bulk_id


class _DownProvider:
    def __init__(self):
        self.polls = 0

    def poll(self, batch_id):
        self.polls += 1
        raise RuntimeError("provider unreachable")


class TestBulkPoller:
    def test_a_poller_that_cannot_reach_the_provider_gives_up_with_a_plain_reason(self):
        _did, bulk_id = _bulk_job()
        provider = _DownProvider()
        bt.run_bulk_poller("poll_t", bulk_id, provider=provider, interval=0, sleep=lambda s: None)
        assert provider.polls == 3
        job = db.get_bulk_job(bulk_id)
        assert job["status"] == "submitted"  # still recoverable: Check now picks it up
        assert job["last_error"].startswith("Stopped checking: 3 status checks in a row failed")

    def test_a_batch_older_than_the_provider_keeps_it_is_failed(self):
        _did, bulk_id = _bulk_job()
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("UPDATE bulk_jobs SET submitted_at = ? WHERE id = ?",
                         ((datetime.datetime.utcnow() - datetime.timedelta(hours=60)).isoformat(), bulk_id))
            conn.commit()
        provider = _DownProvider()
        bt.run_bulk_poller("poll_old", bulk_id, provider=provider, interval=0, sleep=lambda s: None)
        job = db.get_bulk_job(bulk_id)
        assert provider.polls == 0 and job["status"] == "failed"
        assert "expired" in job["last_error"]

    def test_a_reachable_provider_resets_the_streak(self):
        guard = bulk_providers.PollGuard()
        _did, bulk_id = _bulk_job()
        assert guard.failed(bulk_id) == "" and guard.failed(bulk_id) == ""
        guard.reached()
        assert guard.failed(bulk_id) == "" and guard.failed(bulk_id) == ""
        assert guard.failed(bulk_id) != ""


class _OffPeakEngine:
    model = "deepseek-v4-flash"
    supports_reference = True
    checks_replies = False
    name = "deepseek"

    def __init__(self, on_batch):
        self.on_batch, self.seen, self.last_usage = on_batch, [], {}

    def translate_batch(self, zh_lines, context=None):
        self.seen.extend(zh_lines)
        self.last_usage = {"input_tokens": 10, "output_tokens": 5}
        self.on_batch()
        return [f"DS[{z}]" for z in zh_lines]


class TestOffPeakWindow:
    PEAK = datetime.datetime(2026, 9, 23, 2, 0)
    OFFPEAK = datetime.datetime(2026, 9, 23, 5, 0)

    def _schedule(self, n=3):
        did = db.create_drama(title_zh="D", status="aligned")
        db.save_lines(did, _lines(n, translated=False))
        lines = db.load_line_objects(did)
        return did, bt.schedule_offpeak_translation(
            did, lines, "deepseek", "deepseek-v4-flash", {"style_preset": "audio_drama", "batch_size": 1},
            now=self.PEAK)

    def test_the_run_stops_before_a_batch_once_the_window_has_closed(self):
        did, bulk_id = self._schedule()
        window = {"open": True}
        engine = _OffPeakEngine(lambda: window.update(open=False))
        summary = bt.run_scheduled_job(bulk_id, engine, window_open=lambda: window["open"])
        assert engine.seen == ["第0句"] and summary["window_closed"] is True
        assert [r["en"] for r in db.load_lines(did)] == ["DS[第0句]", "", ""]

    def test_a_run_that_finishes_inside_the_window_reports_it_open(self):
        _did, bulk_id = self._schedule(2)
        engine = _OffPeakEngine(lambda: None)
        summary = bt.run_scheduled_job(bulk_id, engine, window_open=lambda: True)
        assert summary["window_closed"] is False and len(engine.seen) == 2

    def test_a_closed_window_sends_the_job_back_to_waiting_for_the_next_one(self):
        did, bulk_id = self._schedule()
        clock = {"now": self.OFFPEAK}
        engine = _OffPeakEngine(lambda: clock.update(now=self.PEAK))

        class StopWaiting(Exception):
            pass

        def sleep(_seconds):
            raise StopWaiting

        with pytest.raises(StopWaiting):
            bt.run_bulk_poller("poll_win", bulk_id, engine=engine, sleep=sleep,
                               now_fn=lambda: clock["now"], monthly_cap_usd=50.0)
        job = db.get_bulk_job(bulk_id)
        assert job["status"] == "scheduled"
        assert datetime.datetime.fromisoformat(job["scheduled_for"]) > self.PEAK
        assert engine.seen == ["第0句"]


    def test_a_scheduled_run_is_refused_when_the_cap_was_removed_after_scheduling(self):
        did, bulk_id = self._schedule()
        engine = _OffPeakEngine(lambda: None)
        bt.run_bulk_poller("poll_nocap", bulk_id, engine=engine, sleep=lambda s: None,
                           now_fn=lambda: self.OFFPEAK)
        job = db.get_bulk_job(bulk_id)
        assert job["status"] == "failed" and "spending cap" in job["last_error"]
        assert engine.seen == []


class TestReflectIsNotChargedTwice:
    class Scripted:
        """Claude-shaped: answers each request from a script; an Exception
        entry is raised instead."""
        supports_reference = True
        name = "claude"
        model = "claude-sonnet-5-5"

        def __init__(self, script):
            self.script, self.prompts = list(script), []
            self.client = SimpleNamespace(messages=SimpleNamespace(create=self._create))

        def _create(self, model, max_tokens, messages, system=None):
            self.prompts.append(messages[0]["content"])
            step = self.script[len(self.prompts) - 1]
            if isinstance(step, Exception):
                raise step
            return _reply(step)

    def test_a_retried_batch_resumes_at_the_pass_that_failed(self):
        engine = self.Scripted([
            '{"1": "Draft."}', '{"1": "critique"}', RuntimeError("blip"), '{"1": "Final."}'])
        billed = []
        lines = [Line(idx=0, start=0, end=1, zh="你好", en="", id=1)]
        out, errors = te.translate_lines_with_engine(
            lines, engine, {}, batch_size=1, reflect=True, check_replies=False,
            usage_cb=lambda *a: billed.append(a))
        assert errors == [] and out[0].en == "Final."
        assert len(engine.prompts) == 4  # the retry sent only the rewrite
        assert "EXPRESSIVENESS" in engine.prompts[3]
        assert len(billed) == 3  # draft, critique, rewrite: each paid for once

    def test_without_a_resume_dict_every_pass_is_sent_again(self):
        engine = self.Scripted(['{"1": "Draft."}', '{"1": "c"}', '{"1": "Final."}'])
        te.reflect_translate_batch(engine, ["你好"], {})
        assert len(engine.prompts) == 3
        again = self.Scripted(['{"1": "Draft."}', '{"1": "c"}', '{"1": "Final."}',
                               '{"1": "Final."}'])
        done = {}
        te.reflect_translate_batch(again, ["你好"], {}, resume=done)
        assert set(done) == {"draft", "critique"}
        te.reflect_translate_batch(again, ["你好"], {}, resume=done)
        assert len(again.prompts) == 4 and "EXPRESSIVENESS" in again.prompts[3]


class TestBenchmarkStops:
    def _case(self, n=1):
        return {"id": n, "label": f"c{n}", "source_text": "你好", "source_language": "zh",
                "reference_text": "Hello"}

    def test_three_failed_cases_stop_that_engine_and_say_why(self, monkeypatch):
        engine = FakeClaude()
        engine.translate_batch = lambda texts, ctx: (_ for _ in ()).throw(RuntimeError("down"))
        monkeypatch.setattr(te, "get_engine", lambda *a, **k: engine)
        monkeypatch.setattr(benchmark_lab_service, "_offline_model_folder", lambda: None)
        cases = [{"id": db.create_benchmark_case(f"c{i}", "translation", "dialogue",
                                                 source_text="你好", reference_text="Hello")}
                 for i in range(5)]
        sid = db.create_benchmark_session({"label": "claude-run", "stage": "translation",
                                           "engine": "claude", "model": engine.model,
                                           "status": "queued", "case_count": 5})
        benchmark_lab_service._run_plan("bench_t", "translation", [(sid, {"engine": "claude",
                                        "model": engine.model}, "k")], [c["id"] for c in cases], False)
        session = db.get_benchmark_session(sid)
        assert session["status"] == "failed" and session["note"] == THREE_FAILED
        assert len(db.list_benchmark_results(sid)) == 3

    def test_a_hung_engine_is_given_up_on_when_the_run_is_cancelled(self, monkeypatch):
        from services import benchmark_case_service as cases_svc
        release = threading.Event()
        engine = FakeClaude()
        engine.translate_batch = lambda texts, ctx: release.wait(30) or ["x"]
        cancelled = {"now": False}
        started = time.monotonic()
        timer = threading.Timer(0.3, lambda: cancelled.update(now=True))
        timer.start()
        try:
            with llm_tasks.bounded_llm_calls("bench_cancel", lambda: cancelled["now"]):
                result = cases_svc.run_translation(engine, self._case(1))
        finally:
            release.set()
            timer.cancel()
        assert result["error"] and time.monotonic() - started < 10


class TestJudgePass:
    def test_a_judge_that_fails_three_cases_in_a_row_ends_the_pass(self, monkeypatch):
        calls = []

        def judge_case(engine, case, usable, usage=None):
            calls.append(case["id"])
            raise RuntimeError("judge down")
        monkeypatch.setattr(benchmark_judge_service, "judge_case", judge_case)
        sessions = [1]
        state = {1: {"status": "running", "note": None, "scores": {}, "cost_usd": 0.0,
                     "input_tokens": 0, "output_tokens": 0}}
        results = {1: {i: {"output_text": "x", "error": None, "case_id": i} for i in range(1, 7)}}
        cases = {i: {"id": i} for i in range(1, 7)}
        monkeypatch.setattr(benchmark_judge_service.lab, "_cap_applies", lambda e: False)
        benchmark_judge_service._judge_cases(
            "judge_t", sessions, {"engine": "claude"}, state, FakeClaude(), "k", results, cases,
            list(cases), time.monotonic() + 600, run_limits.FailureStreak())
        assert calls == [1, 2, 3]
        assert state[1]["status"] == "failed" and state[1]["note"] == THREE_FAILED

    def test_a_pass_past_its_deadline_is_cut_off(self, monkeypatch):
        state = {1: {"status": "running", "note": None, "scores": {}}}
        results = {1: {1: {"output_text": "x", "error": None, "case_id": 1}}}
        monkeypatch.setattr(benchmark_judge_service.lab, "_cap_applies", lambda e: False)
        benchmark_judge_service._judge_cases(
            "judge_late", [1], {"engine": "claude"}, state, FakeClaude(), "k", results, {1: {"id": 1}},
            [1], time.monotonic() - 1, run_limits.FailureStreak())
        assert state[1]["status"] == "failed" and "time limit" in state[1]["note"]


class TestOtherPaidPathsAreLogged:
    def test_the_sources_ai_fallback_logs_its_call_failed_or_not(self):
        from sources import ai_extract
        good = FakeClaude(reply='{"body": []}')
        assert ai_extract.ask_json(good, "prompt") == {"body": []}
        bad = FakeClaude(fail=True)
        with pytest.raises(RuntimeError):
            ai_extract.ask_json(bad, "prompt")
        rows = _usage_rows()
        assert [r["operation"] for r in rows] == ["sources_ai_fallback"] * 2
        assert rows[0]["input_tokens"] == 10 and rows[1]["input_tokens"] == 0

    def test_a_line_ai_action_logs_its_call(self):
        from services import line_ai_service
        did, engine = _drama(), FakeClaude(reply="Better.")
        out = line_ai_service.run(lambda: llm_tasks.call_llm_json(engine, "p", fallback="", expect="text"),
                                  (engine, "line_improve", did))
        assert out == "Better."
        (row,) = _usage_rows()
        assert (row["operation"], row["drama_id"]) == ("line_improve", did)

    def test_a_reader_call_that_fails_is_still_logged(self):
        from services import reader_service
        from services.service_errors import ServiceError
        did, engine = _drama(), FakeClaude(fail=True)
        with pytest.raises(ServiceError):
            reader_service._run_engine(
                lambda: llm_tasks.call_llm_json(engine, "p", fallback=""), (engine, "reader_ask", did))
        (row,) = _usage_rows()
        assert row["operation"] == "reader_ask" and row["input_tokens"] == 0

    def test_a_discover_call_is_logged(self):
        from services import discover_lookup_service
        engine = FakeClaude(reply='["x"]')
        with discover_lookup_service._metered(engine, "discover_translate_query"):
            llm_tasks.call_llm_json(engine, "p", fallback="[]")
        assert _usage_rows()[0]["operation"] == "discover_translate_query"

    def test_a_groq_transcription_is_logged_by_the_audio_hour(self):
        from services import transcribe_service
        did = _drama()
        transcribe_service._apply_transcription(
            "j", did, {"failed_reason": "groq", "detail": "x",
                       "groq_usage": {"seconds": 3600.0, "failed": False}},
            source_language="zh", whisper_size="small", use_gpu=False, transcript_mode="whisper",
            alignment_method=None, hf_token=None, diarize_audio_path=None, expected_speakers=None)
        (row,) = _usage_rows()
        assert (row["engine"], row["operation"]) == ("groq", "transcribe_groq")
        assert row["estimated_cost_usd"] == pytest.approx(0.04)

    def test_a_failed_groq_transcription_is_logged_at_no_cost(self):
        from services import transcribe_service
        did = _drama()
        outcome = {"failed_reason": "groq", "detail": "x",
                   "groq_usage": {"seconds": 3600.0, "failed": True}}
        result = transcribe_service._apply_transcription(
            "j", did, outcome, source_language="zh", whisper_size="small", use_gpu=False,
            transcript_mode="whisper", alignment_method=None, hf_token=None,
            diarize_audio_path=None, expected_speakers=None)
        assert "groq_usage" not in result  # the job result keeps its old shape
        (row,) = _usage_rows()
        assert row["estimated_cost_usd"] == 0
