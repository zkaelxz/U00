"""Feature switches with sign-in on: a switched-off area is simply absent (404, not
401/403) even for an admin, device-token revocation survives the extension switch, and
/api/meta stays a public, boolean-only report. Switch mechanics: test_feature_switches.py."""

import re
import sqlite3

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from fastapi.testclient import TestClient

from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from lib import features
from services import auth_service

PUBLIC_HOST = "baihe.example.com"
SIGN_IN = {"google_client_id": "cid", "google_client_secret": "s3cr3t-value",
           "public_url": f"https://{PUBLIC_HOST}"}
ALL_OFF = "jellyfin,sources,extension"

# One mutating route per switchable area, plus device-token (bridge) paths.
OFF_ROUTES = [
    ("POST", "/api/jellyfin/config", {"url": "http://jf.invalid", "enabled": True}),
    ("GET", "/api/jellyfin/config", None),
    ("GET", "/api/sources/domains", None),
    ("POST", "/api/source-domains", {"domain": "example.com"}),
    ("GET", "/api/extension/status", None),
    ("POST", "/api/extension/enabled", {"enabled": True}),
    ("POST", "/api/extension/token", {}),
    ("GET", "/api/ext-bridge/health", None),
    ("POST", "/api/ext-bridge/page", {}),
]


def _app(off, listener="admin", household=False):
    extra = {"household_port": 8610, "auth_mode": "off", **SIGN_IN} if household else {"auth_mode": "on"}
    return create_app(ApiSettings(serve_frontend=False, features_off=features.parse(off), **extra),
                      listener=listener)


def _client(app, host="127.0.0.1"):
    # A PC-local request has a loopback peer and a loopback Host; anything else is remote.
    # The household app answers only its public Host (as a reverse proxy on the PC sends it).
    household = app.state.settings.is_household
    base = f"http://{PUBLIC_HOST}" if household else (
        "http://127.0.0.1:8600" if host == "127.0.0.1" else "https://" + PUBLIC_HOST)
    return TestClient(app, base_url=base, client=(host, 5000), raise_server_exceptions=False)


def _signed_in(app, host="127.0.0.1"):
    user = auth_service.grant_admin_local("owner@example.com")
    s = auth_service.create_session(user["id"], "pytest", host)
    c = _client(app, host)
    c.headers.update({"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                      api_auth.CSRF_HEADER: s["csrf_token"]})
    return c


def _dump():
    # Sessions record last-used times on every request, so they are not "written data".
    con = sqlite3.connect(db.DB_PATH)
    try:
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")
                  if "session" not in r[0] and r[0] != "sqlite_sequence"]
        return {t: con.execute(f'select * from "{t}"').fetchall() for t in tables}
    finally:
        con.close()


def _send(c, method, path, body):
    return c.request(method, path, json=body) if body is not None else c.request(method, path)


@pytest.mark.parametrize("method,path,body", OFF_ROUTES)
def test_off_area_answers_404_to_signed_in_admin_and_writes_nothing(isolated_db, method, path, body):
    c = _signed_in(_app(ALL_OFF), "127.0.0.1")
    before = _dump()
    r = _send(c, method, path, body)
    assert r.status_code == 404, (path, r.status_code)
    assert _dump() == before


@pytest.mark.parametrize("method,path,body", OFF_ROUTES)
def test_off_area_answers_404_to_signed_in_remote_admin_on_household_listener(
        isolated_db, method, path, body):
    app = _app(ALL_OFF, listener="household", household=True)
    c = _signed_in(app)
    before = _dump()
    r = _send(c, method, path, body)
    assert r.status_code == 404, (path, r.status_code)
    assert _dump() == before


def test_on_baseline_is_not_404_so_the_404s_above_mean_removal(isolated_db):
    c = _signed_in(_app(""))
    assert c.get("/api/jellyfin/config").status_code != 404
    assert c.get("/api/extension/status").status_code != 404


def test_device_token_routes_stay_mounted_with_extension_off(isolated_db):
    c = _signed_in(_app("extension"))
    assert c.get("/api/extension/devices").status_code == 200
    assert c.get("/api/auth/device-tokens").status_code != 404
    # JSON body: a bodyless POST to a local_only route is refused before routing matters.
    # A mounted revoke of an unknown token is a 404 from the service, naming nothing.
    r = c.post("/api/extension/devices/999/revoke", json={})
    assert r.status_code == 404 and "revoked" not in r.text
    assert c.get("/api/extension/status").status_code == 404


def test_admin_revoke_is_still_local_only_with_extension_off(isolated_db):
    app = _app("extension")
    local = _signed_in(app, "127.0.0.1")
    remote = _signed_in(app, "203.0.113.7")
    assert remote.post("/api/extension/devices/1/revoke", json={}).status_code == 403
    assert remote.get("/api/extension/devices").status_code == 403
    from services import device_token_service
    user = auth_service.add_user("member@example.com")
    auth_service.grant_permission(user["id"], "extension.send")
    made = device_token_service.create_token(
        {"user_id": user["id"], "permissions": ("extension.send",)}, "phone")
    token_id = made["device_token"]["id"]
    assert remote.post(f"/api/extension/devices/{token_id}/revoke", json={}).status_code == 403
    assert device_token_service.admin_list()[0]["revoked_at"] is None   # refusal wrote nothing
    assert local.post(f"/api/extension/devices/{token_id}/revoke", json={}).json() == {"revoked": 1}
    # local_only is declared on the route itself, not inferred from the switch.
    declared = {path: decls for _r, path, _m, decls in api_auth.iter_route_declarations(app)}
    assert declared["/api/extension/devices/{device_token_id}/revoke"] == [("local_only", None)]


def test_admin_revoke_refused_remotely_on_household_listener(isolated_db):
    c = _signed_in(_app("extension", listener="household", household=True))
    assert c.post("/api/extension/devices/1/revoke", json={}).status_code == 403


@pytest.mark.parametrize("off,expected", [
    ("", {"jellyfin": True, "sources": True, "extension": True}),
    (ALL_OFF, {"jellyfin": False, "sources": False, "extension": False}),
])
@pytest.mark.parametrize("listener", ["admin", "household"])
def test_meta_is_public_and_reports_only_booleans(isolated_db, off, expected, listener):
    app = _app(off, listener=listener, household=listener == "household")
    c = _client(app)
    r = c.get("/api/meta")        # no cookie: public
    assert r.status_code == 200
    body = r.json()
    assert body["features"] == expected
    assert all(type(v) is bool for v in body["features"].values())
    assert not re.search(r"[A-Za-z]:\\|/home/|/tmp/|s3cr3t|cid|jf\.invalid|BAIHE_", r.text)
