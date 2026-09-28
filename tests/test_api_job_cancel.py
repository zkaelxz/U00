"""Migration Slice 22: cross-process job cancel. Fully mocked."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _own(job_id):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {"status": "running", "cancel_requested": False}


def _disown(job_id):
    with background_jobs._lock:
        background_jobs._jobs.pop(job_id, None)
    background_jobs._last_db_cancel_check.clear()


def test_unknown_404(client):
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_finished_409(client):
    db.save_job_record("j1", "done")
    assert client.post("/api/jobs/j1/cancel").status_code == 409


def test_cross_process_cancel_sets_flag(client):
    db.save_job_record("j2", "running")
    r = client.post("/api/jobs/j2/cancel")
    assert r.status_code == 200
    assert r.json() == {"job_id": "j2", "cancel_requested": True, "status": "running"}
    assert db.is_job_record_cancel_requested("j2")
    db.save_job_record("j2", "done")
    assert not db.is_job_record_cancel_requested("j2")


def test_owner_notices_db_flag_throttled(isolated_db, monkeypatch):
    _own("j3")
    calls = []
    real = db.is_job_record_cancel_requested
    monkeypatch.setattr(db, "is_job_record_cancel_requested",
                        lambda j: calls.append(j) or real(j))
    db.save_job_record("j3", "running")
    try:
        for _ in range(20):
            assert not background_jobs.is_cancel_requested("j3")
        assert len(calls) == 1
        db.request_job_record_cancel("j3")
        assert not background_jobs.is_cancel_requested("j3")  # throttled
        background_jobs._last_db_cancel_check.clear()
        assert background_jobs.is_cancel_requested("j3")
        assert background_jobs._jobs["j3"]["cancel_requested"] is True
    finally:
        _disown("j3")


def test_in_process_job_cancelled_immediately(client):
    _own("j4")
    db.save_job_record("j4", "running")
    try:
        assert client.post("/api/jobs/j4/cancel").status_code == 200
        assert background_jobs.is_cancel_requested("j4")
    finally:
        _disown("j4")
