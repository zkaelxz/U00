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
from services.service_errors import ConflictError, InvalidInputError

UNPRICED = "claude-sonnet-5-5"
PRICED = "claude-sonnet-5"
OLD = 22.5  # 1M in + 100k out at the old whole-family $15/$75
# Highest possible figure for 1M input + 100k output at $2/$10: every input token billed as a cache write.
HIGH = 1.25 * 2.0 + 0.1 * 10.0


def _log(model, cost, created_at=None, inp=1_000_000, out=100_000, cache=0, op="translate"):
    db.log_usage(None, "claude", model, op, inp, out, cost, cache_read_tokens=cache)
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
    one = HIGH
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


def test_apply_touches_only_listed_rows_and_changes_month_spend(seeded):
    one = HIGH
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
    _log(UNPRICED, 0.0001)         # already below the recompute
    before = _rows()
    assert svc.preview()["rows"] == 0
    assert svc.apply(True)["rows"] == 0
    assert _rows() == before


def test_unlisted_models_are_never_touched(isolated_db):
    _log("gemini-pro-latest", 50.0)
    _log("some-local-model", 2.0)
    _log("claude-sonnet-4-6", 50.0)
    before = _rows()
    assert svc.preview()["rows"] == 0
    assert svc.apply(True)["rows"] == 0
    assert _rows() == before


def test_opus_4_8_old_rate_is_lowered_to_5_25(isolated_db):
    _log("claude-opus-4-8", OLD)
    svc.apply(True)
    # 1M input all as cache write at $5 x1.25 + 100k output at $25
    assert _rows()[0]["estimated_cost_usd"] == pytest.approx(1.25 * 5.0 + 0.1 * 25.0)


def test_batch_rows_use_the_batch_factor(isolated_db):
    _log(UNPRICED, OLD, op="translate_bulk")
    _log(UNPRICED, OLD, op="consistency_check_bulk")
    svc.apply(True)
    assert [r["estimated_cost_usd"] for r in _rows()] == [pytest.approx(HIGH / 2)] * 2


def test_cache_read_tokens_are_priced_at_the_read_rate(isolated_db):
    _log(UNPRICED, OLD, cache=600_000)
    svc.apply(True)
    # 600k read at 10%, 400k as cache write at 125%, 100k output
    expected = (600_000 * 0.1 + 400_000 * 1.25) / 1e6 * 2.0 + 0.1 * 10.0
    assert _rows()[0]["estimated_cost_usd"] == pytest.approx(expected)


def test_row_at_or_below_the_high_end_is_skipped(isolated_db):
    _log(UNPRICED, HIGH)          # equal to the high end
    _log(UNPRICED, HIGH - 0.5)    # between low and high
    before = _rows()
    assert svc.preview()["rows"] == 0
    assert svc.apply(True)["rows"] == 0
    assert _rows() == before


def test_second_apply_is_a_noop_and_undo_returns_the_first_original(seeded):
    svc.apply(True)
    # A later price correction lowers the same rows again.
    conn = db.get_conn()
    conn.execute("UPDATE usage_log SET estimated_cost_usd = 9.0 WHERE model = ?", (UNPRICED,))
    conn.commit()
    conn.close()
    assert svc.apply(True)["rows"] == 2
    assert svc.apply(True)["rows"] == 0
    assert svc.undo()["rows"] == 2
    assert [r["estimated_cost_usd"] for r in _rows()] == [pytest.approx(OLD), pytest.approx(OLD), 3.5]


def test_apply_refuses_when_the_previewed_count_differs(seeded):
    before = _rows()
    with pytest.raises(ConflictError):
        svc.apply(True, previewed=1)
    assert _rows() == before
    res = svc.apply(True, previewed=2)
    assert (res["previewed"], res["changed"]) == (2, 2)


def test_row_changed_since_it_was_read_is_skipped(seeded):
    rows = db.usage_recost_candidates(svc.RECOST_MODELS)
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
    assert c.post(BASE + "/apply", json={"confirm": True, "previewed": 5}).status_code == 409
    assert c.post(BASE + "/apply", json={"confirm": True, "previewed": 2}).json()["changed"] == 2
    assert c.get(BASE).json()["recosted_rows"] == 2
    assert c.post(BASE + "/undo", json={}).json()["rows"] == 2


def test_apply_and_undo_are_refused_from_another_device(seeded):
    before = _rows()
    c = _client(local=False)
    assert c.post(BASE + "/apply", json={"confirm": True}).status_code in (401, 403)
    assert c.post(BASE + "/undo", json={}).status_code in (401, 403)
    assert _rows() == before
