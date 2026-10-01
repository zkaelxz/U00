"""User administration and the read-only audit log view
(api/routers/admin_users_routes.py, services/auth_service.py)."""

import contextlib
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services.service_errors import ConflictError, ForbiddenError, NotFoundError

REMOTE = "https://baihe.example.com"
LOCAL_POST = {"X-Baihe-Local": "1"}
ROUTES = [("GET", "/api/admin/users"), ("GET", "/api/admin/audit"),
          ("POST", "/api/admin/users/{id}/deactivate"), ("POST", "/api/admin/users/{id}/activate"),
          ("POST", "/api/admin/users/{id}/revoke-sessions"),
          ("POST", "/api/admin/users/{id}/revoke-admin")]


def _app(auth="on"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False), frontend_dist=None)


def _remote(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _h(session, csrf=True):
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}"}
    if csrf:
        h[api_auth.CSRF_HEADER] = session["csrf_token"]
    return h


def _admin(email="admin@example.com"):
    u = auth_service.grant_admin_local(email)
    return u, auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def _member(email="kid@example.com"):
    u = auth_service.add_user(email)
    return u, auth_service.create_session(u["id"], "pytest", "198.51.100.4")


def _audit(action):
    return [a for a in auth_service.list_audit(500) if a["action"] == action]


# --- permissions ---------------------------------------------------------------

def test_reads_are_admin_users_read_and_writes_admin_users(isolated_db):
    decls = {(m, p): d for _r, p, ms, d in api_auth.iter_route_declarations(_app())
             for m in ms if p.startswith("/api/admin/")}
    assert {(m, p.replace("{id}", "{user_id}")) for m, p in ROUTES} == set(decls)
    for (method, path), d in decls.items():
        want = "admin.users.read" if method == "GET" else "admin.users"
        assert d == [("permission", want)], (method, path)


def test_anonymous_and_household_refused(isolated_db):
    c = _remote(_app())
    target, _ = _member("other@example.com")
    _u, s = _member()
    auth_service.grant_permission(_u["id"], "media.stream")
    for method, path in ROUTES:
        url = path.format(id=target["id"])
        assert c.request(method, url).status_code == 401
        r = c.request(method, url, headers=_h(s))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert db.auth_get_user(target["id"])["is_active"] == 1
    assert _audit("user.deactivate") == [] and _audit("session.revoke_all") == []
    assert _audit("user.revoke_admin") == []


def test_admin_write_needs_csrf(isolated_db):
    c = _remote(_app())
    _a, s = _admin()
    target, _ = _member()
    r = c.post(f"/api/admin/users/{target['id']}/deactivate", headers=_h(s, csrf=False))
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert db.auth_get_user(target["id"])["is_active"] == 1


def test_remote_admin_lists_users_without_session_details(isolated_db):
    c = _remote(_app())
    a, s = _admin()
    m, ms = _member()
    r = c.get("/api/admin/users", headers=_h(s))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    users = {u["id"]: u for u in r.json()["users"]}
    assert users[a["id"]]["is_self"] and not users[m["id"]]["is_self"]
    assert users[m["id"]]["active_sessions"] == 1 and users[m["id"]]["is_active"]
    text = r.text
    for secret in (s["session_token"], s["csrf_token"], ms["session_token"], "203.0.113",
                   "pytest", "id_hash", "csrf_hash", "google_sub"):
        assert secret not in text


# --- deactivate / activate / revoke ------------------------------------------------

