"""Review fixes for the run-stop rules and the extension's text capture:
a run the rate-limit budget stopped sends no more paid requests, and the
browser extension's text translation honours the monthly cap and is logged."""
import contextlib

import pytest

import db
import page_server
import translate_engines as te
from core import Line
from engine_backends import run_limits
from engine_backends.prompts import PRONOUN_DEFAULT_MARKER
from engine_backends.shared import call_with_backoff
from services import translate_run_service


class _Http(Exception):
    def __init__(self, msg, status):
        super().__init__(msg)
        self.status_code = status


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _lines(n):
    return [Line(idx=i, start=i, end=i + 1, zh=f"zh{i}", en="", id=10 + i) for i in range(n)]


class TestBudgetStopIsFinal:
    def test_a_refused_wait_leaves_nothing_in_the_budget(self):
        budget = run_limits.RateLimitWaitBudget(10.0)
        with pytest.raises(run_limits.TranslateRunStopped):
            budget.charge(30.0)
        with pytest.raises(run_limits.TranslateRunStopped):
            budget.charge(1.0)

    def test_call_with_backoff_never_retries_an_error_that_stops_the_run(self):
        calls = []

        def fn():
            calls.append(1)
            raise run_limits.TranslateRunStopped("stop")
        with pytest.raises(run_limits.TranslateRunStopped):
            call_with_backoff(fn)
        assert calls == [1]

    def test_reflect_sends_no_request_after_the_budget_stopped_the_run(self, monkeypatch):
        class Reflecting:
            supports_reference = True
            model = "fake-model"
            name = "reflecting"

            def __init__(self):
                self.client = self
                self.messages = self
                self.calls = 0

            def create(self, model, max_tokens, messages, system=None):
                self.calls += 1
                raise _Http("429 slow down", 429)

        engine = Reflecting()
        at_stop = []
        real_charge = run_limits.RateLimitWaitBudget.charge

        def spy(self, delay):
            try:
                real_charge(self, delay)
            except run_limits.TranslateRunStopped:
                at_stop.append(engine.calls)
                raise
        monkeypatch.setattr(run_limits.RateLimitWaitBudget, "charge", spy)
        out, errors = te.translate_lines_with_engine(
            _lines(30), engine, {}, batch_size=1, reflect=True, check_replies=False)
        assert at_stop, "the budget never stopped the run"
        assert engine.calls == at_stop[0]
        assert "keeps rate-limiting you" in errors[-1]["error"]

    def test_the_pronoun_recheck_stops_instead_of_asking_the_next_chunk(self):
        class Engine:
            supports_reference = True
            model = "m"
            name = "e"

            def __init__(self):
                self.calls = 0

            def translate_batch(self, zh_lines, context):
                self.calls += 1
                if self.calls <= 4:
                    return ["He left." for _ in zh_lines]
                raise run_limits.TranslateRunStopped(run_limits.RATE_LIMIT_STOP_MESSAGE)

        engine = Engine()
        lines = _lines(4)
        _, errors = te.translate_lines_with_engine(
            lines, engine, {}, batch_size=1, check_replies=False,
            style_guidelines=PRONOUN_DEFAULT_MARKER + " she")
        assert engine.calls == 5  # four batches, then one recheck request that stopped the run
        assert "keeps rate-limiting you" in errors[-1]["error"]


def _rows():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT engine, operation, input_tokens, output_tokens, estimated_cost_usd "
            "FROM usage_log ORDER BY id")]


class _Paid:
    name = "claude"
    model = "claude-sonnet-5-5"
    free_tier = False

    def __init__(self):
        self.calls = 0
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, context=None):
        self.calls += 1
        self.last_usage = {"input_tokens": 800, "output_tokens": 300}
        return [f"EN:{t}" for t in texts]


@pytest.fixture
def extension(isolated_db, monkeypatch):
    engine = _Paid()
    page_server.set_config_provider(None)
    page_server.set_translation_config(engine="claude", api_key="k")
    monkeypatch.setattr(page_server, "_build_engine", lambda config: engine)
    return engine


class TestExtensionTextCapture:
    def test_a_spent_cap_refuses_the_paid_engine(self, extension, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 2.0)
        out = page_server.translate_text_block("你好", "zh", "en", store=False)
        assert extension.calls == 0 and out["translated_text"] == ""
        assert out["notes"][0][0] == "warning" and "cap" in out["notes"][0][1].lower()
        assert out["source_text"] == "你好"

    def test_every_request_is_logged(self, extension):
        out = page_server.translate_text_block("你好", "zh", "en", store=False)
        assert out["translated_text"] == "EN:你好"
        [row] = _rows()
        assert (row["engine"], row["operation"], row["input_tokens"], row["output_tokens"]) == (
            "claude", "translate_text", 800, 300)
        assert row["estimated_cost_usd"] > 0

    def test_it_stops_before_the_request_that_would_pass_the_cap(self, extension, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.0001)
        out = page_server.translate_text_block("你好", "zh", "en", store=False)
        assert extension.calls == 0
        assert any("monthly cap" in n[1] for n in out["notes"])

    def test_a_free_engine_is_unchanged(self, isolated_db, monkeypatch):
        class Free(_Paid):
            name = "ollama"
            model = "qwen3:8b"
        engine = Free()
        page_server.set_config_provider(None)
        page_server.set_translation_config(engine="ollama", api_key="local")
        monkeypatch.setattr(page_server, "_build_engine", lambda config: engine)
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.0001)
        db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 5.0)
        out = page_server.translate_text_block("你好", "zh", "en", store=False)
        assert out["translated_text"] == "EN:你好" and not out["notes"]
        assert [r["engine"] for r in _rows()] == ["claude", "ollama"]
