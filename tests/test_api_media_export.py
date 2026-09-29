"""Tests for Migration Slices 29-30: audiobook and burned-in video export jobs.
ffmpeg is never run: subprocess.run / shutil.which are patched."""

import os
import subprocess
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import artifact_service, media_export_service
from services.service_errors import NotFoundError


class FakeFfmpeg:
    def __init__(self):
        self.calls = []
        self.fail = False


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def fake_ffmpeg(monkeypatch):
    """ffmpeg 'installed'; subprocess.run records the command and writes the
    output file (the last argument, relative to cwd when cwd is given)."""
    fake = FakeFfmpeg()
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def fake_run(cmd, **kwargs):
        fake.calls.append((cmd, kwargs))
        if fake.fail:
            raise subprocess.CalledProcessError(1, cmd, stderr=b"/secret/path")
        out = cmd[-1]
        if kwargs.get("cwd"):
            out = os.path.join(kwargs["cwd"], out)
        with open(out, "wb") as f:
            f.write(b"media")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(background_jobs, "run_cancellable",
                        lambda job_id, cmd, **kw: fake_run(cmd, **kw))
    return fake


@pytest.fixture(autouse=True)
def clean_jobs():
    with background_jobs._lock:
        background_jobs._jobs.clear()
    yield
    with background_jobs._lock:
        background_jobs._jobs.clear()


@pytest.fixture
def drama(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [
        Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
        Line(idx=1, start=2.0, end=4.0, zh="再见", en="Bye"),
    ])
    return did


def _wait(job_id):
    for _ in range(300):
        st = background_jobs.get_status(job_id)
        if st["status"] not in ("running", "queued"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _touch(isolated_db, did, name):
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    with open(os.path.join(ddir, name), "wb") as f:
        f.write(b"RIFFxxxx")


def _no_artifact(did, kind):
    with pytest.raises(NotFoundError):
        artifact_service.get_artifact(did, kind)


def test_job_prefixes_registered():
    assert "audiobook_" in background_jobs.DRAMA_JOB_PREFIXES
    assert "burned_video_" in background_jobs.DRAMA_JOB_PREFIXES


# ---- Slice 29: audiobook ----------------------------------------------------

def test_audiobook_runs_and_writes_artifact(client, drama, isolated_db, fake_ffmpeg):
    _touch(isolated_db, drama, "narration_track.wav")
    r = client.post(f"/api/export/dramas/{drama}/audiobook")
    assert r.status_code == 200
    assert r.json() == {"job_id": f"audiobook_{drama}"}
    assert _wait(f"audiobook_{drama}")["status"] == "done"
    assert artifact_service.get_artifact(drama, "audio")["name"] == f"audiobook_{drama}.m4b"
    cmd, kwargs = fake_ffmpeg.calls[0]
    assert cmd[0] == "ffmpeg" and "ipod" in cmd
    assert "shell" not in kwargs
    assert client.get(f"/api/artifacts/dramas/{drama}/audio").status_code == 200


def test_audiobook_unknown_drama_404(client):
    assert client.post("/api/export/dramas/999/audiobook").status_code == 404


def test_audiobook_no_lines_422(client, isolated_db):
    did = isolated_db.create_drama(title_en="Empty")
    r = client.post(f"/api/export/dramas/{did}/audiobook")
    assert r.status_code == 422
    assert _error(r)["message"] == "This drama has no lines to export."


def test_audiobook_no_narration_422(client, drama):
    r = client.post(f"/api/export/dramas/{drama}/audiobook")
    assert r.status_code == 422
    assert "narration" in _error(r)["message"]


def test_audiobook_ffmpeg_missing_503(client, drama, isolated_db, monkeypatch):
    _touch(isolated_db, drama, "narration_track.wav")
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: None)
    r = client.post(f"/api/export/dramas/{drama}/audiobook")
    assert r.status_code == 503
    assert "ffmpeg" in _error(r)["message"]


def test_audiobook_duplicate_409(client, drama, isolated_db):
    _touch(isolated_db, drama, "narration_track.wav")
    with background_jobs._lock:
        background_jobs._jobs[f"audiobook_{drama}"] = {"status": "running"}
    assert client.post(f"/api/export/dramas/{drama}/audiobook").status_code == 409


def test_audiobook_failure_leaves_no_artifact_and_no_paths(client, drama, isolated_db, fake_ffmpeg):
    _touch(isolated_db, drama, "narration_track.wav")
    fake_ffmpeg.fail = True
    client.post(f"/api/export/dramas/{drama}/audiobook")
    st = _wait(f"audiobook_{drama}")
    assert st["status"] == "error"
    assert "/secret" not in st["error"] and isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "audio")


