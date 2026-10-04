"""
/api/data-usage/* over services/disk_usage_service.py. Every route is
local_only: refused for a remote request (auth off and on, even for an
admin), allowed at the PC. No response may name an absolute path.
"""

import json
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import disk_usage_service as dus

BASE = "/api/data-usage"
REMOTE = "https://baihe.example.com"


def _app(auth="off"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _local(app):
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


@pytest.fixture
def tree(isolated_db, monkeypatch):
    lib = db.LIBRARY_DIR
    _write(os.path.join(lib, "source_cache", "x.html"), 400)
    _write(os.path.join(lib, "dramas", "1", "audio.mp3"), 1000)
    return os.path.dirname(lib)


@pytest.fixture
def renames(monkeypatch):
    calls = []
    real = dus._rename

    def spy(src, dst):
        calls.append(dst)
        real(src, dst)
    monkeypatch.setattr(dus, "_rename", spy)
    return calls


def test_scan_clear_flow_and_no_absolute_paths(tree, renames):
    c = _local(_app())
    seen = []
    r = c.get(BASE)
    seen.append(r.text)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == "" and body["items"][0]["name"] == "library"
    r = c.get(BASE, params={"path": "library"})
    seen.append(r.text)
    cache = next(i for i in r.json()["items"] if i["name"] == "source_cache")
    assert cache["size_bytes"] == 400 and cache["regenerable"]
    r = c.post(f"{BASE}/to-trash", json={
        "path": "library/source_cache", "confirm": True,
        "expected_size_bytes": 400, "expected_file_count": 1})
    seen.append(r.text)
    assert r.status_code == 200 and r.json()["moved_bytes"] == 400
    assert len(renames) == 1
    # a changed item is a 409 with details and no path
    r = c.post(f"{BASE}/to-trash", json={
        "path": "library/dramas/1", "confirm": True, "confirm_irreplaceable": True,
        "expected_size_bytes": 5, "expected_file_count": 1})
    seen.append(r.text)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "changed"
    for text in seen:
        assert tree not in text and db.LIBRARY_DIR not in text


@pytest.mark.parametrize("path", ["..", "/etc", "C:\\Windows", "library/../.."])
def test_bad_paths_are_422(tree, renames, path):
    c = _local(_app())
    assert c.get(BASE, params={"path": path}).status_code == 422
    r = c.post(f"{BASE}/to-trash", json={"path": path, "confirm": True,
                                        "expected_size_bytes": 0, "expected_file_count": 0})
    assert r.status_code == 422 and renames == []
    assert tree not in r.text


def test_protected_is_refused_by_the_server(tree, renames):
    r = _local(_app()).post(f"{BASE}/to-trash", json={
        "path": "library/library.db", "confirm": True,
        "expected_size_bytes": 0, "expected_file_count": 0})
    assert r.status_code == 422 and renames == []


def test_unknown_fields_and_missing_sizes_rejected(tree):
    c = _local(_app())
    assert c.post(f"{BASE}/to-trash", json={"path": "library", "confirm": True}).status_code == 422
    assert c.post(f"{BASE}/move", json={"path": "x", "destination": "/", "confirm": True,
                                        "extra": 1}).status_code == 422


def test_move_not_movable_is_400(tree, tmp_path):
    r = _local(_app()).post(f"{BASE}/move", json={
        "path": "model_cache", "destination": str(tmp_path), "confirm": True})
    assert r.status_code in (400, 404), r.text
    assert str(tmp_path) not in r.text


def test_move_backup_folder(tree, tmp_path):
    _write(os.path.join(db.LIBRARY_DIR, "backups", "auto", "note.txt"), 3)
    dest = tmp_path / "bk"
    dest.mkdir()
    r = _local(_app()).post(f"{BASE}/move", json={
        "path": "library/backups/auto", "destination": str(dest), "confirm": True})
    assert r.status_code == 200, r.text
    assert str(dest) not in r.text and tree not in r.text
    assert r.json()["what"] == "backups"


TRASH_ID = "20260101-000000-abcdef12"
ROUTES = [("GET", BASE, None),
          ("GET", f"{BASE}/trash", None),
          ("POST", f"{BASE}/trash/restore", {"id": TRASH_ID, "confirm": True}),
          ("POST", f"{BASE}/trash/purge", {"id": TRASH_ID, "confirm_text": "DELETE",
                                           "expected_size_bytes": 0}),
          ("POST", f"{BASE}/trash/empty", {"confirm_text": "DELETE", "expected_item_count": 0, "expected_size_bytes": 0}),
          ("POST", f"{BASE}/to-trash", {"path": "library/source_cache", "confirm": True,
                                       "expected_size_bytes": 400, "expected_file_count": 1}),
          ("POST", f"{BASE}/move", {"path": "library/backups/auto", "destination": "/tmp",
                                    "confirm": True})]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_remote_request_refused_auth_off(tree, renames, method, path, body):
    c = TestClient(_app("off"), base_url=REMOTE, raise_server_exceptions=False)
    r = c.request(method, path, json=body)
    assert r.status_code == 403
    assert renames == [] and os.path.exists(os.path.join(tree, "library", "source_cache"))


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_remote_admin_refused_auth_on(tree, renames, method, path, body):
    app = _app("on")
    user = auth_service.grant_admin_local("admin@example.com")
    session = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    headers = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
               api_auth.CSRF_HEADER: session["csrf_token"]}
    c = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    r = c.request(method, path, json=body, headers=headers)
    assert r.status_code == 403
    assert renames == []


