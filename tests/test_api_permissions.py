"""
Step 133: the permission layer (api/auth.py) end to end.

THE STATIC TEST (`TestEveryRouteDeclared`) walks every route the app can
dispatch to and fails if any lacks exactly one declaration. The rest drives
the real app through TestClient in both modes (BAIHE_API_AUTH off/on),
including the bypass attempts from the step's adversarial review.

TestClient's default peer is "testclient" and its default Host is
"testserver", i.e. a *remote* request; `_local()` builds a direct loopback one.
"""

import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import APIRouter, FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from starlette.requests import Request

import db
from api import auth as api_auth
from api.api_config import ApiSettings, check_bind_safety
from api.server import create_app
from services import auth_service
from services.service_errors import RateLimitedError

HOW_TO_DECLARE = (
    "Every route needs exactly one of dependencies=[require_permission(\"x.y\")], "
    "[public_route()], [local_only()] or (own-session routes under /api/auth/ only) "
    "[authenticated()] from api/auth.py on its decorator, and a row in "
    "the route table in docs/remote-access-decision.md.")

# Starlette routes FastAPI itself adds for the interactive docs. Only served
# with auth off (loopback-only); create_app drops them when auth is on.
DOCS_PATHS = {"/api/openapi.json", "/api/docs", "/docs/oauth2-redirect"}
REMOTE = "https://baihe.example.com"


@pytest.fixture
def dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>FAKE-INDEX</html>")
    (d / "assets" / "app.js").write_text("console.log(1)")
    return d


def _app(auth="on", dist_dir=None, **kw):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=dist_dir is not None, **kw),
                      frontend_dist=dist_dir)


def _remote(app, **kw):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False, **kw)


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _user(*extra, admin=False):
    if admin:
        u = auth_service.grant_admin_local("admin@example.com")
    else:
        u = auth_service.add_user("kid@example.com")
        for p in extra:
            auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return u, s


def _user_named(email, *extra):
    u = auth_service.add_user(email)
    for p in extra:
        auth_service.grant_permission(u["id"], p)
    return u, auth_service.create_session(u["id"])


def _h(session, csrf=True, **extra):
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}
    if csrf:
        h[api_auth.CSRF_HEADER] = session["csrf_token"]
    h.update(extra)
    return h


# --- the static test ---------------------------------------------------------

def _undeclared(app):
    bad = []
    for route, path, methods, decls in api_auth.iter_route_declarations(app):
        if path in DOCS_PATHS and not app.state.settings.auth_enabled:
            continue
        if len(decls) != 1:
            bad.append(f"{sorted(methods)} {path}: {decls or 'no declaration'}")
    return bad


