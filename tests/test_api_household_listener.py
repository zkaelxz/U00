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
from jobs import job_launch
import db
from api import auth as api_auth
from api import remote_uploads
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


OLLAMA_KEY = "0123456789abcdef0123456789abcdef.AbCdEfGhIjKlMnOpQrStUvWx"


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
        ApiSettings(household_port=8610, **{**SIGN_IN, "public_url": "https://faß.example"}),
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

    def test_settings_refused_even_for_an_admin(self, isolated_db):
        from services import settings_service
        settings_service.set_settings({"tesseract_cmd": "/opt/tess/tesseract"})
        s = _admin_session()
        r = _household_client().get("/api/settings", headers=_h(s))
        assert r.status_code == 403 and "/opt/tess" not in r.text
        prefs = _admin_client().get("/api/settings").json()["preferences"]
        assert prefs["tesseract_cmd"] == "/opt/tess/tesseract"


# --- admin permissions stay on the PC, except managing member accounts -----------

class TestNoAdminPermissionsOnHousehold:
    """D5, the 2026-09-30 and 2026-10-30 decisions: on the household listener
    a signed-in admin keeps the household permissions, the admin view
    permissions (user list, audit log) and `admin.users` (member accounts),
    and no other admin write permission. A change to an admin account stays
    PC-only there."""

    def _admin_routes(self, app):
        return [(sorted(methods), path, perm) for _r, path, methods, decls
                in api_auth.iter_route_declarations(app)
                for kind, perm in decls if kind == "permission" and perm.startswith("admin.")]

    def test_every_admin_permission_is_classified(self):
        """A new admin.* permission fails here until it is listed as view, away
        write or write in services/auth_service.py (unlisted counts as write)."""
        view = set(auth_service.ADMIN_VIEW_PERMISSIONS)
        away = set(auth_service.ADMIN_AWAY_WRITE_PERMISSIONS)
        write = set(auth_service.ADMIN_WRITE_PERMISSIONS)
        assert not view & write and not view & away and not away & write
        assert view | away | write == set(auth_service.ADMIN_PERMISSIONS)
        assert set(auth_service.ADMIN_HOUSEHOLD_PERMISSIONS) == view | away
        in_use = {perm for _m, _p, perm in self._admin_routes(_household_app())}
        declared = {p for p in auth_service.PERMISSIONS if p.startswith("admin.")}
        unclassified = (in_use | declared) - view - away - write
        assert not unclassified, f"classify as admin view or write: {sorted(unclassified)}"

    def test_view_permissions_cover_only_reads(self):
        for methods, path, perm in self._admin_routes(_household_app()):
            if perm in auth_service.ADMIN_VIEW_PERMISSIONS:
                assert set(methods) <= {"GET", "HEAD"}, f"{methods} {path} ({perm})"

    def test_every_admin_write_route_refused_to_a_signed_in_admin(self, isolated_db):
        app = _household_app()
        routes = [r for r in self._admin_routes(app)
                  if r[2] not in auth_service.ADMIN_HOUSEHOLD_PERMISSIONS]
        assert ({perm for _m, _p, perm in routes} == set(auth_service.ADMIN_WRITE_PERMISSIONS))
        c = _household_client(app)
        s = _admin_session()
        bad = []
        for methods, path, _perm in routes:
            for method in methods:
                kw = {} if method in ("GET", "HEAD", "DELETE") else {"json": {}}
                r = c.request(method, _concrete(path), headers=_h(s), **kw)
                if r.status_code != 403 or (method != "HEAD" and r.json() != {
                        "error": {"code": "forbidden", "message": "Not allowed."}}):
                    bad.append(f"{method} {path}: {r.status_code}")
        assert not bad, "household served admin writes to an admin:\n  " + "\n  ".join(bad)

    def test_member_account_changes_served_with_csrf_and_audit(self, isolated_db):
        s = _admin_session()
        admin_id = auth_service.find_user_by_email("owner@example.com")["id"]
        member = auth_service.add_user("kid@example.com")
        auth_service.create_session(member["id"], "pytest", "127.0.0.1")
        h = _household_client()
        url = f"/api/admin/users/{member['id']}"
        no_csrf = {"Cookie": _h(s)["Cookie"]}
        r = h.post(f"{url}/permissions/engines.paid", headers=no_csrf)
        assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
        assert h.post(f"{url}/permissions/engines.paid", headers=_h(s)).status_code == 200
        assert h.delete(f"{url}/permissions/lines.edit", headers=_h(s)).status_code == 200
        assert set(auth_service.effective_permissions(member["id"])) == (
            set(auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS) - {"lines.edit"} | {"engines.paid"})
        assert h.post(f"{url}/revoke-sessions", headers=_h(s)).status_code == 200
        assert not db.auth_list_sessions(member["id"])
        assert h.post(f"{url}/deactivate", headers=_h(s)).status_code == 200
        assert auth_service.get_user(member["id"])["is_active"] is False
        assert h.post(f"{url}/activate", headers=_h(s)).status_code == 200
        assert auth_service.get_user(member["id"])["is_active"] is True
        actions = {(a["user_id"], a["action"]) for a in auth_service.list_audit(500)}
        assert {(admin_id, "permission.grant"), (admin_id, "permission.revoke"),
                (admin_id, "session.revoke_all"), (admin_id, "user.deactivate"),
                (admin_id, "user.activate")} <= actions

    def test_admin_account_changes_still_refused(self, isolated_db, tmp_path, monkeypatch):
        """No second factor away from the PC: a stolen admin session there
        can't sign out, deactivate or demote an admin, the caller included,
        nor remove a key an admin saved as a member before promotion."""
        pytest.importorskip("cryptography")
        import member_keys
        from services import member_key_service, settings_service
        monkeypatch.setattr(settings_service, "default_env_path", lambda: str(tmp_path / ".env"))
        monkeypatch.delenv(member_key_service.SECRET_ENV, raising=False)
        s = _admin_session()
        owner = auth_service.find_user_by_email("owner@example.com")
        other = auth_service.add_user("second@example.com")
        member_key_service.save(other["id"], OLLAMA_KEY)
        other = auth_service.grant_admin_local("second@example.com")
        auth_service.create_session(other["id"], "pytest", "127.0.0.1")
        before = len(auth_service.list_audit(500))
        h = _household_client()
        actions = (("POST", "deactivate"), ("POST", "activate"), ("POST", "revoke-sessions"),
                   ("POST", "revoke-admin"), ("DELETE", "ollama-key"))
        # Your own account's guards (409) come before the PC check; any other admin is 403.
        own = {"deactivate": 409, "activate": 403, "revoke-sessions": 409, "revoke-admin": 409,
               "ollama-key": 403}
        for target, want in ((owner, own), (other, dict.fromkeys(own, 403))):
            for method, action in actions:
                r = h.request(method, f"/api/admin/users/{target['id']}/{action}", headers=_h(s))
                assert r.status_code == want[action], (target["email"], action, r.status_code)
            for method in ("POST", "DELETE"):
                r = h.request(method, f"/api/admin/users/{target['id']}/permissions/lines.edit",
                              headers=_h(s))
                assert r.status_code == 409, method
        r = h.post(f"/api/admin/users/{other['id']}/deactivate", headers=_h(s))
        assert r.status_code == 403 and "only be changed at the PC" in r.json()["error"]["message"]
        for u in (owner, other):
            row = db.auth_get_user(u["id"])
            assert row["is_admin"] == 1 and row["is_active"] == 1
        assert db.auth_list_sessions(other["id"])
        assert member_keys.get(other["id"], "ollama") is not None
        assert len(auth_service.list_audit(500)) == before

    def test_member_ollama_key_removed_from_away(self, isolated_db, tmp_path, monkeypatch):
        pytest.importorskip("cryptography")
        import member_keys
        from services import member_key_service, settings_service
        monkeypatch.setattr(settings_service, "default_env_path", lambda: str(tmp_path / ".env"))
        monkeypatch.delenv(member_key_service.SECRET_ENV, raising=False)
        s = _admin_session()
        admin_id = auth_service.find_user_by_email("owner@example.com")["id"]
        member = auth_service.add_user("kid@example.com")
        member_key_service.save(member["id"], OLLAMA_KEY)
        h = _household_client()
        r = h.delete(f"/api/admin/users/{member['id']}/ollama-key", headers=_h(s))
        assert r.status_code == 200 and r.json()["removed"] is True
        assert OLLAMA_KEY not in r.text
        assert member_keys.get(member["id"], "ollama") is None
        assert [a["user_id"] for a in auth_service.list_audit(500)
                if a["action"] == "member_key.remove"] == [admin_id]

    def test_view_routes_served_audit_emails_masked(self, isolated_db):
        """The user list shows each account's email in full (the admin needs
        to know whose account it is); audit details mask addresses (#522)."""
        app = _household_app()
        views = [(m, p) for methods, p, perm in self._admin_routes(app)
                 if perm in auth_service.ADMIN_VIEW_PERMISSIONS for m in methods]
        assert sorted(views) == [("GET", "/api/admin/audit"), ("GET", "/api/admin/users")]
        s = _admin_session()
        db.auth_insert_audit(None, "login.denied", "not_allowlisted email stranger@gmail.com")
        h = _household_client(app)
        for _m, path in views:
            assert h.get(path, headers=_h(s)).status_code == 200, path
        users = h.get("/api/admin/users", headers=_h(s)).json()["users"]
        assert [u["email"] for u in users] == ["owner@example.com"]
        r = h.get("/api/admin/audit", headers=_h(s))
        assert "stranger@gmail.com" not in r.text and "s***@gmail.com" in r.text

    def test_me_lists_household_admin_view_and_member_admin_permissions_only(self, isolated_db):
        s = _admin_session()
        h = _household_client()
        assert h.get("/api/library/dramas", headers=_h(s)).status_code == 200
        me = h.get("/api/auth/me", headers=_h(s)).json()
        assert me["signed_in"] is True and me["user"]["is_admin"] is True
        assert "library.read" in me["permissions"]
        assert ({p for p in me["permissions"] if p.startswith("admin.")}
                == set(auth_service.ADMIN_HOUSEHOLD_PERMISSIONS))

    def test_admin_listener_unchanged(self, isolated_db):
        assert _admin_client().get("/api/admin/users").status_code == 200
        # The single-listener sign-in setup is the admin listener with auth on:
        # an admin session keeps every admin.* permission there.
        app = create_app(ApiSettings(auth_mode="on", serve_frontend=False))
        s = _admin_session()
        c = _client(app, "http://127.0.0.1:8600")
        assert c.get("/api/admin/users", headers=_h(s)).status_code == 200
        member = auth_service.add_user("kid@example.com")
        r = c.post(f"/api/admin/users/{member['id']}/deactivate", headers=_h(s))
        assert r.status_code == 200
        me = c.get("/api/auth/me", headers=_h(s)).json()
        assert set(auth_service.ADMIN_PERMISSIONS) <= set(me["permissions"])

    def test_listener_principal(self):
        principal = {"user_id": 1, "is_admin": True,
                     "permissions": list(auth_service.PERMISSIONS) + ["admin.unlisted"]}
        household = api_auth.listener_principal(_household_app(), principal)
        # Default-deny: an admin.* permission not listed for the household is dropped.
        assert set(household["permissions"]) == (set(auth_service.PERMISSIONS)
                                                 - set(auth_service.ADMIN_WRITE_PERMISSIONS))
        assert household["admin_override"] is False
        assert "admin.unlisted" in principal["permissions"]   # not mutated
        assert "admin_override" not in principal
        assert api_auth.listener_principal(_admin_app(), principal) is principal
        assert api_auth.listener_principal(_household_app(), None) is None


