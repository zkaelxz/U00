"""
Roadmap Step 43 (redefined 2026-09-29): /api/backups/* over
services/auto_backup_service.py (api/routers/backup_routes.py). FastAPI
TestClient against an isolated temp library; the backup job runs for real on
it (no network, no models) and is waited for and joined before asserting.

Every route is local_only: refused for a remote request with auth off (the
loopback gate) and with auth on (even for an admin), allowed for the owner at
the PC in both modes.
"""

import os
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import auto_backup_service as abs_

BASE = "/api/backups"
REMOTE = "https://baihe.example.com"

ROUTES = {
    ("GET", "/api/backups/settings"), ("POST", "/api/backups/settings"),
    ("POST", "/api/backups/now"), ("GET", "/api/backups/snapshot"),
    ("GET", "/api/backups/snapshot/dramas"), ("POST", "/api/backups/snapshot/restore-drama"),
    ("POST", "/api/backups/snapshot/delete"), ("POST", "/api/backups/import/list"),
    ("POST", "/api/backups/import"),
}


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _remote_off():
    return TestClient(create_app(ApiSettings()), base_url=REMOTE, client=("203.0.113.9", 4000),
                      raise_server_exceptions=False)


def _remote_on():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def _local_on():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                      client=("127.0.0.1", 5000), raise_server_exceptions=False)


def _admin_headers():
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def _clean(resp):
    """No library path in the body or any header."""
    assert db.LIBRARY_DIR not in resp.text
    for k, v in resp.headers.items():
        assert db.LIBRARY_DIR not in v, k
    return resp


def _code(resp):
    return resp.json()["error"]["code"]


