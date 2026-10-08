"""
Browser-extension device tokens: the table (device_tokens.py), the rules
(services/device_token_service.py), the routes (device_token_routes.py) and
the `require_device_token()` dependency in api/auth.py.

The key negative tests: a valid device token opens no existing route (every
route walked, on the single-port sign-in app and the household listener),
and the dependency accepts no session cookie, query string or second header.
"""

import hashlib
import re
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import db
import device_tokens
from api import auth as api_auth
from api.api_config import ApiSettings
from api.error_handlers import install_error_handlers
from api.server import create_app
from services import auth_service
from services import device_token_service as svc
from translate_engines import redact_secrets

REMOTE = "https://baihe.example.com"
PUBLIC_HOST = "baihe.example.com"
SIGN_IN = {"google_client_id": "cid", "google_client_secret": "s3cr3t-value",
           "public_url": f"https://{PUBLIC_HOST}"}
OWN = "/api/auth/device-tokens"
ADMIN = "/api/admin/device-tokens"


@pytest.fixture(autouse=True)
def fresh_limiters(monkeypatch):
    monkeypatch.setattr(svc, "_create_limiter", auth_service.SlidingWindowRateLimiter(5, 3600))
    monkeypatch.setattr(svc, "_fail_by_client", svc._FailureLimiter(20, 600))
    monkeypatch.setattr(svc, "_fail_by_prefix", svc._FailureLimiter(100, 600))


def _member(email="kid@example.com", *extra, send=True):
    u = auth_service.add_user(email)
    for p in (("extension.send",) if send else ()) + extra:
        auth_service.grant_permission(u["id"], p)
    return u, auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def _principal(user_id, is_admin=False):
    return dict(user_id=user_id, is_admin=is_admin,
                permissions=auth_service.effective_permissions(user_id))


