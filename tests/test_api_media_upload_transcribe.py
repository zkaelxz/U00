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
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


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
    assert r.json() == {"upload": {"name": "source.mp3", "size": 9, "kind": "audio", "job_id": None},
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


def test_video_upload_and_transcribe_one_job(client, monkeypatch):
    import time
    import db
    did = _drama()
    monkeypatch.setattr(background_jobs, "run_cancellable",
                        lambda job_id, cmd, cwd=None, **kw: open(cmd[-1], "wb").close())
    from services import transcribe_service
    calls = {}

    def fake_run(drama_id, **opts):
        calls.update(opts, drama_id=drama_id,
                     audio=db.get_drama(drama_id)["audio_filename"])
        background_jobs.start_job(f"transcribe_{drama_id}", lambda: None)
        return {"job_id": f"transcribe_{drama_id}"}
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", fake_run)
    r = client.post(f"/api/media/dramas/{did}/upload-and-transcribe",
                    files={"file": ("a.mp4", b"vid")}, data={"source_language": "ja"})
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == f"extract_audio_{did}" == body["upload"]["job_id"]
    deadline = time.time() + 5
    while background_jobs.get_status(body["job_id"])["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.02)
    assert background_jobs.get_status(body["job_id"])["status"] == "done"
    assert calls["audio"] == "audio.wav" and calls["source_language"] == "ja"


def test_video_upload_and_transcribe_run_error_surfaces(client, monkeypatch):
    import time
    did = _drama()
    monkeypatch.setattr(background_jobs, "run_cancellable",
                        lambda job_id, cmd, cwd=None, **kw: open(cmd[-1], "wb").close())
    from services import transcribe_service
    from services.service_errors import UnsupportedOperationError

    def refuse(drama_id, **opts):
        raise UnsupportedOperationError("no transcript")
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", refuse)
    r = client.post(f"/api/media/dramas/{did}/upload-and-transcribe",
                    files={"file": ("a.mp4", b"vid")})
    job_id = r.json()["job_id"]
    deadline = time.time() + 5
    while background_jobs.get_status(job_id)["status"] == "running":
        assert time.time() < deadline
        time.sleep(0.02)
    job = background_jobs.get_status(job_id)
    assert job["status"] == "error" and "no transcript" in job["error"]


def _wait_status(job_id, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while True:
        job = background_jobs.get_status(job_id)
        if job and job["status"] not in ("running", "queued"):
            return job
        assert time.time() < deadline, f"{job_id} still {job and job['status']}"
        time.sleep(0.02)


def _wait_for(pred, timeout=5.0):
    import time
    deadline = time.time() + timeout
    while not pred():
        assert time.time() < deadline
        time.sleep(0.02)


def _video_post(client, did, data=None):
    return client.post(f"/api/media/dramas/{did}/upload-and-transcribe",
                       files={"file": ("a.mp4", b"vid")}, data=data or {})


@pytest.fixture
def fast_ffmpeg(monkeypatch):
    monkeypatch.setattr(background_jobs, "run_cancellable",
                        lambda job_id, cmd, cwd=None, **kw: open(cmd[-1], "wb").close())


def test_cancel_while_transcribe_child_queued_is_cancelled(client, monkeypatch, fast_ffmpeg):
    import time
    from services import transcribe_service
    did = _drama()
    tid = f"transcribe_{did}"
    background_jobs.clear_job(tid)  # ids repeat across isolated_db tests

    def queued_run(drama_id, **opts):
        with background_jobs._lock:
            background_jobs._jobs[tid] = {"status": "queued", "progress": 0.0, "message": "Waiting",
                                          "error": None, "started_at": time.time(),
                                          "finished_at": None, "cancel_requested": False,
                                          "result": None, "gpu_touching": True,
                                          "description": "t", "kind": "thread"}
        return {"job_id": tid}
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", queued_run)
    job_id = _video_post(client, did).json()["job_id"]
    _wait_for(lambda: background_jobs.get_status(tid) is not None)
    background_jobs.request_cancel(job_id)
    assert _wait_status(job_id)["status"] == "cancelled"
    assert background_jobs.get_status(tid) is None


def test_cancel_while_transcribe_child_running_is_cancelled(client, monkeypatch, fast_ffmpeg):
    import time
    from services import transcribe_service
    did = _drama()
    tid = f"transcribe_{did}"
    background_jobs.clear_job(tid)  # ids repeat across isolated_db tests

    def child():
        while not background_jobs.is_cancel_requested(tid):
            time.sleep(0.01)
        raise background_jobs.JobCancelled(tid)

    def running_run(drama_id, **opts):
        background_jobs.start_job(tid, child)
        return {"job_id": tid}
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", running_run)
    job_id = _video_post(client, did).json()["job_id"]
    _wait_for(lambda: background_jobs.get_status(tid) is not None)
    background_jobs.request_cancel(job_id)
    assert _wait_status(job_id)["status"] == "cancelled"
    assert _wait_status(tid)["status"] == "cancelled"


def test_cancel_between_extraction_and_transcribe_start(client, monkeypatch):
    import db
    from services import transcribe_service
    did = _drama()

    def ffmpeg_then_cancel(job_id, cmd, cwd=None, **kw):
        open(cmd[-1], "wb").close()
        background_jobs.request_cancel(job_id)  # cancel lands after ffmpeg finished
    monkeypatch.setattr(background_jobs, "run_cancellable", ffmpeg_then_cancel)
    started = []
    monkeypatch.setattr(transcribe_service, "start_transcribe_run",
                        lambda *a, **k: started.append(1) or {"job_id": "x"})
    job_id = _video_post(client, did).json()["job_id"]
    assert _wait_status(job_id)["status"] == "cancelled"
    assert started == []
    assert db.get_drama(did)["audio_filename"] == "audio.wav"  # extraction itself is kept


def test_video_bad_transcribe_option_rejected_before_storing(client, fast_ffmpeg):
    import db
    did = db.create_drama(title_en="D", transcript_mode="have_transcript")
    r = _video_post(client, did)  # no transcript_text
    assert r.status_code == 400
    assert not any(n.startswith("source") for n in os.listdir(db.drama_dir(did)))
    did2 = _drama()
    r = _video_post(client, did2, {"source_language": "xx"})
    assert r.status_code in (400, 422)
    assert not any(n.startswith("source") for n in os.listdir(db.drama_dir(did2)))


def test_video_missing_groq_key_503_before_storing(client, monkeypatch, fast_ffmpeg):
    import db
    from services import settings_service
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: None)
    did = _drama()
    db.update_drama(did, use_groq=1)
    r = _video_post(client, did)
    assert r.status_code == 503
    assert not any(n.startswith("source") for n in os.listdir(db.drama_dir(did)))


def test_video_chained_job_passes_through_child_failed_reason(client, monkeypatch, fast_ffmpeg):
    from services import transcribe_service
    did = _drama()
    tid = f"transcribe_{did}"
    background_jobs.clear_job(tid)

    def child():
        background_jobs.set_result(tid, {"failed_reason": "empty"})

    def run(drama_id, **opts):
        background_jobs.start_job(tid, child)
        return {"job_id": tid}
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", run)
    job = _wait_status(_video_post(client, did).json()["job_id"])
    assert job["status"] == "done"
    assert job["result"] == {"failed_reason": "empty"}


def test_audio_run_started_while_upload_claim_held(client, monkeypatch):
    from services import media_upload_service, transcribe_service
    did = _drama()
    seen = {}

    def run(drama_id, **opts):
        seen["claimed"] = drama_id in media_upload_service._claimed
        return {"job_id": f"transcribe_{drama_id}"}
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", run)
    r = _post(client, did)
    assert r.status_code == 200 and r.json()["job_id"] == f"transcribe_{did}"
    assert "transcribe_job_id" not in r.json()["upload"]
    assert seen["claimed"] is True
    assert did not in media_upload_service._claimed