def test_openapi_has_the_routes_and_no_secret_fields(tree):
    schema = json.dumps(_local(_app()).get("/api/openapi.json").json())
    for route in ("to-trash", "trash", "trash/restore", "trash/purge", "trash/empty"):
        assert f"/api/data-usage/{route}" in schema


def test_a_second_scan_at_the_same_time_is_a_409(tree):
    client = _local(_app())
    assert dus._scan_lock.acquire(blocking=False)
    try:
        r = client.get(BASE)
    finally:
        dus._scan_lock.release()
    assert r.status_code == 409 and "already running" in r.text
    assert client.get(BASE).status_code == 200


def test_scan_reports_the_trash_block_and_not_the_removed_recycle_fields(tree):
    body = _local(_app()).get(BASE).json()
    assert body["trash"] == {"size_bytes": 0, "item_count": 0, "partial": False}
    assert "recycle_available" not in body and "recycle_reason" not in body
    assert "not_shown" in body


def test_trash_flow_over_the_api_has_no_absolute_or_trash_paths(tree, renames):
    c = _local(_app())
    seen = []
    moved = c.post(f"{BASE}/to-trash", json={
        "path": "library/source_cache", "confirm": True,
        "expected_size_bytes": 400, "expected_file_count": 1})
    seen.append(moved.text)
    assert moved.status_code == 200, moved.text
    tid = moved.json()["trash_id"]
    scan = c.get(BASE, params={"path": "library"})
    seen.append(scan.text)
    assert scan.json()["trash"]["size_bytes"] == 400
    listing = c.get(f"{BASE}/trash")
    seen.append(listing.text)
    assert listing.status_code == 200
    item = listing.json()["items"][0]
    assert item["id"] == tid and item["restorable"] is True
    assert item["original_path_relative"] == "library/source_cache"
    # restore, then trash again and delete for good
    r = c.post(f"{BASE}/trash/restore", json={"id": tid, "confirm": True})
    seen.append(r.text)
    assert r.status_code == 200 and os.path.exists(os.path.join(tree, "library", "source_cache"))
    tid = c.post(f"{BASE}/to-trash", json={
        "path": "library/source_cache", "confirm": True,
        "expected_size_bytes": 400, "expected_file_count": 1}).json()["trash_id"]
    r = c.post(f"{BASE}/trash/purge", json={"id": tid, "confirm_text": "DELETE",
                                            "expected_size_bytes": 400})
    seen.append(r.text)
    assert r.status_code == 200 and r.json() == {"freed_bytes": 400, "file_count": 1}
    r = c.post(f"{BASE}/trash/empty", json={"confirm_text": "DELETE", "expected_item_count": 0, "expected_size_bytes": 0})
    seen.append(r.text)
    assert r.json() == {"freed_bytes": 0, "removed": 0, "failed": 0}
    for text in seen:
        assert tree not in text and db.LIBRARY_DIR not in text and "baihe_trash" not in text


