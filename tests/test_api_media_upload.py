"""Tests for POST /api/media/dramas/{id}/upload (Migration Slice 31): fully
mocked, tiny fake bytes, isolated library."""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
import core
from api.api_config import ApiSettings
from api.server import create_app


def _fake_extract(src, out):
    with open(out, "wb") as f:
        f.write(b"wav")


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(core, "extract_audio_from_video", _fake_extract)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _up(client, did, name="clip.mp3", data=b"fakeaudio"):
    return client.post(f"/api/media/dramas/{did}/upload", files={"file": (name, data)})


def test_audio_upload(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "My Clip.MP3")
    assert r.status_code == 200
    assert r.json() == {"name": "source.mp3", "size": 9, "kind": "audio"}
    assert db.get_drama(did)["audio_filename"] == "source.mp3"
    assert os.listdir(db.drama_dir(did)) == ["source.mp3"]
    with open(os.path.join(db.drama_dir(did), "source.mp3"), "rb") as f:
        assert f.read() == b"fakeaudio"


def test_video_upload_extracts_audio(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mkv")
    assert r.json()["kind"] == "video"
    d = db.get_drama(did)
    assert d["audio_filename"] == "audio.wav" and d["source_video_filename"] == "source.mkv"


def test_video_extract_failure_cleans_up(client, isolated_db, monkeypatch):
    import db

    def boom(src, out):
        raise RuntimeError("ffmpeg")
    monkeypatch.setattr(core, "extract_audio_from_video", boom)
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp4")
    assert r.status_code == 422
    assert os.listdir(db.drama_dir(did)) == []
    assert not db.get_drama(did).get("audio_filename")


def test_bad_extension(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "evil.exe").status_code == 422
    assert _up(client, did, "noext").status_code == 422
    assert _up(client, did, "a.mp3\x00.exe").status_code == 422
    assert os.listdir(db.drama_dir(did)) == []


def test_traversal_name_is_ignored(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "../../../etc/passwd.wav")
    assert r.status_code == 200
    assert r.json()["name"] == "source.wav"
    assert "etc" not in r.text and "passwd" not in r.text
    assert not os.path.exists(os.path.join(os.path.dirname(db.drama_dir(did)), "source.wav"))


def test_oversize_rejected_and_partial_deleted(client, isolated_db, monkeypatch):
    import db
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "0.00001")  # ~10 bytes
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp3", b"x" * 100)
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "The uploaded file is too large."
    assert os.listdir(db.drama_dir(did)) == []
    assert not db.get_drama(did).get("audio_filename")


def test_empty_upload_rejected(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert _up(client, did, "a.mp3", b"").status_code == 422
    assert os.listdir(db.drama_dir(did)) == []


def test_unknown_drama_404(client, isolated_db):
    assert _up(client, 9999).status_code == 404


def test_running_job_409(client, isolated_db, monkeypatch):
    import db
    did = db.create_drama(title_en="D")
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: True)
    r = _up(client, did)
    assert r.status_code == 409
    assert os.listdir(db.drama_dir(did)) == []


def test_missing_file_field_422(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    assert client.post(f"/api/media/dramas/{did}/upload").status_code == 422


def test_upload_rejected_for_novel_narration_drama(client, isolated_db):
    import db
    did = db.create_drama(title_en="N")
    db.update_drama(did, content_mode="novel_narration")
    r = _up(client, did)
    assert r.status_code == 422
    assert not any(n.startswith("source") for n in os.listdir(db.drama_dir(did)))
    assert db.get_drama(did)["audio_filename"] in (None, "")


def test_upload_allowed_for_streamer_vod(client, isolated_db):
    import db
    did = db.create_drama(title_en="V")
    db.update_drama(did, content_mode="streamer_vod")
    assert _up(client, did).status_code == 200