class TestAdminOverrideIsPcOnly:
    """On the household listener an admin still sees every item and job but
    changes only what a member of their account could: no flipping another
    member's private flag, no stopping a PC job, no editing another member's
    private drama. The PC and the single-port sign-in setup are unchanged."""

    def _world(self):
        s = _admin_session()
        member = auth_service.add_user("kid@example.com")
        private = db.create_drama(title_zh="p", owner_user_id=member["id"], is_private=1)
        db.save_job_record("sources_search", "running", started_at=time.time(),
                           owner_user_id=None)
        return s, private

    def _single_port(self):
        return _client(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                       "http://127.0.0.1:8600")

    def test_views_everything(self, isolated_db):
        s, private = self._world()
        h = _household_client()
        assert h.get(f"/api/library/dramas/{private}", headers=_h(s)).status_code == 200
        assert [j["job_id"] for j in h.get("/api/jobs", headers=_h(s)).json()["items"]] == [
            "sources_search"]

    def test_cannot_flip_another_members_private_drama(self, isolated_db):
        s, private = self._world()
        r = _household_client().post(f"/api/sharing/dramas/{private}/private", headers=_h(s),
                                     json={"private": False})
        assert r.status_code == 403
        assert db.get_item_ownership("drama", private)["is_private"] == 1

    def test_can_cancel_a_pc_job(self, isolated_db):
        # The one admin change allowed from away: stopping a runaway job
        # (owner decision 2026-10-12).
        s, _private = self._world()
        r = _household_client().post("/api/jobs/sources_search/cancel", headers=_h(s))
        assert r.status_code == 200
        assert db.get_job_record("sources_search").get("cancel_requested")

    def test_cannot_edit_another_members_private_drama(self, isolated_db):
        s, private = self._world()
        r = _household_client().post(f"/api/export/dramas/{private}/flag-overlaps", headers=_h(s))
        assert r.status_code == 403

    def test_starts_jobs_on_a_title_made_at_the_pc(self, isolated_db, monkeypatch):
        # Titles made in the PC window have no account owner and start
        # private; from away they are the admin's own work (owner decision
        # 2026-10-18). Another member's private title stays PC-only.
        from services import (model_readiness_service, transcribe_service,
                              translate_run_service)
        s, theirs = self._world()
        pc_made = db.create_drama(title_zh="mine", owner_user_id=None, is_private=1)
        monkeypatch.setattr(model_readiness_service, "require_ready", lambda *a, **k: None)
        monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                            lambda drama_id, **k: {"job_id": f"transcribe_{drama_id}"})
        monkeypatch.setattr(translate_run_service, "start_translate_run",
                            lambda drama_id, **k: {"job_id": f"translate_{drama_id}",
                                                   "drama_id": drama_id, "engine": "ollama",
                                                   "target_line_count": 1})
        h = _household_client()
        assert h.post(f"/api/transcribe/dramas/{pc_made}/run", headers=_h(s),
                      json={}).json() == {"job_id": f"transcribe_{pc_made}"}
        r = h.post(f"/api/translate-run/dramas/{pc_made}/run", headers=_h(s),
                   json={"engine": "ollama"})
        assert r.status_code == 200 and r.json()["job_id"] == f"translate_{pc_made}"
        assert h.post(f"/api/transcribe/dramas/{theirs}/run", headers=_h(s),
                      json={}).status_code == 403
        assert h.post(f"/api/translate-run/dramas/{theirs}/run", headers=_h(s),
                      json={"engine": "ollama"}).status_code == 403

    @pytest.mark.parametrize("where", ["admin", "single_port"])
    def test_pc_and_single_port_unchanged(self, isolated_db, where):
        s, private = self._world()
        c = _admin_client() if where == "admin" else self._single_port()
        h = {} if where == "admin" else _h(s)
        post = {"X-Baihe-Local": "1"} if where == "admin" else {}
        assert c.post(f"/api/export/dramas/{private}/flag-overlaps",
                      headers={**h, **post}).status_code == 200
        assert c.post(f"/api/sharing/dramas/{private}/private", headers={**h, **post},
                      json={"private": False}).status_code == 200
        assert c.post("/api/jobs/sources_search/cancel", headers={**h, **post}).status_code == 200


