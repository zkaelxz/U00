"""Standalone Translate has no length limit, so its money safety is per request:
each group of chunks is priced before it is sent and a run stops before the one
that would pass the monthly cap, keeping what it finished. Every request is
logged, including a failed or abandoned one."""
import contextlib

import pytest

import db
import translate_engines
from engine_backends import llm_tasks, standalone
from engine_backends.shared import LLMTaskTimeout
from services import translate_run_service, translate_service
from lib.errors import InvalidInputError

PARAGRAPH = "段" * 1400  # one chunk each; 8 chunks go out per request
TEXT = "\n\n".join(PARAGRAPH for _ in range(10))  # 10 chunks: a request of 8, then one of 2


class _Billed:
    name = "claude"
    model = "claude-sonnet-5-5"
    free_tier = False

    def __init__(self, behaviour=None, tokens=(1000, 500)):
        self.calls = []
        self.behaviour = behaviour
        self.tokens = tokens
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, context=None):
        self.calls.append(list(texts))
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        if self.behaviour:
            self.behaviour(len(self.calls))
        self.last_usage = {"input_tokens": self.tokens[0], "output_tokens": self.tokens[1]}
        return [f"EN:{t[:1]}" for t in texts]


@pytest.fixture
def paid(isolated_db, monkeypatch):
    engine = _Billed()
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: engine)
    return engine


def _rows():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT engine, model, operation, input_tokens, output_tokens, estimated_cost_usd "
            "FROM usage_log ORDER BY id")]


def _request_cost():
    return translate_engines.estimate_translation_cost(_Billed(), [PARAGRAPH] * 8)


class TestCapPerRequest:
    def test_stops_before_the_request_that_would_pass_the_cap(self, paid, monkeypatch):
        first = _request_cost()
        paid.tokens = translate_engines.estimate_translation_tokens([PARAGRAPH] * 8)
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: first * 1.2)
        out = translate_service.translate(TEXT, "claude", "zh", "en")
        assert len(paid.calls) == 1
        assert out["translated_text"].count("EN:") == 8
        assert out["message"] == ("Stopped at chunk 9 of 10: the next chunk would pass your "
                                  "monthly cap. The 8 finished chunks are returned.")
        assert [r["operation"] for r in _rows()] == ["translate_text"]

    def test_the_first_request_is_refused_when_it_alone_would_pass_the_cap(self, paid, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: _request_cost() / 2)
        out = translate_service.translate(TEXT, "claude", "zh", "en")
        assert paid.calls == [] and out["translated_text"] == ""
        assert out["message"].startswith("Stopped at chunk 1 of 10")
        assert db.list_translate_history() == []

    def test_spend_by_something_else_between_requests_is_seen(self, paid, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 100.0)
        paid.behaviour = lambda n: db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 99.99)
        out = translate_service.translate(TEXT, "claude", "zh", "en")
        assert len(paid.calls) == 1 and "monthly cap" in out["message"]

    def test_a_run_inside_the_cap_is_whole_and_has_no_message(self, paid, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 100.0)
        out = translate_service.translate(TEXT, "claude", "zh", "en")
        assert set(out) == {"translated_text"} and out["translated_text"].count("EN:") == 10

    def test_no_cap_set_means_no_stop(self, paid, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.0)
        assert "message" not in translate_service.translate(TEXT, "claude", "zh", "en")

    def test_a_stopped_run_keeps_its_finished_text_in_history(self, paid, monkeypatch):
        paid.tokens = translate_engines.estimate_translation_tokens([PARAGRAPH] * 8)
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: _request_cost() * 1.2)
        translate_service.translate(TEXT, "claude", "zh", "en")
        assert db.list_translate_history()[0]["translated_text"].count("EN:") == 8