def test_admin_deactivates_activates_and_revokes_with_audit(isolated_db):
    c = _remote(_app())
    a, s = _admin()
    m, ms = _member()
    extra = auth_service.create_session(m["id"])
    r = c.post(f"/api/admin/users/{m['id']}/revoke-sessions", headers=_h(s))
    assert r.status_code == 200 and r.json() == {"user_id": m["id"], "revoked": 2}
    assert auth_service.resolve_session(ms["session_token"]) is None
    assert auth_service.resolve_session(extra["session_token"]) is None
    assert [(e["user_id"], e["detail_redacted"]) for e in _audit("session.revoke_all")] == \
        [(a["id"], f"user {m['id']}: 2")]

    again = auth_service.create_session(m["id"])
    r = c.post(f"/api/admin/users/{m['id']}/deactivate", headers=_h(s))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == m["id"] and not body["is_active"] and body["active_sessions"] == 0
    assert auth_service.resolve_session(again["session_token"]) is None
    assert [(e["user_id"], e["detail_redacted"]) for e in _audit("user.deactivate")] == \
        [(a["id"], f"user {m['id']}")]

    r = c.post(f"/api/admin/users/{m['id']}/activate", headers=_h(s))
    assert r.status_code == 200 and r.json()["is_active"]
    assert [(e["user_id"], e["detail_redacted"]) for e in _audit("user.activate")] == \
        [(a["id"], f"user {m['id']}")]
    # The admin's own session is untouched by all of it.
    assert auth_service.resolve_session(s["session_token"])["user_id"] == a["id"]


def test_unknown_user_is_404_and_not_audited(isolated_db):
    c = _remote(_app())
    _a, s = _admin()
    for action in ("deactivate", "activate", "revoke-sessions"):
        r = c.post(f"/api/admin/users/9999/{action}", headers=_h(s))
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    for bad in ("0", "-1", str(2 ** 70), "abc"):
        assert c.post(f"/api/admin/users/{bad}/deactivate", headers=_h(s)).status_code == 422
    assert not _audit("user.deactivate") and not _audit("user.activate") \
        and not _audit("session.revoke_all")


def test_admin_cannot_deactivate_or_sign_out_self(isolated_db):
    c = _local(_app())
    a, s = _admin()
    _admin("second@example.com")   # another admin exists, so only the self rule applies
    r = c.post(f"/api/admin/users/{a['id']}/deactivate", headers=_h(s))
    assert r.status_code == 409 and "your own account" in r.json()["error"]["message"]
    r = c.post(f"/api/admin/users/{a['id']}/revoke-sessions", headers=_h(s))
    assert r.status_code == 409 and "Sign out" in r.json()["error"]["message"]
    assert db.auth_get_user(a["id"])["is_active"] == 1
    assert auth_service.resolve_session(s["session_token"])
    assert not _audit("user.deactivate") and not _audit("session.revoke_all")


def test_last_active_admin_cannot_be_deactivated(isolated_db):
    c = _local(_app())
    a, s = _admin()
    b, _ = _admin("second@example.com")
    assert c.post(f"/api/admin/users/{b['id']}/deactivate", headers=_h(s)).status_code == 200
    # a is now the only active admin: the local owner can't deactivate them either.
    r = _local(_app("off")).post(f"/api/admin/users/{a['id']}/deactivate", headers=LOCAL_POST)
    assert r.status_code == 409 and "last active admin" in r.json()["error"]["message"]
    assert db.auth_get_user(a["id"])["is_active"] == 1
    assert len(_audit("user.deactivate")) == 1
    # An inactive admin doesn't count; deactivating them again is harmless.
    assert _local(_app("off")).post(f"/api/admin/users/{b['id']}/deactivate",
                                    headers=LOCAL_POST).status_code == 200


def test_admin_target_is_pc_only(isolated_db):
    """A remote admin session manages non-admin accounts only; anything
    touching an admin account is refused (403) and not audited."""
    remote = _remote(_app())
    _a, s = _admin()
    b, bs = _admin("second@example.com")
    for action in ("deactivate", "revoke-sessions", "activate"):
        r = remote.post(f"/api/admin/users/{b['id']}/{action}", headers=_h(s))
        assert r.status_code == 403, action
        assert r.json()["error"]["message"] == auth_service.ADMIN_AT_PC_ONLY
    assert db.auth_get_user(b["id"])["is_active"] == 1
    assert auth_service.resolve_session(bs["session_token"])
    assert not _audit("user.deactivate") and not _audit("user.activate") \
        and not _audit("session.revoke_all")
    # The same admin session at the PC may.
    local = _local(_app())
    assert local.post(f"/api/admin/users/{b['id']}/revoke-sessions",
                      headers=_h(s)).status_code == 200
    assert local.post(f"/api/admin/users/{b['id']}/deactivate", headers=_h(s)).status_code == 200
    # Activating an inactive admin is PC-only too.
    r = remote.post(f"/api/admin/users/{b['id']}/activate", headers=_h(s))
    assert r.status_code == 403 and not db.auth_get_user(b["id"])["is_active"]
    assert local.post(f"/api/admin/users/{b['id']}/activate", headers=_h(s)).status_code == 200