class TestEveryRouteDeclared:
    @pytest.mark.parametrize("auth", ["off", "on"])
    def test_every_route_has_exactly_one_declaration(self, auth, dist):
        app = _app(auth, dist)
        bad = _undeclared(app)
        assert not bad, "Routes without exactly one permission declaration:\n  " + \
            "\n  ".join(bad) + "\n" + HOW_TO_DECLARE

    def test_household_listener_has_the_same_declared_routes(self, dist):
        """The household app (D5) is the same route table with auth on."""
        household = create_app(ApiSettings(household_port=8610, google_client_id="cid",
                                           google_client_secret="s",
                                           public_url="https://baihe.example.com"),
                               frontend_dist=dist, listener="household")
        bad = _undeclared(household)
        assert not bad, "Routes without exactly one permission declaration:\n  " + \
            "\n  ".join(bad) + "\n" + HOW_TO_DECLARE
        def table(app):
            return sorted((p, sorted(m), d)
                          for _r, p, m, d in api_auth.iter_route_declarations(app))
        assert table(household) == table(_app("on", dist))

    def test_doc_route_table_matches_the_app(self, dist):
        """Every row of the route table in docs/remote-access-decision.md
        (declaration, count, listed METHOD /path) equals what the app declares."""
        import pathlib
        import re
        doc = (pathlib.Path(__file__).resolve().parent.parent / "docs"
               / "remote-access-decision.md").read_text(encoding="utf-8")
        header = doc.index("| Declaration | Routes | Paths |")
        documented, counts = {}, {}
        for line in doc[header:].splitlines()[2:]:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip().strip("|").split("|", 2)]
            name = cells[0]
            documented[name] = set(re.findall(r"`([A-Z]+ /[^`]*)`", cells[2]))
            counts[name] = int(cells[1])

        def label(decls):
            kind, perm = decls[0]
            return {"public": "public()", "local_only": "local_only()",
                    "authenticated": "authenticated()"}.get(kind, perm)

        actual = {}
        for _r, path, methods, decls in api_auth.iter_route_declarations(_app("on", dist)):
            if path in DOCS_PATHS:
                continue
            for m in methods:
                actual.setdefault(label(decls), set()).add(f"{m} {path}")

        problems = []
        for name in sorted(set(documented) | set(actual)):
            doc_routes, app_routes = documented.get(name, set()), actual.get(name, set())
            if name not in documented:
                problems.append(f"{name}: no row in the doc (app has {len(app_routes)} routes)")
                continue
            if name not in actual:
                problems.append(f"{name}: row in the doc but the app declares no such routes")
                continue
            if counts[name] != len(doc_routes):
                problems.append(f"{name}: Routes column says {counts[name]} but the row lists {len(doc_routes)}")
            if counts[name] != len(app_routes):
                problems.append(f"{name}: Routes column says {counts[name]} but the app has {len(app_routes)}")
            for r in sorted(app_routes - doc_routes):
                problems.append(f"{name}: missing from the doc: {r}")
            for r in sorted(doc_routes - app_routes):
                problems.append(f"{name}: in the doc but not declared in the app: {r}")
        assert not problems, ("docs/remote-access-decision.md route table is out of date:\n  "
                              + "\n  ".join(problems))

    def test_walker_sees_every_route(self, dist):
        app = _app("off", dist)
        paths = {p for _r, p, _m, _d in api_auth.iter_route_declarations(app)}
        # One from each end of the router list, the frontend and a nested prefix.
        for p in ("/api/health", "/api/sources/{name}/attempts", "/api/discover/titles",
                  "/{path:path}", "/api/library/dramas"):
            assert p in paths
        # Cross-check against the source: every @router.<method>(...) decorator in
        # api/routers is one route the walker must have visited.
        import pathlib
        import re
        routers = pathlib.Path(api_auth.__file__).parent / "routers"
        decorators = sum(len(re.findall(r"^@router\.(?:get|post|put|patch|delete|head|api_route)\(",
                                        f.read_text(), re.M))
                         for f in routers.glob("*.py"))
        walked = [r for r, p, _m, _d in api_auth.iter_route_declarations(app)
                  if p.startswith("/api/") and p not in DOCS_PATHS]
        assert len(walked) == decorators

    def test_checker_catches_undeclared_and_double_declared_routes(self):
        app = FastAPI()
        app.state.settings = ApiSettings(auth_mode="on")
        bare = APIRouter(prefix="/api/bare")

        @bare.get("/x")
        def x():
            return {}
        both = APIRouter(prefix="/api/both")

        @both.get("/y", dependencies=[api_auth.require_permission("lines.edit")])
        def y():
            return {}
        app.include_router(bare)
        app.include_router(both, dependencies=[api_auth.require_permission("library.read")])
        bad = _undeclared(app)
        assert any("/api/bare/x" in b and "no declaration" in b for b in bad)
        assert any("/api/both/y" in b for b in bad)

    def test_docs_and_schema_absent_with_auth_on(self, dist):
        app = _app("on", dist)
        paths = {p for _r, p, _m, _d in api_auth.iter_route_declarations(app)}
        assert not (paths & DOCS_PATHS)
        c = _remote(app)
        for p in ("/api/docs", "/api/openapi.json", "/api/redoc"):
            r = c.get(p)
            assert r.status_code == 401 and "openapi" not in r.text.lower()

    def test_authenticated_only_on_own_session_routes(self):
        """authenticated() (signed in, no permission) is the fourth declaration
        kind; it is only for routes on the caller's own session, so it may not
        spread to routes that touch shared data."""
        app = _app("on")
        uses = sorted(f"{sorted(m)} {p}" for _r, p, m, d in api_auth.iter_route_declarations(app)
                      if ("authenticated", None) in d)
        assert uses == ["['GET'] /api/auth/sessions", "['POST'] /api/auth/logout",
                        "['POST'] /api/auth/sessions/revoke-others",
                        "['POST'] /api/auth/sessions/{auth_session_id}/revoke"]
        kinds = {d[0] for _r, _p, _m, decls in api_auth.iter_route_declarations(app)
                 for d in decls}
        assert kinds == {"permission", "public", "local_only", "authenticated"}

    def test_unknown_permission_name_is_a_startup_error(self):
        with pytest.raises(ValueError):
            api_auth.require_permission("library.raed")


# --- modes -------------------------------------------------------------------