# --- admin uploads from away (media.upload) ---------------------------------------

def _remote_uploads(on=True, developer_mode=True, on_at=None):
    from services import settings_service
    db.set_app_setting("assistant.developer_mode", developer_mode)
    if on and developer_mode:
        settings_service.set_settings({"remote_uploads": True})
    else:
        db.set_app_setting("remote_uploads", on)
    if on_at is not None:
        db.set_app_setting("remote_uploads.on_at", on_at)


class TestRemoteUploads:
    """An admin may upload from away only while the PC's switch is on
    (Developer Mode, turned on in the last 7 days); members never may, and
    a refused upload is refused before its body is read."""

    @pytest.fixture
    def parsed(self, monkeypatch):
        read = []
        import starlette.formparsers as fp
        orig = fp.MultiPartParser.parse

        async def spy(self):
            read.append(1)
            return await orig(self)
        monkeypatch.setattr(fp.MultiPartParser, "parse", spy)
        return read

    def _paste(self, session):
        did = db.create_drama(title_en="T", source_language="zh")
        return _household_client().post(f"/api/novel/dramas/{did}/raw-novel/text",
                                        json={"text": "第一章 你好"}, headers=_h(session))

    def test_off_by_default(self, isolated_db, parsed):
        s = _admin_session()
        files = {"file": ("a.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n", "text/plain")}
        r = _household_client().post("/api/subtitle-import/dramas/1/preview", files=files,
                                     headers=_h(s))
        assert r.status_code == 403 and parsed == []
        assert self._paste(s).status_code == 403
        me = _household_client().get("/api/auth/me", headers=_h(s)).json()
        assert auth_service.UPLOAD_PERMISSION not in me["permissions"]

    def test_switch_on_allows_an_admin(self, isolated_db, parsed):
        _remote_uploads()
        s = _admin_session()
        assert self._paste(s).status_code == 200
        did = db.create_drama(title_en="T", source_language="zh")
        files = {"file": ("a.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n", "text/plain")}
        r = _household_client().post(f"/api/subtitle-import/dramas/{did}/preview",
                                     files=files, headers=_h(s))
        assert r.status_code == 200 and parsed == [1]
        me = _household_client().get("/api/auth/me", headers=_h(s)).json()
        assert auth_service.UPLOAD_PERMISSION in me["permissions"]

    @pytest.mark.parametrize("state", [
        dict(developer_mode=False),
        dict(on=False),
        dict(on_at=time.time() - 8 * 86400),
        dict(on_at=0.0),
    ], ids=["no-developer-mode", "switch-off", "lapsed", "never-stamped"])
    def test_switch_needs_developer_mode_and_lapses(self, isolated_db, state):
        _remote_uploads(**state)
        assert self._paste(_admin_session()).status_code == 403

    def test_member_never(self, isolated_db, parsed):
        _remote_uploads()
        user = auth_service.add_user("kid@example.com")
        for p in auth_service.OPT_IN_PERMISSIONS:
            auth_service.grant_permission(user["id"], p)
        with pytest.raises(Exception):
            auth_service.grant_permission(user["id"], auth_service.UPLOAD_PERMISSION)
        s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
        files = {"file": ("a.wav", b"0" * 4096, "audio/wav")}
        r = _household_client().post("/api/media/dramas/1/upload", files=files, headers=_h(s))
        assert r.status_code == 403 and parsed == []
        assert self._paste(s).status_code == 403
        assert auth_service.UPLOAD_PERMISSION not in auth_service.effective_permissions(
            user["id"])

    def test_stored_member_row_is_ignored(self, isolated_db):
        user = auth_service.add_user("kid@example.com")
        db.auth_grant_permission(user["id"], auth_service.UPLOAD_PERMISSION)
        assert auth_service.UPLOAD_PERMISSION not in auth_service.effective_permissions(
            user["id"])

    def test_size_checked_before_the_body(self, isolated_db, parsed, monkeypatch):
        from services import media_upload_service
        monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 1024)
        _remote_uploads()
        s = _admin_session()
        did = db.create_drama(title_en="T", source_language="zh")
        url = f"/api/media/dramas/{did}/upload"
        big = {"file": ("a.wav", b"0" * (2 * 1024 * 1024), "audio/wav")}
        r = _household_client().post(url, files=big, headers=_h(s))
        assert r.status_code == 413 and parsed == []

        def chunked():
            yield b"--x\r\n"
        r = _household_client().post(url, content=chunked(), headers=_h(
            s, **{"Content-Type": "multipart/form-data; boundary=x"}))
        assert r.status_code == 422 and parsed == []

    def test_under_reported_length_is_cut_off(self, isolated_db, monkeypatch):
        from services import media_upload_service
        monkeypatch.setattr(media_upload_service, "max_upload_bytes", lambda: 0)
        scope = {"type": "http", "headers": [(b"content-length", b"10")]}
        chunks = [{"type": "http.request", "body": b"0" * (2 * 1024 * 1024), "more_body": True}]

        async def source():
            return chunks.pop(0)
        refusal, receive = remote_uploads.capped_upload(Request(scope), source)
        assert refusal is None
        assert asyncio.run(receive()) == {"type": "http.disconnect"}

    def test_upload_and_transcribe_refuses_a_tesseract_path(self, isolated_db, monkeypatch):
        stored = []
        from services import media_upload_service
        monkeypatch.setattr(media_upload_service, "upload_media",
                            lambda *a, **k: stored.append(k) or {"job_id": "j"})
        _remote_uploads()
        s = _admin_session()
        did = db.create_drama(title_en="T", source_language="zh")
        url = f"/api/media/dramas/{did}/upload-and-transcribe"
        files = {"file": ("a.wav", b"RIFF", "audio/wav")}
        r = _household_client().post(url, files=files, data={"tesseract_cmd": "/usr/bin/x"},
                                     headers=_h(s))
        assert r.status_code == 403 and "Tesseract" in r.json()["error"]["message"]
        assert stored == []

    def test_deletes_stay_pc_only(self, isolated_db):
        _remote_uploads()
        s = _admin_session()
        did = db.create_drama(title_en="T", source_language="zh")
        for path in (f"/api/novel/dramas/{did}/raw-novel/remove",
                     f"/api/novel/dramas/{did}/reference/remove",
                     f"/api/media/dramas/{did}/remove"):
            r = _household_client().post(path, json={"confirm": True}, headers=_h(s))
            assert r.status_code == 403, path

    def test_at_the_pc_no_switch_is_needed(self, isolated_db):
        did = db.create_drama(title_en="T", source_language="zh")
        r = _admin_client().post(f"/api/novel/dramas/{did}/raw-novel/text",
                                 json={"text": "第一章 你好"})
        assert r.status_code == 200


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
        # serve_frontend as in ADMIN: otherwise a built frontend/dist adds the
        # catch-all to one side only.
        today, now = create_app(ApiSettings(serve_frontend=False)), _admin_app()
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


