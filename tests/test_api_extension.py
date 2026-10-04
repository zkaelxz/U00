"""Browser-extension bridge control routes (API batch 1): status, on/off and
the token, all local_only(). page_server and the scheduler are faked: no
thread, no port."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import page_server
from api import auth as api_auth
from api import background
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from sources import store as src_store

REMOTE = "https://baihe.example.com"


@pytest.fixture
def fakes(isolated_db, monkeypatch):
    from sources import chapter_check
    calls = {"scheduler": 0, "page_server": 0, "stop": 0}
    state = {"on": False}

    def serve(port=page_server.DEFAULT_PORT):
        calls["page_server"] += 1
        state["on"] = True
        return True

    def stop():
        calls["stop"] += 1
        was_on, state["on"] = state["on"], False
        return was_on

    def sched():
        calls["scheduler"] += 1

    monkeypatch.setattr(page_server, "ensure_server_started", serve)
    monkeypatch.setattr(page_server, "stop_server", stop)
    monkeypatch.setattr(page_server, "server_running", lambda: state["on"])
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", sched)
    monkeypatch.setattr(background, "_started", None)
    return calls


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _token():
    return page_server.load_or_create_token()


def _no_leak(r, token):
    assert token not in r.text
    assert str(page_server.DEFAULT_PORT) not in r.text
    assert db.LIBRARY_DIR not in r.text


def test_create_app_in_tests_starts_nothing(fakes):
    with TestClient(create_app(ApiSettings())) as c:
        assert c.get("/api/extension/status").status_code == 200
        c.post("/api/extension/enabled", json={"enabled": True})
    assert fakes == {"scheduler": 0, "page_server": 0, "stop": 0}
    assert src_store.get_setting("page_server_enabled") is True


def test_status_enable_disable_flow(fakes):
    token = _token()
    c = TestClient(create_app(ApiSettings(background_services=True)),
                   raise_server_exceptions=False)
    r = c.get("/api/extension/status")
    assert r.json() == {"enabled": False, "running": False}
    _no_leak(r, token)
    r = c.post("/api/extension/enabled", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "running": True, "restart_needed": False}
    _no_leak(r, token)
    assert fakes["page_server"] == 1
    r = c.post("/api/extension/enabled", json={"enabled": False})
    assert r.json() == {"enabled": False, "running": False, "restart_needed": False}
    assert src_store.get_setting("page_server_enabled") is False
    _no_leak(r, token)
    assert fakes["page_server"] == 1 and fakes["stop"] == 1
    assert c.get("/api/extension/status").json() == {"enabled": False, "running": False}


def test_off_when_not_running_only_persists(fakes):
    src_store.set_setting("page_server_enabled", True)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.post("/api/extension/enabled", json={"enabled": False})
    assert r.json() == {"enabled": False, "running": False, "restart_needed": False}
    assert src_store.get_setting("page_server_enabled") is False
    assert fakes["page_server"] == 0


def test_on_again_after_off_starts_it_again(fakes):
    c = TestClient(create_app(ApiSettings(background_services=True)),
                   raise_server_exceptions=False)
    for enabled, running in ((True, True), (False, False), (True, True)):
        r = c.post("/api/extension/enabled", json={"enabled": enabled})
        assert r.json() == {"enabled": enabled, "running": running, "restart_needed": False}
    assert fakes["page_server"] == 2 and fakes["stop"] == 1


def test_an_off_racing_an_on_is_applied_after_it(fakes, monkeypatch):
    import threading
    from services import extension_service
    entered, release = threading.Event(), threading.Event()
    real_start = page_server.ensure_server_started

    def slow_start(port=page_server.DEFAULT_PORT):
        entered.set()
        release.wait(5)
        return real_start(port)
    monkeypatch.setattr(page_server, "ensure_server_started", slow_start)
    on = threading.Thread(target=extension_service.set_enabled, args=(True,))
    on.start()
    assert entered.wait(5)
    result = {}
    off = threading.Thread(target=lambda: result.update(extension_service.set_enabled(False)))
    off.start()
    off.join(0.2)
    assert off.is_alive()            # waits for the start instead of interleaving
    release.set()
    on.join(5)
    off.join(5)
    assert result == {"enabled": False, "running": False, "restart_needed": False}
    assert extension_service.get_status() == {"enabled": False, "running": False}


def test_off_reports_restart_needed_if_it_could_not_stop(fakes, monkeypatch):
    c = TestClient(create_app(ApiSettings(background_services=True)),
                   raise_server_exceptions=False)
    c.post("/api/extension/enabled", json={"enabled": True})
    monkeypatch.setattr(page_server, "stop_server", lambda: False)
    r = c.post("/api/extension/enabled", json={"enabled": False})
    assert r.json() == {"enabled": False, "running": True, "restart_needed": True}


def test_enable_without_background_services_only_persists(fakes):
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.post("/api/extension/enabled", json={"enabled": True})
    assert r.json() == {"enabled": True, "running": False, "restart_needed": False}
    assert fakes["page_server"] == 0


def test_startup_hook_starts_page_server_only_when_enabled(fakes):
    app = create_app(ApiSettings(background_services=True))
    with TestClient(app):
        pass
    assert fakes == {"scheduler": 1, "page_server": 0, "stop": 0}
    background._started = None
    src_store.set_setting("page_server_enabled", True)
    with TestClient(app):
        pass
    assert fakes["page_server"] == 1


def test_token_only_in_its_own_response(fakes):
    token = _token()
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.post("/api/extension/token", json={"confirm": True})
    assert r.status_code == 200 and r.json() == {"token": token}
    assert r.headers["cache-control"] == "no-store"
    for body in ({}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1},
                 {"confirm": True, "extra": 1}):
        r = c.post("/api/extension/token", json=body)
        assert r.status_code == 422, body
        _no_leak(r, token)
    assert c.get("/api/extension/token").status_code == 405
    for body in ({"enabled": "yes"}, {}, {"enabled": True, "port": 1}):
        assert c.post("/api/extension/enabled", json=body).status_code == 422, body


def test_token_is_not_logged(fakes, caplog):
    import logging
    token = _token()
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    with caplog.at_level(logging.DEBUG):
        c.post("/api/extension/token", json={"confirm": True})
    assert token not in caplog.text


ROUTES = (("get", "/api/extension/status", None),
          ("post", "/api/extension/enabled", {"enabled": True}),
          ("post", "/api/extension/token", {"confirm": True}))


def test_remote_refused_even_for_admin(fakes):
    token = _token()
    app = create_app(ApiSettings(auth_mode="on", background_services=True))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    for method, path, body in ROUTES:
        kw = {"json": body} if body is not None else {}
        for headers in ({}, h):
            r = getattr(remote, method)(path, headers=headers, **kw)
            assert r.status_code == 403, (path, headers)
            _no_leak(r, token)
    local = _local(app)
    for header in ("X-Forwarded-For", "Forwarded", "Via"):
        for method, path, body in ROUTES:
            kw = {"json": body} if body is not None else {}
            r = getattr(local, method)(path, headers={header: "127.0.0.1"}, **kw)
            assert r.status_code == 403, (path, header)
            _no_leak(r, token)
    r = local.post("/api/extension/token", json={"confirm": True},
                   headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    _no_leak(r, token)
    assert fakes["page_server"] == 0
    assert src_store.get_setting("page_server_enabled") is False
    assert local.post("/api/extension/token", json={"confirm": True}).json() == {"token": token}
