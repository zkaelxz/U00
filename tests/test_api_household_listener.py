"""
The two listeners in one process (D5): the admin app on BAIHE_API_PORT (the
PC's own: auth off, loopback only, docs, background services) and the
household app on BAIHE_API_HOUSEHOLD_PORT (sign-in on, no docs, no
background services, and nothing on it is ever "the PC").

TestClient requests here are direct loopback ones (peer 127.0.0.1, loopback
Host, no proxy headers): exactly what a reverse proxy that strips its
forwarding headers would send, which the household app must still treat as
remote.
"""

import asyncio
import signal
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient
from starlette.requests import Request

import background_jobs
import db
from api import auth as api_auth
from api.api_config import (ApiSettings, check_bind_safety, check_household_bind_safety,
                            household_settings, load_settings)
from api.server import create_app
from services import auth_service
from services.service_errors import ForbiddenError

HOUSEHOLD_PORT = 8610
ADMIN = ApiSettings(household_port=HOUSEHOLD_PORT, serve_frontend=False)


def _admin_app():
    return create_app(ADMIN)


def _household_app():
    return create_app(ADMIN, listener="household")


def _client(app, port):
    return TestClient(app, base_url=f"http://127.0.0.1:{port}", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _admin_client(app=None):
    return _client(app or _admin_app(), 8600)


def _household_client(app=None):
    return _client(app or _household_app(), HOUSEHOLD_PORT)


def _admin_session():
    user = auth_service.grant_admin_local("owner@example.com")
    return auth_service.create_session(user["id"], "pytest", "127.0.0.1")


def _h(session, **extra):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
            api_auth.CSRF_HEADER: session["csrf_token"], **extra}


def _local_only_routes(app):
    return [(sorted(methods), path) for _r, path, methods, decls
            in api_auth.iter_route_declarations(app) if decls == [("local_only", None)]]


def _concrete(path):
    import re
    return re.sub(r"\{[^}]+\}", "1", path)


# --- settings ------------------------------------------------------------------

class TestSettings:
    def test_off_unless_set(self):
        assert load_settings({}).household_port == 0
        assert load_settings({"BAIHE_API_HOUSEHOLD_PORT": " 8610 "}).household_port == 8610
        for bad in ("abc", "0", "70000", "-1"):
            with pytest.raises(ValueError):
                load_settings({"BAIHE_API_HOUSEHOLD_PORT": bad})

    def test_household_settings_are_derived_and_locked_down(self):
        admin = ApiSettings(household_port=HOUSEHOLD_PORT, allow_key_writes=True,
                            background_services=True, cookie_secure=False)
        h = household_settings(admin)
        assert (h.host, h.port) == ("127.0.0.1", HOUSEHOLD_PORT)
        assert h.auth_enabled and h.is_household and not h.background_services
        assert h.allow_key_writes is False
        assert h.cookie_secure is False   # carried over, as for the admin listener
        assert admin.listener == "admin" and not admin.is_household

    @pytest.mark.parametrize("settings", [
        ApiSettings(household_port=8600),                               # the admin port
        ApiSettings(port=8700, household_port=8700),
        ApiSettings(household_port=8756),                               # extension bridge
        ApiSettings(host="0.0.0.0", auth_mode="on", household_port=8610),   # not loopback
        ApiSettings(host="192.168.1.5", auth_mode="on", household_port=8610),
        ApiSettings(auth_mode="on", household_port=8610),               # admin must be off
        ApiSettings(environment="development", household_port=8610),
        ApiSettings(household_port=0),
    ])
    def test_bind_safety_refusals(self, settings):
        with pytest.raises(ValueError):
            check_household_bind_safety(settings)
        with pytest.raises(ValueError):
            create_app(settings, listener="household")

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_hosts_allowed(self, host):
        check_household_bind_safety(ApiSettings(host=host, household_port=HOUSEHOLD_PORT))

    def test_admin_bind_safety_unchanged(self):
        # Same semantics as before for the admin listener, household set or not.
        for hp in (0, HOUSEHOLD_PORT):
            with pytest.raises(ValueError):
                check_bind_safety(ApiSettings(host="0.0.0.0", household_port=hp))
            check_bind_safety(ApiSettings(host="0.0.0.0", auth_mode="on", household_port=hp))
            check_bind_safety(ApiSettings(household_port=hp))

    def test_household_marked_by_hand_still_checked(self):
        for s in (ApiSettings(listener="household"),
                  ApiSettings(listener="household", auth_mode="on", host="0.0.0.0")):
            with pytest.raises(ValueError):
                check_bind_safety(s)
        with pytest.raises(ValueError):
            create_app(ApiSettings(listener="household", auth_mode="on"))
        with pytest.raises(ValueError):
            create_app(ADMIN, listener="bogus")