# ---- Slice 30: burned-in video ----------------------------------------------

def _add_video(isolated_db, did, name="source.mp4"):
    _touch(isolated_db, did, name)
    isolated_db.update_drama(did, source_video_filename=name)


def test_burned_video_runs_and_writes_artifact(client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    r = client.post(f"/api/export/dramas/{drama}/burned-video", json={"style": {"size": 30}})
    assert r.status_code == 200
    assert r.json() == {"job_id": f"burned_video_{drama}"}
    assert _wait(f"burned_video_{drama}")["status"] == "done"
    assert artifact_service.get_artifact(drama, "video")["name"] == f"burned_video_{drama}.mp4"
    cmd, kwargs = fake_ffmpeg.calls[0]
    assert cmd[0] == "ffmpeg" and isinstance(cmd, list) and "shell" not in kwargs
    assert cmd[cmd.index("-vf") + 1] == "subtitles=subs.ass"
    assert kwargs["cwd"]
    assert client.get(f"/api/artifacts/dramas/{drama}/video").status_code == 200


def test_burned_video_body_optional(client, drama, isolated_db):
    _add_video(isolated_db, drama)
    assert client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 200
    _wait(f"burned_video_{drama}")


@pytest.mark.parametrize("body", [
    {"style": {"font": "Ar\nial"}}, {"style": {"font": "A\x07"}}, {"style": {"size": 999}},
    {"preset": "Nope"}, {"style": {"primary": "red"}}])
def test_burned_video_bad_style_422(client, drama, isolated_db, body, fake_ffmpeg):
    _add_video(isolated_db, drama)
    assert client.post(f"/api/export/dramas/{drama}/burned-video", json=body).status_code == 422
    assert not fake_ffmpeg.calls


def test_burned_video_unknown_drama_404(client):
    assert client.post("/api/export/dramas/999/burned-video").status_code == 404


def test_burned_video_no_lines_422(client, isolated_db):
    did = isolated_db.create_drama(title_en="Empty")
    assert client.post(f"/api/export/dramas/{did}/burned-video").status_code == 422


def test_burned_video_no_source_video_422(client, drama):
    r = client.post(f"/api/export/dramas/{drama}/burned-video")
    assert r.status_code == 422
    assert _error(r)["message"] == "No source video uploaded for this drama."


def test_burned_video_traversal_filename_rejected(client, drama, isolated_db):
    isolated_db.update_drama(drama, source_video_filename="../evil.mp4")
    assert client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 422


def test_burned_video_ffmpeg_missing_503(client, drama, isolated_db, monkeypatch):
    _add_video(isolated_db, drama)
    monkeypatch.setattr(media_export_service.shutil, "which", lambda name: None)
    assert client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 503


def test_burned_video_duplicate_409(client, drama, isolated_db):
    _add_video(isolated_db, drama)
    with background_jobs._lock:
        background_jobs._jobs[f"burned_video_{drama}"] = {"status": "queued"}
    assert client.post(f"/api/export/dramas/{drama}/burned-video").status_code == 409


def test_burned_video_failure_no_artifact_no_paths(client, drama, isolated_db, fake_ffmpeg):
    _add_video(isolated_db, drama)
    fake_ffmpeg.fail = True
    client.post(f"/api/export/dramas/{drama}/burned-video")
    st = _wait(f"burned_video_{drama}")
    assert st["status"] == "error"
    assert "/secret" not in st["error"] and isolated_db.drama_dir(drama) not in st["error"]
    _no_artifact(drama, "video")


# ---- B-05: cancel kills the ffmpeg run -------------------------------------

@pytest.fixture
def cancelled_ffmpeg(monkeypatch):
    def fake(job_id, cmd, **kw):
        raise background_jobs.JobCancelled(job_id)
    monkeypatch.setattr(background_jobs, "run_cancellable", fake)


def test_audiobook_cancel_ends_cancelled_no_artifact(client, drama, isolated_db,
                                                      fake_ffmpeg, cancelled_ffmpeg):
    _touch(isolated_db, drama, "narration_track.wav")
    client.post(f"/api/export/dramas/{drama}/audiobook")
    assert _wait(f"audiobook_{drama}")["status"] == "cancelled"
    _no_artifact(drama, "audio")


def test_burned_video_cancel_ends_cancelled_no_artifact(client, drama, isolated_db,
                                                         fake_ffmpeg, cancelled_ffmpeg):
    _add_video(isolated_db, drama)
    client.post(f"/api/export/dramas/{drama}/burned-video")
    assert _wait(f"burned_video_{drama}")["status"] == "cancelled"
    _no_artifact(drama, "video")
