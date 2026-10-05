"""
Monthly spend counter reset (db.get_month_spend, services/settings_service.py
and /api/settings/month-counter/*): the cap counts rows since the reset;
history is kept.
"""
import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from engine_backends import pricing
from services import benchmark_lab_service, line_ai_service, metadata_research_service
from services import settings_service, translate_run_service, usage_recost_service
from services.service_errors import UnsupportedOperationError

NOW = datetime.datetime(2026, 10, 15, 12, 0, 0)


def _log(cost, when):
    db.log_usage(None, "claude", "m", "translate", 1, 1, cost)
    conn = db.get_conn()
    conn.execute("UPDATE usage_log SET created_at = ? WHERE id = (SELECT MAX(id) FROM usage_log)",
                 (when.isoformat(),))
    conn.commit()
    conn.close()


def _set_reset(when):
    db.set_app_setting(db.MONTHLY_SPEND_RESET_KEY, when.isoformat())


@pytest.fixture
def spent(isolated_db):
    _log(4.0, datetime.datetime(2026, 10, 2))
    _log(6.0, datetime.datetime(2026, 10, 10))
    _log(50.0, datetime.datetime(2026, 9, 20))  # last month, never counted
    return isolated_db


def test_without_a_reset_the_whole_month_counts(spent):
    assert db.get_month_spend(NOW) == 10.0
    assert db.get_month_spend_reset_at(NOW) is None


def test_reset_counts_only_rows_at_or_after_it(spent):
    _set_reset(datetime.datetime(2026, 10, 5))
    assert db.get_month_spend(NOW) == 6.0
    assert db.get_month_spend(NOW, since_reset=False) == 10.0
    _log(1.5, datetime.datetime(2026, 10, 12))
    assert db.get_month_spend(NOW) == 7.5


def test_a_reset_from_an_earlier_month_is_ignored(spent):
    _set_reset(datetime.datetime(2026, 9, 25))
    assert db.get_month_spend(NOW) == 10.0
    assert db.get_month_spend_reset_at(NOW) is None
    # September's own view still honours it: the 20 Sep row is before the reset.
    sept = datetime.datetime(2026, 9, 28)
    assert db.get_month_spend(sept, since_reset=False) == 60.0
    assert db.get_month_spend(sept) == 10.0


def test_a_future_dated_reset_is_ignored(spent):
    # PC clock ahead when Reset was pressed, then corrected.
    _set_reset(datetime.datetime(2026, 10, 20))
    assert db.get_month_spend_reset_at(NOW) is None
    assert db.get_month_spend(NOW) == 10.0


@pytest.mark.parametrize("garbage", ["not a date", "", "2026-13-40", "9999"])
def test_a_garbage_reset_is_ignored(spent, garbage):
    db.set_app_setting(db.MONTHLY_SPEND_RESET_KEY, garbage)
    assert db.get_month_spend_reset_at(NOW) is None
    assert db.get_month_spend(NOW) == 10.0


def test_a_reset_earlier_this_month_is_honoured_up_to_now(spent):
    _set_reset(NOW)
    assert db.get_month_spend_reset_at(NOW) == NOW.isoformat()
    _set_reset(datetime.datetime(2026, 10, 5))
    assert db.get_month_spend_reset_at(NOW) == "2026-10-05T00:00:00"
    assert db.get_month_spend(NOW) == 6.0


def test_reset_and_undo_keep_every_row(isolated_db):
    db.log_usage(None, "claude", "m", "translate", 1, 1, 4.0)
    db.log_usage(None, "claude", "m", "translate", 1, 1, 6.0)
    assert _count() == 2
    out = settings_service.reset_month_counter()
    assert out["before"]["month_spend_reset_at"] is None
    assert out["after"]["month_spend_reset_at"]
    assert out["after"]["month_spend_counted_usd"] == 0.0
    assert out["after"]["month_spend_usd"] == out["before"]["month_spend_usd"]
    assert _count() == 2
    undone = settings_service.undo_month_counter_reset()
    assert undone["before"]["month_spend_reset_at"] == out["after"]["month_spend_reset_at"]
    assert undone["after"]["month_spend_reset_at"] is None
    assert undone["after"]["month_spend_counted_usd"] == undone["after"]["month_spend_usd"]


def _count():
    conn = db.get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    finally:
        conn.close()


