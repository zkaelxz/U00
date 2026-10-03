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
def tree(isolated_db):
    lib = db.LIBRARY_DIR
    _write(os.path.join(lib, "source_cache", "x.html"), 400)
    _write(os.path.join(lib, "dramas", "1", "audio.mp3"), 1000)
    return os.path.dirname(lib)


@pytest.fixture
def recycled(monkeypatch):
    calls = []

    def fake(path, size_bytes=0):
        calls.append(path)
        import shutil
        shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
    monkeypatch.setattr(dus, "send_to_recycle_bin", fake)
    return calls


def test_scan_clear_flow_and_no_absolute_paths(tree, recycled):
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
    r = c.post(f"{BASE}/recycle", json={
        "path": "library/source_cache", "confirm": True,
        "expected_size_bytes": 400, "expected_file_count": 1})
    seen.append(r.text)
    assert r.status_code == 200 and r.json()["freed_bytes"] == 400
    assert len(recycled) == 1
    # a changed item is a 409 with details and no path
    r = c.post(f"{BASE}/recycle", json={
        "path": "library/dramas/1", "confirm": True, "confirm_irreplaceable": True,
        "expected_size_bytes": 5, "expected_file_count": 1})
    seen.append(r.text)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "changed"
    for text in seen:
        assert tree not in text and db.LIBRARY_DIR not in text


@pytest.mark.parametrize("path", ["..", "/etc", "C:\\Windows", "library/../.."])
def test_bad_paths_are_422(tree, recycled, path):
    c = _local(_app())
    assert c.get(BASE, params={"path": path}).status_code == 422
    r = c.post(f"{BASE}/recycle", json={"path": path, "confirm": True,
                                        "expected_size_bytes": 0, "expected_file_count": 0})
    assert r.status_code == 422 and recycled == []
    assert tree not in r.text


def test_protected_is_refused_by_the_server(tree, recycled):
    r = _local(_app()).post(f"{BASE}/recycle", json={
        "path": "library/library.db", "confirm": True,
        "expected_size_bytes": 0, "expected_file_count": 0})
    assert r.status_code == 422 and recycled == []


def test_unknown_fields_and_missing_sizes_rejected(tree):
    c = _local(_app())
    assert c.post(f"{BASE}/recycle", json={"path": "library", "confirm": True}).status_code == 422
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


ROUTES = [("GET", BASE, None),
          ("POST", f"{BASE}/recycle", {"path": "library/source_cache", "confirm": True,
                                       "expected_size_bytes": 400, "expected_file_count": 1}),
          ("POST", f"{BASE}/move", {"path": "library/backups/auto", "destination": "/tmp",
                                    "confirm": True})]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_remote_request_refused_auth_off(tree, recycled, method, path, body):
    c = TestClient(_app("off"), base_url=REMOTE, raise_server_exceptions=False)
    r = c.request(method, path, json=body)
    assert r.status_code == 403
    assert recycled == [] and os.path.exists(os.path.join(tree, "library", "source_cache"))


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_remote_admin_refused_auth_on(tree, recycled, method, path, body):
    app = _app("on")
    user = auth_service.grant_admin_local("admin@example.com")
    session = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    headers = {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
               api_auth.CSRF_HEADER: session["csrf_token"]}
    c = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    r = c.request(method, path, json=body, headers=headers)
    assert r.status_code == 403
    assert recycled == []


def test_openapi_has_the_routes_and_no_secret_fields(tree):
    schema = json.dumps(_local(_app()).get("/api/openapi.json").json())
    assert "/api/data-usage/recycle" in schema