# --- the household app never counts as the PC ------------------------------------

class TestHouseholdNeverLocal:
    @pytest.fixture(autouse=True)
    def _no_page_server(self, monkeypatch):
        import page_server
        monkeypatch.setattr(page_server, "ensure_server_started", lambda *a, **k: True)
        monkeypatch.setattr(page_server, "load_or_create_token", lambda: "tok")

    def test_every_local_only_route_refused(self, isolated_db):
        app = _household_app()
        routes = _local_only_routes(app)
        assert len(routes) > 100
        assert (["POST"], "/api/settings") in routes
        assert (["POST"], "/api/system/shutdown") in routes
        c = _household_client(app)
        s = _admin_session()
        bad = []
        for methods, path in routes:
            for method in methods:
                url = _concrete(path)
                for headers in ({"X-Baihe-Local": "1"}, _h(s, **{"X-Baihe-Local": "1"})):
                    kw = {} if method in ("GET", "HEAD", "DELETE") else {"json": {"confirm": True}}
                    r = c.request(method, url, headers=headers, **kw)
                    if r.status_code != 403 or (method != "HEAD" and r.json() != {
                            "error": {"code": "forbidden", "message": "Not allowed."}}):
                        bad.append(f"{method} {path}: {r.status_code}")
        assert not bad, "household served PC-only routes:\n  " + "\n  ".join(bad)

    def test_same_routes_work_on_admin(self, isolated_db):
        c = _admin_client()
        assert c.get("/api/extension/status").status_code == 200
        assert c.post("/api/extension/token", json={"confirm": True}).json() == {"token": "tok"}
        h = _household_client()
        assert h.get("/api/extension/status").status_code == 403
        r = h.post("/api/extension/token", json={"confirm": True})
        assert r.status_code == 403 and "tok" not in r.text

    def test_handler_never_runs(self, isolated_db, monkeypatch):
        from services import settings_service
        calls = []
        monkeypatch.setattr(settings_service, "set_settings", lambda *a, **k: calls.append(1))
        s = _admin_session()
        r = _household_client().post("/api/settings", json={}, headers=_h(s))
        assert r.status_code == 403 and calls == []

    def test_dependency_refuses_without_the_middleware(self, isolated_db):
        """local_only() itself refuses on the household app, not only the
        early gate, and is_local_request is False there."""
        app = _household_app()
        scope = {"type": "http", "method": "GET", "path": "/api/extension/status",
                 "headers": [(b"host", b"127.0.0.1:8610")], "client": ("127.0.0.1", 5000),
                 "app": app, "query_string": b""}
        request = Request(scope)
        assert api_auth.is_local_request(request) is False
        dependency = api_auth.local_only().dependency
        with pytest.raises(ForbiddenError):
            dependency(request)
        scope["app"] = _admin_app()
        assert api_auth.is_local_request(Request(scope)) is True

    def test_meta_and_me_say_not_the_pc(self, isolated_db):
        assert _admin_client().get("/api/meta").json()["local"] is True
        assert _admin_client().get("/api/auth/me").json()["zone"] == "pc"
        s = _admin_session()
        h = _household_client()
        assert h.get("/api/meta").json()["local"] is False
        assert h.get("/api/auth/me").json()["zone"] == "internet"
        assert h.get("/api/auth/me", headers=_h(s)).json()["zone"] == "internet"

    def test_tesseract_path_refused(self, isolated_db, monkeypatch):
        from services import transcribe_service
        started = []
        monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                            lambda *a, **k: started.append(k) or {"job_id": "j"})
        did = db.create_drama(title_en="T", source_language="zh")
        url = f"/api/transcribe/dramas/{did}/run"
        s = _admin_session()
        r = _household_client().post(url, json={"tesseract_cmd": "/usr/bin/tesseract"},
                                     headers=_h(s))
        assert r.status_code == 403 and started == []
        r = _admin_client().post(url, json={"tesseract_cmd": "/usr/bin/tesseract"})
        assert r.status_code == 200 and started[-1]["tesseract_cmd"] == "/usr/bin/tesseract"

    def test_glossary_overwrite_refused(self, isolated_db, monkeypatch):
        from services import glossary_service
        calls = []
        monkeypatch.setattr(glossary_service, "import_glossary_text",
                            lambda *a, **k: calls.append(k))
        did = db.create_drama(title_en="T", source_language="zh")
        body = {"text": "a,b", "overwrite_existing": True, "confirm": True}
        s = _admin_session()
        r = _household_client().post(f"/api/glossary/dramas/{did}/import", json=body,
                                     headers=_h(s))
        assert r.status_code == 403 and calls == []

    def test_global_style_refused(self, isolated_db, monkeypatch):
        from services import review_extras_service
        monkeypatch.setattr(review_extras_service, "style_scope_is_global", lambda did: True)
        learned = []
        monkeypatch.setattr(review_extras_service, "learn_style",
                            lambda *a, **k: learned.append(1))
        did = db.create_drama(title_en="T", source_language="zh")
        s = _admin_session()
        r = _household_client().post(f"/api/review-extras/dramas/{did}/style/learn",
                                     json={"engine": "ollama"}, headers=_h(s))
        assert r.status_code == 403 and learned == []

    def test_settings_paths_blanked_even_for_an_admin(self, isolated_db):
        from services import settings_service
        settings_service.set_settings({"tesseract_cmd": "/opt/tess/tesseract"})
        s = _admin_session()
        prefs = _household_client().get("/api/settings", headers=_h(s)).json()["preferences"]
        assert prefs["tesseract_cmd"] == "" and prefs["tesseract_cmd_configured"] is True
        prefs = _admin_client().get("/api/settings").json()["preferences"]
        assert prefs["tesseract_cmd"] == "/opt/tess/tesseract"