def _h(session, csrf=True):
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}
    if csrf:
        h[api_auth.CSRF_HEADER] = session["csrf_token"]
    return h


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _app(auth="on"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _remote(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _make_token(user_id, label="Laptop", days=None, **kw):
    return svc.create_token(_principal(user_id), label, days, **kw)


def _bridge_app(auth="on", listener="admin"):
    """A stand-in for the future bridge routes: one GET and one POST under
    require_device_token(), and one drama-scoped route for the path guard."""
    app = FastAPI()
    app.state.settings = ApiSettings(auth_mode=auth, listener=listener)
    install_error_handlers(app)

    @app.get("/api/bridge/ping", dependencies=[api_auth.require_device_token()])
    def ping(request: Request):
        p = request.state.principal
        return {"user_id": p["user_id"], "permissions": p["permissions"],
                "is_admin": p["is_admin"], "admin_override": p["admin_override"]}

    @app.post("/api/bridge/send", dependencies=[api_auth.require_device_token()])
    def send():
        return {"ok": True}

    @app.get("/api/bridge/dramas/{drama_id}", dependencies=[api_auth.require_device_token()])
    def drama(drama_id: int):
        return {"drama_id": drama_id}
    return app


def _bridge(app=None, ip="198.51.100.7"):
    return TestClient(app or _bridge_app(), base_url=REMOTE, client=(ip, 5000),
                      raise_server_exceptions=False)


# --- creation, storage, shown once ---------------------------------------------

class TestCreate:
    def test_token_shape_and_only_the_hash_is_stored(self, isolated_db):
        u, _ = _member()
        out = _make_token(u["id"], "  Work   laptop ")
        token = out["token"]
        assert re.fullmatch(r"baihe_dt_[A-Za-z0-9_-]{43}", token)
        assert out["device_token"]["label"] == "Work laptop"
        assert out["device_token"]["status"] == "active"
        assert "token_hash" not in out["device_token"]
        con = sqlite3.connect(db.DB_PATH)
        dump = "\n".join(con.iterdump())
        con.close()
        assert token not in dump and token[len("baihe_dt_"):] not in dump
        assert hashlib.sha256(token.encode()).hexdigest() in dump

    def test_never_listed_or_audited_again(self, isolated_db):
        u, s = _member()
        c = _remote(_app())
        r = c.post(OWN, json={"label": "Laptop"}, headers=_h(s))
        assert r.status_code == 200, r.text
        assert r.headers["cache-control"] == "no-store"
        token = r.json()["token"]
        listed = c.get(OWN, headers=_h(s))
        assert listed.headers["cache-control"] == "no-store"
        assert token not in listed.text and "hash" not in listed.text
        assert [t["label"] for t in listed.json()["tokens"]] == ["Laptop"]
        audit = str(auth_service.list_audit(50))
        assert "device_token.create" in audit and token not in audit
        admin = _local(_app("off")).get(ADMIN)
        assert admin.status_code == 200 and token not in admin.text and "hash" not in admin.text

    def test_tokens_are_unique(self, isolated_db):
        u, _ = _member()
        assert _make_token(u["id"])["token"] != _make_token(u["id"])["token"]

    @pytest.mark.parametrize("label", ["", "   ", "x" * 41, "bad\x00name", "tab‎name"])
    def test_label_bounds(self, isolated_db, label):
        u, s = _member()
        r = _remote(_app()).post(OWN, json={"label": label}, headers=_h(s))
        assert r.status_code == 422

    @pytest.mark.parametrize("days", [0, 366, -1, "30", 1.5, True])
    def test_expiry_bounds(self, isolated_db, days):
        u, s = _member()
        r = _remote(_app()).post(OWN, json={"label": "L", "expires_in_days": days}, headers=_h(s))
        assert r.status_code == 422

    def test_needs_extension_send(self, isolated_db):
        u, s = _member(send=False)
        r = _remote(_app()).post(OWN, json={"label": "Laptop"}, headers=_h(s))
        assert r.status_code == 403
        assert device_tokens.list_for_user(u["id"]) == []

    def test_needs_csrf(self, isolated_db):
        u, s = _member()
        r = _remote(_app()).post(OWN, json={"label": "Laptop"}, headers=_h(s, csrf=False))
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"

    def test_extra_fields_refused(self, isolated_db):
        u, s = _member()
        r = _remote(_app()).post(OWN, json={"label": "L", "user_id": 99}, headers=_h(s))
        assert r.status_code == 422

    def test_cap_per_user(self, isolated_db, monkeypatch):
        monkeypatch.setattr(svc, "_create_limiter", auth_service.SlidingWindowRateLimiter(99, 1))
        u, _ = _member()
        ids = [_make_token(u["id"], f"D{i}")["device_token"]["id"] for i in range(svc.MAX_LIVE_TOKENS)]
        with pytest.raises(svc.ConflictError):
            _make_token(u["id"], "one too many")
        svc.revoke_own(u["id"], ids[0], is_admin=False, at_pc=False)
        _make_token(u["id"], "fits again")
        other, _ = _member("other@example.com")
        _make_token(other["id"])   # the cap is per user

    def test_cap_counts_concurrent_creates_once(self, isolated_db):
        u, _ = _member()
        for i in range(svc.MAX_LIVE_TOKENS):
            assert device_tokens.insert_under_cap(u["id"], "x", f"h{i}", 1.0, None, 10)
        assert device_tokens.insert_under_cap(u["id"], "x", "h-last", 1.0, None, 10) is None

    def test_creation_rate_limited_per_user(self, isolated_db):
        u, s = _member()
        c = _remote(_app())
        codes = [c.post(OWN, json={"label": f"D{i}"}, headers=_h(s)).status_code for i in range(6)]
        assert codes == [200] * 5 + [429]
        other, s2 = _member("other@example.com")
        assert c.post(OWN, json={"label": "D"}, headers=_h(s2)).status_code == 200

    def test_auth_off_owner_has_no_account(self, isolated_db):
        c = _local(_app("off"))
        assert c.post(OWN, json={"label": "Laptop"}).status_code == 404
        assert c.get(OWN).status_code == 404

    def test_admin_account_creates_only_at_the_pc(self, isolated_db):
        admin = auth_service.grant_admin_local("admin@example.com")
        p = _principal(admin["id"], is_admin=True)
        with pytest.raises(svc.ForbiddenError):
            svc.create_token(p, "Laptop", at_pc=False)
        assert svc.create_token(p, "Laptop", at_pc=True)["token"]

    def test_old_ended_rows_are_pruned_on_create(self, isolated_db):
        u, _ = _member()
        old = _make_token(u["id"], "old", now=1000.0)["device_token"]["id"]
        svc.revoke_own(u["id"], old, is_admin=False, at_pc=False, now=1000.0)
        _make_token(u["id"], "new", now=1000.0 + 31 * 86400)
        assert [t["label"] for t in svc.list_own(u["id"])] == ["new"]


# --- list and revoke: own vs other, admin ------------------------------------------

class TestOwnership:
    def test_member_sees_and_revokes_only_their_own(self, isolated_db):
        a, sa = _member("a@example.com")
        b, sb = _member("b@example.com")
        b_token = _make_token(b["id"], "B's laptop")
        b_id = b_token["device_token"]["id"]
        c = _remote(_app())
        assert c.get(OWN, headers=_h(sa)).json()["tokens"] == []
        r = c.post(f"{OWN}/{b_id}/revoke", headers=_h(sa))
        missing = c.post(f"{OWN}/999999/revoke", headers=_h(sa))
        assert r.status_code == missing.status_code == 404
        assert r.json() == missing.json()
        assert device_tokens.get(b_id)["revoked_at"] is None
        assert c.get(ADMIN, headers=_h(sa)).status_code == 403
        assert c.post(f"{ADMIN}/{b_id}/revoke", headers=_h(sa)).status_code == 403
        # B revokes their own: the token stops working at once.
        assert _bridge().get("/api/bridge/ping", headers=_bearer(b_token["token"])).status_code == 200
        r = c.post(f"{OWN}/{b_id}/revoke", headers=_h(sb))
        assert r.status_code == 200 and r.json() == {"revoked": 1}
        assert c.post(f"{OWN}/{b_id}/revoke", headers=_h(sb)).status_code == 404
        assert _bridge().get("/api/bridge/ping", headers=_bearer(b_token["token"])).status_code == 401
        listed = c.get(OWN, headers=_h(sb)).json()["tokens"]
        assert listed[0]["status"] == "revoked" and listed[0]["revoked_at"]
        assert "device_token.revoke" in str(auth_service.list_audit(50))

    def test_revoke_needs_csrf(self, isolated_db):
        u, s = _member()
        tid = _make_token(u["id"])["device_token"]["id"]
        r = _remote(_app()).post(f"{OWN}/{tid}/revoke", headers=_h(s, csrf=False))
        assert r.status_code == 403
        assert device_tokens.get(tid)["revoked_at"] is None

    def test_member_without_extension_send_can_still_list_and_revoke(self, isolated_db):
        u, s = _member(send=False)
        tid = device_tokens.insert_under_cap(u["id"], "old", "h", 1.0, None, 10)
        c = _remote(_app())
        assert [t["id"] for t in c.get(OWN, headers=_h(s)).json()["tokens"]] == [tid]
        assert c.post(f"{OWN}/{tid}/revoke", headers=_h(s)).status_code == 200

    def test_pc_owner_lists_and_revokes_anyones(self, isolated_db):
        a, _ = _member("jane@example.com")
        tok = _make_token(a["id"], "Jane's PC")
        c = _local(_app("off"))
        rows = c.get(ADMIN).json()["tokens"]
        assert [(r["label"], r["user_id"], r["user_name"]) for r in rows] == [
            ("Jane's PC", a["id"], "j***@example.com")]
        r = c.post(f"{ADMIN}/{rows[0]['id']}/revoke", json={})
        assert r.status_code == 200
        assert c.post(f"{ADMIN}/{rows[0]['id']}/revoke", json={}).status_code == 404
        assert _bridge().get("/api/bridge/ping", headers=_bearer(tok["token"])).status_code == 401
        assert "device_token.admin_revoke" in str(auth_service.list_audit(50))

    def test_admin_routes_refused_on_the_household_listener(self, isolated_db):
        admin = auth_service.grant_admin_local("owner@example.com")
        s = auth_service.create_session(admin["id"], "pytest", "127.0.0.1")
        app = create_app(ApiSettings(household_port=8610, serve_frontend=False, **SIGN_IN),
                         listener="household")
        c = TestClient(app, base_url=f"http://{PUBLIC_HOST}", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
        assert c.get(ADMIN, headers=_h(s)).status_code == 403
        assert c.post(f"{ADMIN}/1/revoke", headers=_h(s)).status_code == 403
        # An admin account's own tokens are PC-only too.
        r = c.post(OWN, json={"label": "Laptop"}, headers=_h(s))
        assert r.status_code == 403 and device_tokens.list_for_user(admin["id"]) == []

    def test_member_creates_on_the_household_listener(self, isolated_db):
        u, s = _member()
        app = create_app(ApiSettings(household_port=8610, serve_frontend=False, **SIGN_IN),
                         listener="household")
        c = TestClient(app, base_url=f"http://{PUBLIC_HOST}", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
        r = c.post(OWN, json={"label": "Laptop", "expires_in_days": 90}, headers=_h(s))
        assert r.status_code == 200, r.text
        assert r.json()["device_token"]["expires_at"] is not None

    def test_losing_the_permission_or_account_revokes_tokens(self, isolated_db):
        u, _ = _member()
        t1 = _make_token(u["id"])["device_token"]["id"]
        auth_service.revoke_permission(u["id"], "extension.send")
        assert device_tokens.get(t1)["revoked_at"] is not None
        auth_service.grant_permission(u["id"], "extension.send")
        t2 = _make_token(u["id"])["device_token"]["id"]
        auth_service.deactivate_user(u["id"])
        assert device_tokens.get(t2)["revoked_at"] is not None

    def test_demoted_admin_loses_tokens(self, isolated_db):
        a = auth_service.grant_admin_local("a1@example.com")
        auth_service.grant_admin_local("a2@example.com")
        p = _principal(a["id"], is_admin=True)
        tid = svc.create_token(p, "Laptop", at_pc=True)["device_token"]["id"]
        auth_service.revoke_admin(a["id"], at_pc=True)
        assert device_tokens.get(tid)["revoked_at"] is not None


# --- the dependency ------------------------------------------------------------

class TestRequireDeviceToken:
    def test_valid_token_is_its_user_with_member_rights(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        r = _bridge().get("/api/bridge/ping", headers=_bearer(tok))
        assert r.status_code == 200
        assert r.json()["user_id"] == u["id"] and "extension.send" in r.json()["permissions"]

    def test_admin_token_carries_no_admin_rights(self, isolated_db):
        admin = auth_service.grant_admin_local("admin@example.com")
        p = _principal(admin["id"], is_admin=True)
        tok = svc.create_token(p, "Laptop", at_pc=True)["token"]
        body = _bridge().get("/api/bridge/ping", headers=_bearer(tok)).json()
        assert body["is_admin"] is False and body["admin_override"] is False
        assert "extension.send" in body["permissions"]
        assert not [x for x in body["permissions"] if x.startswith("admin.")]

    @pytest.mark.parametrize("auth", ["on", "off"])
    def test_session_cookie_is_not_accepted(self, isolated_db, auth):
        u, s = _member()
        c = _bridge(_bridge_app(auth))
        for headers in (_h(s), {"Cookie": f"{api_auth.DEV_COOKIE_NAME}={s['session_token']}"},
                        {"Authorization": f"Bearer {s['session_token']}"}, {}):
            r = c.get("/api/bridge/ping", headers=headers)
            assert r.status_code == 401, headers
            assert r.json() == {"error": {"code": "unauthenticated",
                                          "message": "Authentication required."}}

    def test_only_the_header_counts(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        c = _bridge()
        assert c.get(f"/api/bridge/ping?token={tok}").status_code == 401
        assert c.get(f"/api/bridge/ping?access_token={tok}").status_code == 401
        assert c.get("/api/bridge/ping", headers={"Cookie": f"token={tok}"}).status_code == 401

    def test_malformed_headers_are_one_uniform_401(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        c = _bridge()
        bodies = set()
        for value in (tok, f"Basic {tok}", f"Bearer {tok}x", f"Bearer {tok[:-1]}",
                      "Bearer baihe_dt_" + "A" * 43, f"Bearer  {tok} extra", "Bearer", ""):
            r = c.get("/api/bridge/ping", headers={"Authorization": value})
            assert r.status_code == 401, value
            bodies.add(r.text)
        assert len(bodies) == 1
        assert c.get("/api/bridge/ping", headers={"Authorization": f"bearer {tok}"}).status_code == 200

    def test_two_authorization_headers_refused(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        r = _bridge().get("/api/bridge/ping",
                          headers=[("Authorization", f"Bearer {tok}"), ("Authorization", f"Bearer {tok}")])
        assert r.status_code == 401

    def test_revoked_expired_inactive_all_look_the_same(self, isolated_db, monkeypatch):
        u, _ = _member()
        c = _bridge()
        revoked = _make_token(u["id"], "r")
        svc.revoke_own(u["id"], revoked["device_token"]["id"], is_admin=False, at_pc=False)
        expiring = _make_token(u["id"], "e", days=1)
        assert c.get("/api/bridge/ping", headers=_bearer(expiring["token"])).status_code == 200
        real_time = svc.time.time
        monkeypatch.setattr(svc.time, "time", lambda: real_time() + 2 * 86400)
        bodies = {c.get("/api/bridge/ping", headers=_bearer(t["token"])).text
                  for t in (revoked, expiring)}
        assert svc.list_own(u["id"])[0]["status"] == "expired"
        monkeypatch.setattr(svc.time, "time", real_time)
        v, _ = _member("v@example.com")
        live = _make_token(v["id"])
        db.auth_update_user(v["id"], is_active=0)   # no hook: the lookup refuses
        bodies.add(c.get("/api/bridge/ping", headers=_bearer(live["token"])).text)
        bodies.add(c.get("/api/bridge/ping", headers=_bearer("baihe_dt_" + "b" * 43)).text)
        assert len(bodies) == 1 and "Authentication required." in bodies.pop()

    def test_without_extension_send_403(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        db.auth_revoke_permission(u["id"], "extension.send")   # bypasses the revoke hook
        r = _bridge().get("/api/bridge/ping", headers=_bearer(tok))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

    def test_post_needs_no_csrf_but_the_token(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        c = _bridge()
        assert c.post("/api/bridge/send", headers=_bearer(tok)).status_code == 200
        assert c.post("/api/bridge/send").status_code == 401

    def test_path_guard_applies(self, isolated_db):
        a, _ = _member("a@example.com")
        b, _ = _member("b@example.com")
        did = db.create_drama(title_en="A's private", owner_user_id=a["id"], is_private=1)
        c = _bridge()
        assert c.get(f"/api/bridge/dramas/{did}",
                     headers=_bearer(_make_token(a["id"])["token"])).status_code == 200
        assert c.get(f"/api/bridge/dramas/{did}",
                     headers=_bearer(_make_token(b["id"])["token"])).status_code == 404

    def test_household_listener_accepts_a_member_token(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        c = _bridge(_bridge_app(listener="household"))
        assert c.get("/api/bridge/ping", headers=_bearer(tok)).status_code == 200

    def test_failures_are_throttled_per_address(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        c = _bridge(ip="198.51.100.7")
        codes = [c.get("/api/bridge/ping", headers=_bearer("baihe_dt_" + "x" * 43)).status_code
                 for _ in range(21)]
        assert codes == [401] * 20 + [429]
        # Blocked before any lookup, a valid token included, from that address only.
        assert c.get("/api/bridge/ping", headers=_bearer(tok)).status_code == 429
        assert _bridge(ip="192.0.2.5").get("/api/bridge/ping",
                                            headers=_bearer(tok)).status_code == 200

    def test_successes_and_403s_are_not_failures(self, isolated_db):
        u, _ = _member()
        tok = _make_token(u["id"])["token"]
        c = _bridge()
        for _ in range(30):
            assert c.get("/api/bridge/ping", headers=_bearer(tok)).status_code == 200

    def test_last_use_is_recorded_at_most_once_a_minute(self, isolated_db, monkeypatch):
        u, _ = _member()
        tok = _make_token(u["id"], now=1000.0)["token"]
        touches = []
        real_touch = device_tokens.touch
        monkeypatch.setattr(device_tokens, "touch",
                            lambda *a: (touches.append(a[1]), real_touch(*a)))
        for now in (1000.0, 1030.0, 1059.0, 1061.0, 1100.0):
            svc.authenticate([f"Bearer {tok}"], ip="2001:db8:1:2::9", now=now)
        assert touches == [1000.0, 1061.0]
        row = svc.list_own(u["id"])[0]
        assert row["last_used_at"] == 1061.0 and row["last_used_ip_prefix"] == "2001:db8:1::/48"


# --- no existing route accepts a device token -------------------------------------

def _concrete(path):
    return re.sub(r"\{[^}]+\}", "1", path)


def _walk_with_token(app, client, token):
    accepted = []
    for _r, path, methods, decls in api_auth.iter_route_declarations(app):
        if decls == [("public", None)] or not path.startswith("/api"):
            continue
        for method in sorted(methods):
            r = client.request(method, _concrete(path), headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                content=b"{}" if method in ("POST", "PUT", "PATCH") else None)
            if r.status_code not in (401, 403):
                accepted.append(f"{method} {path}: {r.status_code}")
    return accepted


class TestNoOtherRouteAcceptsADeviceToken:
    def test_single_port_sign_in(self, isolated_db):
        u, _ = _member("kid@example.com", "media.stream", "engines.paid", "sources.import",
                       "media.import_url")
        tok = _make_token(u["id"])["token"]
        app = _app()
        assert _walk_with_token(app, _remote(app), tok) == []

    def test_household_listener(self, isolated_db):
        admin = auth_service.grant_admin_local("owner@example.com")
        p = _principal(admin["id"], is_admin=True)
        tok = svc.create_token(p, "Laptop", at_pc=True)["token"]
        app = create_app(ApiSettings(household_port=8610, serve_frontend=False, **SIGN_IN),
                         listener="household")
        c = TestClient(app, base_url=f"http://{PUBLIC_HOST}", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
        assert _walk_with_token(app, c, tok) == []

    def test_no_route_declares_the_device_token_dependency_yet(self):
        uses = [p for _r, p, _m, decls in api_auth.iter_route_declarations(_app())
                if any(kind == "device_token" for kind, _perm in decls)]
        assert uses == []


# --- redaction, schema ----------------------------------------------------------

def test_redactor_scrubs_device_tokens():
    tok = "baihe_dt_" + "Ab3-_" * 8 + "xyz"
    for text in (f"failed with {tok} here", f"Authorization: Bearer {tok}", f"{tok}"):
        out = redact_secrets(text)
        assert tok not in out and tok[9:] not in out and "[REDACTED]" in out
    assert redact_secrets("baihe_dt_short") == "baihe_dt_short"


def test_init_db_creates_the_table_on_an_existing_library(isolated_db):
    con = sqlite3.connect(db.DB_PATH)
    con.execute("DROP TABLE extension_device_tokens")
    con.commit()
    con.close()
    db.init_db()
    con = sqlite3.connect(db.DB_PATH)
    cols = {r[1] for r in con.execute("PRAGMA table_info(extension_device_tokens)")}
    con.close()
    assert cols == {"id", "user_id", "label", "token_hash", "created_at", "last_used_at",
                    "last_used_ip_prefix", "revoked_at", "expires_at"}


def test_label_limit_matches_the_frontend():
    from pathlib import Path
    from api.schemas import DeviceTokenCreateRequest
    ts = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "pages" / "settings"
          / "extensionDevicesModel.ts").read_text(encoding="utf-8")
    assert f"export const MAX_DEVICE_LABEL_CHARS = {svc.MAX_LABEL_CHARS}\n" in ts
    max_length = next(m.max_length for m in DeviceTokenCreateRequest.model_fields["label"].metadata
                      if hasattr(m, "max_length"))
    assert max_length == svc.MAX_LABEL_CHARS