def test_trash_restore_and_purge_refusals(tree):
    c = _local(_app())
    tid = c.post(f"{BASE}/to-trash", json={
        "path": "library/source_cache", "confirm": True,
        "expected_size_bytes": 400, "expected_file_count": 1}).json()["trash_id"]
    _write(os.path.join(tree, "library", "source_cache", "again.html"), 1)
    r = c.post(f"{BASE}/trash/restore", json={"id": tid, "confirm": True})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "cannot_restore"
    assert tree not in r.text
    for body in ({"id": tid, "confirm_text": "delete", "expected_size_bytes": 400},
                 {"id": tid, "confirm_text": "DELETE", "expected_size_bytes": 1},
                 {"id": "..", "confirm_text": "DELETE", "expected_size_bytes": 0},
                 {"id": "../library", "confirm_text": "DELETE", "expected_size_bytes": 0}):
        r = c.post(f"{BASE}/trash/purge", json=body)
        assert r.status_code in (404, 409, 422), (body, r.text)
    assert c.post(f"{BASE}/trash/empty", json={"confirm_text": "nope", "expected_item_count": 0, "expected_size_bytes": 0}).status_code == 422
    assert os.path.exists(os.path.join(tree, dus.TRASH_DIRNAME, tid, "payload", "x.html"))


def test_trash_requests_reject_unknown_and_loose_fields(tree):
    c = _local(_app())
    tid = "20260101-000000-abcdef12"
    assert c.post(f"{BASE}/trash/purge", json={"id": tid, "confirm_text": "DELETE",
                                               "expected_size_bytes": 0, "x": 1}).status_code == 422
    assert c.post(f"{BASE}/trash/purge", json={"id": tid, "confirm_text": "DELETE",
                                               "expected_size_bytes": "0"}).status_code == 422
    assert c.post(f"{BASE}/trash/restore", json={"id": tid, "confirm": "true"}).status_code == 422
    assert c.post(f"{BASE}/trash/empty", json={
        "confirm_text": "DELETE", "expected_item_count": 0, "expected_size_bytes": 0,
        "all": True}).status_code == 422
    assert c.post(f"{BASE}/trash/empty", json={}).status_code == 422
    # the count and size shown are required, and a mismatch is a 409 that deletes nothing
    assert c.post(f"{BASE}/trash/empty", json={"confirm_text": "DELETE"}).status_code == 422
    r = c.post(f"{BASE}/trash/empty", json={"confirm_text": "DELETE", "expected_item_count": 5,
                                            "expected_size_bytes": 0})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "changed"


def test_the_trash_folder_cannot_be_cleared_through_the_normal_route(tree):
    c = _local(_app())
    tid = c.post(f"{BASE}/to-trash", json={
        "path": "library/source_cache", "confirm": True,
        "expected_size_bytes": 400, "expected_file_count": 1}).json()["trash_id"]
    for rel in ("baihe_trash", f"baihe_trash/{tid}", f"baihe_trash/{tid}/payload"):
        r = c.post(f"{BASE}/to-trash", json={"path": rel, "confirm": True,
                                             "expected_size_bytes": 0, "expected_file_count": 0})
        assert r.status_code == 422, (rel, r.text)
    assert os.path.exists(os.path.join(tree, dus.TRASH_DIRNAME, tid, "payload", "x.html"))