# --- docs, admin unchanged ------------------------------------------------------

class TestDocsAndAdmin:
    def test_docs_only_on_admin(self, isolated_db):
        a = _admin_client()
        assert a.get("/api/docs").status_code == 200
        assert a.get("/api/openapi.json").status_code == 200
        h = _household_client()
        s = _admin_session()
        for p in ("/api/docs", "/api/openapi.json", "/api/redoc"):
            r = h.get(p)
            assert r.status_code == 401 and "openapi" not in r.text.lower()
            r = h.get(p, headers=_h(s))
            assert r.status_code == 404 and "openapi" not in r.text.lower()

    @pytest.mark.parametrize("headers", [{"X-Forwarded-For": "203.0.113.9"},
                                         {"Host": "baihe.example.com"}])
    def test_admin_still_refuses_proxied_requests(self, isolated_db, headers):
        r = _admin_client().get("/api/health", headers=headers)
        assert r.status_code == 403

    def test_household_needs_sign_in(self, isolated_db):
        h = _household_client()
        assert h.get("/api/health").status_code == 200
        assert h.get("/api/library/dramas").status_code == 401
        assert h.get("/api/library/dramas", headers=_h(_admin_session())).status_code == 200

    def test_disabled_household_leaves_admin_as_today(self):
        today, now = create_app(ApiSettings()), _admin_app()
        assert ([m.cls.__name__ for m in today.user_middleware]
                == [m.cls.__name__ for m in now.user_middleware])
        assert ({p for _r, p, _m, _d in api_auth.iter_route_declarations(today)}
                == {p for _r, p, _m, _d in api_auth.iter_route_declarations(now)})
        assert today.docs_url == now.docs_url == "/api/docs"

    def test_household_lifespan_does_no_pc_shutdown_work(self, isolated_db, monkeypatch):
        from services import lncrawl_service
        calls = []
        monkeypatch.setattr(lncrawl_service, "shutdown", lambda: calls.append(1))
        with TestClient(_household_app()):
            pass
        assert calls == []
        with TestClient(_admin_app()):
            pass
        assert calls == [1]


# --- one job list ----------------------------------------------------------------

