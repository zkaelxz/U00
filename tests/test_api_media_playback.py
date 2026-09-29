"""Tests for GET/HEAD /api/media/dramas/{id}/audio|video (Migration Slice 52):
tiny tmp files, isolated library."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app

DATA = bytes(range(100))


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def did(isolated_db):
    import db
    d = db.create_drama(title_en="D")
    with open(os.path.join(db.drama_dir(d), "source.mp3"), "wb") as f:
        f.write(DATA)
    with open(os.path.join(db.drama_dir(d), "source.mp4"), "wb") as f:
        f.write(DATA)
    db.update_drama(d, audio_filename="source.mp3", source_video_filename="source.mp4")
    return d


def _url(d, kind="audio"):
    return f"/api/media/dramas/{d}/{kind}"


def test_full(client, did):
    r = client.get(_url(did))
    assert r.status_code == 200 and r.content == DATA
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"].startswith("audio/")
    assert client.get(_url(did, "video")).headers["content-type"].startswith("video/")


def test_partial_suffix_open_ended(client, did):
    r = client.get(_url(did), headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == DATA[10:20]
    assert r.headers["content-range"] == "bytes 10-19/100"
    r = client.get(_url(did), headers={"Range": "bytes=-5"})
    assert r.status_code == 206 and r.content == DATA[-5:]
    r = client.get(_url(did), headers={"Range": "bytes=90-"})
    assert r.status_code == 206 and r.content == DATA[90:]
    assert r.headers["content-range"] == "bytes 90-99/100"


def test_unsatisfiable(client, did):
    r = client.get(_url(did), headers={"Range": "bytes=500-600"})
    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */100"


def test_head(client, did):
    r = client.head(_url(did))
    assert r.status_code == 200 and r.content == b""
    assert r.headers["content-length"] == "100" and r.headers["accept-ranges"] == "bytes"


def test_missing_drama_and_file(client, isolated_db, did):
    import db
    assert client.get(_url(9999)).status_code == 404
    other = db.create_drama(title_en="E")
    r = client.get(_url(other))
    assert r.status_code == 404
    os.remove(os.path.join(db.drama_dir(did), "source.mp3"))
    assert client.get(_url(did)).status_code == 404


def _assert_no_leak(r, *secrets):
    for s in secrets:
        assert s not in r.text


def test_traversal_and_symlink_escape(client, isolated_db, did, tmp_path):
    import db
    secret = tmp_path / "secret.mp3"
    secret.write_bytes(b"topsecret")
    db.update_drama(did, audio_filename="../../secret.mp3")
    r = client.get(_url(did))
    assert r.status_code == 404
    _assert_no_leak(r, str(tmp_path), "secret.mp3", db.drama_dir(did))
    db.update_drama(did, audio_filename=str(secret))
    assert client.get(_url(did)).status_code == 404
    link = os.path.join(db.drama_dir(did), "link.mp3")
    try:
        os.symlink(secret, link)
    except OSError:
        pytest.skip("symlinks unavailable")
    db.update_drama(did, audio_filename="link.mp3")
    r = client.get(_url(did))
    assert r.status_code == 404 and b"topsecret" not in r.content
    _assert_no_leak(r, str(tmp_path), "link.mp3")


def test_bad_extension_refused(client, isolated_db, did):
    import db
    with open(os.path.join(db.drama_dir(did), "notes.txt"), "wb") as f:
        f.write(b"x")
    db.update_drama(did, audio_filename="notes.txt")
    assert client.get(_url(did)).status_code == 404


def test_error_bodies_have_no_paths(client, isolated_db, did):
    import db
    for r in (client.get(_url(9999)), client.get(_url(did), headers={"Range": "bytes=500-"}),
              client.get(_url(db.create_drama(title_en="E")))):
        _assert_no_leak(r, db.drama_dir(did), "source.mp3", str(isolated_db) if isolated_db else "zz")
