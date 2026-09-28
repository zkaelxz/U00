"""Tests for POST /api/dub/dramas/{id}/run (Migration Slice 26). The process
job is faked: start_process_job is captured so no subprocess, TTS, GPU or
network is used; the on_done hook is called by hand."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import dub_service, settings_service


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: None)
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(dub_service, "_missing_engine_dependency", lambda e: None)
    monkeypatch.setattr(background_jobs, "get_status", lambda j: None)

    def fake_start(job_id, target, args=(), gpu_touching=False, description=None, on_done=None):
        calls.append(dict(job_id=job_id, args=args, gpu=gpu_touching, on_done=on_done))
        return True
    monkeypatch.setattr(background_jobs, "start_process_job", fake_start)
    return calls


def _seed(db, en="hi", **fields):
    fields.setdefault("title_en", "D")
    did = db.create_drama(**fields)
    db.save_lines(did, [
        Line(idx=0, start=0, end=1, zh="你好", en=en, speaker="S1", flag="x", flag_note="n"),
        Line(idx=1, start=1, end=2, zh="再见", en=en, speaker="S2"),
    ])
    return did


def test_start_ok_and_args(client, isolated_db, started):
    did = _seed(isolated_db)
    r = client.post(f"/api/dub/dramas/{did}/run", json={"max_speedup": 1.5})
    assert r.status_code == 200
    assert r.json() == {"job_id": f"dub_{did}"}
    call = started[0]
    assert call["args"][5] == "edge_tts" and call["args"][8] == 1.5
    assert call["gpu"] is False


def test_on_done_applies_field_scoped(client, isolated_db, started):
    did = _seed(isolated_db)
    client.post(f"/api/dub/dramas/{did}/run", json={})
    produced = isolated_db.load_line_objects(did)
    for ln in produced:
        ln.dub_filename = f"clip{ln.idx}.wav"
        ln.speaker = "WRONG"
    # a user edit made while the job ran must survive
    cur = isolated_db.load_line_objects(did)
    cur[0].flag_note = "edited"
    isolated_db.save_lines(did, cur, fields=("flag_note",))
    started[0]["on_done"](f"dub_{did}", {"lines": produced, "out_path": "x", "errors": []})
    rows = isolated_db.load_line_objects(did)
    assert [r.dub_filename for r in rows] == ["clip0.wav", "clip1.wav"]
    assert [r.speaker for r in rows] == ["S1", "S2"]
    assert rows[0].flag == "x" and rows[0].flag_note == "edited"
    assert isolated_db.get_drama(did)["status"] == "dubbed"


def test_duplicate_409(client, isolated_db, started, monkeypatch):
    did = _seed(isolated_db)
    monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: False)
    r = client.post(f"/api/dub/dramas/{did}/run", json={})
    assert r.status_code == 409


def test_unknown_drama_404(client, started):
    assert client.post("/api/dub/dramas/999/run", json={}).status_code == 404


def test_no_translation_400(client, isolated_db, started):
    did = _seed(isolated_db, en="")
    r = client.post(f"/api/dub/dramas/{did}/run", json={})
    assert r.status_code == 422 and started == []


def test_bad_engine_400(client, isolated_db, started):
    did = _seed(isolated_db)
    assert client.post(f"/api/dub/dramas/{did}/run", json={"tts_engine": "nope"}).status_code == 422


def test_engine_unavailable_503(client, isolated_db, started, monkeypatch):
    did = _seed(isolated_db)
    monkeypatch.setattr(dub_service, "_missing_engine_dependency", lambda e: "missing")
    r = client.post(f"/api/dub/dramas/{did}/run", json={})
    assert r.status_code == 503 and "/" not in r.json()["error"]["message"]
    assert started == []