def test_job_started_on_one_app_is_seen_and_cancelled_on_the_other(isolated_db, monkeypatch):
    from services import narration_service
    did = db.create_drama(title_en="T", source_language="zh")
    job_id = f"narration_{did}"
    seen_cancel = threading.Event()

    def work(jid):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if background_jobs.is_cancel_requested(jid):
                seen_cancel.set()
                return
            time.sleep(0.01)

    def start(drama_id, **_kw):
        assert background_jobs.start_job(job_id, work, job_id)
        return {"job_id": job_id}
    monkeypatch.setattr(narration_service, "start_narration_run", start)
    s = _admin_session()
    household, admin = _household_client(), _admin_client()
    r = household.post(f"/api/narration/dramas/{did}/run", json={"engine": "ollama"},
                       headers=_h(s))
    assert r.status_code == 200, r.text
    try:
        assert admin.get(f"/api/jobs/{job_id}").json()["status"] == "running"
        assert household.get(f"/api/jobs/{job_id}", headers=_h(s)).status_code == 200
        r = admin.post(f"/api/jobs/{job_id}/cancel", headers={"X-Baihe-Local": "1"})
        assert r.status_code == 200
        assert seen_cancel.wait(5)
    finally:
        background_jobs.request_cancel(job_id)
        deadline = time.monotonic() + 5
        while ((background_jobs.get_status(job_id) or {}).get("status") in ("running", "queued")
               and time.monotonic() < deadline):
            time.sleep(0.02)


# --- python -m api -----------------------------------------------------------------

class _FakeServer:
    def __init__(self, fail=False):
        self.should_exit = self.force_exit = self.stopped = False
        self.fail = fail

    async def serve(self):
        if self.fail:
            raise SystemExit(1)       # what uvicorn does when the port is taken
        while not self.should_exit:
            await asyncio.sleep(0.005)
        self.stopped = True


class TestRunServers:
    def test_a_failed_start_stops_the_other(self):
        from api.__main__ import _run_servers
        ok, bad = _FakeServer(), _FakeServer(fail=True)
        assert _run_servers([ok, bad]) is True
        assert ok.stopped

    def test_one_stopping_stops_both(self):
        from api.__main__ import _run_servers
        a, b = _FakeServer(), _FakeServer()
        threading.Timer(0.05, lambda: setattr(b, "should_exit", True)).start()
        assert _run_servers([a, b]) is False
        assert a.stopped and b.stopped

    def test_signal_handlers_restored(self):
        from api.__main__ import _run_servers, _stop_all
        before = signal.getsignal(signal.SIGINT)
        a, b = _FakeServer(), _FakeServer()
        threading.Timer(0.05, lambda: _stop_all([a, b])).start()
        _run_servers([a, b])
        assert signal.getsignal(signal.SIGINT) is before

    def _serve(self, monkeypatch, env):
        import process_guard
        import uvicorn
        from api import __main__ as main_mod
        from services import shutdown_service
        for k in ("BAIHE_API_HOUSEHOLD_PORT", "BAIHE_API_PORT", "BAIHE_API_HOST",
                  "BAIHE_API_AUTH", "BAIHE_API_ENV"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(process_guard, "contain_children", lambda: True)
        monkeypatch.setattr(process_guard, "install_console_close_handler", lambda fn: None)
        monkeypatch.setattr(shutdown_service, "take_token_from_environment", lambda: None)
        monkeypatch.setattr(shutdown_service, "_stopper", None)
        monkeypatch.setattr(shutdown_service, "_background_stopper", None)
        cleaned, ran, single = [], [], []
        monkeypatch.setattr(shutdown_service, "clean_shutdown", lambda *a: cleaned.append(1))

        class Single:
            def __init__(self, config):
                self.config, self.should_exit = config, False
                if type(self) is Single:     # not _quiet_server's subclass
                    single.append(config)

            def run(self):
                pass
        monkeypatch.setattr(uvicorn, "Server", Single)
        monkeypatch.setattr(main_mod, "_run_servers", lambda servers: ran.append(servers))
        main_mod._serve()
        return single, ran, cleaned, shutdown_service

    def test_without_household_port_one_server_as_today(self, monkeypatch):
        single, ran, cleaned, _svc = self._serve(monkeypatch, {})
        assert ran == [] and len(single) == 1 and cleaned == [1]
        assert (single[0].app, single[0].host, single[0].port) == ("api.server:app",
                                                                  "127.0.0.1", 8600)

    def test_with_household_port_two_servers_one_stopper(self, monkeypatch):
        single, ran, cleaned, svc = self._serve(monkeypatch,
                                                {"BAIHE_API_HOUSEHOLD_PORT": "8610"})
        assert single == [] and len(ran) == 1 and cleaned == [1]
        admin, household = ran[0]
        assert [s.config.port for s in ran[0]] == [8600, 8610]
        assert {s.config.host for s in ran[0]} == {"127.0.0.1"}
        assert not admin.config.app.state.settings.is_household
        assert household.config.app.state.settings.is_household
        svc._stopper()
        assert admin.should_exit and household.should_exit

    def test_unsafe_household_port_refused_at_startup(self, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8756"})
        assert "8756" in str(exc.value)
