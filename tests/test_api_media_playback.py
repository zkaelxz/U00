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
    # `..` must hit a file that really exists outside the folder, or the
    # isfile check alone would pass this test without the containment check.
    outside = os.path.realpath(os.path.join(db.drama_dir(did), "..", "..", "outside.mp3"))
    with open(outside, "wb") as f:
        f.write(b"outsidebytes")
    assert os.path.isfile(os.path.join(db.drama_dir(did), "../../outside.mp3"))
    db.update_drama(did, audio_filename="../../outside.mp3")
    r = client.get(_url(did))
    assert r.status_code == 404 and b"outsidebytes" not in r.content
    _assert_no_leak(r, str(tmp_path), "outside.mp3", db.drama_dir(did), db.LIBRARY_DIR)
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
        assert r.status_code in (404, 416)
        _assert_no_leak(r, db.drama_dir(did), "source.mp3", db.LIBRARY_DIR)



# --- review fixes -----------------------------------------------------------

def test_inline_disposition_with_generic_filename(client, did):
    r = client.get(_url(did))
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline") and f'filename="drama_{did}_audio.mp3"' in cd
    assert r.headers["x-content-type-options"] == "nosniff"


def test_fixed_content_type_map(client, isolated_db, did):
    import db
    for name, ctype in (("a.m4a", "audio/mp4"), ("a.flac", "audio/flac"), ("a.wav", "audio/wav")):
        with open(os.path.join(db.drama_dir(did), name), "wb") as f:
            f.write(DATA)
        db.update_drama(did, audio_filename=name)
        assert client.get(_url(did)).headers["content-type"] == ctype
    with open(os.path.join(db.drama_dir(did), "v.mkv"), "wb") as f:
        f.write(DATA)
    db.update_drama(did, source_video_filename="v.mkv")
    assert client.get(_url(did, "video")).headers["content-type"] == "video/x-matroska"


def test_commonpath_valueerror_is_404(client, did, monkeypatch):
    """A stored name on another Windows drive makes commonpath raise."""
    from services import media_playback_service as mps

    def boom(paths):
        raise ValueError("Paths don't have the same drive")
    monkeypatch.setattr(mps.os.path, "commonpath", boom)
    assert client.get(_url(did)).status_code == 404


def test_if_range_match_and_stale(client, did):
    etag = client.head(_url(did)).headers["etag"]
    r = client.get(_url(did), headers={"Range": "bytes=0-9", "If-Range": etag})
    assert r.status_code == 206 and r.content == DATA[:10]
    r = client.get(_url(did), headers={"Range": "bytes=0-9", "If-Range": '"stale-etag"'})
    assert r.status_code == 200 and r.content == DATA


def test_multi_range(client, did):
    r = client.get(_url(did), headers={"Range": "bytes=0-4,10-14"})
    assert r.status_code == 206
    assert r.headers["content-type"].startswith("multipart/byteranges")
    assert DATA[0:5] in r.content and DATA[10:15] in r.content


def test_head_with_range(client, did):
    r = client.head(_url(did), headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == b""
    assert r.headers["content-range"] == "bytes 10-19/100"
    assert r.headers["content-length"] == "10"


@pytest.mark.parametrize("header", ["bytes=abc", "items=0-5", "bytes=5-2", "bytes="])
def test_malformed_range(client, did, header):
    """Malformed Range is either ignored (full 200) or refused (400/416),
    never a 500 and never a path."""
    import db
    r = client.get(_url(did), headers={"Range": header})
    assert r.status_code in (200, 400, 416)
    if r.status_code == 200:
        assert r.content == DATA
    _assert_no_leak(r, db.LIBRARY_DIR)


def test_media_stream_permission_on_all_four_routes(isolated_db, did):
    from api import auth as api_auth
    from services import auth_service
    app = create_app(ApiSettings(auth_mode="on"))
    c = TestClient(app, base_url="https://baihe.example.com", raise_server_exceptions=False)

    def headers(session):
        return {"Cookie": f"{api_auth.COOKIE_NAME}={session['session_token']}",
                api_auth.CSRF_HEADER: session["csrf_token"]}
    kid = auth_service.add_user("kid@example.com")
    no = headers(auth_service.create_session(kid["id"]))
    viewer = auth_service.add_user("viewer@example.com")
    auth_service.grant_permission(viewer["id"], "media.stream")
    yes = headers(auth_service.create_session(viewer["id"]))
    for kind in ("audio", "video"):
        assert c.get(_url(did, kind)).status_code == 401
        assert c.get(_url(did, kind), headers=no).status_code == 403
        assert c.head(_url(did, kind), headers=no).status_code == 403
        assert c.get(_url(did, kind), headers=yes).status_code == 200
        assert c.head(_url(did, kind), headers=yes).status_code == 200