class TestAuthOff:
    def test_off_is_the_default_and_grants_everything(self, isolated_db):
        assert ApiSettings().auth_mode == "off"
        c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
        assert c.get("/api/library/dramas").status_code == 200
        # POST with no session and no CSRF reaches the handler (404: no drama),
        # given the X-Baihe-Local header (a bodyless POST is a simple request).
        assert c.post("/api/export/dramas/999/flag-overlaps",
                      headers={"X-Baihe-Local": "1"}).status_code == 404
        assert c.get("/api/docs").status_code == 200

    @pytest.mark.parametrize("headers", [
        {"X-Forwarded-For": "203.0.113.9"},                 # Caddy on the same PC
        {"X-Forwarded-Proto": "https"},
        {"Forwarded": "for=203.0.113.9"},
        {"Via": "1.1 caddy"},
        {"X-Real-IP": "203.0.113.9"},
        {"Host": "baihe.example.com"},                      # public name / DNS rebinding
        {"Origin": "https://evil.example"},
    ])
    @pytest.mark.parametrize("path", ["/api/library/dramas", "/api/health", "/api/docs", "/"])
    def test_off_mode_refuses_anything_not_direct_loopback(self, isolated_db, dist, headers,
                                                         path):
        c = TestClient(_app("off", dist), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
        r = c.get(path, headers=headers)
        assert r.status_code == 403
        assert r.json() == {"error": {"code": "forbidden", "message": "Not allowed."}}

    def test_off_mode_refuses_remote_peer_and_default_testclient(self, isolated_db):
        app = _app("off")
        assert _remote(app).get("/api/health").status_code == 403
        c = TestClient(app, base_url="http://127.0.0.1:8600", client=("192.168.1.9", 1),
                       raise_server_exceptions=False)
        assert c.get("/api/library/dramas").status_code == 403

    @pytest.mark.parametrize("base", ["http://127.0.0.1:8600", "http://localhost:8600",
                                      "http://[::1]:8600"])
    def test_off_mode_direct_loopback_still_works(self, isolated_db, base):
        c = TestClient(_app("off"), base_url=base, client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/library/dramas",
                     headers={"Origin": "http://localhost:5173"}).status_code == 200

    def test_create_app_enforces_bind_safety(self):
        with pytest.raises(ValueError):
            create_app(ApiSettings(host="0.0.0.0"))
        create_app(ApiSettings(host="0.0.0.0", auth_mode="on"))

    def test_unbuilt_settings_fail_closed(self, isolated_db):
        app = FastAPI()
        app.state.settings = None
        assert api_auth.is_auth_enabled(app) is True

    def test_mode_parsing(self):
        from api.api_config import load_settings
        assert load_settings({}).auth_enabled is False
        assert load_settings({"BAIHE_API_AUTH": " ON "}).auth_enabled is True
        with pytest.raises(ValueError):
            load_settings({"BAIHE_API_AUTH": "yes"})
        assert load_settings({}).cookie_secure is True
        assert load_settings({"BAIHE_API_COOKIE_SECURE": "0"}).cookie_secure is False

    def test_bind_refused_off_loopback_unless_auth_on(self):
        for host in ("0.0.0.0", "192.168.1.5", "::", "baihe.example.com"):
            with pytest.raises(ValueError):
                check_bind_safety(ApiSettings(host=host))
            check_bind_safety(ApiSettings(host=host, auth_mode="on"))
        for host in ("127.0.0.1", "localhost", "::1"):
            check_bind_safety(ApiSettings(host=host))


class TestAuthOn:
    def test_401_without_session_generic(self, isolated_db):
        r = _remote(_app()).get("/api/library/dramas")
        assert r.status_code == 401
        assert r.json() == {"error": {"code": "unauthenticated",
                                      "message": "Authentication required."}}

    def test_garbage_and_oversized_cookie_401(self, isolated_db):
        c = _remote(_app())
        for tok in ("x", "a" * 5000, ""):
            assert c.get("/api/library/dramas",
                         headers={"Cookie": f"baihe_session={tok}"}).status_code == 401

    def test_403_without_permission_200_with_it(self, isolated_db):
        c = _remote(_app())
        u, s = _user()
        assert c.get("/api/dub/dramas/1/track", headers=_h(s)).status_code == 403  # media.stream
        assert c.get("/api/library/dramas", headers=_h(s)).status_code == 200
        assert c.get("/api/diagnostics", headers=_h(s)).status_code == 403          # admin
        body = c.get("/api/diagnostics", headers=_h(s)).json()
        assert body == {"error": {"code": "forbidden", "message": "Not allowed."}}

    def test_admin_gets_admin_routes(self, isolated_db):
        c = _remote(_app())
        _u, s = _user(admin=True)
        assert c.get("/api/settings", headers=_h(s)).status_code == 200

    def test_public_routes_need_nothing(self, isolated_db, dist):
        c = _remote(_app("on", dist))
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/meta").status_code == 200
        assert "FAKE-INDEX" in c.get("/").text
        assert c.get("/assets/app.js").status_code == 200

    def test_permission_change_applies_on_next_request(self, isolated_db):
        c = _remote(_app())
        u, s = _user()
        assert c.get("/api/library/dramas", headers=_h(s)).status_code == 200
        auth_service.revoke_permission(u["id"], "library.read")
        assert c.get("/api/library/dramas", headers=_h(s)).status_code == 403
        auth_service.grant_permission(u["id"], "library.read")
        assert c.get("/api/library/dramas", headers=_h(s)).status_code == 200

    def test_expired_revoked_and_deactivated_sessions_rejected(self, isolated_db, monkeypatch):
        c = _remote(_app())
        u, s = _user()
        ok = lambda: c.get("/api/library/dramas", headers=_h(s)).status_code  # noqa: E731
        assert ok() == 200
        auth_service.revoke_session(s["session_id"])
        assert ok() == 401
        s = auth_service.create_session(u["id"])
        auth_service.deactivate_user(u["id"])
        assert ok() == 401
        auth_service.activate_user(u["id"])
        assert ok() == 401              # deactivation deleted it; no resurrection
        s = auth_service.create_session(u["id"])
        assert ok() == 200
        real = auth_service.time.time
        monkeypatch.setattr(auth_service.time, "time",
                            lambda: real() + auth_service.IDLE_TIMEOUT_SECONDS + 5)
        assert ok() == 401
        monkeypatch.setattr(auth_service.time, "time", real)
        s = auth_service.create_session(u["id"])
        # Kept active by touches, still dies at the absolute limit.
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("UPDATE auth_sessions SET expires_at = ? WHERE id = ?",
                         (real() - 1, s["session_id"]))
        assert ok() == 401

    def test_deactivated_user_with_live_row_rejected(self, isolated_db):
        c = _remote(_app())
        u, s = _user()
        db.auth_update_user(u["id"], is_active=0)   # bypass the session wipe
        assert c.get("/api/library/dramas", headers=_h(s)).status_code == 401


class TestCsrf:
    def test_post_needs_matching_csrf(self, isolated_db):
        c = _remote(_app())
        _u, s = _user()
        url = "/api/export/dramas/999/flag-overlaps"        # lines.edit
        assert c.post(url, headers=_h(s, csrf=False)).status_code == 403
        assert c.post(url, headers=_h(s, csrf=False, **{api_auth.CSRF_HEADER: "nope"})
                      ).status_code == 403
        assert c.post(url, headers=_h(s)).status_code == 404   # past auth, no such drama

    def test_csrf_of_another_session_refused(self, isolated_db):
        c = _remote(_app())
        u, s = _user()
        s2 = auth_service.create_session(u["id"])
        h = _h(s, csrf=False, **{api_auth.CSRF_HEADER: s2["csrf_token"]})
        assert c.post("/api/export/dramas/999/flag-overlaps", headers=h).status_code == 403

    @pytest.mark.parametrize("method", ["put", "patch", "delete"])
    def test_other_unsafe_methods(self, isolated_db, method):
        c = _remote(_app())
        _u, s = _user()
        r = getattr(c, method)("/api/glossary/dramas/999/terms/1", headers=_h(s, csrf=False))
        assert r.status_code == 403

    def test_method_override_header_ignored(self, isolated_db):
        c = _remote(_app())
        _u, s = _user()
        h = _h(s, csrf=False, **{"X-HTTP-Method-Override": "GET", "X-Method-Override": "GET"})
        assert c.post("/api/export/dramas/999/flag-overlaps", headers=h).status_code == 403

    def test_multipart_refused_before_body_is_read(self, isolated_db, monkeypatch):
        c = _remote(_app())
        _u, s = _user(admin=True)
        read = []
        import starlette.formparsers as fp
        orig = fp.MultiPartParser.parse

        async def spy(self):
            read.append(1)
            return await orig(self)
        monkeypatch.setattr(fp.MultiPartParser, "parse", spy)
        files = {"file": ("a.mp4", b"0" * 4096, "video/mp4")}
        assert c.post("/api/media/dramas/1/upload", files=files).status_code == 403
        assert c.post("/api/media/dramas/1/upload", files=files,
                      headers=_h(s, csrf=False)).status_code == 403
        # Even with a valid admin session + CSRF the upload is PC-only
        # (local_only), and it is refused before the body is spooled.
        assert c.post("/api/media/dramas/1/upload", files=files,
                      headers=_h(s)).status_code == 403
        assert read == []


class TestLocalOnlyNeedsPreflightedPost:
    """A page on another loopback port passes the Origin check and can send
    a no-cors "simple" POST (text/plain, form-urlencoded). local_only POSTs
    must be JSON or carry X-Baihe-Local: 1 (multipart is a simple type too,
    so uploads need the header; the React upload helper sends it)."""

    @pytest.fixture(autouse=True)
    def _no_page_server(self, monkeypatch):
        import page_server
        monkeypatch.setattr(page_server, "ensure_server_started", lambda *a, **k: True)
        monkeypatch.setattr(page_server, "load_or_create_token", lambda: "tok")

    @pytest.mark.parametrize("auth", ["off", "on"])
    @pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded",
                                       None])
    def test_simple_post_refused(self, isolated_db, auth, ctype):
        c = _local(_app(auth))
        headers = {"Content-Type": ctype} if ctype else {}
        for path, body in (("/api/extension/token", b'{"confirm": true}'),
                           ("/api/diagnostics/reset-library",
                            b'{"confirm": true, "confirm_text": "RESET"}')):
            r = c.post(path, content=body, headers=headers)
            assert r.status_code == 403, (path, ctype)
            assert "tok" not in r.text

    def test_simple_post_refused_on_key_write_that_parses_any_body(self, isolated_db):
        c = _local(_app("off", allow_key_writes=True))
        r = c.post("/api/settings/keys/nope/clear", content=b'{"confirm": true}',
                   headers={"Content-Type": "text/plain"})
        assert r.status_code == 403
        r = c.post("/api/settings/keys/nope/clear", json={"confirm": True})
        assert r.status_code not in (401, 403)

    @pytest.mark.parametrize("auth", ["off", "on"])
    def test_json_or_custom_header_allowed(self, isolated_db, auth):
        c = _local(_app(auth))
        r = c.post("/api/extension/token", json={"confirm": True})
        assert r.status_code == 200 and r.json() == {"token": "tok"}
        r = c.post("/api/extension/token", content=b'{"confirm": true}',
                   headers={"Content-Type": "application/json; charset=utf-8"})
        assert r.status_code == 200
        r = c.post("/api/extension/token", content=b"x",
                   headers={"Content-Type": "text/plain", "X-Baihe-Local": "1"})
        assert r.status_code not in (401, 403)

    @pytest.mark.parametrize("auth", ["off", "on"])
    def test_multipart_needs_the_header(self, isolated_db, auth):
        c = _local(_app(auth))
        files = {"file": ("a.wav", b"RIFF")}
        for path in ("/api/media/dramas/999/upload", "/api/novel/dramas/999/attach-epub"):
            assert c.post(path, files=files).status_code == 403, path
            r = c.post(path, files=files, headers={"X-Baihe-Local": "1"})
            assert r.status_code not in (401, 403), path
        # a JSON-body route sent multipart without the header: refused, not parsed
        r = c.post("/api/extension/token", files=files, data={"confirm": "true"})
        assert r.status_code == 403 and "tok" not in r.text

    @pytest.mark.parametrize("auth", ["off", "on"])
    def test_refused_before_the_body_is_read(self, isolated_db, auth, monkeypatch):
        """L5: the early gate refuses before Starlette parses (spools) the
        multipart body."""
        read = []
        import starlette.formparsers as fp
        orig = fp.MultiPartParser.parse

        async def spy(self):
            read.append(1)
            return await orig(self)
        monkeypatch.setattr(fp.MultiPartParser, "parse", spy)
        c = _local(_app(auth))
        files = {"file": ("a.mp4", b"0" * 4096, "video/mp4")}
        assert c.post("/api/media/dramas/1/upload", files=files).status_code == 403
        assert read == []
        r = c.post("/api/media/dramas/1/upload", files=files, headers={"X-Baihe-Local": "1"})
        assert r.status_code not in (401, 403) and read == [1]

    def test_header_value_must_be_exactly_1(self, isolated_db):
        c = _local(_app("off"))
        for v in ("0", "true", "", "1 "):
            r = c.post("/api/media/dramas/999/upload", files={"file": ("a.wav", b"RIFF")},
                       headers={"X-Baihe-Local": v})
            assert r.status_code == 403, v

    def test_delete_and_get_unaffected(self, isolated_db):
        c = _local(_app("off"))
        assert c.delete("/api/dramas/999").status_code not in (401, 403)
        assert c.get("/api/extension/status").status_code == 200