def test_admin_target_check_order(isolated_db):
    """404, then self (409), then admin target (403), then last admin (409)."""
    remote = _remote(_app())
    a, s = _admin()
    r = remote.post("/api/admin/users/9999/deactivate", headers=_h(s))
    assert r.status_code == 404
    r = remote.post(f"/api/admin/users/{a['id']}/deactivate", headers=_h(s))
    assert r.status_code == 409 and "your own account" in r.json()["error"]["message"]
    with pytest.raises(ForbiddenError):
        auth_service.admin_set_active(a["id"], False, actor_id=None)
    with pytest.raises(ConflictError):
        auth_service.admin_set_active(a["id"], False, actor_id=None, at_pc=True)


def test_guarded_write_refuses_the_second_of_two(isolated_db):
    """Two admins deactivating each other: the second write is refused (the
    check and the write are one statement, so they can't interleave)."""
    a = auth_service.grant_admin_local("a@example.com")
    b = auth_service.grant_admin_local("b@example.com")
    assert db.auth_deactivate_user_keeping_an_admin(a["id"]) is True
    assert db.auth_deactivate_user_keeping_an_admin(b["id"]) is False
    assert db.auth_get_user(b["id"])["is_active"] == 1
    m = auth_service.add_user("m@example.com")
    assert db.auth_deactivate_user_keeping_an_admin(m["id"]) is True   # not an admin
    assert db.auth_deactivate_user_keeping_an_admin(9999) is False


def test_service_guards_and_cli_path_unchanged(isolated_db):
    a = auth_service.grant_admin_local("a@example.com")
    with pytest.raises(ConflictError):
        auth_service.admin_set_active(a["id"], False, actor_id=None, at_pc=True)
    with pytest.raises(NotFoundError):
        auth_service.admin_revoke_sessions(4242, actor_id=None)
    # The CLI recovery path (deactivate_user without keep_an_admin) is not guarded.
    assert not auth_service.deactivate_user(a["id"])["is_active"]


def test_local_owner_manages_users_with_auth_off(isolated_db):
    c = _local(_app("off"))
    m, _ = _member()
    r = c.get("/api/admin/users")
    assert r.status_code == 200 and not r.json()["users"][0]["is_self"]
    r = c.post(f"/api/admin/users/{m['id']}/deactivate", headers=LOCAL_POST)
    assert r.status_code == 200 and not r.json()["is_active"]
    assert [e["user_id"] for e in _audit("user.deactivate")] == [None]
    # Off mode serves loopback only.
    assert _remote(_app("off")).get("/api/admin/users").status_code == 403


# --- revoke admin ------------------------------------------------------------------

def test_revoke_admin_makes_a_normal_active_member(isolated_db):
    a, _s = _admin()
    b, bs = _admin("second@example.com")
    extra = auth_service.create_session(b["id"])
    row = auth_service.revoke_admin(b["id"], actor_id=a["id"], at_pc=True)
    assert row["id"] == b["id"] and not row["is_admin"] and row["is_active"]
    assert row["active_sessions"] == 0 and not row["is_self"]
    assert row["permissions"] == sorted(auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS)
    stored = db.auth_get_user(b["id"])
    assert stored["is_admin"] == 0 and stored["is_active"] == 1
    # Sessions ended: no cached admin session survives.
    assert auth_service.resolve_session(bs["session_token"]) is None
    assert auth_service.resolve_session(extra["session_token"]) is None
    assert [(e["user_id"], e["detail_redacted"]) for e in _audit("user.revoke_admin")] == \
        [(a["id"], f"user {b['id']}")]
    # A fresh sign-in is a member's: no admin.* permission, no override.
    p = auth_service.resolve_session(auth_service.create_session(b["id"])["session_token"])
    assert not p["is_admin"] and not p["admin_override"]
    assert not any(x.startswith("admin.") for x in p["permissions"])


