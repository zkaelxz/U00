"""The signed-in user's own devices (api/routers/auth_routes.py, the
`/api/auth/sessions` routes; services/auth_service.py). A user lists and
signs out only their own sessions; anything else is a 404."""

import contextlib
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings, load_settings
from api.server import create_app
from services import auth_service
from services.service_errors import ConflictError, ForbiddenError, NotFoundError

REMOTE = "https://baihe.example.com"
PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0")
ROUTES = [("GET", "/api/auth/sessions"), ("POST", "/api/auth/sessions/revoke-others"),
          ("POST", "/api/auth/sessions/{auth_session_id}/revoke")]


def _app(auth="on", **kw):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False, **kw), frontend_dist=None)


def _remote(app, ip="203.0.113.9"):
    return TestClient(app, base_url=REMOTE, client=(ip, 5000), raise_server_exceptions=False)


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _h(session, csrf=True):
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}
    if csrf:
        h[api_auth.CSRF_HEADER] = session["csrf_token"]
    return h


def _member(email="kid@example.com", admin=False):
    u = auth_service.grant_admin_local(email) if admin else auth_service.add_user(email)
    phone = auth_service.create_session(u["id"], PHONE_UA, "203.0.113.77")
    pc = auth_service.create_session(u["id"], PC_UA, "2001:db8:1:2::5")
    return u, phone, pc


def _audit(action):
    return [a for a in auth_service.list_audit(500) if a["action"] == action]


def _alive(session) -> bool:
    return auth_service.resolve_session(session["session_token"], touch=False) is not None


# --- declarations -----------------------------------------------------------------

def test_routes_are_authenticated_only(isolated_db):
    decls = {(m, p): d for _r, p, ms, d in api_auth.iter_route_declarations(_app())
             for m in ms if p.startswith("/api/auth/sessions")}
    assert set(decls) == set(ROUTES)
    assert all(d == [("authenticated", None)] for d in decls.values())