def _snap_path():
    """The newest copy in the default folder (a path that never exists when
    there is none)."""
    folder = os.path.join(db.LIBRARY_DIR, "backups", "auto")
    copies = abs_._list_copies(folder)
    return copies[0]["path"] if copies else os.path.join(folder, "no-copy")


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _wait_job(timeout=30):
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = background_jobs.get_status(abs_.JOB_ID)
        if st and st["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"backup job did not finish: {st}")
    for t in threading.enumerate():
        if t.name == f"job:{abs_.JOB_ID}":
            t.join(15)
    return st


def _put_job(job_id, status="running"):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {
            "status": status, "progress": 0.0, "message": "", "result": None,
            "error": None, "started_at": time.time(), "finished_at": None}


def _world():
    """Two dramas, a snapshot of both, then drama A deleted."""
    a = db.create_drama(title_en="Alpha", media_type="audio")
    b = db.create_drama(title_en="Beta")
    db.save_lines(a, [])
    abs_._backup_job(abs_.JOB_ID, False)
    db.delete_drama(a)
    return a, b


RESTORE_OK = {"confirm": True, "confirm_text": "RESTORE"}
DELETE_OK = {"confirm": True, "confirm_text": "DELETE"}
DELETE_ALL = {**DELETE_OK, "all": True}


def _requests(a):
    return [
        ("get", "/settings", {}),
        ("post", "/settings", {"json": {"enabled": True, "frequency": "daily"}}),
        ("post", "/now", {"json": {"replace": True}}),
        ("get", "/snapshot", {}),
        ("get", "/snapshot/dramas", {}),
        ("post", "/snapshot/restore-drama", {"json": {"drama_id": a, **RESTORE_OK}}),
        ("post", "/snapshot/delete", {"json": DELETE_ALL}),
    ]


# ---- route table -------------------------------------------------------------

@pytest.mark.parametrize("auth", ["off", "on"])
def test_every_backup_route_is_local_only(isolated_db, auth):
    app = create_app(ApiSettings(auth_mode=auth))
    found = set()
    for _r, path, methods, decls in api_auth.iter_route_declarations(app):
        if path.startswith(BASE):
            for m in methods:
                found.add((m, path))
            assert decls == [("local_only", None)], path
    assert {(m, p) for m, p in found if m != "HEAD"} == ROUTES


# ---- remote refusal ----------------------------------------------------------

class TestRemoteRefused:
    def _assert_nothing_changed(self, a, snap):
        assert abs_.get_settings() == abs_.DEFAULT_SETTINGS
        assert _read(_snap_path()) == snap
        assert db.get_drama(a) is None
        assert background_jobs.get_status(abs_.JOB_ID) is None

    @pytest.mark.parametrize("i", range(7))
    def test_auth_off_remote(self, isolated_db, i):
        a, _ = _world()
        snap = _read(_snap_path())
        method, path, kw = _requests(a)[i]
        r = _clean(getattr(_remote_off(), method)(BASE + path, **kw))
        assert r.status_code == 403, (path, r.text)
        self._assert_nothing_changed(a, snap)

    @pytest.mark.parametrize("i", range(7))
    def test_auth_on_remote_admin(self, isolated_db, i):
        a, _ = _world()
        snap = _read(_snap_path())
        method, path, kw = _requests(a)[i]
        r = _clean(getattr(_remote_on(), method)(BASE + path, headers=_admin_headers(), **kw))
        assert r.status_code == 403, (path, r.text)
        self._assert_nothing_changed(a, snap)

    @pytest.mark.parametrize("i", range(7))
    def test_auth_on_remote_no_session(self, isolated_db, i):
        a, _ = _world()
        snap = _read(_snap_path())
        method, path, kw = _requests(a)[i]
        r = getattr(_remote_on(), method)(BASE + path, **kw)
        assert r.status_code in (401, 403), (path, r.text)
        self._assert_nothing_changed(a, snap)

    @pytest.mark.parametrize("headers", [{"X-Forwarded-For": "203.0.113.9"},
                                         {"Origin": "https://evil.example.com"}])
    def test_loopback_peer_via_proxy_or_foreign_origin_refused(self, isolated_db, headers):
        a, _ = _world()
        snap = _read(_snap_path())
        r = _local_on().post(f"{BASE}/snapshot/delete", json=DELETE_ALL, headers=headers)
        assert r.status_code == 403
        self._assert_nothing_changed(a, snap)

    def test_form_post_refused(self, client):
        """A cross-site 'simple' form POST can't drive a local_only route."""
        a, _ = _world()
        snap = _read(_snap_path())
        r = client.post(f"{BASE}/snapshot/delete",
                        data={"confirm": "true", "confirm_text": "DELETE"})
        assert r.status_code in (403, 415, 422)
        assert _read(_snap_path()) == snap


# ---- local owner -------------------------------------------------------------

class TestLocal:
    def test_defaults(self, client):
        r = _clean(client.get(f"{BASE}/settings"))
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False and body["frequency"] == "daily"
        assert body["include_media"] is False and body["folder"] == ""
        assert body["next_run_at"] is None and body["running"] is False
        assert body["copies"] == []
        snap = _clean(client.get(f"{BASE}/snapshot")).json()
        assert snap["exists"] is False and snap["copies"] == []

    def test_no_snapshot_404s(self, client):
        assert _code(_clean(client.get(f"{BASE}/snapshot/dramas"))) == "not_found"
        r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                               json={"drama_id": 1, **RESTORE_OK}))
        assert r.status_code == 404
        r = _clean(client.post(f"{BASE}/snapshot/delete", json=DELETE_ALL))
        assert r.status_code == 404

    def test_full_flow(self, client):
        a = db.create_drama(title_en="Alpha")
        b = db.create_drama(title_en="Beta")
        r = _clean(client.post(f"{BASE}/settings", json={"enabled": True, "frequency": "daily"}))
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True and r.json()["frequency"] == "daily"
        assert r.json()["next_run_at"] is not None

        # no body at all: local_only's cross-site rule wants JSON or X-Baihe-Local
        assert client.post(f"{BASE}/now").status_code == 403
        assert background_jobs.get_status(abs_.JOB_ID) is None
        r = _clean(client.post(f"{BASE}/now", headers={"X-Baihe-Local": "1"}))
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": abs_.JOB_ID}
        assert _wait_job()["status"] == "done"

        info = _clean(client.get(f"{BASE}/snapshot")).json()
        assert info["exists"] and info["readable"] and info["kind"] == "db-only"
        assert info["drama_count"] == 2 and info["size"] > 0

        first = info["copies"][0]["name"]
        assert [c["name"] for c in info["copies"]] == [first]
        assert set(info["copies"][0]) == {"name", "created_at", "size", "kind", "drama_count",
                                          "readable", "kept_as"}
        assert "/" not in first and "\\" not in first
        settings = _clean(client.get(f"{BASE}/settings")).json()
        assert settings["copies"] == info["copies"]

        db.delete_drama(a)
        listing = _clean(client.get(f"{BASE}/snapshot/dramas")).json()
        assert {d["id"]: d["exists_now"] for d in listing["dramas"]} == {a: False, b: True}

        r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                               json={"drama_id": a, **RESTORE_OK}))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["drama_id"] == a and body["restored_as_new"] is False
        assert body["title"] == "Alpha" and body["snapshot_kind"] == "db-only"
        assert body["snapshot"] == first        # the copy used when none was named
        assert body["skipped_tables"] == ["bulk_jobs", "metadata_research_results", "usage_log"]
        assert db.get_drama(a)["title_en"] == "Alpha"

        r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                               json={"drama_id": b, **RESTORE_OK}))
        assert r.status_code == 200
        assert r.json()["restored_as_new"] is True and r.json()["drama_id"] not in (a, b)
        assert "(restored " in r.json()["title"]
        assert db.get_drama(b)["title_en"] == "Beta"

        time.sleep(1.05)   # copy names have one-second resolution
        # replace is still accepted (and ignored): a second copy is added
        r = _clean(client.post(f"{BASE}/now", json={"replace": True, "include_media": True}))
        assert r.status_code == 200
        assert _wait_job()["status"] == "done"
        info = _clean(client.get(f"{BASE}/snapshot")).json()
        assert info["kind"] == "full"
        assert [c["kind"] for c in info["copies"]] == ["full", "db-only"]
        assert info["copies"][1]["name"] == first

        # the older copy by name: its own dramas, restore from it, delete it
        listing = _clean(client.get(f"{BASE}/snapshot/dramas",
                                    params={"snapshot": first})).json()
        assert listing["name"] == first and listing["kind"] == "db-only"
        r = _clean(client.post(f"{BASE}/snapshot/delete", json={**DELETE_OK, "snapshot": first}))
        assert r.status_code == 200 and r.json() == {"deleted": True, "count": 1}
        info = client.get(f"{BASE}/snapshot").json()
        assert [c["kind"] for c in info["copies"]] == ["full"]

        r = _clean(client.post(f"{BASE}/snapshot/delete", json=DELETE_ALL))
        assert r.status_code == 200 and r.json() == {"deleted": True, "count": 1}
        assert client.get(f"{BASE}/snapshot").json() == {
            "exists": False, "readable": None, "created_at": None, "kind": None, "size": None,
            "app_version": None, "drama_count": None, "copies": []}
        assert not os.path.exists(_snap_path())

    def test_restore_from_a_named_copy(self, client):
        a, _ = _world()
        first = abs_.snapshot_info()["copies"][0]["name"]
        r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                               json={"drama_id": a, "snapshot": first, **RESTORE_OK}))
        assert r.status_code == 200, r.text
        assert r.json()["drama_id"] == a and db.get_drama(a) is not None

    @pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "other.zip",
                                     "baihe_snapshot-20990101-000000.zip"])
    def test_named_copy_must_be_listed(self, client, bad):
        a, _ = _world()
        snap = _read(_snap_path())
        absolute = os.path.join(os.path.dirname(_snap_path()),
                                os.path.basename(_snap_path()))
        # an unknown name is 404; a full path is too long to be a name (422)
        for name in (bad, absolute):
            r = _clean(client.get(f"{BASE}/snapshot/dramas", params={"snapshot": name}))
            assert r.status_code in (404, 422), r.text
            r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                                   json={"drama_id": a, "snapshot": name, **RESTORE_OK}))
            assert r.status_code in (404, 422), r.text
            r = _clean(client.post(f"{BASE}/snapshot/delete",
                                   json={**DELETE_OK, "snapshot": name}))
            assert r.status_code in (404, 422), r.text
            assert name not in r.text
        assert db.get_drama(a) is None
        assert _read(_snap_path()) == snap

    def test_auth_on_local_owner_allowed(self, isolated_db):
        a, _ = _world()
        c = _local_on()
        assert c.get(f"{BASE}/settings").status_code == 200
        r = _clean(c.post(f"{BASE}/snapshot/restore-drama", json={"drama_id": a, **RESTORE_OK}))
        assert r.status_code == 200, r.text
        assert db.get_drama(a) is not None
        r = c.post(f"{BASE}/snapshot/delete", json=DELETE_ALL)
        assert r.status_code == 200
        assert not os.path.exists(_snap_path())

    def test_restore_writes_audit_entry(self, isolated_db):
        a, _ = _world()
        r = _local_on().post(f"{BASE}/snapshot/restore-drama", json={"drama_id": a, **RESTORE_OK})
        assert r.status_code == 200
        import sqlite3
        with sqlite3.connect(db.DB_PATH) as c:
            actions = [x[0] for x in c.execute("SELECT action FROM audit_log")]
        assert "library.restore_drama" in actions