class TestLocalOnly:
    def test_remote_refused_even_as_admin(self, isolated_db):
        c = _remote(_app())
        _u, s = _user(admin=True)
        assert c.delete("/api/dramas/999", headers=_h(s)).status_code == 403

    def test_direct_loopback_allowed(self, isolated_db):
        r = _local(_app()).delete("/api/dramas/999")
        assert r.status_code not in (401, 403)

    @pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP", "Via"])
    def test_loopback_peer_behind_proxy_is_not_local(self, isolated_db, header):
        c = _local(_app())
        assert c.delete("/api/dramas/999", headers={header: "127.0.0.1"}).status_code == 403

    def test_remote_host_header_or_origin_not_local(self, isolated_db):
        c = _local(_app())
        assert c.delete("/api/dramas/999", headers={"Host": "baihe.example.com"}
                        ).status_code == 403
        assert c.delete("/api/dramas/999", headers={"Origin": "https://evil.example"}
                        ).status_code == 403

    @pytest.mark.parametrize("path", ["/api/settings/usage-recost/apply", "/api/settings/usage-recost/undo"])
    def test_usage_recost_writes_refused_to_an_admin_off_the_pc(self, isolated_db, path):
        _u, s = _user(admin=True)
        body = {"confirm": True}
        assert _remote(_app()).post(path, json=body, headers=_h(s)).status_code == 403
        # A loopback peer reached through a proxy or household listener is not the PC either.
        assert _local(_app()).post(path, json=body, headers=_h(s, **{"X-Forwarded-For": "192.168.1.5"})
                                   ).status_code == 403
        assert _local(_app()).post(path, json=body, headers=_h(s, Host="baihe.example.com")
                                   ).status_code == 403

    def test_spoofed_forwarded_for_from_remote_peer(self, isolated_db):
        c = _remote(_app())
        r = c.delete("/api/dramas/999", headers={"X-Forwarded-For": "127.0.0.1",
                                                  "Host": "127.0.0.1"})
        assert r.status_code == 403