def test_cap_check_uses_since_reset(spent):
    cap, refusal = pricing.resolve_cost_cap(None, 8.0, db.get_month_spend(NOW))
    assert cap is None and refusal
    _set_reset(datetime.datetime(2026, 10, 5))
    cap, refusal = pricing.resolve_cost_cap(None, 8.0, db.get_month_spend(NOW))
    assert refusal is None and cap == pytest.approx(2.0)


def test_running_job_cap_math_after_a_reset(isolated_db):
    # A job that started with 7.0 of an 8.0 cap spent (cap 1.0) is re-based by a reset.
    cap, _ = pricing.resolve_cost_cap(None, 8.0, 7.0)
    assert cap == pytest.approx(1.0)
    db.log_usage(None, "claude", "m", "translate", 1, 1, 7.0)
    settings_service.reset_month_counter()
    db.log_usage(None, "claude", "m", "translate", 1, 1, 0.5)
    cap, refusal = pricing.resolve_cost_cap(None, 8.0, db.get_month_spend())
    assert refusal is None and cap == pytest.approx(7.5)


def test_overview_reports_both_figures_and_no_secrets(isolated_db):
    db.log_usage(None, "claude", "m", "translate", 1, 1, 3.0)
    settings_service.reset_month_counter()
    db.log_usage(None, "claude", "m", "translate", 1, 1, 1.0)
    o = settings_service.get_settings_overview()
    assert o["month_spend_usd"] == pytest.approx(4.0)
    assert o["month_spend_counted_usd"] == pytest.approx(1.0)
    assert isinstance(o["month_spend_reset_at"], str)


def _client(local=True):
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    if local:
        return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                          raise_server_exceptions=False)
    return TestClient(app, base_url="https://baihe.example.com", client=("203.0.113.9", 5000),
                      raise_server_exceptions=False)


def test_routes_reset_and_undo(isolated_db):
    db.log_usage(None, "claude", "m", "translate", 1, 1, 2.0)
    c = _client()
    r = c.post("/api/settings/month-counter/reset", json={})
    assert r.status_code == 200
    assert set(r.json()) == {"before", "after"}
    assert r.json()["after"]["month_spend_counted_usd"] == 0.0
    assert c.get("/api/settings").json()["month_spend_reset_at"]
    u = c.post("/api/settings/month-counter/undo", json={})
    assert u.json()["after"]["month_spend_counted_usd"] == pytest.approx(2.0)
    assert c.get("/api/settings").json()["month_spend_reset_at"] is None


def test_routes_refused_from_another_device(isolated_db):
    c = _client(local=False)
    for path in ("reset", "undo"):
        assert c.post(f"/api/settings/month-counter/{path}", json={}).status_code in (401, 403)
    assert db.get_app_setting(db.MONTHLY_SPEND_RESET_KEY) is None


def test_line_tool_cap_check_refuses_then_allows_after_reset(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 5.0)
    db.log_usage(None, "claude", "m", "translate", 1, 1, 6.0)
    with pytest.raises(UnsupportedOperationError):
        line_ai_service.refuse_if_over_monthly_cap("claude", False)
    settings_service.reset_month_counter()
    line_ai_service.refuse_if_over_monthly_cap("claude", False)


def test_recost_preview_reports_the_full_month_during_a_reset(isolated_db):
    db.log_usage(None, "claude", "m", "translate", 1, 1, 3.0)
    settings_service.reset_month_counter()
    assert usage_recost_service.preview()["month_stored_usd"] == pytest.approx(3.0)


@pytest.mark.parametrize("cap, shown", [(0.0, 4.0), (10.0, 1.0)])
def test_spend_shown_with_no_cap_is_the_real_month(isolated_db, monkeypatch, cap, shown):
    # "Spent this month: $X (no monthly cap)" is the real spend; "$X of $cap"
    # is what the cap counts since the reset.
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: cap)
    db.log_usage(None, "claude", "m", "translate", 1, 1, 3.0)
    settings_service.reset_month_counter()
    db.log_usage(None, "claude", "m", "translate", 1, 1, 1.0)
    assert metadata_research_service.budget_status()["month_spend_usd"] == pytest.approx(shown)
    benchmark_lab_service.create_case("c", "你好", "Hello")
    est = benchmark_lab_service.estimate("translation", [{"engine": "fake"}])
    assert est["month_spend_usd"] == pytest.approx(shown)
    monkeypatch.setattr(translate_run_service, "ollama_reachable", lambda: False)
    config = translate_run_service.get_translate_config(db.create_drama(title_zh="D"))
    assert config["month_spend"] == pytest.approx(shown)
