"""Tests for POST /api/media/dramas/{id}/upload (Migration Slice 31): fully
mocked, tiny fake bytes, isolated library."""

import os
import subprocess
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app


def _fake_ffmpeg(job_id, cmd, cwd=None, **kw):
    with open(cmd[-1], "wb") as f:
        f.write(b"wav")


def _wait(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish")


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(background_jobs, "run_cancellable", _fake_ffmpeg)
    # X-Baihe-Local: what the React upload helper sends; local_only refuses
    # a multipart POST without it (api/auth.py _cross_site_safe)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _up(client, did, name="clip.mp3", data=b"fakeaudio"):
    return client.post(f"/api/media/dramas/{did}/upload", files={"file": (name, data)})


def test_audio_upload(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "My Clip.MP3")
    assert r.status_code == 200
    assert r.json() == {"name": "source.mp3", "size": 9, "kind": "audio", "job_id": None}
    assert db.get_drama(did)["audio_filename"] == "source.mp3"
    assert os.listdir(db.drama_dir(did)) == ["source.mp3"]
    with open(os.path.join(db.drama_dir(did), "source.mp3"), "rb") as f:
        assert f.read() == b"fakeaudio"


def test_video_upload_extracts_audio(client, isolated_db):
    import db
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mkv")
    assert r.status_code == 200
    assert r.json() == {"name": "source.mkv", "size": 9, "kind": "video",
                        "job_id": f"extract_audio_{did}"}
    assert _wait(f"extract_audio_{did}")["status"] == "done"
    d = db.get_drama(did)
    assert d["audio_filename"] == "audio.wav" and d["source_video_filename"] == "source.mkv"
    assert sorted(os.listdir(db.drama_dir(did))) == ["audio.wav", "source.mkv"]


def test_video_upload_returns_before_extraction_finishes(client, isolated_db, monkeypatch):
    import db
    gate = threading.Event()

    def slow_ffmpeg(job_id, cmd, cwd=None, **kw):
        assert kw.get("timeout")  # a stuck ffmpeg is bounded
        gate.wait(5)
        _fake_ffmpeg(job_id, cmd)
    monkeypatch.setattr(background_jobs, "run_cancellable", slow_ffmpeg)
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp4")
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert background_jobs.get_status(job_id)["status"] == "running"
    assert not db.get_drama(did).get("audio_filename")
    # a second upload while extracting is refused and does not touch the file
    r2 = _up(client, did, "b.mp4", b"other")
    assert r2.status_code == 409
    gate.set()
    assert _wait(job_id)["status"] == "done"
    assert db.get_drama(did)["audio_filename"] == "audio.wav"
    with open(os.path.join(db.drama_dir(did), "source.mp4"), "rb") as f:
        assert f.read() == b"fakeaudio"


def test_video_extract_failure_is_job_error_and_cleans_up(client, isolated_db, monkeypatch):
    import db

    def boom(job_id, cmd, cwd=None, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"key sk-ant-abcdefghijklmnopqrstuvwxyz0123")
    monkeypatch.setattr(background_jobs, "run_cancellable", boom)
    did = db.create_drama(title_en="D")
    r = _up(client, did, "a.mp4")
    assert r.status_code == 200
    job = _wait(r.json()["job_id"])
    assert job["status"] == "error"
    assert "Could not read audio from that video file." in job["error"]
    assert "sk-ant" not in job["error"] and "sk-ant" not in (job.get("traceback") or "")
    assert os.listdir(db.drama_dir(did)) == []
    assert not db.get_drama(did).get("audio_filename")


def test_extract_ffmpeg_opens_local_files_only(client, isolated_db, monkeypatch):
    # an uploaded "mp4" could really be an HLS playlist naming network URLs
    import db
    cmds = []

    def recording(job_id, cmd, cwd=None, **kw):
        cmds.append(cmd)
        _fake_ffmpeg(job_id, cmd)
    monkeypatch.setattr(background_jobs, "run_cancellable", recording)
    did = db.create_drama(title_en="D")
    assert _wait(_up(client, did, "a.mp4").json()["job_id"])["status"] == "done"
    i = cmds[0].index("-i")
    assert cmds[0][i - 2:i] == ["-protocol_whitelist", "file"]


def test_video_extract_cancel_is_cancelled_and_cleans_up(client, isolated_db, monkeypatch):
    import db

    def cancelled(job_id, cmd, cwd=None, **kw):
        raise background_jobs.JobCancelled(job_id)
    monkeypatch.setattr(background_jobs, "run_cancellable", cancelled)
    did = db.create_drama(title_en="D")
    job = _wait(_up(client, did, "a.mp4").json()["job_id"])
    assert job["status"] == "cancelled"
    assert os.listdir(db.drama_dir(did)) == []


def test_run_cancellable_timeout_kills_process(isolated_db):
    import sys
    background_jobs.start_job("t_timeout", lambda: None)
    t0 = time.time()
    with pytest.raises(subprocess.TimeoutExpired):
        background_jobs.run_cancellable("t_timeout", [sys.executable, "-c", "import time; time.sleep(30)"],
                                        timeout=0.5)
    assert time.time() - t0 < 10


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
    assert r.json()["error"]["message"] .startswith("That file is larger than the")
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
    # multipart without the "file" field (a body-less POST is now refused
    # earlier by local_only's content-type check, see api/auth.py)
    r = client.post(f"/api/media/dramas/{did}/upload", files={"other": ("a.txt", b"x")})
    assert r.status_code == 422


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


def test_concurrent_upload_loser_never_touches_winner_file(client, isolated_db):
    import db
    from services import media_upload_service
    did = db.create_drama(title_en="D")
    reading, release = threading.Event(), threading.Event()

    class SlowBody:
        def __init__(self):
            self.sent = False

        def read(self, n):
            if self.sent:
                return b""
            reading.set()
            release.wait(5)
            self.sent = True
            return b"winner"
    out = {}
    t = threading.Thread(target=lambda: out.update(
        r=media_upload_service.upload_media(did, "a.mp4", SlowBody())))
    t.start()
    assert reading.wait(5)
    r = _up(client, did, "b.mp4", b"loser")  # same source.mp4 target
    assert r.status_code == 409
    release.set()
    t.join(5)
    assert out["r"]["job_id"] == f"extract_audio_{did}"
    _wait(out["r"]["job_id"])
    with open(os.path.join(db.drama_dir(did), "source.mp4"), "rb") as f:
        assert f.read() == b"winner"
    assert not any(n.startswith(".upload_") for n in os.listdir(db.drama_dir(did)))