def test_grant_admin_restores_a_demoted_admin(isolated_db):
    a, _s = _admin()
    b, _bs = _admin("second@example.com")
    auth_service.revoke_admin(b["id"], actor_id=a["id"], at_pc=True)
    again = auth_service.grant_admin_local("second@example.com")
    assert again["id"] == b["id"] and again["is_admin"] and again["is_active"]


def test_revoke_admin_gives_defaults_only_to_an_empty_account(isolated_db):
    _admin()
    b = auth_service.grant_admin_local("b@example.com")
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        db.auth_revoke_permission(b["id"], p)
    db.auth_grant_permission(b["id"], "admin.users")   # a stale admin row doesn't count
    assert auth_service.revoke_admin(b["id"], at_pc=True)["permissions"] == \
        sorted(auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS)
    # A member permission already stored: kept as is, nothing added.
    c = auth_service.grant_admin_local("c@example.com")
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        db.auth_revoke_permission(c["id"], p)
    db.auth_grant_permission(c["id"], "media.stream")
    assert auth_service.revoke_admin(c["id"], at_pc=True)["permissions"] == ["media.stream"]


def test_revoke_admin_refusals(isolated_db):
    a, _s = _admin()
    m, _ms = _member()
    with pytest.raises(NotFoundError):
        auth_service.revoke_admin(4242, at_pc=True)
    with pytest.raises(ConflictError, match="isn't an admin"):
        auth_service.revoke_admin(m["id"], at_pc=True)
    b = auth_service.grant_admin_local("b@example.com")
    with pytest.raises(ConflictError, match="your own admin rights"):
        auth_service.revoke_admin(b["id"], actor_id=b["id"], at_pc=True)
    with pytest.raises(ForbiddenError):
        auth_service.revoke_admin(b["id"], actor_id=a["id"])   # away from the PC
    auth_service.revoke_admin(b["id"], at_pc=True)
    # a is now the only active admin.
    with pytest.raises(ConflictError, match="last active admin"):
        auth_service.revoke_admin(a["id"], at_pc=True)
    assert db.auth_get_user(a["id"])["is_admin"] == 1
    assert len(_audit("user.revoke_admin")) == 1
    # An inactive admin doesn't count towards the last one, and can be demoted.
    c = auth_service.grant_admin_local("c@example.com")
    auth_service.deactivate_user(c["id"])
    assert not auth_service.revoke_admin(c["id"], at_pc=True)["is_admin"]
    assert db.auth_get_user(c["id"])["is_active"] == 0


def test_revoke_admin_guarded_write_refuses_the_second_of_two(isolated_db):
    a = auth_service.grant_admin_local("a@example.com")
    b = auth_service.grant_admin_local("b@example.com")
    assert db.auth_revoke_admin_keeping_an_admin(a["id"]) is True
    assert db.auth_revoke_admin_keeping_an_admin(b["id"]) is False
    assert db.auth_get_user(b["id"])["is_admin"] == 1
    assert db.auth_revoke_admin_keeping_an_admin(a["id"]) is False   # not an admin now
    assert db.auth_revoke_admin_keeping_an_admin(9999) is False
    # Against a concurrent deactivate too: with only b and c active admins, a
    # deactivate of c and a demote of b can't both pass.
    c = auth_service.grant_admin_local("c@example.com")
    assert db.auth_deactivate_user_keeping_an_admin(c["id"]) is True
    assert db.auth_revoke_admin_keeping_an_admin(b["id"]) is False