# ---- validation --------------------------------------------------------------

class TestValidation:
    @pytest.mark.parametrize("body", [
        {"drama_id": 1},
        {"drama_id": 1, "confirm": False, "confirm_text": "RESTORE"},
        {"drama_id": 1, "confirm": True, "confirm_text": "restore"},
        {"drama_id": 1, "confirm": True, "confirm_text": "DELETE"},
        {"drama_id": 1, "confirm": True, "confirm_text": ""},
        {"drama_id": 1, "confirm": "true", "confirm_text": "RESTORE"},
        {"drama_id": "1", **RESTORE_OK},
        {"drama_id": 0, **RESTORE_OK},
        {"drama_id": -3, **RESTORE_OK},
        {"drama_id": 2**31, **RESTORE_OK},
        {"drama_id": 1.5, **RESTORE_OK},
        {"drama_id": True, **RESTORE_OK},
        {**RESTORE_OK},
        {"drama_id": 1, **RESTORE_OK, "extra": 1},
        {"drama_id": 1, "confirm": True, "confirm_text": "R" * 33},
        {"drama_id": 1, **RESTORE_OK, "snapshot": ""},
        {"drama_id": 1, **RESTORE_OK, "snapshot": "x" * 65},
        {"drama_id": 1, **RESTORE_OK, "snapshot": 5},
    ])
    def test_restore_422_nothing_restored(self, client, body):
        a, _ = _world()
        body = {k: (a if (k == "drama_id" and type(v) is int and v == 1) else v)
                for k, v in body.items()}
        r = _clean(client.post(f"{BASE}/snapshot/restore-drama", json=body))
        assert r.status_code == 422, (body, r.text)
        assert _code(r) == "validation_error"
        assert db.get_drama(a) is None
        assert len(db.list_dramas()) == 1

    @pytest.mark.parametrize("body", [
        {}, {"confirm": True}, {"confirm": False, "confirm_text": "DELETE"},
        {"confirm": True, "confirm_text": "delete"}, {"confirm": True, "confirm_text": "RESTORE"},
        {"confirm": 1, "confirm_text": "DELETE"}, {**DELETE_OK, "path": "/etc"},
        {**DELETE_OK, "snapshot": ""}, {**DELETE_OK, "snapshot": "x" * 65},
        {**DELETE_OK, "snapshot": ["a"]},
        # deleting every copy needs an explicit all=true, and not with a name
        DELETE_OK, {**DELETE_OK, "all": False}, {**DELETE_OK, "all": "true"},
        {**DELETE_OK, "all": 1}, {**DELETE_ALL, "confirm": False},
        {**DELETE_ALL, "snapshot": "baihe_snapshot-20260302-030000.zip"},
    ])
    def test_delete_422_snapshot_kept(self, client, body):
        _world()
        snap = _read(_snap_path())
        r = _clean(client.post(f"{BASE}/snapshot/delete", json=body))
        assert r.status_code == 422, (body, r.text)
        assert _read(_snap_path()) == snap

    @pytest.mark.parametrize("body", [
        {"enabled": "yes"}, {"enabled": 1}, {"frequency": "hourly"}, {"include_media": "true"},
        {"folder": "relative/dir"}, {"folder": "x" * 1025}, {"unknown": True},
    ])
    def test_settings_422_unchanged(self, client, body):
        r = _clean(client.post(f"{BASE}/settings", json=body))
        assert r.status_code == 422, (body, r.text)
        assert abs_.get_settings() == abs_.DEFAULT_SETTINGS

    def test_settings_folder_inside_library_422(self, client):
        inside = os.path.join(db.LIBRARY_DIR, "dramas")
        os.makedirs(inside, exist_ok=True)
        r = client.post(f"{BASE}/settings", json={"folder": inside})
        assert r.status_code == 422
        assert abs_.get_settings()["folder"] == ""

    def test_settings_folder_outside_ok_and_echoed(self, client, tmp_path):
        r = client.post(f"{BASE}/settings", json={"folder": str(tmp_path)})
        assert r.status_code == 200, r.text
        assert r.json()["folder"] == str(tmp_path)

    @pytest.mark.parametrize("body", [{"replace": "true"}, {"replace": 1},
                                      {"include_media": "no"}, {"replace": True, "x": 1}])
    def test_now_422(self, client, body):
        r = client.post(f"{BASE}/now", json=body)
        assert r.status_code == 422
        assert background_jobs.get_status(abs_.JOB_ID) is None