class TestEveryRequestIsLogged:
    def test_one_row_per_request(self, paid):
        translate_service.translate(TEXT, "claude", "zh", "en")
        rows = _rows()
        assert [(r["input_tokens"], r["output_tokens"]) for r in rows] == [(1000, 500)] * 2

    def test_a_failed_request_is_logged_and_the_error_still_surfaces(self, paid):
        def fail(n):
            if n == 2:
                raise RuntimeError("boom")
        paid.behaviour = fail
        with pytest.raises(RuntimeError):
            translate_service.translate(TEXT, "claude", "zh", "en")
        rows = _rows()
        assert len(rows) == 2 and rows[1]["input_tokens"] == 0 and rows[0]["input_tokens"] == 1000

    def test_an_abandoned_request_is_logged_at_its_estimate_once(self, paid, monkeypatch):
        late = {}

        def hang(engine, fn, on_late_result=None):
            late["fn"] = fn
            raise LLMTaskTimeout("deadline")
        monkeypatch.setattr(standalone, "call_batch_bounded", hang)
        with pytest.raises(LLMTaskTimeout):
            translate_service.translate(TEXT, "claude", "zh", "en")
        [row] = _rows()
        assert row["estimated_cost_usd"] == pytest.approx(_request_cost())
        late["fn"]()  # the abandoned request finally returns
        assert len(_rows()) == 1


class TestFreeEnginesLeaveNoEmptyRows:
    def test_a_free_engine_that_reports_no_tokens_is_not_logged(self, isolated_db, monkeypatch):
        class Free(_Billed):
            name = "ollama"
            model = "qwen3:8b"
        engine = Free(tokens=(0, 0))
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "local")
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: engine)
        translate_service.translate(TEXT, "ollama", "zh", "en")
        assert _rows() == []

    def test_a_failed_paid_request_with_no_tokens_is_still_logged(self, paid):
        paid.tokens = (0, 0)
        translate_service.translate("你好", "claude", "zh", "en")
        assert len(_rows()) == 1


class TestUsageMeterIsTransparent:
    def test_a_slow_ollama_model_keeps_its_long_timeout_behind_the_meter(self):
        from engine_backends.local import OllamaEngine, ollama_max_request_seconds
        engine = OllamaEngine(model="gemma4:31b")
        meter = translate_service._UsageMeter(engine, "ollama")
        assert llm_tasks._request_timeout_for(meter) == ollama_max_request_seconds("gemma4:31b")
        assert llm_tasks._request_timeout_for(meter) == llm_tasks._request_timeout_for(engine)


class TestModelMustBeOffered:
    def test_an_unlisted_model_is_refused_before_anything_is_sent(self, paid):
        with pytest.raises(InvalidInputError):
            translate_service.translate(TEXT, "claude", "zh", "en", model="claude-made-up-9")
        assert paid.calls == [] and _rows() == []


class TestEstimate:
    def test_reports_the_cost_and_what_is_left_of_the_cap(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 5.0)
        db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 1.0)
        est = translate_service.estimate(TEXT, "claude", "zh", "en")
        assert est["chunks"] == 10 and est["requests"] == 2
        assert est["estimated_usd"] == pytest.approx(
            translate_engines.estimate_translation_cost(_Billed(), [PARAGRAPH] * 8)
            + translate_engines.estimate_translation_cost(_Billed(), [PARAGRAPH] * 2))
        assert est["cap_applies"] is True and est["monthly_cap_usd"] == 5.0
        assert est["cap_left_usd"] == pytest.approx(4.0)
        assert est["fits_cap"] is True

    def test_says_when_it_would_not_fit(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.0001)
        assert translate_service.estimate(TEXT, "claude", "zh", "en")["fits_cap"] is False

    def test_a_free_engine_costs_nothing_and_has_no_cap(self, isolated_db, monkeypatch):
        monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
        est = translate_service.estimate(TEXT, "ollama", "zh", "en")
        assert est["estimated_usd"] == 0 and est["cap_applies"] is False
        assert est["cap_left_usd"] is None

    def test_reads_only(self, isolated_db):
        translate_service.estimate(TEXT, "claude", "zh", "en")
        assert _rows() == [] and db.list_translate_history() == []
