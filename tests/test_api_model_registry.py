"""
Step 40: /api/models routes (api/routers/model_registry_routes.py).
Permissions, confirm, rate limit and that no key reaches a response.
Mocked provider responses only.
"""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, translate_service
from services import model_registry_service as svc

REMOTE = "https://baihe.example.com"
LOCAL_HDR = {"X-Baihe-Local": "1"}
SECRET = "sk-ant-SECRETKEY1234567890abcdef"


def _app(auth="off"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _local(auth="off"):
    return TestClient(_app(auth), base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _remote():
    return TestClient(_app("on"), base_url=REMOTE, raise_server_exceptions=False)


def _session(admin):
    if admin:
        u = auth_service.grant_admin_local("admin@example.com")
    else:
        u = auth_service.add_user("kid@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


class FakeResp:
    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(svc, "_last_check_started", 0.0)
    monkeypatch.setattr(translate_service, "resolve_api_key",
                        lambda name, env_path=None: SECRET if name == "claude" else None)


def test_status_read_permissions(isolated_db):
    assert _local().get("/api/models/status").status_code == 200
    remote = _remote()
    assert remote.get("/api/models/status").status_code == 401
    assert remote.get("/api/models/status", headers=_session(False)).status_code == 403
    assert remote.get("/api/models/status", headers=_session(True)).status_code == 200


def test_check_and_switch_are_pc_only(isolated_db):
    remote = _remote()
    adm = {**_session(True), **LOCAL_HDR}
    assert remote.post("/api/models/check", headers=adm).status_code == 403
    db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-chat")
    pid = db.list_presets()[0]["id"]
    r = remote.post(f"/api/models/presets/{pid}/switch", headers=adm,
                    json={"from_model": "deepseek-chat", "to_model": "deepseek-v4-flash",
                          "confirm": True})
    assert r.status_code == 403
    assert db.list_presets()[0]["engine_model"] == "deepseek-chat"


def test_check_flags_unlisted_model_without_leaking_key(isolated_db, monkeypatch):
    import requests
    db.save_preset("P", translation_engine="claude", engine_model="claude-opus-4-8")
    monkeypatch.setattr(requests, "get", lambda url, **kw: FakeResp({"data": [{"id": "claude-sonnet-5"}]}))
    r = _local().post("/api/models/check", headers=LOCAL_HDR)
    assert r.status_code == 200
    assert SECRET not in r.text
    item = next(i for i in r.json()["items"] if i["model"] == "claude-opus-4-8" and i["kind"] == "preset")
    assert item["status"] == "not_listed"
    again = _local().post("/api/models/check", headers=LOCAL_HDR)
    assert again.status_code == 429
    status = _local().get("/api/models/status")
    assert SECRET not in status.text and status.json()["warnings"] >= 1


def test_switch_needs_confirm_then_switches(isolated_db):
    db.save_preset("P", translation_engine="deepseek", engine_model="deepseek-chat")
    pid = db.list_presets()[0]["id"]
    c = _local()
    body = {"from_model": "deepseek-chat", "to_model": "deepseek-v4-flash"}
    assert c.post(f"/api/models/presets/{pid}/switch", json=body).status_code == 422
    assert db.list_presets()[0]["engine_model"] == "deepseek-chat"
    r = c.post(f"/api/models/presets/{pid}/switch", json={**body, "confirm": True})
    assert r.status_code == 200 and r.json()["to_model"] == "deepseek-v4-flash"
    stale = c.post(f"/api/models/presets/{pid}/switch", json={**body, "confirm": True})
    assert stale.status_code == 409


def test_switch_unknown_preset_and_extra_fields(isolated_db):
    c = _local()
    assert c.post("/api/models/presets/999/switch",
                  json={"from_model": "a", "to_model": "b", "confirm": True}).status_code == 404
    assert c.post("/api/models/presets/1/switch",
                  json={"from_model": "a", "to_model": "b", "confirm": True,
                        "api_key": "x"}).status_code == 422


def test_offer_provider_models_toggle_is_pc_only_and_shows_in_status(isolated_db):
    assert _local().get("/api/models/status").json()["offer_provider_models"] is False
    adm = {**_session(True), **LOCAL_HDR}
    assert _remote().post("/api/settings", headers=adm,
                          json={"offer_provider_models": True}).status_code == 403
    assert _local().post("/api/settings", headers=LOCAL_HDR,
                         json={"offer_provider_models": "yes"}).status_code == 422
    r = _local().post("/api/settings", headers=LOCAL_HDR, json={"offer_provider_models": True})
    assert r.status_code == 200 and r.json()["offer_provider_models"] is True
    assert _local().get("/api/models/status").json()["offer_provider_models"] is True


def test_translate_engines_route_labels_extra_models(isolated_db):
    import json
    db.set_app_setting("offer_provider_models", True)
    db.set_app_setting(svc.CHECK_CACHE_KEY, json.dumps({"checked_at": "2026-10-01T00:00:00", "engines": {
        "claude": {"ok": True, "models": ["claude-sonnet-6"]}}}))
    items = _local().get("/api/translate/engines").json()["items"]
    claude = next(e for e in items if e["name"] == "claude")
    assert "claude-sonnet-6" in claude["models"]
    assert "newly listed" in claude["model_labels"]["claude-sonnet-6"]


def test_override_set_and_clear_are_pc_only(isolated_db):
    remote = _remote()
    adm = {**_session(True), **LOCAL_HDR}
    body = {"kind": "default", "key": "deepseek", "from_model": "deepseek-v4-flash",
            "to_model": "deepseek-v4-pro", "confirm": True}
    assert remote.post("/api/models/overrides", headers=adm, json=body).status_code == 403
    assert remote.post("/api/models/overrides/clear", headers=adm,
                       json={"kind": "default", "key": "deepseek", "confirm": True}).status_code == 403
    assert db.get_app_setting("model_overrides.defaults") is None


def test_override_set_status_stale_and_clear(isolated_db):
    c = _local()
    body = {"kind": "default", "key": "deepseek", "from_model": "deepseek-v4-flash",
            "to_model": "deepseek-v4-pro"}
    assert c.post("/api/models/overrides", json=body).status_code == 422   # no confirm
    assert db.get_app_setting("model_overrides.defaults") is None
    assert c.post("/api/models/overrides", json={**body, "confirm": True}).status_code == 200
    assert c.post("/api/models/overrides", json={**body, "confirm": True}).status_code == 409
    assert c.post("/api/models/overrides", json={**body, "to_model": "gpt-4", "confirm": True,
                                                 "from_model": "deepseek-v4-pro"}).status_code == 422
    item = next(i for i in c.get("/api/models/status").json()["items"]
                if i["kind"] == "default" and i["engine"] == "deepseek")
    assert item["model"] == "deepseek-v4-pro" and item["is_override"] is True
    assert item["builtin_model"] == "deepseek-v4-flash" and item["key"] == "deepseek"
    clear = {"kind": "default", "key": "deepseek"}
    assert c.post("/api/models/overrides/clear", json=clear).status_code == 422
    r = c.post("/api/models/overrides/clear", json={**clear, "confirm": True})
    assert r.status_code == 200 and r.json()["model"] == "deepseek-v4-flash"
    assert db.get_app_setting("model_overrides.defaults") == {}
