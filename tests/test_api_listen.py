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


def _narrated(db, en="hi", **fields):
    did = db.create_drama(title_en="N", content_mode="novel_narration", **fields)
    db.save_lines(did, [
        Line(idx=0, start=0, end=1, zh="你好", en=en, speaker="S1", flag="x", flag_note="n"),
        Line(idx=1, start=1, end=2, zh="再见", en=en, speaker="S2"),
    ])
    return did


def test_run_gives_each_speaker_its_own_designed_omnivoice_voice(client, isolated_db, started):
    did = _narrated(isolated_db)
    assert client.post(f"/api/listen/dramas/{did}/run", json={}).status_code == 200
    _, _, clone_map = started[0]["args"]
    assert {k: v["engine"] for k, v in clone_map.items()} == {"S1": "omnivoice", "S2": "omnivoice"}
    assert clone_map["S1"]["instruct"] != clone_map["S2"]["instruct"]
    assert started[0]["launch"]["gpu_touching"] is True


def test_on_done_applies_field_scoped(client, isolated_db, started):
    did = _narrated(isolated_db)
    client.post(f"/api/listen/dramas/{did}/run", json={})
    produced = isolated_db.load_line_objects(did)
    for ln in produced:
        ln.dub_filename = f"clip{ln.idx}.wav"
        ln.speaker = "WRONG"
    cur = isolated_db.load_line_objects(did)
    cur[0].flag_note = "edited"   # a user edit made while the job ran must survive
    isolated_db.save_lines(did, cur, fields=("flag_note",))
    started[0]["launch"]["on_done"](f"dub_{did}", {"lines": produced, "out_path": "x", "errors": []})
    rows = isolated_db.load_line_objects(did)
    assert [r.dub_filename for r in rows] == ["clip0.wav", "clip1.wav"]
    assert [r.speaker for r in rows] == ["S1", "S2"]
    assert rows[0].flag == "x" and rows[0].flag_note == "edited"
    assert isolated_db.get_drama(did)["status"] == "dubbed"


def test_a_run_already_going_is_409(client, isolated_db, started, monkeypatch):
    did = _narrated(isolated_db)
    monkeypatch.setattr(job_launch, "start_process_job", lambda *a, **k: False)
    assert client.post(f"/api/listen/dramas/{did}/run", json={}).status_code == 409


def test_run_of_an_unknown_title_is_404(client, started):
    assert client.post("/api/listen/dramas/999/run", json={}).status_code == 404


def test_run_with_no_translated_lines_is_refused(client, isolated_db, started):
    did = _narrated(isolated_db, en="")
    r = client.post(f"/api/listen/dramas/{did}/run", json={})
    assert r.status_code == 422 and started == []


def test_run_with_an_unknown_engine_is_refused(client, isolated_db, started):
    did = _narrated(isolated_db)
    assert client.post(f"/api/listen/dramas/{did}/run", json={"tts_engine": "nope"}).status_code == 422
    assert started == []


def test_a_character_stored_with_a_removed_engine_refuses_the_run_but_keeps_its_row(
        client, isolated_db, started):
    did = _narrated(isolated_db)
    isolated_db.upsert_character(did, "S1", character_name="Lin", clone_engine="f5tts",
                                 ref_audio_filename="ref.wav", ref_text="hi")
    r = client.post(f"/api/listen/dramas/{did}/run", json={})
    assert r.status_code == 422
    assert r.json()["error"]["message"] == "Lin: The F5-TTS engine was removed. Pick another voice engine."
    assert started == []
    row = next(c for c in isolated_db.list_characters(did) if c["speaker_label"] == "S1")
    assert (row["clone_engine"], row["ref_audio_filename"]) == ("f5tts", "ref.wav")
    isolated_db.upsert_character(did, "S1", clone_engine="omnivoice")
    assert client.post(f"/api/listen/dramas/{did}/run", json={}).status_code == 200


def test_a_missing_engine_package_is_503_without_a_path(client, isolated_db, started, monkeypatch):
    did = _narrated(isolated_db)
    monkeypatch.setattr(listen_service, "_missing_engine_dependency", lambda e: "missing")
    r = client.post(f"/api/listen/dramas/{did}/run", json={})
    assert r.status_code == 503 and "/" not in r.json()["error"]["message"]
    assert started == []