class TestBypassAttempts:
    """Adversarial review: each of these is refused for an anonymous remote client."""

    @pytest.mark.parametrize("method,path", [
        ("HEAD", "/api/library/dramas"),
        ("OPTIONS", "/api/library/dramas"),
        ("GET", "/api/library/dramas/"),               # trailing slash (no redirect leak)
        ("GET", "/api/library/dramas?x=1"),
        ("GET", "/api/library/nope"),                  # unknown path: no 404 enumeration
        ("GET", "/api/%6Cibrary/dramas"),              # percent-encoded letter
        ("GET", "/api/library/dramas%2F"),
        ("GET", "/api/./library/dramas"),
        ("PATCH", "/api/library/dramas"),              # wrong method: no 405 leak
    ])
    def test_anonymous_variants(self, isolated_db, method, path):
        r = _remote(_app()).request(method, path)
        assert r.status_code == 401, (method, path, r.status_code, r.text)

    @pytest.mark.parametrize("path", ["//api/library/dramas", "/API/library/dramas",
                                      "/%2Fapi/library/dramas"])
    def test_paths_that_miss_the_api_never_return_data(self, isolated_db, dist, path):
        from core import Line  # noqa: F401  (library stays empty; body must be the shell)
        r = _remote(_app("on", dist)).get(path)
        assert r.status_code in (401, 404) or "FAKE-INDEX" in r.text

    def test_malformed_json_gets_401_not_422(self, isolated_db):
        r = _remote(_app()).post("/api/dramas", content=b"{not json",
                                 headers={"Content-Type": "application/json"})
        assert r.status_code == 401

    def test_bad_input_not_echoed(self, isolated_db):
        c = _remote(_app())
        _u, s = _user(admin=True)
        r = c.post("/api/dramas", json={"title": 12345, "sk-secretvalue": "sk-ant-XYZXYZXYZ"},
                   headers=_h(s))
        assert "sk-ant-XYZXYZXYZ" not in r.text

    def test_frontend_api_branch_does_not_leak_405(self, isolated_db, dist):
        r = _remote(_app("on", dist)).get("/api/settings/keys/claude")
        assert r.status_code == 401


