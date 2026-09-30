"""
/api/system/update* (api/routers/update_routes.py): every route is PC-only,
responses carry no URL, path or secret, install needs confirm=true. The
service's HTTP layer is faked (tests/test_update_service.py covers it).
"""

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import update_service as us

from tests.test_update_service import (NAME, FakePopen, _serve_release,  # noqa: F401
                                       http)

REMOTE = "https://baihe.example.com"
ROUTES = [("GET", "/api/system/update", None),
          ("POST", "/api/system/update/check", {}),
          ("POST", "/api/system/update/settings", {"auto_check": True}),
          ("POST", "/api/system/update/download", {}),
          ("POST", "/api/system/update/install", {"confirm": True})]


@pytest.fixture
def client(http):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _clean(resp):
    text = resp.text
    assert "http://" not in text and "https://" not in text
    assert db.LIBRARY_DIR not in text
    return resp


def _admin_headers():
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_route_refused_away_from_the_pc(http, method, path, body):
    remote_off = TestClient(create_app(ApiSettings()), base_url=REMOTE,
                            client=("203.0.113.9", 4000), raise_server_exceptions=False)
    assert remote_off.request(method, path, json=body).status_code == 403
    remote_on = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                           raise_server_exceptions=False)
    assert remote_on.request(method, path, json=body, headers=_admin_headers()).status_code == 403
    assert http.calls == []


def test_status_check_download_and_install_flow(client, http, monkeypatch):
    s = _clean(client.get("/api/system/update")).json()
    assert s["current"] == "0.1.0" and s["latest"] is None and s["auto_check"] is False
    assert http.calls == []                  # the status never goes to the network

    _serve_release(http)
    s = _clean(client.post("/api/system/update/check", json={})).json()
    assert s["update_available"] and s["installer_name"] == NAME and s["size"] > 0

    r = _clean(client.post("/api/system/update/download", json={}))
    assert r.status_code == 202
    us._download_thread.join(10)
    s = _clean(client.get("/api/system/update")).json()
    assert s["verified"] and s["download"] == "verified"

    monkeypatch.setattr(us, "_is_windows", lambda: True)
    monkeypatch.setattr(us.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(us.background_jobs, "active_job_ids", lambda: [])
    FakePopen.calls = []
    assert client.post("/api/system/update/install", json={"confirm": False}).status_code == 422
    assert FakePopen.calls == []
    r = _clean(client.post("/api/system/update/install", json={"confirm": True}))
    assert r.status_code == 200 and r.json() == {"launched": True, "installer_name": NAME}
    assert len(FakePopen.calls) == 1


def test_install_off_windows_is_refused(client, http, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: False)
    r = _clean(client.post("/api/system/update/install", json={"confirm": True}))
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_operation"


def test_install_without_download_is_refused(client, http, monkeypatch):
    monkeypatch.setattr(us, "_is_windows", lambda: True)
    r = _clean(client.post("/api/system/update/install", json={"confirm": True}))
    assert r.status_code == 409


def test_download_without_newer_release_is_refused(client, http):
    r = _clean(client.post("/api/system/update/download", json={}))
    assert r.status_code == 409


def test_check_failure_is_generic(client, http):
    r = _clean(client.post("/api/system/update/check", json={}))
    assert r.status_code == 503 and r.json()["error"]["message"] == "Couldn't reach GitHub."


def test_settings_toggle_and_strict_body(client, http):
    r = client.post("/api/system/update/settings", json={"auto_check": True})
    assert r.status_code == 200 and r.json()["auto_check"] is True
    assert client.post("/api/system/update/settings", json={"auto_check": "yes"}).status_code == 422
    assert client.post("/api/system/update/settings",
                       json={"auto_check": False, "repo": "x/y"}).status_code == 422
    assert json.loads(client.get("/api/system/update").text)["auto_check"] is True
