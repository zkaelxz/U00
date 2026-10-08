"""
Spend history (services/spend_history_service.py and
/api/settings/spend-history): spend by month and what caused it.
"""
import datetime

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from services import spend_history_service as svc
from services.service_errors import InvalidInputError

NOW = datetime.datetime(2026, 10, 15, 12, 0, 0)
MEMBER = {"user_id": 7, "is_admin": False, "is_local_owner": False}


def _log(cost, when, drama_id=None, engine="claude", model="m", op="translate", inp=10, out=5, cache=0):
    db.log_usage(drama_id, engine, model, op, inp, out, cost, cache_read_tokens=cache)
    conn = db.get_conn()
    conn.execute("UPDATE usage_log SET created_at = ? WHERE id = (SELECT MAX(id) FROM usage_log)",
                 (when.isoformat() if when else None,))
    conn.commit()
    conn.close()


def _by(rows):
    return {r["label"]: r for r in rows}


def test_zero_rows(isolated_db):
    h = svc.get_spend_history(now=NOW)
    assert h["months"] == [] and h["selected_month"] is None
    assert h["by_operation"] == h["by_engine_model"] == h["by_title"] == []
    assert svc.export_months_csv(now=NOW).count("\n") == 1  # header only


def test_months_newest_first_with_totals(isolated_db):
    _log(1.5, datetime.datetime(2026, 8, 31, 23, 59, 59), inp=100, out=50, cache=20)
    _log(2.0, datetime.datetime(2026, 9, 1), inp=1, out=1)
    _log(3.0, datetime.datetime(2026, 9, 30, 12))
    _log(4.0, datetime.datetime(2026, 10, 3))
    _log(9.0, None)  # no timestamp: in no month
    h = svc.get_spend_history(now=NOW)
    assert [m["month"] for m in h["months"]] == ["2026-10", "2026-09", "2026-08"]
    sep = h["months"][1]
    assert (sep["cost_usd"], sep["calls"], sep["input_tokens"]) == (5.0, 2, 11)
    assert h["months"][2]["cache_read_tokens"] == 20
    assert h["selected_month"] == "2026-10"
    assert svc.get_spend_history("2026-09", now=NOW)["by_operation"][0]["cost_usd"] == 5.0


def test_month_window_is_capped(isolated_db):
    _log(1.0, datetime.datetime(2023, 10, 31))  # 36 months back is 2023-11
    _log(1.0, datetime.datetime(2023, 11, 1))
    months = [m["month"] for m in svc.get_spend_history(now=NOW)["months"]]
    assert months == ["2023-11"]


def test_same_total_as_the_cap_and_reset_marks_its_month(isolated_db):
    _log(4.0, datetime.datetime(2026, 10, 2))
    _log(6.0, datetime.datetime(2026, 10, 10))
    _log(50.0, datetime.datetime(2026, 9, 20))
    db.set_app_setting(db.MONTHLY_SPEND_RESET_KEY, datetime.datetime(2026, 10, 5).isoformat())
    h = svc.get_spend_history(now=NOW)
    oct_, sep = h["months"]
    assert h["cap_reset_at"] == "2026-10-05T00:00:00"
    assert oct_["overlaps_reset"] and oct_["cost_usd"] == 10.0
    assert oct_["cap_counted_usd"] == db.get_month_spend(NOW) == 6.0
    assert not sep["overlaps_reset"] and sep["cap_counted_usd"] is None


def test_reset_from_an_earlier_month_marks_nothing(isolated_db):
    _log(1.0, datetime.datetime(2026, 10, 2))
    db.set_app_setting(db.MONTHLY_SPEND_RESET_KEY, datetime.datetime(2026, 9, 5).isoformat())
    h = svc.get_spend_history(now=NOW)
    assert h["cap_reset_at"] is None and not h["months"][0]["overlaps_reset"]


def test_reads_the_recosted_column(isolated_db):
    _log(10.0, datetime.datetime(2026, 10, 2))
    conn = db.get_conn()
    conn.execute("UPDATE usage_log SET estimated_cost_usd = 2.5")
    conn.commit()
    conn.close()
    assert svc.get_spend_history(now=NOW)["months"][0]["cost_usd"] == 2.5


