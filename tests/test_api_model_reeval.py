"""
Step 40b: /api/models/reeval routes. Permissions, confirm gates, and that
nothing is promoted without the explicit promote call.
"""
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, settings_service
from services import benchmark_lab_service as lab
from tests.test_model_reeval import GoodEngine, WeakEngine

REMOTE = "https://baihe.example.com"
LOCAL = {"X-Baihe-Local": "1"}


def _local():
    return TestClient(create_app(ApiSettings(auth_mode="off", serve_frontend=False)),
                      base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _remote():
    return TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                      base_url=REMOTE, raise_server_exceptions=False)


def _session(admin):
    u = auth_service.grant_admin_local("admin@example.com") if admin else auth_service.add_user("k@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def world(isolated_db, monkeypatch):
    monkeypatch.setitem(translate_engines.ENGINES, "fake_good", GoodEngine)
    monkeypatch.setitem(translate_engines.ENGINES, "ollama", WeakEngine)
    settings_service.set_settings({"default_engine": "ollama"})
    lab.import_golden_set("g", "你好\tHello\n谢谢\tThanks\n", "tsv", "public")
    background_jobs.clear_job(lab.JOB_ID)
    yield
    background_jobs.clear_job(lab.JOB_ID)


def _wait():
    end = time.time() + 15
    while time.time() < end:
        st = background_jobs.get_status(lab.JOB_ID)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_reads_are_admin_only(world):
    assert _local().get("/api/models/reeval").status_code == 200
    r = _remote()
    assert r.get("/api/models/reeval").status_code == 401
    assert r.get("/api/models/reeval", headers=_session(False)).status_code == 403
    assert r.get("/api/models/reeval", headers=_session(True)).status_code == 200
    assert r.get("/api/models/reeval/decisions", headers=_session(True)).status_code == 200


def test_writes_are_pc_only(world):
    r = _remote()
    adm = {**_session(True), **LOCAL}
    for path, body in (("/api/models/reeval/settings", {"schedule_enabled": True, "interval_days": 30}),
                       ("/api/models/reeval/candidates", {"engine": "fake_good"}),
                       ("/api/models/reeval/run", {"confirm": True}),
                       ("/api/models/reeval/candidates/1/promote", {"confirm": True}),
                       ("/api/models/reeval/candidates/1/reject", {"reason": "x"}),
                       ("/api/models/reeval/candidates/1/reopen", None)):
        resp = r.post(path, headers=adm, json=body) if body is not None else r.post(path, headers=adm)
        assert resp.status_code == 403, path
    assert db.list_model_candidates("translation") == []


def test_full_flow_needs_explicit_promotion(world):
    c = _local()
    cand = c.post("/api/models/reeval/candidates", json={"engine": "fake_good", "note": "try"}).json()
    cid = cand["candidate"]["id"]
    assert c.post("/api/models/reeval/settings",
                  json={"schedule_enabled": False, "interval_days": 30, "tier": "public",
                        "set_name": "g"}).status_code == 200
    est = c.post("/api/models/reeval/estimate", headers=LOCAL)
    assert est.status_code == 200 and est.json()["case_count"] == 2
    assert c.post("/api/models/reeval/run", json={}).status_code == 422
    started = c.post("/api/models/reeval/run", json={"confirm": True})
    assert started.status_code == 200 and started.json()["candidate_ids"] == [cid]
    _wait()
    over = c.get("/api/models/reeval").json()
    assert over["production"]["engine"] == "ollama"
    assert over["report"]["rows"][0]["quality_delta"] > 0
    assert c.post(f"/api/models/reeval/candidates/{cid}/promote", json={}).status_code == 422
    assert c.get("/api/models/reeval").json()["production"]["engine"] == "ollama"
    ok = c.post(f"/api/models/reeval/candidates/{cid}/promote", json={"confirm": True, "reason": "better"})
    assert ok.status_code == 200 and ok.json()["production"]["engine"] == "fake_good"
    decisions = c.get("/api/models/reeval/decisions").json()["decisions"]
    assert decisions[0]["decision"] == "promoted" and decisions[0]["reason"] == "better"


def test_rejected_candidate_readd_surfaces_decision(world):
    c = _local()
    cid = c.post("/api/models/reeval/candidates", json={"engine": "fake_good"}).json()["candidate"]["id"]
    assert c.post(f"/api/models/reeval/candidates/{cid}/reject", json={"reason": "stiff"}).status_code == 200
    again = c.post("/api/models/reeval/candidates", json={"engine": "fake_good"}).json()
    assert again["already_registered"] is True
    assert "rejected: stiff" in again["candidate"]["last_decision"]["summary"]
    assert c.post(f"/api/models/reeval/candidates/{cid}/reopen", headers=LOCAL).status_code == 200


def test_bad_input(world):
    c = _local()
    assert c.post("/api/models/reeval/settings", json={"schedule_enabled": True,
                                                        "interval_days": 0}).status_code == 422
    assert c.post("/api/models/reeval/candidates", json={"engine": "nope"}).status_code == 422
    assert c.post("/api/models/reeval/candidates", json={"engine": "fake_good", "x": 1}).status_code == 422
    assert c.post("/api/models/reeval/candidates/999/reject", json={}).status_code == 404


def test_enabling_the_schedule_needs_a_limit(world, monkeypatch):
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda env_path=None: 0.0)
    c = _local()
    c.post("/api/models/reeval/candidates", json={"engine": "fake_good"})
    body = {"schedule_enabled": True, "interval_days": 30, "tier": "public", "set_name": "g"}
    refused = c.post("/api/models/reeval/settings", json=body)
    assert refused.status_code == 422
    assert "monthly spending cap" in refused.text and "max_cost_usd" in refused.text
    ok = c.post("/api/models/reeval/settings", json={**body, "max_cost_usd": 0.5})
    assert ok.status_code == 200
    out = ok.json()
    assert out["settings"]["schedule_enabled"] is True and out["settings"]["enabled_at"]
    assert out["schedule_estimate"]["case_count"] == 2
    assert out["next_due_at"] > out["settings"]["enabled_at"]
