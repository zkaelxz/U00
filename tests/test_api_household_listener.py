"""
The two listeners in one process (D5): the admin app on BAIHE_API_PORT (the
PC's own: auth off, loopback only, docs, background services) and the
household app on BAIHE_API_HOUSEHOLD_PORT (sign-in on, no docs, no
background services, and nothing on it is ever "the PC").

TestClient requests here are direct loopback ones (peer 127.0.0.1, no proxy
headers; to the household app with the BAIHE_PUBLIC_URL Host, the only one it
answers): exactly what a reverse proxy that strips its forwarding headers
would send, which the household app must still treat as remote.
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
PUBLIC_HOST = "baihe.example.com"
SIGN_IN = {"google_client_id": "cid", "google_client_secret": "s3cr3t-value",
           "public_url": f"https://{PUBLIC_HOST}"}
SIGN_IN_ENV = {"BAIHE_GOOGLE_CLIENT_ID": "cid", "BAIHE_GOOGLE_CLIENT_SECRET": "s3cr3t-value",
               "BAIHE_PUBLIC_URL": f"https://{PUBLIC_HOST}"}
ADMIN = ApiSettings(household_port=HOUSEHOLD_PORT, serve_frontend=False, **SIGN_IN)


def _admin_app():
    return create_app(ADMIN)


def _household_app():
    return create_app(ADMIN, listener="household")


def _client(app, base_url):
    return TestClient(app, base_url=base_url, client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _admin_client(app=None):
    return _client(app or _admin_app(), "http://127.0.0.1:8600")


def _household_client(app=None):
    # What the reverse proxy on this PC passes on: plain http, public Host.
    return _client(app or _household_app(), f"http://{PUBLIC_HOST}")


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
                            background_services=True, cookie_secure=False, **SIGN_IN)
        h = household_settings(admin)
        assert (h.host, h.port) == ("127.0.0.1", HOUSEHOLD_PORT)
        assert h.auth_enabled and h.is_household and not h.background_services
        assert h.allow_key_writes is False
        assert h.cookie_secure is False   # carried over, as for the admin listener
        assert admin.listener == "admin" and not admin.is_household

    @pytest.mark.parametrize("settings", [
        ApiSettings(household_port=8600, **SIGN_IN),                    # the admin port
        ApiSettings(port=8700, household_port=8700, **SIGN_IN),
        ApiSettings(household_port=8756, **SIGN_IN),                    # extension bridge
        ApiSettings(host="0.0.0.0", auth_mode="on", household_port=8610, **SIGN_IN),
        ApiSettings(host="192.168.1.5", auth_mode="on", household_port=8610, **SIGN_IN),
        ApiSettings(auth_mode="on", household_port=8610, **SIGN_IN),    # admin must be off
        ApiSettings(environment="development", household_port=8610, **SIGN_IN),
        ApiSettings(household_port=0, **SIGN_IN),
        ApiSettings(household_port=8610),                               # no sign-in
        ApiSettings(household_port=8610, **{**SIGN_IN, "google_client_id": ""}),
        ApiSettings(household_port=8610, **{**SIGN_IN, "google_client_secret": ""}),
        ApiSettings(household_port=8610, **{**SIGN_IN, "public_url": ""}),
    ])
    def test_bind_safety_refusals(self, settings):
        with pytest.raises(ValueError):
            check_household_bind_safety(settings)
        with pytest.raises(ValueError):
            create_app(settings, listener="household")

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_hosts_allowed(self, host):
        check_household_bind_safety(ApiSettings(host=host, household_port=HOUSEHOLD_PORT,
                                                **SIGN_IN))

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


# --- Host allowlist and security headers (household only) ------------------------

def _gate(public_url=f"https://{PUBLIC_HOST}"):
    """HouseholdGate around an app that records whether it was reached and
    answers with its own CSP (a route that sets a stricter one keeps it)."""
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["type"])
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-security-policy", b"default-src 'none'")]})
            await send({"type": "http.response.body", "body": b"ok"})
    return api_auth.HouseholdGate(inner, public_url=public_url), reached


def _raw(app, headers, scope_type="http", client=("127.0.0.1", 5000)):
    scope = {"type": scope_type, "method": "GET", "path": "/api/health",
             "raw_path": b"/api/health", "query_string": b"", "headers": headers,
             "client": client, "server": ("127.0.0.1", HOUSEHOLD_PORT), "scheme": "http",
             "http_version": "1.1", "root_path": "", "asgi": {"version": "3.0"}}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)
    asyncio.run(app(scope, receive, send))
    return sent


def _start_headers(sent):
    start = next(m for m in sent if m["type"] == "http.response.start")
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}


SECURITY_HEADER_NAMES = ("content-security-policy", "x-content-type-options",
                         "x-frame-options", "referrer-policy", "permissions-policy")


class TestHostAllowlist:
    @pytest.mark.parametrize("host", [PUBLIC_HOST, "BAIHE.Example.COM", f"{PUBLIC_HOST}.",
                                      f"{PUBLIC_HOST}:443", f"{PUBLIC_HOST.upper()}.:443"])
    def test_public_host_accepted(self, isolated_db, host):
        r = _household_client().get("/api/health", headers={"Host": host})
        assert r.status_code == 200, host

    @pytest.mark.parametrize("host", [
        "evil.example", f"{PUBLIC_HOST}.evil.example", f"evil.{PUBLIC_HOST}",
        "127.0.0.1", f"127.0.0.1:{HOUSEHOLD_PORT}", "localhost", f"[::1]:{HOUSEHOLD_PORT}",
        f"{PUBLIC_HOST}:80", f"{PUBLIC_HOST}:8443", f"{PUBLIC_HOST}:", f"{PUBLIC_HOST}:abc",
        f"{PUBLIC_HOST}:99999", f"{PUBLIC_HOST}..", f"user@{PUBLIC_HOST}",
        f"{PUBLIC_HOST}/x", "", "[baihe]"])
    def test_other_hosts_refused_before_routing(self, isolated_db, monkeypatch, host):
        from services import settings_service
        calls = []
        monkeypatch.setattr(settings_service, "set_settings", lambda *a, **k: calls.append(1))
        c = _household_client()
        s = _admin_session()
        for method, path in (("GET", "/api/health"), ("GET", "/api/meta"),
                             ("GET", "/api/library/dramas"), ("GET", "/"),
                             ("POST", "/api/settings")):
            r = c.request(method, path, headers=_h(s, Host=host),
                          **({"json": {}} if method == "POST" else {}))
            assert r.status_code == 400, (host, path, r.status_code)
            assert r.json() == {"error": {"code": "invalid_host", "message": "Unknown host."}}
            assert all(n in r.headers for n in SECURITY_HEADER_NAMES)
        assert calls == []

    def test_x_forwarded_host_ignored(self, isolated_db):
        c = _household_client()
        r = c.get("/api/health", headers={"Host": "evil.example",
                                          "X-Forwarded-Host": PUBLIC_HOST})
        assert r.status_code == 400
        r = c.get("/api/health", headers={"X-Forwarded-Host": "evil.example"})
        assert r.status_code == 200

    def test_missing_or_repeated_host_refused(self):
        gate, reached = _gate()
        for headers in ([], [(b"host", PUBLIC_HOST.encode())] * 2,
                        [(b"host", PUBLIC_HOST.encode()), (b"host", b"evil.example")]):
            status, _h2 = _start_headers(_raw(gate, headers))
            assert status == 400
        assert reached == []
        status, _h2 = _start_headers(_raw(gate, [(b"host", PUBLIC_HOST.encode())]))
        assert status == 200 and reached == ["http"]

    def test_websocket_with_other_host_closed(self):
        gate, reached = _gate()
        sent = _raw(gate, [(b"host", b"evil.example")], scope_type="websocket")
        assert sent == [{"type": "websocket.close", "code": 1008}] and reached == []

    @pytest.mark.parametrize("public_url,ok,bad", [
        ("https://[2001:db8::1]", ["[2001:db8::1]", "[2001:DB8:0::1]:443"],
         ["[2001:db8::2]", "2001:db8::1", "[2001:db8::1]:80", "[::1]"]),
        (f"https://{PUBLIC_HOST}:8443", [f"{PUBLIC_HOST}:8443", f"{PUBLIC_HOST}.:8443"],
         [PUBLIC_HOST, f"{PUBLIC_HOST}:443"]),
        ("https://Baihe.Example.com.", [PUBLIC_HOST, f"{PUBLIC_HOST}."], ["example.com"]),
        ("https://bücher.example", ["xn--bcher-kva.example"], ["bucher.example"]),
    ])
    def test_public_url_forms(self, public_url, ok, bad):
        gate, _reached = _gate(public_url)
        for host in ok:
            assert _start_headers(_raw(gate, [(b"host", host.encode())]))[0] == 200, host
        for host in bad:
            assert _start_headers(_raw(gate, [(b"host", host.encode())]))[0] == 400, host

    def test_no_public_url_refuses_everything(self):
        gate, reached = _gate("")
        assert _start_headers(_raw(gate, [(b"host", b"localhost")]))[0] == 400
        assert reached == []

    @pytest.mark.parametrize("host", ["127.0.0.1:8600", "localhost:8600", "[::1]:8600",
                                      "127.0.0.1", "localhost"])
    def test_admin_listener_keeps_loopback_hosts(self, isolated_db, host):
        a = _admin_client()
        assert a.get("/api/health", headers={"Host": host}).status_code == 200
        assert a.get("/api/health", headers={"Host": PUBLIC_HOST}).status_code == 403


class TestSecurityHeaders:
    def _dist(self, tmp_path):
        (tmp_path / "index.html").write_text("<!doctype html><title>t</title>")
        return tmp_path

    def test_on_every_household_reply(self, isolated_db, tmp_path):
        from dataclasses import replace
        app = create_app(replace(ADMIN, serve_frontend=True), frontend_dist=self._dist(tmp_path),
                         listener="household")
        c = _household_client(app)
        s = _admin_session()
        replies = [c.get("/api/health"), c.get("/api/library/dramas"),
                   c.get("/api/library/dramas", headers=_h(s)), c.get("/api/nope", headers=_h(s)),
                   c.get("/api/settings/keys", headers=_h(s)), c.get("/"),
                   c.post("/api/settings", json={}, headers=_h(s))]
        assert [r.status_code for r in replies] == [200, 401, 200, 404, 404, 200, 403]
        for r in replies:
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["referrer-policy"] == "same-origin"
            assert "camera=()" in r.headers["permissions-policy"]
            csp = r.headers["content-security-policy"]
            assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
            assert "default-src 'self'" in csp and "connect-src 'self'" in csp
            assert "strict-transport-security" not in r.headers   # plain http, no proxy word

    def test_route_set_header_kept(self):
        gate, _reached = _gate()
        _status, headers = _start_headers(_raw(gate, [(b"host", PUBLIC_HOST.encode())]))
        assert headers["content-security-policy"] == "default-src 'none'"
        assert headers["x-frame-options"] == "DENY"

    def test_hsts_only_over_https_from_the_proxy_on_this_pc(self, isolated_db):
        c = _household_client()
        r = c.get("/api/health", headers={"X-Forwarded-Proto": "https"})
        assert r.headers["strict-transport-security"] == "max-age=31536000"
        for proto in ("http", "HTTPS, http", ""):
            r = c.get("/api/health", headers={"X-Forwarded-Proto": proto})
            assert "strict-transport-security" not in r.headers, proto
        gate, _reached = _gate()
        host = (b"host", PUBLIC_HOST.encode())
        https = (b"x-forwarded-proto", b"https")
        assert "strict-transport-security" in _start_headers(_raw(gate, [host, https]))[1]
        for headers, client in (([host, https, https], ("127.0.0.1", 5000)),
                                ([host, https], ("203.0.113.9", 5000)),
                                ([host, https], None)):
            got = _start_headers(_raw(gate, headers, client=client))[1]
            assert "strict-transport-security" not in got

    def test_admin_replies_unchanged(self, isolated_db, tmp_path):
        from dataclasses import replace
        app = create_app(replace(ADMIN, serve_frontend=True), frontend_dist=self._dist(tmp_path))
        a = _admin_client(app)
        for r in (a.get("/api/health"), a.get("/"), a.get("/api/library/dramas")):
            assert r.status_code == 200
            for name in ("content-security-policy", "x-frame-options", "referrer-policy",
                         "permissions-policy", "strict-transport-security"):
                assert name not in r.headers
        assert "HouseholdGate" not in [m.cls.__name__ for m in app.user_middleware]

    def test_session_cookies_keep_their_flags(self, isolated_db):
        """Sign-in cookies set on the household listener stay __Host-,
        Secure, HttpOnly (session) and SameSite, with plain-http proxying."""
        from fastapi import Response
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": [
            (b"host", PUBLIC_HOST.encode())], "client": ("127.0.0.1", 5000),
            "app": _household_app(), "query_string": b"", "scheme": "http"})
        response = Response()
        api_auth.set_session_cookie(response, request, "tok")
        api_auth.set_csrf_cookie(response, request, "csrf")
        cookies = response.headers.getlist("set-cookie")
        session = next(c for c in cookies if c.startswith(api_auth.COOKIE_NAME + "="))
        csrf = next(c for c in cookies if c.startswith(api_auth.CSRF_COOKIE_NAME + "="))
        for cookie in (session, csrf):
            assert "Secure" in cookie and "Path=/" in cookie and "Domain" not in cookie
        assert "HttpOnly" in session and "SameSite=lax" in session
        assert "HttpOnly" not in csrf and "SameSite=strict" in csrf


def test_meta_has_no_environment_on_household(isolated_db):
    assert _admin_client().get("/api/meta").json()["environment"] == "production"
    assert _household_client().get("/api/meta").json()["environment"] == ""


def test_startup_sweeps_stale_sessions_on_the_admin_listener_only(isolated_db, monkeypatch):
    from dataclasses import replace
    from api import background
    from services import jobs_service
    for name in ("start_background_services", "start_gpu_queue_poller",
                 "start_reeval_scheduler", "stop_gpu_queue_poller", "stop_reeval_scheduler"):
        monkeypatch.setattr(background, name, lambda: None)
    monkeypatch.setattr(jobs_service, "sweep_stale_job_records", lambda: 0)
    swept = []
    monkeypatch.setattr(auth_service, "sweep_stale_sessions", lambda: swept.append(1) or 0)
    with TestClient(create_app(replace(ADMIN, background_services=True), listener="household")):
        pass
    assert swept == []
    with TestClient(create_app(replace(ADMIN, background_services=True))):
        pass
    assert swept == [1]


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
        from services import settings_service
        for k in ("BAIHE_API_HOUSEHOLD_PORT", "BAIHE_API_PORT", "BAIHE_API_HOST",
                  "BAIHE_API_AUTH", "BAIHE_API_ENV", *SIGN_IN_ENV):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setattr(settings_service, "_read_env_file", lambda *a, **k: {})
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
                                                {"BAIHE_API_HOUSEHOLD_PORT": "8610",
                                                 **SIGN_IN_ENV})
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
            self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8756", **SIGN_IN_ENV})
        assert "8756" in str(exc.value)

    @pytest.mark.parametrize("missing", list(SIGN_IN_ENV))
    def test_household_port_without_sign_in_refused_at_startup(self, monkeypatch, missing):
        env = {"BAIHE_API_HOUSEHOLD_PORT": "8610", **SIGN_IN_ENV}
        del env[missing]
        with pytest.raises(SystemExit) as exc:
            self._serve(monkeypatch, env)
        message = str(exc.value)
        assert message.startswith("ERROR: ") and "sign-in" in message
        assert all(name in message for name in SIGN_IN_ENV)
        assert "s3cr3t-value" not in message and PUBLIC_HOST not in message

    @staticmethod
    def _warned(capsys):
        import applog
        err = capsys.readouterr().err
        logged = [ln for ln in applog.tail() if "single port" in ln]
        return "Migrating from single-port sign-in" in err, logged

    def test_single_port_sign_in_warns_but_starts(self, isolated_db, monkeypatch, capsys):
        single, ran, _cleaned, _svc = self._serve(monkeypatch, {"BAIHE_API_AUTH": "on"})
        assert len(single) == 1 and ran == []
        printed, logged = self._warned(capsys)
        assert printed and len(logged) == 1 and "WARNING" in logged[0]
        assert "two-port setup" in logged[0]

    def test_no_warning_with_auth_off(self, isolated_db, monkeypatch, capsys):
        self._serve(monkeypatch, {"BAIHE_API_AUTH": "off"})
        assert self._warned(capsys) == (False, [])

    def test_no_warning_with_household_port_and_auth_off(self, isolated_db, monkeypatch, capsys):
        self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8610", **SIGN_IN_ENV})
        assert self._warned(capsys) == (False, [])

    def test_auth_on_with_household_port_still_refused(self, isolated_db, monkeypatch, capsys):
        with pytest.raises(SystemExit) as exc:
            self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8610", "BAIHE_API_AUTH": "on",
                                      **SIGN_IN_ENV})
        assert "BAIHE_API_AUTH=off" in str(exc.value)
        assert self._warned(capsys) == (False, [])

    def test_warning_only_for_the_single_port_sign_in_setup(self):
        from api.api_config import single_port_sign_in_warning
        assert single_port_sign_in_warning(ApiSettings(auth_mode="on"))
        assert not single_port_sign_in_warning(ApiSettings(auth_mode="off"))
        assert not single_port_sign_in_warning(ApiSettings(auth_mode="off", household_port=8610))
        assert not single_port_sign_in_warning(
            household_settings(ApiSettings(auth_mode="off", household_port=8610, **SIGN_IN)))
