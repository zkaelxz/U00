"""Live with a paid engine: refused at start once the monthly cap is spent,
stopped mid-session when it is crossed, every translation request logged, and
stopped after repeated failures."""
import contextlib
import time

import pytest

import background_jobs
import db
import live_translate
from live_cue_translation import CueTranslator
from services import live_service, translate_run_service, translate_service
from services.service_errors import UnsupportedOperationError
from tests.test_live_service import _start, _terminal, live  # noqa: F401  (the live fixture)


class _PaidEngine:
    name = "claude"
    model = "claude-sonnet-4-5"
    supports_reference = True
    free_tier = False

    def __init__(self, script=("ok",), tokens=(1000, 200)):
        self.script = list(script)
        self.calls = 0
        self.tokens = tokens
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, context):
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if step == "fail":
            raise RuntimeError("provider down https://api.example.com/v1?key=sk-ant-api03-abcdefghijklmnop12")
        self.last_usage = {"input_tokens": self.tokens[0], "output_tokens": self.tokens[1]}
        return [f"en {t}" for t in texts]


def _usage_rows():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT engine, model, operation, input_tokens, output_tokens, estimated_cost_usd "
            "FROM usage_log ORDER BY id")]


@pytest.fixture
def guard_env(isolated_db, monkeypatch):
    stops = []
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
    return stops


def _guard(engine, stops):
    from services import live_spend_service
    return live_spend_service.LiveSpendGuard(engine, "claude", stops.append)


def _spend(usd):
    db.log_usage(None, "claude", "claude-sonnet-4-5", "other", 1, 1, usd)


class TestStartRefusal:
    def test_a_paid_engine_is_refused_when_the_cap_is_spent(self, live, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        _spend(1.5)
        with pytest.raises(UnsupportedOperationError):
            _start(engine="claude", model=next(iter(translate_service.ENGINE_MODEL_DICTS["claude"])))
        assert live_service._sessions == {}

    def test_a_local_engine_is_not_refused(self, live, monkeypatch):
        import translate_engines
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        monkeypatch.setattr(translate_engines, "check_ollama_model_installed", lambda *a: None)
        monkeypatch.setattr(live_translate, "run_live_job", lambda *a, **k: None)
        _spend(1.5)
        assert _terminal(_start(engine="ollama"))

    def test_the_session_folder_is_reaped_as_before(self, live, monkeypatch):
        import os
        seen = {}

        def run(job_id, url, out_dir, *a, spend_guard=None, **k):
            seen["dir"] = out_dir
            seen["guard"] = spend_guard
        monkeypatch.setattr(live_translate, "run_live_job", run)
        sid = _start(engine="claude", model=next(iter(translate_service.ENGINE_MODEL_DICTS["claude"])))
        assert _terminal(sid)
        assert seen["guard"] is not None
        assert _wait_gone(seen["dir"]) and not os.path.exists(seen["dir"])


def _wait_gone(path, timeout=5.0):
    import os
    end = time.time() + timeout
    while time.time() < end and os.path.exists(path):
        time.sleep(0.02)
    return not os.path.exists(path)


class TestDuringTheSession:
    def test_every_request_is_logged_failed_or_not(self, guard_env):
        engine = _PaidEngine(["ok", "fail", "ok"])
        t = CueTranslator(engine, "zh", spend_guard=_guard(engine, guard_env))
        t.translate("a")
        t.translate("b")
        t.translate_chunk(["c", "d"])
        rows = _usage_rows()
        assert [r["operation"] for r in rows] == ["live_translate"] * 3
        assert [r["input_tokens"] for r in rows] == [1000, 0, 1000]
        assert rows[0]["estimated_cost_usd"] > 0 and rows[1]["estimated_cost_usd"] == 0

    def test_a_free_local_engine_is_not_logged(self, guard_env):
        engine = _PaidEngine()
        engine.name, engine.model = "ollama", "qwen3:8b"
        from services import live_spend_service
        guard = live_spend_service.LiveSpendGuard(engine, "ollama", guard_env.append)
        CueTranslator(engine, "zh", spend_guard=guard).translate("a")
        assert _usage_rows() == []

    def test_the_session_stops_when_the_cap_is_crossed_between_chunks(self, guard_env):
        engine = _PaidEngine()
        t = CueTranslator(engine, "zh", spend_guard=_guard(engine, guard_env))
        t.translate("a")
        _spend(2.0)
        with pytest.raises(background_jobs.JobCancelled):
            t.translate_chunk(["b", "c"])
        assert engine.calls == 1
        assert len(guard_env) == 1 and "cap" in guard_env[0].lower()
        with pytest.raises(background_jobs.JobCancelled):
            t.translate("d")
        assert engine.calls == 1 and len(guard_env) == 1

    def test_three_failed_requests_in_a_row_stop_the_session(self, guard_env):
        engine = _PaidEngine(["fail"])
        t = CueTranslator(engine, "zh", spend_guard=_guard(engine, guard_env))
        for text in "abc":
            t.translate(text)
        assert len(guard_env) == 1
        reason = guard_env[0]
        assert "3" in reason and "provider down" in reason
        for leak in ("https://", "api.example.com", "sk-ant"):
            assert leak not in reason
        with pytest.raises(background_jobs.JobCancelled):
            t.translate("d")
        assert engine.calls == 3

    def test_a_success_resets_the_failure_count(self, guard_env):
        engine = _PaidEngine(["fail", "fail", "ok"])
        t = CueTranslator(engine, "zh", spend_guard=_guard(engine, guard_env))
        for text in "abcdef":
            t.translate(text)
        assert guard_env == [] and engine.calls == 6

    def test_a_service_session_stops_with_the_reason_in_its_notes(self, live, monkeypatch):
        engine = _PaidEngine(["fail"])
        import translate_engines
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: engine)

        def run(job_id, url, out_dir, *a, spend_guard=None, **k):
            t = CueTranslator(engine, "zh", spend_guard=spend_guard)
            for text in "abcd":
                if background_jobs.is_cancel_requested(job_id):
                    break
                t.translate(text)
        monkeypatch.setattr(live_translate, "run_live_job", run)
        sid = _start(engine="claude", model=next(iter(translate_service.ENGINE_MODEL_DICTS["claude"])))
        assert _terminal(sid)
        notes = live_service.get_session(sid)["notes"]
        assert any("in a row" in n for n in notes)
        assert engine.calls == 3