class TestPaidEngines:
    def test_household_limited_to_free_engines(self, isolated_db):
        c = _remote(_app())
        _u, s = _user()
        did = db.create_drama(title_en="T", source_language="zh")   # the B2 guard 404s a missing one
        url = f"/api/translate-run/dramas/{did}/run"
        assert c.post(url, json={"engine": "claude"}, headers=_h(s)).status_code == 403
        assert c.post(url, json={}, headers=_h(s)).status_code == 403   # default: maybe paid
        assert c.post(url, json={"engine": "ollama",
                                 "fallback_chain": [{"engine": "claude"}]},
                      headers=_h(s)).status_code == 403
        assert c.post(url, json={"engine": "ollama"}, headers=_h(s)).status_code not in (401, 403)
        assert c.post(f"/api/review-jobs/dramas/{did}/flag", json={"engine": "gemini"},
                      headers=_h(s)).status_code == 403

    def test_resegment_llm_is_gated(self, isolated_db):
        c = _remote(_app())
        _u, s = _user()
        did = db.create_drama(title_en="T", source_language="zh")
        url = f"/api/restructure/dramas/{did}/resegment"
        base = {"expected_line_ids": [1], "confirm": True}
        for body in ({**base, "use_llm": True}, {**base, "use_llm": True, "engine": "claude"}):
            assert c.post(url, json=body, headers=_h(s)).status_code == 403
        for body in (base, {**base, "use_llm": True, "engine": "ollama"}):
            assert c.post(url, json=body, headers=_h(s)).status_code not in (401, 403)

    def test_groq_transcription_needs_engines_paid(self, isolated_db):
        drama_id = db.create_drama(title_en="T", source_language="zh")
        c = _remote(_app())
        _u, s = _user()
        cfg = f"/api/transcribe/dramas/{drama_id}/config"
        assert c.post(cfg, json={"use_groq": True}, headers=_h(s)).status_code == 403
        assert c.post(cfg, json={"use_groq": False}, headers=_h(s)).status_code == 200
        db.update_drama(drama_id, use_groq=1)            # set by the owner at the PC
        run = f"/api/transcribe/dramas/{drama_id}/run"
        assert c.post(run, json={}, headers=_h(s)).status_code == 403
        _u2, paid = _user_named("paid@example.com", "engines.paid")
        assert c.post(cfg, json={"use_groq": True}, headers=_h(paid)).status_code == 200
        assert c.post(run, json={}, headers=_h(paid)).status_code not in (401, 403)

    def test_remote_transcribe_run_cannot_pick_tesseract_binary(self, isolated_db, monkeypatch):
        """Security review (PR #439): the server executes tesseract_cmd, so a
        remote caller (even an admin) gets 403 and nothing starts; the PC may."""
        from services import transcribe_service
        started = []
        monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                            lambda *a, **k: started.append(k) or {"job_id": "j"})
        drama_id = db.create_drama(title_en="T", source_language="zh")
        run = f"/api/transcribe/dramas/{drama_id}/run"
        _u, s = _user(admin=True)
        r = _remote(_app()).post(run, json={"tesseract_cmd": r"\\evil\share\x.exe"},
                                 headers=_h(s))
        assert r.status_code == 403 and "at the PC" in r.json()["error"]["message"]
        assert started == []
        assert _remote(_app()).post(run, json={}, headers=_h(s)).status_code == 200
        assert started[-1]["tesseract_cmd"] is None
        r = _local(_app("off")).post(run, json={"tesseract_cmd": "/usr/bin/tesseract"},
                                     )
        assert r.status_code == 200 and started[-1]["tesseract_cmd"] == "/usr/bin/tesseract"

    def test_paid_summary_engine_skipped_without_engines_paid(self, isolated_db, monkeypatch):
        """Security review (PR #439): a free run by a household user must not
        trigger a cloud episode summary on the owner's key (single and bulk)."""
        from services import library_admin_service, translate_run_service
        runs, bulks = [], []
        # A real shared drama: the ownership guard (auth B2) 404s a missing one.
        did = db.create_drama(title_en="Shared", source_language="zh")
        monkeypatch.setattr(translate_run_service, "start_translate_run",
                            lambda *a, **k: runs.append(k) or {})
        monkeypatch.setattr(library_admin_service, "bulk_translate_engines",
                            lambda ids, principal=None: {"engines": ["ollama"],
                                                         "by_drama": {did: "ollama"}})
        monkeypatch.setattr(library_admin_service, "start_bulk_translate",
                            lambda *a, **k: bulks.append(k) or {})
        c = _remote(_app())
        _u, s = _user("jobs.start")
        _u2, paid = _user_named("paid@example.com", "jobs.start", "engines.paid")
        url = f"/api/translate-run/dramas/{did}/run"
        c.post(url, json={"engine": "ollama"}, headers=_h(s))
        c.post(url, json={"engine": "ollama"}, headers=_h(paid))
        assert [r["allow_paid_summary"] for r in runs] == [False, True]
        bulk = "/api/library/admin/bulk/translate"
        c.post(bulk, json={"drama_ids": [did]}, headers=_h(s))
        c.post(bulk, json={"drama_ids": [did]}, headers=_h(paid))
        assert [b["allow_paid_summary"] for b in bulks] == [False, True]

    def test_engines_paid_unlocks(self, isolated_db):
        c = _remote(_app())
        _u, s = _user("engines.paid")
        r = c.post("/api/translate-run/dramas/999/run", json={"engine": "claude"}, headers=_h(s))
        assert r.status_code not in (401, 403)