def test_anonymous_is_401_and_auth_off_is_404(isolated_db):
    c = _remote(_app())
    for method, path in ROUTES:
        assert c.request(method, path.format(auth_session_id=1)).status_code == 401
    local = TestClient(_app("off"), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
    for method, path in ROUTES:
        r = local.request(method, path.format(auth_session_id=1),
                          headers={"X-Baihe-Local": "1"})
        assert r.status_code == 404


def _household_app():
    # The household listener needs sign-in configured and answers only to
    # the BAIHE_PUBLIC_URL host (REMOTE).
    return create_app(ApiSettings(household_port=8610, serve_frontend=False,
                                  google_client_id="cid", google_client_secret="s3cr3t-value",
                                  public_url=REMOTE),
                      frontend_dist=None, listener="household")


def test_household_listener_serves_them(isolated_db):
    app = _household_app()
    _u, phone, _pc = _member()
    r = _remote(app).get("/api/auth/sessions", headers=_h(phone, csrf=False))
    assert r.status_code == 200 and len(r.json()["sessions"]) == 2


# --- listing -------------------------------------------------------------------------

def test_lists_only_own_live_sessions_with_coarse_details(isolated_db):
    u, phone, pc = _member()
    other = auth_service.add_user("other@example.com")
    other_s = auth_service.create_session(other["id"], PC_UA, "198.51.100.4")
    r = _remote(_app()).get("/api/auth/sessions", headers=_h(phone, csrf=False))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["idle_timeout_days"] == 14 and body["absolute_timeout_days"] == 30
    rows = body["sessions"]
    assert [s["id"] for s in rows] == [phone["session_id"], pc["session_id"]]  # this device first
    assert set(rows[0]) == {"id", "device", "created_at", "last_seen_at", "expires_at",
                            "ip_prefix", "current"}
    assert [(s["device"], s["ip_prefix"], s["current"]) for s in rows] == [
        ("Safari on iPhone", "203.0.113", True), ("Edge on Windows", "2001:db8:1::/48", False)]
    text = r.text
    for secret in (phone["session_token"], phone["csrf_token"], pc["session_token"],
                   other_s["session_token"], auth_service._hash(phone["session_token"]),
                   "203.0.113.77", "2001:db8:1:2::5", "Mozilla", "Win64", "15E148"):
        assert secret not in text
    assert str(other_s["session_id"]) not in [str(s["id"]) for s in rows]


def test_expired_and_idle_sessions_are_not_listed(isolated_db):
    u = auth_service.add_user("kid@example.com")
    now = time.time()
    current = auth_service.create_session(u["id"], PHONE_UA, "203.0.113.1", now=now)
    auth_service.create_session(u["id"], PC_UA, "203.0.113.2",
                                now=now - auth_service.IDLE_TIMEOUT_SECONDS - 5)
    rows = auth_service.list_own_sessions(u["id"], current["session_id"], now=now)
    assert [s["id"] for s in rows] == [current["session_id"]]


# --- signing out one device ----------------------------------------------------------

def test_revoke_one_other_device_is_immediate_and_audited(isolated_db):
    u, phone, pc = _member()
    c = _remote(_app())
    r = c.post(f"/api/auth/sessions/{pc['session_id']}/revoke", headers=_h(phone))
    assert r.status_code == 200 and r.json() == {"revoked": 1}
    assert r.headers["cache-control"] == "no-store"
    assert not _alive(pc) and _alive(phone)
    assert c.get("/api/library/dramas", headers=_h(pc, csrf=False)).status_code == 401
    rows = _audit("session.revoke")
    assert [(a["user_id"], a["detail_redacted"]) for a in rows] == [
        (u["id"], f"session {pc['session_id']} ip 203.0.113")]
    # Revoking it again: gone.
    r = c.post(f"/api/auth/sessions/{pc['session_id']}/revoke", headers=_h(phone))
    assert r.status_code == 404


def test_cannot_revoke_another_users_session_and_learns_nothing(isolated_db):
    _u, phone, _pc = _member()
    other = auth_service.add_user("other@example.com")
    victim = auth_service.create_session(other["id"], PC_UA, "198.51.100.4")
    c = _remote(_app())
    theirs = c.post(f"/api/auth/sessions/{victim['session_id']}/revoke", headers=_h(phone))
    missing = c.post("/api/auth/sessions/999999/revoke", headers=_h(phone))
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()
    assert _alive(victim)
    assert _audit("session.revoke") == []


def test_this_device_is_refused_use_sign_out(isolated_db):
    _u, phone, _pc = _member()
    r = _remote(_app()).post(f"/api/auth/sessions/{phone['session_id']}/revoke",
                             headers=_h(phone))
    assert r.status_code == 409 and "Sign out" in r.json()["error"]["message"]
    assert _alive(phone)


@pytest.mark.parametrize("bad", ["0", "-1", "abc", str(2 ** 63)])
def test_bad_ids_are_refused_before_any_lookup(isolated_db, bad):
    _u, phone, pc = _member()
    r = _remote(_app()).post(f"/api/auth/sessions/{bad}/revoke", headers=_h(phone))
    assert r.status_code == 422
    assert _alive(pc)


# --- signing out every other device ------------------------------------------------------

def test_revoke_others_keeps_this_device_and_other_users(isolated_db):
    u, phone, pc = _member()
    third = auth_service.create_session(u["id"], "", "203.0.113.80")
    other = auth_service.add_user("other@example.com")
    theirs = auth_service.create_session(other["id"], PC_UA, "198.51.100.4")
    c = _remote(_app(), ip="203.0.113.50")
    r = c.post("/api/auth/sessions/revoke-others", headers=_h(phone))
    assert r.status_code == 200 and r.json() == {"revoked": 2}
    assert r.headers["cache-control"] == "no-store"
    assert not _alive(pc) and not _alive(third) and _alive(theirs)
    assert [(a["user_id"], a["detail_redacted"]) for a in _audit("session.revoke_others")] == [
        (u["id"], f"user {u['id']}: 2 ip 203.0.113")]
    # This device was rotated: its old cookie (and any copy of it) is dead,
    # and the response set a new session and CSRF cookie.
    assert not _alive(phone)
    new = {"session_token": r.cookies.get(api_auth.COOKIE_NAME),
           "csrf_token": r.cookies.get(api_auth.CSRF_COOKIE_NAME)}
    assert new["session_token"] and new["csrf_token"]
    assert new["session_token"] not in r.text and new["csrf_token"] not in r.text
    assert new["session_token"] != phone["session_token"]
    assert new["csrf_token"] != phone["csrf_token"]
    assert c.get("/api/auth/sessions", headers=_h(phone, csrf=False)).status_code == 401
    rows = c.get("/api/auth/sessions", headers=_h(new, csrf=False)).json()["sessions"]
    assert len(rows) == 1 and rows[0]["current"] and rows[0]["id"] != phone["session_id"]
    assert rows[0]["device"] == "Safari on iPhone" and rows[0]["ip_prefix"] == "203.0.113"
    # The old CSRF token doesn't pass with the new session; the new one does.
    stale = dict(_h(new, csrf=False), **{api_auth.CSRF_HEADER: phone["csrf_token"]})
    assert c.post("/api/auth/sessions/revoke-others", headers=stale).status_code == 403
    again = c.post("/api/auth/sessions/revoke-others", headers=_h(new))
    assert again.status_code == 200 and again.json() == {"revoked": 0}
    # Nothing to sign out: not audited.
    assert len(_audit("session.revoke_others")) == 1


def test_rotation_keeps_the_sign_in_time_and_expiry(isolated_db):
    u = auth_service.add_user("kid@example.com")
    now = time.time()
    s = auth_service.create_session(u["id"], PHONE_UA, "203.0.113.7", now=now - 5 * 86400)
    auth_service.resolve_session(s["session_token"], now=now - 60)
    out = auth_service.revoke_other_sessions(u["id"], s["session_id"], "198.51.100.3",
                                             is_admin=False, at_pc=False, now=now)
    assert out["revoked"] == 0 and _audit("session.revoke_others") == []
    [row] = db.auth_list_sessions(u["id"])
    assert row["id"] == out["session_id"] != s["session_id"]
    assert row["created_at"] == now - 5 * 86400
    assert row["expires_at"] == s["expires_at"]
    assert row["last_seen_at"] == now
    assert (row["device_label"], row["ip_prefix"]) == ("Safari on iPhone", "198.51.100")
    assert auth_service.resolve_session(out["session_token"], touch=False)["session_id"] == row["id"]
    assert auth_service.verify_csrf(out["session_token"], out["csrf_token"])


def test_rotation_of_a_session_revoked_meanwhile_is_401(isolated_db, monkeypatch):
    u, phone, pc = _member()
    real = db.auth_delete_user_sessions

    def racing(user_id, except_id=None):
        n = real(user_id, except_id=except_id)
        db.auth_delete_session(phone["session_id"])   # signed out elsewhere meanwhile
        return n
    monkeypatch.setattr(db, "auth_delete_user_sessions", racing)
    r = _remote(_app()).post("/api/auth/sessions/revoke-others", headers=_h(phone))
    assert r.status_code == 401
    assert api_auth.COOKIE_NAME not in r.cookies
    assert db.auth_list_sessions(u["id"]) == []


# --- admins: PC-only (D5) ------------------------------------------------------------------

def test_admin_away_from_the_pc_can_list_but_not_revoke(isolated_db):
    u, phone, pc = _member("admin@example.com", admin=True)
    c = _remote(_app())
    assert c.get("/api/auth/sessions", headers=_h(phone, csrf=False)).status_code == 200
    for url in (f"/api/auth/sessions/{pc['session_id']}/revoke",
                "/api/auth/sessions/revoke-others",
                "/api/auth/sessions/999999/revoke",
                f"/api/auth/sessions/{phone['session_id']}/revoke"):
        r = c.post(url, headers=_h(phone))
        assert r.status_code == 403, url
        assert r.json()["error"]["code"] == "forbidden"
        assert "at the PC" in r.json()["error"]["message"]
        assert api_auth.COOKIE_NAME not in r.cookies
    assert _alive(phone) and _alive(pc)
    assert _audit("session.revoke") == _audit("session.revoke_others") == []


def test_admin_on_the_household_listener_is_refused(isolated_db):
    _u, phone, pc = _member("admin@example.com", admin=True)
    # What the reverse proxy on this PC passes on: a loopback peer, the public Host.
    c = TestClient(_household_app(), base_url="http://baihe.example.com",
                   client=("127.0.0.1", 5000), raise_server_exceptions=False)
    assert c.post(f"/api/auth/sessions/{pc['session_id']}/revoke",
                  headers=_h(phone)).status_code == 403
    assert _alive(pc)


def test_admin_at_the_pc_can_revoke(isolated_db):
    u, phone, pc = _member("admin@example.com", admin=True)
    third = auth_service.create_session(u["id"], PC_UA, "203.0.113.80")
    c = _local(_app())
    r = c.post(f"/api/auth/sessions/{third['session_id']}/revoke", headers=_h(pc))
    assert r.status_code == 200 and not _alive(third)
    r = c.post("/api/auth/sessions/revoke-others", headers=_h(pc))
    assert r.status_code == 200 and r.json() == {"revoked": 1}
    assert not _alive(phone) and not _alive(pc) and r.cookies.get(api_auth.COOKIE_NAME)


# --- CSRF ----------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/auth/sessions/revoke-others", "/api/auth/sessions/{pc}/revoke"])
def test_writes_need_the_csrf_header(isolated_db, path):
    _u, phone, pc = _member()
    c = _remote(_app())
    url = path.format(pc=pc["session_id"])
    for headers in (_h(phone, csrf=False), dict(_h(phone, csrf=False), **{api_auth.CSRF_HEADER: "x" * 43}),
                    dict(_h(phone, csrf=False), **{api_auth.CSRF_HEADER: pc["csrf_token"]})):
        r = c.post(url, headers=headers)
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert _alive(pc)


# --- timeouts --------------------------------------------------------------------------

def test_timeout_settings_defaults_bounds_and_errors():
    s = load_settings({})
    assert (s.session_idle_days, s.session_max_days) == (14, 30)
    s = load_settings({"BAIHE_API_SESSION_IDLE_DAYS": "2", "BAIHE_API_SESSION_MAX_DAYS": "7"})
    assert (s.session_idle_days, s.session_max_days) == (2, 7)
    for env in ({"BAIHE_API_SESSION_IDLE_DAYS": "0"}, {"BAIHE_API_SESSION_IDLE_DAYS": "91"},
                {"BAIHE_API_SESSION_MAX_DAYS": "366"}, {"BAIHE_API_SESSION_MAX_DAYS": "1.5"},
                {"BAIHE_API_SESSION_IDLE_DAYS": "31"}):   # idle beyond the default 30-day max
        with pytest.raises(ValueError):
            load_settings(env)
    with pytest.raises(ValueError):
        auth_service.configure_timeouts(10, 5)


def test_configured_timeouts_apply_to_existing_sessions(isolated_db, monkeypatch):
    # Restored after the test, whatever create_app set.
    monkeypatch.setattr(auth_service, "IDLE_TIMEOUT_SECONDS", auth_service.IDLE_TIMEOUT_SECONDS)
    monkeypatch.setattr(auth_service, "ABSOLUTE_TIMEOUT_SECONDS",
                        auth_service.ABSOLUTE_TIMEOUT_SECONDS)
    u = auth_service.add_user("kid@example.com")
    now = time.time()
    old = auth_service.create_session(u["id"], now=now - 3 * 86400)   # stored expiry: +30 days
    auth_service.resolve_session(old["session_token"], now=now - 60)  # recently used
    app = _app(session_idle_days=1, session_max_days=2)
    assert auth_service.IDLE_TIMEOUT_SECONDS == 86400
    assert auth_service.ABSOLUTE_TIMEOUT_SECONDS == 2 * 86400
    # Over the new 2-day absolute limit although its stored expiry is weeks away.
    assert auth_service.resolve_session(old["session_token"], now=now) is None
    fresh = auth_service.create_session(u["id"], now=now)
    assert fresh["expires_at"] == now + 2 * 86400
    r = _remote(app).get("/api/auth/sessions", headers=_h(fresh, csrf=False))
    body = r.json()
    assert (body["idle_timeout_days"], body["absolute_timeout_days"]) == (1, 2)
    assert auth_service.resolve_session(fresh["session_token"], now=now + 86400 + 1) is None


# --- open event streams ------------------------------------------------------------------

@pytest.mark.parametrize("how", ["one", "others"])
def test_revoke_ends_the_devices_open_event_stream_at_once(isolated_db, monkeypatch, how):
    from services import event_stream_service as ev
    monkeypatch.setattr(ev, "MAX_STREAM_SECONDS", 30.0)
    # Heartbeat and re-check intervals stay long: only the wake-up can end it fast.
    monkeypatch.setattr(ev, "HEARTBEAT_SECONDS", 15.0)
    monkeypatch.setattr(ev, "AUTH_RECHECK_SECONDS", 5.0)
    monkeypatch.setattr(ev, "JOB_SWEEP_SECONDS", 60.0)
    _u, phone, pc = _member()
    app = _app()
    answers, acted_at = [], []

    def act():
        deadline = time.time() + 20
        while ev.open_count() < 1 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)
        url = (f"/api/auth/sessions/{pc['session_id']}/revoke" if how == "one"
               else "/api/auth/sessions/revoke-others")
        answers.append(_remote(app).post(url, headers=_h(phone)).status_code)
        acted_at.append(time.time())
    t = threading.Thread(target=act, daemon=True)
    t.start()
    r = _remote(app).get("/api/events", headers=_h(pc, csrf=False))
    ended = time.time()
    t.join(20)
    assert r.status_code == 200 and answers == [200]
    assert -1 < ended - acted_at[0] < 3
    assert ev.open_count() == 0