def _raw(app, headers, scope_type="http", client=("127.0.0.1", 5000), scheme="http"):
    scope = {"type": scope_type, "method": "GET", "path": "/api/health",
             "raw_path": b"/api/health", "query_string": b"", "headers": headers,
             "client": client, "server": ("127.0.0.1", HOUSEHOLD_PORT), "scheme": scheme,
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
        ("https://xn--bcher-kva.example", ["xn--bcher-kva.example", "XN--BCHER-KVA.example."],
         ["bucher.example", "bücher.example"]),
        ("https://xn--fa-hia.example", ["xn--fa-hia.example"], ["fass.example", "faß.example"]),
    ])
    def test_public_url_forms(self, public_url, ok, bad):
        gate, _reached = _gate(public_url)
        for host in ok:
            assert _start_headers(_raw(gate, [(b"host", host.encode())]))[0] == 200, host
        for host in bad:
            assert _start_headers(_raw(gate, [(b"host", host.encode())]))[0] == 400, host

    def test_non_ascii_public_url_refused_at_startup(self):
        """Python's IDNA 2003 codec turns faß into fass, but browsers send
        xn--fa-hia: a Unicode name must be given in its punycode form."""
        unicode_url = ApiSettings(household_port=HOUSEHOLD_PORT,
                                  **{**SIGN_IN, "public_url": "https://faß.example"})
        with pytest.raises(ValueError, match="punycode"):
            check_household_bind_safety(unicode_url)
        check_household_bind_safety(ApiSettings(
            household_port=HOUSEHOLD_PORT,
            **{**SIGN_IN, "public_url": "https://xn--fa-hia.example"}))
        # Reached without the startup check, the gate still answers no Host.
        gate, reached = _gate("https://faß.example")
        for host in ("fass.example", "xn--fa-hia.example", "faß.example"):
            assert _start_headers(_raw(gate, [(b"host", host.encode())]))[0] == 400, host
        assert reached == []

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
            # Only the two stream-video hosts may be framed; scripts only from the app's files.
            assert "frame-src https://www.youtube-nocookie.com https://player.twitch.tv;" in csp
            assert csp.count("frame-src") == 1
            assert "script-src 'self';" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
            assert "strict-transport-security" not in r.headers   # plain http, no proxy word

    def test_route_set_header_kept(self):
        gate, _reached = _gate()
        _status, headers = _start_headers(_raw(gate, [(b"host", PUBLIC_HOST.encode())]))
        assert headers["content-security-policy"] == "default-src 'none'"
        assert headers["x-frame-options"] == "DENY"

    def test_hsts_only_over_https_from_the_proxy_on_this_pc(self, isolated_db):
        """As served: uvicorn's proxy headers handling runs first and replaces
        the loopback peer with the browser's address from X-Forwarded-For."""
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        seen = []
        app = _household_app()

        @app.get("/api/test-peer", dependencies=[api_auth.public_route()])
        def _peer(request: Request):
            seen.append((request.client.host, api_auth.client_ip(request)))
            return {}
        served = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")

        def get(peer, headers, path="/api/health"):
            return TestClient(served, base_url=f"http://{PUBLIC_HOST}", client=(peer, 5000),
                              raise_server_exceptions=False).get(path, headers=headers)
        caddy = {"X-Forwarded-For": "198.51.100.7", "X-Forwarded-Proto": "https"}
        r = get("127.0.0.1", caddy)
        assert r.status_code == 200
        assert r.headers["strict-transport-security"] == "max-age=31536000"
        get("127.0.0.1", caddy, "/api/test-peer")
        assert seen == [("198.51.100.7", "198.51.100.7")]
        for peer, headers in (("203.0.113.9", caddy),                         # not the proxy
                              ("127.0.0.1", {"X-Forwarded-For": "198.51.100.7"}),
                              ("127.0.0.1", {**caddy, "X-Forwarded-Proto": "http"}),
                              ("127.0.0.1", {**caddy, "X-Forwarded-Proto": "HTTPS, http"})):
            r = get(peer, headers)
            assert r.status_code == 200
            assert "strict-transport-security" not in r.headers, (peer, headers)
        # Without the proxy headers handling the scheme stays http.
        r = _household_client().get("/api/health", headers={"X-Forwarded-Proto": "https"})
        assert "strict-transport-security" not in r.headers
        gate, _reached = _gate()
        host = (b"host", PUBLIC_HOST.encode())
        https = (b"x-forwarded-proto", b"https")
        assert "strict-transport-security" in _start_headers(
            _raw(gate, [host, https], scheme="https"))[1]
        for headers, scheme in (([host, https, https], "https"), ([host], "https"),
                                ([host, https], "http")):
            got = _start_headers(_raw(gate, headers, scheme=scheme))[1]
            assert "strict-transport-security" not in got, (headers, scheme)

    def test_admin_replies_refuse_framing_only(self, isolated_db, tmp_path):
        """The PC listener has owner rights and no CSRF token, so another
        website must not frame it (clickjacking: a frame load is a GET with
        no Origin, which LoopbackOnlyGate accepts). It gets only the
        anti-framing headers, never the household policy."""
        from dataclasses import replace
        app = create_app(replace(ADMIN, serve_frontend=True), frontend_dist=self._dist(tmp_path))
        a = _admin_client(app)
        ok = [a.get("/api/health"), a.get("/"), a.get("/api/library/dramas"),
              a.get("/", headers={"Sec-Fetch-Dest": "iframe"})]
        refused = a.get("/", headers={"X-Forwarded-For": "203.0.113.9"})
        assert [r.status_code for r in ok] == [200, 200, 200, 200]
        assert refused.status_code == 403
        for r in ok + [refused]:
            assert r.headers["x-frame-options"] == "DENY"
            assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
            assert r.headers["x-content-type-options"] == "nosniff"
            for name in ("referrer-policy", "permissions-policy", "strict-transport-security"):
                assert name not in r.headers
        assert "HouseholdGate" not in [m.cls.__name__ for m in app.user_middleware]

    def test_admin_route_csp_kept(self, isolated_db):
        """A route's own stricter CSP (cover and review images) is kept on
        the PC listener too."""
        sent = []

        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-security-policy", b"default-src 'none'")]})
            await send({"type": "http.response.body", "body": b"ok"})

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}
        scope = {"type": "http", "method": "GET", "path": "/x", "raw_path": b"/x",
                 "query_string": b"", "headers": [(b"host", b"127.0.0.1:8600")],
                 "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8600),
                 "scheme": "http", "http_version": "1.1", "root_path": "",
                 "asgi": {"version": "3.0"}}
        asyncio.run(api_auth.LoopbackOnlyGate(inner)(scope, receive, send))
        headers = _start_headers(sent)[1]
        assert headers["content-security-policy"] == "default-src 'none'"
        assert headers["x-frame-options"] == "DENY"

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
        assert job_launch.start_job(job_id, work, job_id)
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
        monkeypatch.setattr(settings_service, "read_env_file", lambda *a, **k: {})
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

    def test_single_port_access_log_off_when_sign_in_is_on(self, isolated_db, monkeypatch, capsys):
        # Request lines carry the sign-in code in the query string.
        single, _ran, _c, _s = self._serve(monkeypatch, {"BAIHE_API_AUTH": "on"})
        assert single[0].access_log is False

    def test_single_port_access_log_kept_when_sign_in_is_off(self, isolated_db, monkeypatch):
        single, _ran, _c, _s = self._serve(monkeypatch, {"BAIHE_API_AUTH": "off"})
        assert single[0].access_log is True

    @pytest.mark.parametrize("auth,expected", [("on", False), ("off", True)])
    def test_dev_server_access_log_follows_sign_in(self, isolated_db, monkeypatch, capsys,
                                                    auth, expected):
        import uvicorn
        from api import __main__ as main_mod
        from services import settings_service
        monkeypatch.setattr(settings_service, "read_env_file", lambda *a, **k: {})
        for k in ("BAIHE_API_HOUSEHOLD_PORT", "BAIHE_API_PORT", "BAIHE_API_HOST", *SIGN_IN_ENV):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("BAIHE_API_ENV", "development")
        monkeypatch.setenv("BAIHE_API_AUTH", auth)
        calls = []
        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append(k))
        main_mod._serve()
        assert calls[0]["access_log"] is expected

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
        # The household access log would print sign-in codes from query strings.
        assert admin.config.access_log is True and household.config.access_log is False
        svc._stopper()
        assert admin.should_exit and household.should_exit

    def test_unsafe_household_port_skips_only_the_household_listener(self, isolated_db,
                                                                      monkeypatch, capsys):
        single, ran, _c, _s = self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8756",
                                                        **SIGN_IN_ENV})
        assert len(single) == 1 and ran == [] and single[0].port == 8600
        err = capsys.readouterr().err
        assert "household listener was not started" in err and "8756" in err

    @pytest.mark.parametrize("missing", list(SIGN_IN_ENV))
    def test_household_port_without_sign_in_skips_household_listener(self, isolated_db,
                                                                      monkeypatch, capsys,
                                                                      missing):
        env = {"BAIHE_API_HOUSEHOLD_PORT": "8610", **SIGN_IN_ENV}
        del env[missing]
        single, ran, _c, _s = self._serve(monkeypatch, env)
        assert len(single) == 1 and ran == []
        message = capsys.readouterr().err
        assert "household listener was not started" in message and "sign-in" in message
        assert all(name in message for name in SIGN_IN_ENV)
        assert "s3cr3t-value" not in message and PUBLIC_HOST not in message

    @pytest.mark.parametrize("bad", ["http://example.com", "https://example.com/path", "htps:/x"])
    def test_bad_public_url_keeps_pc_listener(self, isolated_db, monkeypatch, capsys, bad):
        env = {**SIGN_IN_ENV, "BAIHE_PUBLIC_URL": bad}
        for port_env in ({}, {"BAIHE_API_HOUSEHOLD_PORT": "8610"}):
            single, ran, _c, _s = self._serve(monkeypatch, {**env, **port_env})
            assert len(single) == 1 and ran == [] and single[0].port == 8600
            err = capsys.readouterr().err
            assert "BAIHE_PUBLIC_URL" in err and "treated as not configured" in err
            assert "s3cr3t-value" not in err and bad not in err

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

    def test_auth_on_refused_even_with_a_bad_public_url(self, isolated_db, monkeypatch):
        with pytest.raises(SystemExit):
            self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8610", "BAIHE_API_AUTH": "on",
                                      **{**SIGN_IN_ENV, "BAIHE_PUBLIC_URL": "http://x.example"}})

    def test_non_loopback_host_with_household_port_still_refused(self, isolated_db, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._serve(monkeypatch, {"BAIHE_API_HOUSEHOLD_PORT": "8610", "BAIHE_API_HOST": "0.0.0.0",
                                      "BAIHE_API_AUTH": "off", **SIGN_IN_ENV})
        assert "0.0.0.0" in str(exc.value)

    def test_warning_only_for_the_single_port_sign_in_setup(self):
        from api.api_config import single_port_sign_in_warning
        assert single_port_sign_in_warning(ApiSettings(auth_mode="on"))
        assert not single_port_sign_in_warning(ApiSettings(auth_mode="off"))
        assert not single_port_sign_in_warning(ApiSettings(auth_mode="off", household_port=8610))
        assert not single_port_sign_in_warning(
            household_settings(ApiSettings(auth_mode="off", household_port=8610, **SIGN_IN)))


@pytest.mark.parametrize("settings,listener,expected", [
    (ApiSettings(serve_frontend=False), "admin", ["LocalOnlyCrossSiteGate", "LoopbackOnlyGate"]),
    (ApiSettings(serve_frontend=False, environment="development",
                 cors_origins=("http://localhost:5173",)), "admin",
     ["CORSMiddleware", "LocalOnlyCrossSiteGate", "LoopbackOnlyGate"]),
    (ApiSettings(serve_frontend=False, auth_mode="on", **SIGN_IN), "admin",
     ["LocalOnlyCrossSiteGate", "EarlyAuthGate", "RemoteUploadGate",
      "ActingPrincipalMiddleware"]),
    (ADMIN, "household",
     ["HouseholdGate", "LocalOnlyCrossSiteGate", "EarlyAuthGate",
      "RemoteUploadGate", "ActingPrincipalMiddleware"]),
])
def test_middleware_order_outermost_first(settings, listener, expected):
    """The gates' order is the security boundary: the Host check and headers
    wrap everything, the cross-site refusal runs before auth reads a body,
    and the acting principal is set innermost."""
    app = create_app(settings, listener=listener)
    assert [m.cls.__name__ for m in app.user_middleware] == expected