# --- tokens, cookies, rate limit, CLI ------------------------------------------

def _all_db_text():
    with sqlite3.connect(db.DB_PATH) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        out = []
        for t in tables:
            for row in conn.execute(f'SELECT * FROM "{t}"'):
                out.extend(str(v) for v in row)
    return "\n".join(out)


def test_raw_tokens_never_stored_listed_or_audited(isolated_db):
    c = _remote(_app())
    u, s = _user()
    c.get("/api/library/dramas", headers=_h(s))
    c.post("/api/export/dramas/1/flag-overlaps", headers=_h(s))
    text = _all_db_text()
    for raw in (s["session_token"], s["csrf_token"]):
        assert raw not in text
        blob = repr(auth_service.list_sessions(u["id"])) + repr(auth_service.list_audit(500)) \
            + repr(auth_service.list_users())
        assert raw not in blob
        assert auth_service._hash(raw) not in blob


class TestCookie:
    def _cookie(self, app, base, client, headers=None):
        seen = {}

        @app.get("/__probe", dependencies=[api_auth.public_route()])
        def probe(request: Request):
            resp = Response()
            api_auth.set_session_cookie(resp, request, "tok")
            seen["h"] = resp.headers["set-cookie"]
            return resp
        TestClient(app, base_url=base, client=client).get("/__probe", headers=headers or {})
        return seen["h"].lower()

    def test_flags_remote(self, isolated_db):
        h = self._cookie(_app("on", cookie_secure=False), REMOTE, ("203.0.113.9", 1))
        assert "httponly" in h and "secure" in h and "samesite=lax" in h and "path=/" in h

    def test_secure_by_default_even_on_loopback(self, isolated_db):
        h = self._cookie(_app("on"), "http://127.0.0.1:8600", ("127.0.0.1", 1))
        assert "secure" in h

    def test_relaxed_only_for_direct_loopback_http_dev(self, isolated_db):
        app = _app("on", cookie_secure=False)
        assert "secure" not in self._cookie(app, "http://127.0.0.1:8600", ("127.0.0.1", 1))
        app = _app("on", cookie_secure=False)
        h = self._cookie(app, "http://127.0.0.1:8600", ("127.0.0.1", 1),
                         {"X-Forwarded-Proto": "http", "X-Forwarded-For": "203.0.113.9"})
        assert "secure" in h                       # behind Caddy: always Secure