def test_revoke_admin_route_pc_only_and_member_forbidden(isolated_db):
    remote, local = _remote(_app()), _local(_app())
    a, s = _admin()
    b, bs = _admin("second@example.com")
    m, ms = _member()
    r = remote.post(f"/api/admin/users/{b['id']}/revoke-admin", headers=_h(s))
    assert r.status_code == 403 and r.json()["error"]["message"] == auth_service.ADMIN_AT_PC_ONLY
    r = local.post(f"/api/admin/users/{b['id']}/revoke-admin", headers=_h(ms))
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    r = local.post(f"/api/admin/users/{b['id']}/revoke-admin", headers=_h(s, csrf=False))
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert db.auth_get_user(b["id"])["is_admin"] == 1 and auth_service.resolve_session(bs["session_token"])
    assert not _audit("user.revoke_admin")
    r = local.post(f"/api/admin/users/{a['id']}/revoke-admin", headers=_h(s))
    assert r.status_code == 409 and "your own admin rights" in r.json()["error"]["message"]
    r = local.post(f"/api/admin/users/{m['id']}/revoke-admin", headers=_h(s))
    assert r.status_code == 409 and "isn't an admin" in r.json()["error"]["message"]
    assert local.post("/api/admin/users/9999/revoke-admin", headers=_h(s)).status_code == 404
    r = local.post(f"/api/admin/users/{b['id']}/revoke-admin", headers=_h(s))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["id"] == b["id"] and not body["is_admin"] and body["is_active"]
    assert auth_service.resolve_session(bs["session_token"]) is None
    # The local owner (auth off) can't demote the last active admin either.
    r = _local(_app("off")).post(f"/api/admin/users/{a['id']}/revoke-admin", headers=LOCAL_POST)
    assert r.status_code == 409 and "last active admin" in r.json()["error"]["message"]
    assert db.auth_get_user(a["id"])["is_admin"] == 1


def test_cli_revoke_admin(isolated_db, capsys):
    from api.__main__ import main
    a = auth_service.grant_admin_local("a@example.com")
    b = auth_service.grant_admin_local("b@example.com")
    s = auth_service.create_session(b["id"])
    assert main(["revoke-admin", "B@Example.com"]) == 0
    out = capsys.readouterr().out
    assert "b@example.com is no longer an admin" in out and out.count("\n") == 1
    assert s["session_token"] not in out
    assert not db.auth_get_user(b["id"])["is_admin"] and db.auth_get_user(b["id"])["is_active"]
    assert [e["user_id"] for e in _audit("user.revoke_admin")] == [None]
    assert main(["revoke-admin", "b@example.com"]) == 2
    assert "isn't an admin" in capsys.readouterr().err
    assert main(["revoke-admin", "ghost@example.com"]) == 2
    assert "No such user" in capsys.readouterr().err
    assert main(["revoke-admin", a["email"]]) == 2
    assert "last active admin" in capsys.readouterr().err
    assert db.auth_get_user(a["id"])["is_admin"] == 1


# --- audit view --------------------------------------------------------------------

def test_audit_newest_first_paged_and_filtered(isolated_db):
    for i in range(7):
        auth_service.write_audit(1 if i % 2 else 2, "user.add" if i < 5 else "logout", f"n {i}")
    page = auth_service.audit_page(3)
    assert [e["detail"] for e in page["events"]] == ["n 6", "n 5", "n 4"]
    assert page["next_before_id"] == page["events"][-1]["id"]
    assert set(page["actions"]) == {"user.add", "logout"}
    page2 = auth_service.audit_page(3, before_id=page["next_before_id"])
    assert [e["detail"] for e in page2["events"]] == ["n 3", "n 2", "n 1"]
    last = auth_service.audit_page(3, before_id=page2["next_before_id"])
    assert [e["detail"] for e in last["events"]] == ["n 0"] and last["next_before_id"] is None
    assert [e["detail"] for e in auth_service.audit_page(50, action="logout")["events"]] == \
        ["n 6", "n 5"]
    assert [e["detail"] for e in auth_service.audit_page(50, user_id=2)["events"]] == \
        ["n 6", "n 4", "n 2", "n 0"]
    assert auth_service.audit_page(50, action="user.add", user_id=1)["events"][0]["detail"] == "n 3"
    assert len(auth_service.audit_page(10 ** 6)["events"]) == 7   # clamped, not an error


