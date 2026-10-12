"""Retrying a blocked line spends money like any other paid call: it honours the
monthly cap first and its usage is always logged."""
import contextlib

import pytest

import db
import translate_engines
from services import blocked_retry_service, translate_run_service
from services.service_errors import ServiceError, UnsupportedOperationError
from tests.test_api_versions_retry_blocked import FakeEngine, _blocked, _use_engine


class _Metered(FakeEngine):
    free_tier = False

    def __init__(self, behaviour, model="claude-sonnet-4-5"):
        super().__init__(behaviour, model)
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, zh_lines, context):
        self.last_usage = {"input_tokens": 0, "output_tokens": 0}
        out = super().translate_batch(zh_lines, context)
        self.last_usage = {"input_tokens": 500, "output_tokens": 50}
        return out


def _rows():
    with contextlib.closing(db.get_conn()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT drama_id, engine, model, operation, input_tokens, output_tokens, "
            "estimated_cost_usd FROM usage_log ORDER BY id")]


def test_a_spent_cap_refuses_before_any_engine_is_built(isolated_db, monkeypatch):
    did, ids = _blocked()
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
    db.log_usage(None, "claude", "claude-sonnet-4-5", "other", 1, 1, 2.0)
    built = []
    _use_engine(monkeypatch, _Metered(lambda zh, ctx: ["x"]))
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: built.append(a))
    with pytest.raises(UnsupportedOperationError):
        blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
    assert built == []
    assert next(r for r in db.load_lines(did) if r["id"] == ids[1])["flag"] == "content_blocked"


def test_a_local_engine_ignores_the_cap(isolated_db, monkeypatch):
    did, ids = _blocked()
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 1.0)
    db.log_usage(None, "claude", "claude-sonnet-4-5", "other", 1, 1, 2.0)
    _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Bye"]))
    assert blocked_retry_service.retry_blocked_line(did, ids[1], "ollama")["retried"] is True


def test_a_successful_retry_is_logged(isolated_db, monkeypatch):
    did, ids = _blocked()
    _use_engine(monkeypatch, _Metered(lambda zh, ctx: ["Bye"]))
    blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
    (row,) = [r for r in _rows() if r["operation"] == "retry_blocked_line"]
    assert (row["drama_id"], row["engine"], row["input_tokens"], row["output_tokens"]) == (
        did, "claude", 500, 50)
    assert row["estimated_cost_usd"] > 0


def test_a_blocked_again_retry_is_logged(isolated_db, monkeypatch):
    did, ids = _blocked()

    def block(zh, ctx):
        raise translate_engines.ContentModerationBlocked("claude", "refusal")
    _use_engine(monkeypatch, _Metered(block))
    out = blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
    assert out["blocked"] is True
    assert [r["operation"] for r in _rows()] == ["retry_blocked_line"]


def test_a_failed_retry_is_logged_and_stays_a_fixed_message(isolated_db, monkeypatch):
    did, ids = _blocked()

    def fail(zh, ctx):
        raise RuntimeError("down")
    _use_engine(monkeypatch, _Metered(fail))
    with pytest.raises(ServiceError):
        blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
    assert [r["operation"] for r in _rows()] == ["retry_blocked_line"]
    assert _rows()[0]["input_tokens"] == 0


def test_a_local_engine_is_not_logged(isolated_db, monkeypatch):
    did, ids = _blocked()
    _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Bye"]))
    blocked_retry_service.retry_blocked_line(did, ids[1], "ollama")
    assert _rows() == []