def test_breakdowns_unknown_operations_and_deleted_title(isolated_db):
    a = db.create_drama(title_en="Alpha")
    b = db.create_drama(title_en="", title_zh="贝塔")
    gone = db.create_drama(title_en="Gone")
    when = datetime.datetime(2026, 10, 4)
    _log(3.0, when, a, op="benchmark_judge", model="x")
    _log(1.0, when, a, op="translate", engine="openai", model="y")
    _log(2.0, when, b, op="brand_new_thing")
    _log(0.5, when, None, op=None)
    _log(4.0, when, gone)
    conn = db.get_conn()
    conn.execute("DELETE FROM dramas WHERE id = ?", (gone,))
    conn.commit()
    conn.close()
    h = svc.get_spend_history(now=NOW)
    ops = _by(h["by_operation"])
    assert ops["Benchmark judge"]["key"] == "benchmark_judge"
    assert ops["Brand new thing"]["cost_usd"] == 2.0
    assert ops["Unknown"]["cost_usd"] == 0.5
    titles = _by(h["by_title"])
    assert titles["deleted title"]["cost_usd"] == 4.5  # deleted drama and no drama
    assert titles["Alpha"]["cost_usd"] == 4.0 and titles["贝塔"]["calls"] == 1
    assert [r["label"] for r in h["by_title"]][0] == "deleted title"
    assert "claude / x" in _by(h["by_engine_model"]) and "openai / y" in _by(h["by_engine_model"])


def test_top_rows_then_other(isolated_db):
    when = datetime.datetime(2026, 10, 4)
    for i in range(svc.MAX_BREAKDOWN_ROWS + 3):
        _log(float(i + 1), when, op=f"op_{i}")
    ops = svc.get_spend_history(now=NOW)["by_operation"]
    assert len(ops) == svc.MAX_BREAKDOWN_ROWS + 1
    assert ops[-1]["label"] == "Other" and ops[-1]["is_other"] and ops[-1]["key"] is None
    assert ops[-1]["cost_usd"] == 1 + 2 + 3 and ops[-1]["calls"] == 3
    assert ops[0]["cost_usd"] == svc.MAX_BREAKDOWN_ROWS + 3


def test_member_sees_only_visible_titles(isolated_db):
    mine = db.create_drama(title_en="Mine", owner_user_id=7)
    shared = db.create_drama(title_en="Shared")
    hidden = db.create_drama(title_en="Hidden", owner_user_id=8, is_private=1)
    when = datetime.datetime(2026, 10, 4)
    for did, cost in ((mine, 1.0), (shared, 2.0), (hidden, 4.0), (None, 8.0)):
        _log(cost, when, did)
    member = svc.get_spend_history(principal=MEMBER, now=NOW)
    assert member["months"][0]["cost_usd"] == 3.0
    assert set(_by(member["by_title"])) == {"Mine", "Shared"}
    assert svc.get_spend_history(now=NOW)["months"][0]["cost_usd"] == 15.0


def test_bad_month_is_refused(isolated_db):
    for bad in ("2026-13", "2026-1", "x'; DROP TABLE usage_log;--", "202610"):
        with pytest.raises(InvalidInputError):
            svc.get_spend_history(bad, now=NOW)


def test_csv(isolated_db):
    _log(1.25, datetime.datetime(2026, 10, 2), inp=3, out=4)
    lines = svc.export_months_csv(now=NOW).splitlines()
    assert lines[0].startswith("month,estimated_cost_usd,calls")
    assert lines[1] == "2026-10,1.250000,1,3,4,0,"


def _client(local=True):
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    if local:
        return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                          raise_server_exceptions=False)
    return TestClient(app, base_url="https://baihe.example.com", client=("203.0.113.9", 5000),
                      raise_server_exceptions=False)


def test_routes(isolated_db):
    db.create_drama(title_en="T")
    _log(2.0, datetime.datetime.utcnow())
    c = _client()
    body = c.get("/api/settings/spend-history").json()
    assert body["months"][0]["cost_usd"] == 2.0 and body["max_months"] == 36
    assert c.get("/api/settings/spend-history?month=bad").status_code == 422
    r = c.get("/api/settings/spend-history/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]


def test_csv_is_pc_only(isolated_db):
    assert _client(local=False).get("/api/settings/spend-history/export.csv").status_code == 403