# ---- conflicts ---------------------------------------------------------------

class TestConflicts:
    def test_restore_409_while_backup_runs(self, client):
        a, _ = _world()
        _put_job(abs_.JOB_ID)
        r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                               json={"drama_id": a, **RESTORE_OK}))
        assert r.status_code == 409
        assert db.get_drama(a) is None

    def test_now_409_while_backup_runs(self, client):
        _put_job(abs_.JOB_ID)
        assert client.post(f"{BASE}/now", json={}).status_code == 409

    def test_now_409_during_maintenance(self, client):
        assert background_jobs.enter_maintenance()
        try:
            assert client.post(f"{BASE}/now", json={}).status_code == 409
        finally:
            background_jobs.exit_maintenance()

    def test_delete_409_while_backup_runs(self, client):
        _world()
        _put_job(abs_.JOB_ID)
        assert client.post(f"{BASE}/snapshot/delete", json=DELETE_ALL).status_code == 409
        assert os.path.exists(_snap_path())

    def test_settings_folder_change_409_while_backup_runs(self, client, tmp_path):
        _world()
        _put_job(abs_.JOB_ID)
        r = client.post(f"{BASE}/settings", json={"folder": str(tmp_path)})
        assert r.status_code == 409
        assert abs_.get_settings()["folder"] == ""
        assert os.path.exists(_snap_path())

    def test_restore_unknown_drama_404(self, client):
        a, b = _world()
        r = _clean(client.post(f"{BASE}/snapshot/restore-drama",
                               json={"drama_id": b + 100, **RESTORE_OK}))
        assert r.status_code == 404