def test_audit_page_is_bounded(isolated_db):
    for i in range(auth_service.AUDIT_PAGE_MAX + 5):
        db.auth_insert_audit(None, "x", str(i))
    page = auth_service.audit_page(10 ** 6)
    assert len(page["events"]) == auth_service.AUDIT_PAGE_MAX
    assert page["next_before_id"] is not None


def test_audit_details_scrubbed_on_the_way_out(isolated_db):
    # Written straight to the table, as a row from before a scrub rule would be.
    raw = ("key sk-abcdefghijklmnopqrstuvwxyz0123456789 token "
           "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_- file C:\\Users\\bob\\library.db "
           "and /home/bob/.baihe/library.db url https://example.com/x?key=1 "
           "ip 203.0.113 ip6 2001:db8:1::/48")
    db.auth_insert_audit(3, "login.success", raw)
    detail = auth_service.audit_page(5)["events"][0]["detail"]
    for leaked in ("sk-abc", "AbCdEfGhIjKlMnOp", "Users", "bob", "example.com", "key=1"):
        assert leaked not in detail
    assert "ip 203.0.113" in detail and "2001:db8:1::/48" in detail   # coarse prefixes kept


def test_audit_route(isolated_db):
    c = _remote(_app())
    a, s = _admin()
    m, ms = _member()
    r = c.get("/api/admin/audit", headers=_h(s))
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    ids = [e["id"] for e in body["events"]]
    assert ids == sorted(ids, reverse=True) and body["events"][0]["action"] == "session.create"
    assert set(body["events"][0]) == {"id", "ts", "user_id", "action", "detail"}
    for secret in (s["session_token"], s["csrf_token"], ms["session_token"],
                   auth_service._hash(s["session_token"])):
        assert secret not in r.text
    r = c.get("/api/admin/audit", params={"action": "user.add", "limit": 1}, headers=_h(s))
    assert [e["detail"] for e in r.json()["events"]] == [f"user {m['id']}"]
    assert r.json()["next_before_id"] is None
    r = c.get("/api/admin/audit", params={"user_id": m["id"]}, headers=_h(s))
    assert {e["user_id"] for e in r.json()["events"]} == {m["id"]}
    for bad in ({"limit": 0}, {"limit": auth_service.AUDIT_PAGE_MAX + 1}, {"before_id": 0},
                {"user_id": "x"}, {"action": "a" * 81}):
        assert c.get("/api/admin/audit", params=bad, headers=_h(s)).status_code == 422


def test_audit_is_read_only(isolated_db):
    c = _remote(_app())
    _a, s = _admin()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert c.request(method, "/api/admin/audit", headers=_h(s)).status_code == 405


@pytest.mark.parametrize("stored, shown", [
    ("jane.doe@gmail.com", "j***@gmail.com"),
    ("a@example.com", "***@example.com"),            # one-character local part: nothing of it
    ("ab@example.com", "a***@example.com"),
    ("nobody-at-all", "***"),                         # no "@"
    ("@example.com", "***@example.com"),
    ("émile李@例え.jp", "é***@例え.jp"),                # unicode
])
def test_refused_sign_in_email_masked_on_read_but_stored_in_full(isolated_db, stored, shown):
    db.auth_insert_audit(None, "login.denied", f"not_allowlisted ip 203.0.113 email {stored}")
    detail = auth_service.audit_page(5)["events"][0]["detail"]
    assert detail == f"not_allowlisted ip 203.0.113 email {shown}"
    if stored.split("@")[0]:   # something before the "@" (or no "@"): never returned whole
        assert stored not in detail
    assert auth_service.list_audit(5)[0]["detail_redacted"].endswith(f"email {stored}")


def test_masked_email_through_the_route_and_other_rows(isolated_db):
    c = _remote(_app())
    _a, s = _admin()
    db.auth_insert_audit(None, "login.denied", "not_allowlisted ip 198.51.100 email stranger@gmail.com")
    db.auth_insert_audit(None, "note", "contact someone.else@example.org please")
    r = c.get("/api/admin/audit", headers=_h(s))
    assert r.status_code == 200
    assert "stranger@gmail.com" not in r.text and "someone.else@example.org" not in r.text
    details = [e["detail"] for e in r.json()["events"]]
    assert "not_allowlisted ip 198.51.100 email s***@gmail.com" in details
    assert "contact s***@example.org please" in details


