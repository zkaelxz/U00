"""The monthly spending cap covers Ollama's hosted (-cloud) tags, and the
maintenance assistant's chat calls are logged and held to the cap."""
import contextlib

import pytest

import db
import qa
from engine_backends import local
from lib.errors import UnsupportedOperationError
from services import assistant_diagnosis_service as diag
from services import line_ai_service, translate_run_service, translate_service

CLOUD = "gemma4:31b-cloud"


def _rows():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT engine, model, operation, input_tokens, output_tokens, estimated_cost_usd "
            "FROM usage_log ORDER BY id")]


@pytest.fixture
def cap_spent(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
    db.log_usage(None, "claude", "claude-sonnet-5-5", "other", 1, 1, 5.0)


def test_the_cap_covers_a_cloud_tag_but_not_a_local_one():
    assert translate_run_service.engine_cap_applies("ollama", False, CLOUD)
    assert translate_run_service.engine_cap_applies("ollama", False, "gemma4:cloud")
    assert not translate_run_service.engine_cap_applies("ollama", False, "gemma4:12b")
    assert not translate_run_service.engine_cap_applies("ollama", False)


def test_a_cloud_tag_is_refused_once_the_cap_is_spent(cap_spent):
    with pytest.raises(UnsupportedOperationError):
        translate_run_service.refuse_when_cap_spent("ollama", False, CLOUD)
    translate_run_service.refuse_when_cap_spent("ollama", False, "gemma4:12b")


def test_a_pasted_text_translation_on_a_cloud_tag_stops_at_the_cap(cap_spent):
    class Engine:
        model = CLOUD
        free_tier = False

        def translate_batch(self, texts, context=None):
            raise AssertionError("a request was sent past the cap")

    with pytest.raises(UnsupportedOperationError):
        translate_service.translate_metered(Engine(), "ollama", "你好", "zh", "en")


def test_the_estimate_for_a_cloud_tag_says_the_cap_applies(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
    assert line_ai_service.refuse_if_over_monthly_cap("ollama", False, CLOUD) is None
    db.log_usage(None, "claude", "claude-sonnet-5-5", "other", 1, 1, 5.0)
    with pytest.raises(UnsupportedOperationError):
        line_ai_service.refuse_if_over_monthly_cap("ollama", False, CLOUD)
    line_ai_service.refuse_if_over_monthly_cap("ollama", False, "gemma4:12b")


class _Engine:
    supports_reference = True

    def __init__(self, name, model):
        self.name, self.model = name, model


def test_an_assistant_chat_call_is_logged_with_its_cost(isolated_db, monkeypatch):
    def reply(system_prompt, messages, engine, max_tokens=0, usage_cb=None):
        usage_cb({"input_tokens": 10_000, "output_tokens": 2_000})
        return "answer"

    monkeypatch.setattr(qa, "dispatch_chat", reply)
    assert diag._chat("sys", [{"role": "user", "content": "q"}],
                      _Engine("claude", "claude-sonnet-5-5")) == "answer"
    [row] = _rows()
    assert (row["engine"], row["operation"], row["input_tokens"], row["output_tokens"]) == (
        "claude", "assistant", 10_000, 2_000)
    assert row["estimated_cost_usd"] == pytest.approx(0.04)


def test_an_assistant_cloud_tag_call_is_costed_and_a_local_one_is_free(isolated_db, monkeypatch):
    def reply(system_prompt, messages, engine, max_tokens=0, usage_cb=None):
        usage_cb({"input_tokens": 1_000_000, "output_tokens": 0})
        return "answer"

    monkeypatch.setattr(qa, "dispatch_chat", reply)
    diag._chat("s", [], _Engine("ollama", "gemma4:12b"))
    diag._chat("s", [], _Engine("ollama", CLOUD))
    local_row, cloud_row = _rows()
    assert local_row["estimated_cost_usd"] == 0
    assert cloud_row["estimated_cost_usd"] == pytest.approx(0.14)


def test_a_failed_paid_assistant_call_is_still_logged(isolated_db, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(qa, "dispatch_chat", boom)
    with pytest.raises(Exception):
        diag._chat("s", [], _Engine("claude", "claude-sonnet-5-5"))
    [row] = _rows()
    assert row["operation"] == "assistant" and row["estimated_cost_usd"] == 0


def test_each_assistant_round_is_refused_once_the_cap_is_spent(cap_spent, monkeypatch):
    monkeypatch.setattr(qa, "dispatch_chat", lambda *a, **k: pytest.fail("called past the cap"))
    for engine in (_Engine("claude", "claude-sonnet-5-5"), _Engine("ollama", CLOUD)):
        with pytest.raises(UnsupportedOperationError):
            diag._chat("s", [], engine)


def test_dispatch_chat_reports_each_providers_tokens(monkeypatch):
    seen = []
    monkeypatch.setattr(qa, "post_json", lambda *a, **k: (
        {"message": {"content": " hi "}, "prompt_eval_count": 7, "eval_count": 3}, {}))
    engine = local.OllamaEngine(model=CLOUD)
    assert qa.dispatch_chat("s", [{"role": "user", "content": "q"}], engine,
                            usage_cb=seen.append) == "hi"
    assert seen == [{"input_tokens": 7, "output_tokens": 3}]
