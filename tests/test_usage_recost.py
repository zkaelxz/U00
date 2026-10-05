"""
Opt-in re-cost of old usage_log rows (services/usage_recost_service.py and
/api/settings/usage-recost).
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from engine_backends import pricing
from services import usage_recost_service as svc
from services.service_errors import InvalidInputError

UNPRICED = "claude-sonnet-5-5"
PRICED = "claude-sonnet-5"
OLD = pricing.estimate_cost("claude-opus-4-8", 1_000_000, 100_000)  # the inflated Opus-rate figure


def _log(model, cost, created_at=None, inp=1_000_000, out=100_000, cache=0):
    db.log_usage(None, "claude", model, "translate", inp, out, cost, cache_read_tokens=cache)
    if created_at:
        conn = db.get_conn()
        conn.execute("UPDATE usage_log SET created_at = ? WHERE id = (SELECT MAX(id) FROM usage_log)",
                     (created_at,))
        conn.commit()
        conn.close()


def _rows():
    conn = db.get_conn()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM usage_log ORDER BY id")]
    finally:
        conn.close()


@pytest.fixture
def seeded(isolated_db):
    _log(UNPRICED, OLD)
    _log(UNPRICED, OLD, created_at="2000-01-15T00:00:00")
    _log(PRICED, 3.5)
    return isolated_db


def test_preview_math_and_no_writes(seeded):
    before = _rows()
    p = svc.preview()
    one = pricing.estimate_cost(UNPRICED, 1_000_000, 100_000)
    assert one < OLD
    assert p["rows"] == 2
    assert p["models"] == [{"model": UNPRICED, "rows": 2, "stored_usd": pytest.approx(2 * OLD),
                            "recomputed_usd": pytest.approx(2 * one)}]
    assert p["difference_usd"] == pytest.approx(2 * (OLD - one))
    # Month figures count only this month's rows (plus the priced row).
    assert p["month_stored_usd"] == pytest.approx(OLD + 3.5)
    assert p["month_recomputed_usd"] == pytest.approx(one + 3.5)
    assert p["recosted_rows"] == 0
    assert _rows() == before


def test_apply_needs_confirmation(seeded):
    before = _rows()
    with pytest.raises(InvalidInputError):
        svc.apply(False)
    assert _rows() == before


def test_apply_touches_only_unpriced_rows_and_changes_month_spend(seeded):
    one = pricing.estimate_cost(UNPRICED, 1_000_000, 100_000)
    assert db.get_month_spend() == pytest.approx(OLD + 3.5)
    res = svc.apply(True)
    assert res["rows"] == 2
    assert res["month_spend_usd"] == pytest.approx(one + 3.5)
    rows = _rows()
    assert [r["estimated_cost_usd"] for r in rows] == [pytest.approx(one), pytest.approx(one), 3.5]
    assert [r["estimated_cost_usd_before_recost"] for r in rows] == [pytest.approx(OLD), pytest.approx(OLD), None]


def test_apply_is_idempotent(seeded):
    svc.apply(True)
    after = _rows()
    assert svc.apply(True)["rows"] == 0
    assert _rows() == after
    assert svc.preview()["rows"] == 0
    assert svc.preview()["recosted_rows"] == 2


def test_undo_restores_and_is_idempotent(seeded):
    before = _rows()
    svc.apply(True)
    assert svc.undo()["rows"] == 2
    assert _rows() == before
    assert svc.undo()["rows"] == 0
    assert _rows() == before


def test_never_raises_or_zeroes_a_cost(isolated_db):
    _log(UNPRICED, 0.0)            # free tier or unlogged: stays 0
    _log("some-local-model", 2.0)  # no known rate: recompute is 0, left alone
    _log(UNPRICED, 0.0001)         # already below the recompute
    before = _rows()
    assert svc.preview()["rows"] == 0
    assert svc.apply(True)["rows"] == 0
    assert _rows() == before


def test_row_changed_since_it_was_read_is_skipped(seeded):
    rows = db.usage_recost_candidates(tuple(pricing.PRICING_PER_MILLION_TOKENS))
    conn = db.get_conn()
    conn.execute("UPDATE usage_log SET estimated_cost_usd = 9 WHERE id = ?", (rows[0]["id"],))
    conn.commit()
    conn.close()
    assert db.apply_usage_recost([(r["id"], r["estimated_cost_usd"], 0.5) for r in rows]) == 1
    assert _rows()[0]["estimated_cost_usd"] == 9


# ---- routes ----------------------------------------------------------------

BASE = "/api/settings/usage-recost"


def _client(local=True):
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    if local:
        return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                          raise_server_exceptions=False)
    return TestClient(app, base_url="https://baihe.example.com", client=("203.0.113.9", 5000),
                      raise_server_exceptions=False)


def test_routes_preview_apply_undo(seeded):
    c = _client()
    assert c.get(BASE).json()["rows"] == 2
    assert c.post(BASE + "/apply", json={}).status_code == 422
    assert c.post(BASE + "/apply", json={"confirm": True}).json()["rows"] == 2
    assert c.get(BASE).json()["recosted_rows"] == 2
    assert c.post(BASE + "/undo", json={}).json()["rows"] == 2


def test_apply_and_undo_are_refused_from_another_device(seeded):
    before = _rows()
    c = _client(local=False)
    assert c.post(BASE + "/apply", json={"confirm": True}).status_code in (401, 403)
    assert c.post(BASE + "/undo", json={}).status_code in (401, 403)
    assert _rows() == before