def test_signed_out_and_non_admin_cannot_read_the_audit(isolated_db):
    c = _remote(_app())
    db.auth_insert_audit(None, "login.denied", "not_allowlisted email stranger@gmail.com")
    r = c.get("/api/admin/audit")
    assert r.status_code == 401 and "stranger" not in r.text
    _m, s = _member()
    r = c.get("/api/admin/audit", headers=_h(s))
    assert r.status_code == 403 and "stranger" not in r.text


def test_every_user_id_route_is_admin_users_or_pc_only(isolated_db):
    for auth in ("on", "off"):
        found = [(p, d) for _r, p, _m, d in api_auth.iter_route_declarations(_app(auth))
                 if "{user_id}" in p]
        assert found
        for path, decls in found:
            assert decls in ([("permission", "admin.users")], [("permission", "admin.users.read")],
                             [("local_only", None)]), path


def _set_flags(user_id, **flags):
    with contextlib.closing(db.get_conn()) as conn:
        for k, v in flags.items():
            conn.execute(f"UPDATE users SET {k} = ? WHERE id = ?", (v, user_id))
        conn.commit()


def test_guarded_write_reads_null_and_nonzero_flags_like_the_service(isolated_db):
    a = auth_service.grant_admin_local("a@example.com")
    b = auth_service.grant_admin_local("b@example.com")
    # b's flags are 2/2: truthy, so the service treats b as an active admin, and so does the guard.
    _set_flags(b["id"], is_admin=2, is_active=2)
    assert auth_service.resolve_session(auth_service.create_session(b["id"])["session_token"])
    assert db.auth_deactivate_user_keeping_an_admin(a["id"]) is True
    assert db.auth_deactivate_user_keeping_an_admin(b["id"]) is False
    # NULL flags are off: a NULL-admin is not an admin, a NULL-active admin is not active.
    m = auth_service.add_user("m@example.com")
    _set_flags(m["id"], is_admin=None)
    assert db.auth_deactivate_user_keeping_an_admin(m["id"]) is True
    _set_flags(a["id"], is_active=None)
    assert db.auth_deactivate_user_keeping_an_admin(b["id"]) is False   # a doesn't count
    _set_flags(a["id"], is_active=1)
    assert db.auth_deactivate_user_keeping_an_admin(b["id"]) is True


@pytest.mark.parametrize("action", ["deactivate", "revoke-sessions"])
def test_deactivate_and_revoke_end_the_users_open_event_stream(isolated_db, monkeypatch, action):
    from services import event_stream_service as ev
    monkeypatch.setattr(ev, "MAX_STREAM_SECONDS", 30.0)
    monkeypatch.setattr(ev, "HEARTBEAT_SECONDS", 0.3)
    monkeypatch.setattr(ev, "JOB_SWEEP_SECONDS", 0.2)
    monkeypatch.setattr(ev, "MIN_BATCH_SECONDS", 0.01)
    monkeypatch.setattr(ev, "AUTH_RECHECK_SECONDS", 0.0)
    _a, s = _admin()
    m, ms = _member()
    admin_client, member_client = _remote(_app()), _remote(_app())
    answers, acted_at = [], []

    def act():
        deadline = time.time() + 20
        while ev.open_count() < 1 and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.05)
        answers.append(admin_client.post(f"/api/admin/users/{m['id']}/{action}",
                                         headers=_h(s)).status_code)
        acted_at.append(time.time())
    t = threading.Thread(target=act, daemon=True)
    t.start()
    r = member_client.get("/api/events", headers=_h(ms, csrf=False))
    ended = time.time()
    t.join(20)
    assert r.status_code == 200 and answers == [200]
    # Ended by the action (the next session re-check), long before the 30 s cap.
    assert -1 < ended - acted_at[0] < 10
    assert ev.open_count() == 0
