"""The Reader's Listen routes: only a narrated novel has them, and a run is the
narration job. The process job is faked so no TTS, GPU or subprocess runs."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import background_jobs
import dub_narration
from core import Line
from jobs import job_launch
from services import listen_service


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(listen_service, "_missing_engine_dependency", lambda e: None)
    monkeypatch.setattr(background_jobs, "get_status", lambda j: None)

    def fake_start(job_id, target, args=(), **launch):
        calls.append(dict(job_id=job_id, target=target, args=args, launch=launch))
        return True
    monkeypatch.setattr(job_launch, "start_process_job", fake_start)
    return calls


def _drama(db, **fields):
    did = db.create_drama(title_en="N", **fields)
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="Hello", speaker="S1")])
    return did


def test_config_of_a_narrated_novel(client, isolated_db):
    did = _drama(isolated_db, content_mode="novel_narration")
    body = client.get(f"/api/listen/dramas/{did}/config").json()
    assert body["drama_id"] == did
    assert body["speakable_line_count"] == 1
    assert body["track_available"] is False
    assert "speakers" not in body and "defaults" not in body


def test_a_title_that_is_not_a_narrated_novel_has_no_listen(client, isolated_db):
    did = _drama(isolated_db)
    assert client.get(f"/api/listen/dramas/{did}/config").status_code == 404
    assert client.post(f"/api/listen/dramas/{did}/run", json={}).status_code == 404
    assert client.get(f"/api/listen/dramas/{did}/track").status_code == 404


def test_run_starts_the_narration_job_in_the_chosen_language(client, isolated_db, started):
    did = _drama(isolated_db, content_mode="novel_narration")
    r = client.post(f"/api/listen/dramas/{did}/run", json={"narration_language": "original"})
    assert r.status_code == 200, r.text
    assert r.json() == {"job_id": f"dub_{did}"}
    assert started[0]["launch"]["line_fields"] == ("dub_filename", "start", "end")
    assert started[0]["target"].func is dub_narration.build_narration_subprocess_worker
    assert started[0]["target"].keywords["narrate_original"] is True


def test_run_rejects_pacing_and_background_fields(client, isolated_db, started):
    did = _drama(isolated_db, content_mode="novel_narration")
    r = client.post(f"/api/listen/dramas/{did}/run", json={"max_speedup": 1.5})
    assert r.status_code == 422
    assert started == []


def test_track_is_404_until_it_exists_then_streams(client, isolated_db):
    import os
    did = _drama(isolated_db, content_mode="novel_narration")
    assert client.get(f"/api/listen/dramas/{did}/track").status_code == 404
    with open(os.path.join(isolated_db.drama_dir(did), "narration_track.wav"), "wb") as f:
        f.write(b"RIFFxxxxWAVE")
    r = client.get(f"/api/listen/dramas/{did}/track")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