def test_login_style_rate_limit_returns_429(isolated_db):
    """A public login-style route (as step 134 will add) rate-limited per
    client address: the third attempt in the window is a 429 with the
    standard error body; other clients are unaffected; the window slides."""
    now = [0.0]
    limiter = auth_service.SlidingWindowRateLimiter(2, 60, clock=lambda: now[0])
    app = _app()

    @app.post("/api/__login_probe", dependencies=[api_auth.public_route()])
    def login_probe(request: Request):
        limiter.hit(request.client.host)
        return {"ok": True}
    c = _remote(app)
    assert [c.post("/api/__login_probe").status_code for _ in range(3)] == [200, 200, 429]
    assert c.post("/api/__login_probe").json()["error"]["code"] == "rate_limited"
    other = TestClient(app, base_url=REMOTE, client=("198.51.100.7", 1))
    assert other.post("/api/__login_probe").status_code == 200
    now[0] = 61.0
    assert c.post("/api/__login_probe").status_code == 200
    with pytest.raises(RateLimitedError):
        for _ in range(3):
            limiter.hit("k2")


def test_cli_grant_admin_and_list_users(isolated_db, capsys):
    from api.__main__ import main
    assert main(["grant-admin", "Owner@Example.com"]) == 0
    s = auth_service.create_session(auth_service.list_users()[0]["id"])
    assert main(["list-users"]) == 0
    out = capsys.readouterr().out
    assert "owner@example.com" in out and "admin" in out
    assert s["session_token"] not in out and "hash" not in out.lower()
    assert main(["grant-admin", "not-an-email"]) == 2
