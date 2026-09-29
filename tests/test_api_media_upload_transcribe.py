"""Tests for Migration Slice 32: upload-and-transcribe + media status. Fully mocked."""
import inspect
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    # X-Baihe-Local: what the React upload helper sends; local_only refuses
    # a multipart POST without it (api/auth.py _cross_site_safe)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _drama(**kw):
    import db
    return db.create_drama(title_en="D", transcript_mode="whisper", **kw)


def _post(client, did, data=None, name="a.mp3"):
    return client.post(f"/api/media/dramas/{did}/upload-and-transcribe",
                       files={"file": (name, b"fakeaudio")}, data=data or {})


def test_upload_then_run_with_use_gpu(client, monkeypatch):
    import db
    did = _drama()
    db.set_app_setting("use_gpu", True)
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        names = list(inspect.signature(target).parameters)
        captured.update(dict(zip(names, a)))
        captured["job_id"] = job_id
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    r = _post(client, did, {"initial_prompt": "names", "source_language": "ja"})
    assert r.status_code == 200
    assert r.json() == {"upload": {"name": "source.mp3", "size": 9, "kind": "audio"},
                        "job_id": f"transcribe_{did}"}
    assert captured["use_gpu"] is True
    assert captured["initial_prompt"] == "names"
    assert captured["source_language"] == "ja"


def test_run_failure_keeps_upload(client, monkeypatch):
    import db
    did = _drama()
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
    r = _post(client, did)
    assert r.status_code == 409
    assert os.path.exists(os.path.join(db.drama_dir(did), "source.mp3"))
    assert db.get_drama(did)["audio_filename"] == "source.mp3"


def test_bad_option_stores_nothing(client):
    import db
    did = _drama()
    r = _post(client, did, {"expected_speakers": "99"})
    assert r.status_code == 422
    assert not os.path.exists(os.path.join(db.drama_dir(did), "source.mp3"))


def test_status(client, monkeypatch):
    import db
    monkeypatch.setenv("BAIHE_MAX_UPLOAD_MB", "5")
    did = _drama()
    r = client.get(f"/api/media/dramas/{did}/status")
    assert r.json() == {"drama_id": did, "has_audio": False, "has_source_video": False,
                        "upload_max_mb": 5}
    client.post(f"/api/media/dramas/{did}/upload", files={"file": ("a.wav", b"x")})
    assert client.get(f"/api/media/dramas/{did}/status").json()["has_audio"] is True
    assert client.get("/api/media/dramas/9999/status").status_code == 404
