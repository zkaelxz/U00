"""Tests for Migration Slice 28: artifact convention + download endpoint."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from services import artifact_service
from services.service_errors import InvalidInputError


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def did(isolated_db):
    return isolated_db.create_drama(title_en="D")


def _write(did, kind, name, data=b"hello"):
    path = artifact_service.output_path(did, kind, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def test_download_and_info(client, did):
    _write(did, "subtitle", "a b.srt", b"12345")
    r = client.get(f"/api/artifacts/dramas/{did}/subtitle")
    assert r.status_code == 200 and r.content == b"12345"
    assert r.headers["content-disposition"] == 'attachment; filename="a_b.srt"'
    info = client.get(f"/api/artifacts/dramas/{did}/subtitle/info").json()
    assert info == {"name": "a b.srt", "size": 5, "kind": "subtitle"}


def test_newest_file_wins(client, did):
    old = _write(did, "epub", "old.epub", b"old")
    os.utime(old, (1, 1))
    _write(did, "epub", "new.epub", b"new")
    assert client.get(f"/api/artifacts/dramas/{did}/epub").content == b"new"


def test_missing_and_unknown(client, did):
    assert client.get(f"/api/artifacts/dramas/{did}/video").status_code == 404
    assert client.get("/api/artifacts/dramas/9999/video").status_code == 404
    assert client.get(f"/api/artifacts/dramas/{did}/secrets").status_code == 422


@pytest.mark.parametrize("bad", ["../x", "a/b", "/etc/passwd", "..", "", "a\\b", "x\x00y"])
def test_output_path_rejects_bad_names(did, bad):
    with pytest.raises(InvalidInputError):
        artifact_service.output_path(did, "subtitle", bad)


def test_traversal_kind_rejected(client, did):
    r = client.get(f"/api/artifacts/dramas/{did}/..%2F..%2Fx")
    assert r.status_code in (404, 422)


def test_symlink_escape_ignored(client, did, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP")
    folder = os.path.dirname(artifact_service.output_path(did, "audio", "keep.txt"))
    os.symlink(secret, os.path.join(folder, "link.txt"))
    r = client.get(f"/api/artifacts/dramas/{did}/audio")
    assert r.status_code == 404
    assert "TOP" not in r.text and str(tmp_path) not in r.text


def test_symlink_dir_escape_ignored(client, did, tmp_path, isolated_db):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "f.txt").write_text("TOP")
    exports = os.path.join(isolated_db.drama_dir(did), "exports")
    os.makedirs(exports)
    os.symlink(tmp_path / "out", os.path.join(exports, "video"))
    r = client.get(f"/api/artifacts/dramas/{did}/video")
    assert r.status_code == 404


def test_errors_and_info_leak_no_path(client, did, isolated_db):
    _write(did, "archive", "z.zip")
    body = client.get(f"/api/artifacts/dramas/{did}/archive/info").text
    assert isolated_db.LIBRARY_DIR not in body
    err = client.get(f"/api/artifacts/dramas/{did}/video").text
    assert isolated_db.LIBRARY_DIR not in err