def test_request_recheck_wakes_only_that_users_streams():
    import asyncio
    from services import event_stream_service as ev
    loop = asyncio.new_event_loop()
    try:
        mine = ev.open_subscription({"user_id": 1}, ev.TOPICS, loop=loop)
        theirs = ev.open_subscription({"user_id": 2}, ev.TOPICS, loop=loop)
        ev.request_recheck(1)
        assert mine.pending() and not theirs.pending()
        assert mine.drain()["recheck"] is True and mine.drain()["recheck"] is False
        ev.request_recheck(None)
        assert theirs.drain()["recheck"] is True
    finally:
        ev.close_subscription(mine)
        ev.close_subscription(theirs)
        loop.close()


# --- service ------------------------------------------------------------------------------

@pytest.mark.parametrize("ua,label", [
    (PHONE_UA, "Safari on iPhone"),
    (PC_UA, "Edge on Windows"),
    ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/129.0 Mobile Safari/537.36",
     "Chrome on Android"),
    ("Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 SamsungBrowser/25.0 "
     "Chrome/121.0 Mobile Safari/537.36", "Samsung Internet on Android"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 CriOS/129.0 "
     "Mobile/15E148 Safari/604.1", "Chrome on iPhone"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5; rv:130.0) Gecko/20100101 Firefox/130.0",
     "Firefox on Mac"),
    ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36",
     "Chrome on Linux"),
    ("curl/8.5.0", "Unknown device"),
    ("", "Unknown device"),
    ("<script>alert(1)</script> Windows", "Browser on Windows"),
])
def test_device_label(ua, label):
    assert auth_service.device_label(ua) == label


