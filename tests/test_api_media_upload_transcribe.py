"""Tests for Migration Slice 32: upload-and-transcribe + media status. Fully mocked."""
import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("multipart")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app
from tests.test_transcribe_service import _capture_worker_start


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
    captured = _capture_worker_start(monkeypatch)
    r = _post(client, did, {"initial_prompt": "names", "source_language": "ja"})
    assert r.status_code == 200
    assert r.json() == {"upload": {"name": "source.mp3", "size": 9, "kind": "audio", "job_id": None},
                        "job_id": f"transcribe_{did}"}
    assert captured["use_gpu"] is True
    assert captured["initial_prompt"] == "names"
    assert captured["source_language"] == "ja"


def test_upload_passes_extra_names(client, monkeypatch):
    import db
    sid = db.get_or_create_series("S")
    db.upsert_glossary_term(sid, "苏杉", "Su Shan")
    did = _drama(series_id=sid)
    captured = _capture_worker_start(monkeypatch)
    r = _post(client, did, {"extra_names": "沈清疑"})
    assert r.status_code == 200, r.text
    assert captured["initial_prompt"] == "苏杉、沈清疑。"
    r = _post(client, did, {"extra_names": "x" * 1001})
    assert r.status_code in (400, 422)


def test_run_failure_keeps_upload(client, monkeypatch):
    import db
    did = _drama()
    _capture_worker_start(monkeypatch, started=False)
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
                        "reads_burned_in_subtitles": False, "upload_max_mb": 5,
                        "kept_media_files": 0, "kept_media_bytes": 0}
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
        seen["claimed"] = drama_id in media_upload_service.claimed
        return {"job_id": f"transcribe_{drama_id}"}
    monkeypatch.setattr(transcribe_service, "start_transcribe_run", run)
    r = _post(client, did)
    assert r.status_code == 200 and r.json()["job_id"] == f"transcribe_{did}"
    assert "transcribe_job_id" not in r.json()["upload"]
    assert seen["claimed"] is True
    assert did not in media_upload_service.claimed


def test_upload_passes_the_speaker_range(client, monkeypatch):
    """Step 105: Min/Max speakers reach the chained speaker detection here too."""
    did = _drama()
    captured = _capture_worker_start(monkeypatch)
    r = _post(client, did, {"run_diarize": "true", "min_speakers": "2", "max_speakers": "4"})
    assert r.status_code == 200, r.text
    assert (captured["min_speakers"], captured["max_speakers"]) == (2, 4)


def test_upload_refuses_a_bad_speaker_range_and_stores_nothing(client, monkeypatch):
    import db
    did = _drama()
    monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: True)
    r = _post(client, did, {"run_diarize": "true", "min_speakers": "5", "max_speakers": "2"})
    assert r.status_code == 422, r.text
    assert not db.get_drama(did).get("audio_filename")


def test_replacing_audio_needs_confirm(client, monkeypatch):
    import db
    did = _drama()
    _capture_worker_start(monkeypatch)
    assert _post(client, did).status_code == 200
    r = _post(client, did)
    assert r.status_code == 422 and r.json()["error"]["details"]["reason"] == "confirm_replace_audio"
    r = _post(client, did, {"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    assert os.listdir(os.path.join(db.drama_dir(did), "kept_media"))


def test_audio_over_a_hardsub_ocr_video_switches_the_mode_and_the_run_starts(client, monkeypatch):
    import db
    did = db.create_drama(title_en="D", transcript_mode="hardsub_ocr")
    ddir = db.drama_dir(did)
    for name in ("source.mp4", "audio.wav"):
        with open(os.path.join(ddir, name), "wb") as f:
            f.write(b"old")
    db.update_drama(did, source_video_filename="source.mp4", audio_filename="audio.wav")
    captured = _capture_worker_start(monkeypatch)
    r = _post(client, did, {"confirm_replace_audio": "true"})
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == f"transcribe_{did}"
    d = db.get_drama(did)
    assert (d["source_video_filename"], d["transcript_mode"]) == (None, "whisper")
    assert captured
    assert "kept_media" in os.listdir(ddir)


def test_follow_job_stops_a_child_that_never_ends_and_errors_plainly(isolated_db, monkeypatch):
    from services import media_upload_service as mus
    stopped = []
    monkeypatch.setattr(mus.background_jobs, "get_status",
                        lambda cid: {"status": "running", "progress": 0.5, "message": "x"})
    monkeypatch.setattr(mus.background_jobs, "is_cancel_requested", lambda jid: False)
    monkeypatch.setattr(mus.background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(mus.background_jobs, "cancel_queued", lambda cid: False)
    monkeypatch.setattr(mus.background_jobs, "request_cancel", stopped.append)
    monkeypatch.setattr(mus, "_FOLLOW_POLL_SECONDS", 0.01)
    started = time.monotonic()
    with pytest.raises(RuntimeError) as err:
        mus._follow_job("extract_audio_1", "transcribe_1", 0.1)
    assert time.monotonic() - started < 5
    assert str(err.value) == mus.FOLLOW_TIMEOUT_MESSAGE
    assert stopped == ["transcribe_1"]


def test_follow_job_clock_starts_when_the_child_runs_not_while_queued(isolated_db, monkeypatch):
    from services import media_upload_service as mus
    started = time.monotonic()
    stopped, results = [], []

    def status(cid):
        elapsed = time.monotonic() - started
        if elapsed < 0.4:   # queued for four times the deadline
            return {"status": "queued"}
        if elapsed < 0.45:
            return {"status": "running", "progress": 0.5, "message": "x"}
        return {"status": "done", "result": {"ok": 1}}
    monkeypatch.setattr(mus.background_jobs, "get_status", status)
    monkeypatch.setattr(mus.background_jobs, "is_cancel_requested", lambda jid: False)
    monkeypatch.setattr(mus.background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(mus.background_jobs, "cancel_queued", lambda cid: False)
    monkeypatch.setattr(mus.background_jobs, "request_cancel", stopped.append)
    monkeypatch.setattr(mus.background_jobs, "set_result", lambda jid, r: results.append(r))
    monkeypatch.setattr(mus, "_FOLLOW_POLL_SECONDS", 0.01)
    mus._follow_job("extract_audio_1", "transcribe_1", 0.1)
    assert stopped == [] and results == [{"ok": 1}]


def test_follow_deadline_has_a_floor_and_grows_with_the_media():
    from services import media_upload_service as mus
    assert mus.follow_deadline_seconds(None) == mus.follow_deadline_seconds(60) == 2 * 60 * 60
    assert mus.follow_deadline_seconds(10 * 3600) == 50 * 3600