def test_service_scopes_everything_to_the_caller(isolated_db):
    u, phone, pc = _member()
    other = auth_service.add_user("other@example.com")
    theirs = auth_service.create_session(other["id"])
    member = dict(is_admin=False, at_pc=False)
    with pytest.raises(NotFoundError):
        auth_service.revoke_own_session(u["id"], theirs["session_id"], phone["session_id"],
                                        **member)
    with pytest.raises(ConflictError):
        auth_service.revoke_own_session(u["id"], phone["session_id"], phone["session_id"],
                                        **member)
    with pytest.raises(ForbiddenError):
        auth_service.revoke_own_session(u["id"], pc["session_id"], phone["session_id"],
                                        is_admin=True, at_pc=False)
    with pytest.raises(ForbiddenError):
        auth_service.revoke_other_sessions(u["id"], phone["session_id"], is_admin=True,
                                           at_pc=False)
    assert _alive(pc)
    assert [s["id"] for s in auth_service.list_own_sessions(other["id"], None)] == [
        theirs["session_id"]]
    out = auth_service.revoke_other_sessions(u["id"], phone["session_id"], **member)
    assert out["revoked"] == 1
    assert _alive(theirs) and not _alive(pc) and _alive(out)


def test_init_db_labels_old_rows_from_the_stored_prefix_then_blanks_it(isolated_db):
    u = auth_service.add_user("kid@example.com")
    old = auth_service.create_session(u["id"])
    kept = auth_service.create_session(u["id"], PC_UA)
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE auth_sessions SET device_label = '', user_agent_short = ? "
                     "WHERE id = ?", (PHONE_UA[:60], old["session_id"]))
        conn.commit()
    db.init_db()
    rows = {r["id"]: r for r in db.auth_list_sessions(u["id"])}
    assert rows[old["session_id"]]["device_label"] == "Browser on iPhone"
    assert rows[kept["session_id"]]["device_label"] == "Edge on Windows"
    assert all(r["user_agent_short"] == "" for r in rows.values())
    assert "Mozilla" not in repr(rows)
    db.init_db()   # idempotent
    assert {r["id"]: r for r in db.auth_list_sessions(u["id"])} == rows
    listed = auth_service.list_own_sessions(u["id"], old["session_id"])
    assert listed[0]["device"] == "Browser on iPhone"
