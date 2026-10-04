"""
Unused voice clips in the Disk usage view: GET /api/data-usage/unused-voice-clips
and POST .../to-trash over services/disk_usage_service.py. A clip is offered
only when no speaker of its title points at it, no clip-reading job runs, and
it is one of the file kinds the app writes; responses carry no file name or
path, and every removal re-checks under the library hold.
"""

import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app
from services import disk_usage_service as dus

BASE = "/api/data-usage/unused-voice-clips"
HEX = "a" * 32
HEX2 = "b" * 32


def _client():
    app = create_app(ApiSettings(auth_mode="off", serve_frontend=False))
    return TestClient(app, base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                      raise_server_exceptions=False)


def _drama(title="Show"):
    return db.create_drama(title_en=title)


def _clip(did, name, size=100):
    path = os.path.join(db.drama_dir(did), "voice_refs", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)
    return path


def _point(did, label, rel):
    db.upsert_character(did, label, ref_audio_filename=rel)


def _ids(client):
    body = client.get(BASE).json()
    return body, [c for t in body["titles"] for c in t["clips"]]


@pytest.fixture
def c(isolated_db):
    return _client()


def test_referenced_and_unreferenced(c):
    did = _drama()
    used = _clip(did, f"clone_ref_{HEX}.wav")
    _clip(did, f"clone_pick_{HEX2}.wav", 250)
    _clip(did, "voicebank_3_Ann.mp3", 50)
    _point(did, "A", f"voice_refs/clone_ref_{HEX}.wav")
    body, clips = _ids(c)
    assert sorted(x["size_bytes"] for x in clips) == [50, 250]
    assert sorted(x["file_type"] for x in clips) == ["mp3", "wav"]
    assert body["total_bytes"] == 300 and body["total_count"] == 2
    assert body["titles"][0]["title"] == "Show"
    assert os.path.exists(used)


@pytest.mark.parametrize("stored", ["voice_refs\\clone_ref_{h}.wav", "CLONE_REF_{h}.WAV",
                                    "clone_ref_{h}.wav"])
def test_loose_stored_names_still_count_as_referenced(c, stored):
    did = _drama()
    _clip(did, f"clone_ref_{HEX}.wav")
    _point(did, "A", stored.format(h=HEX))
    assert _ids(c)[1] == []


def test_same_name_in_other_title_is_independent(c):
    a, b = _drama("A"), _drama("B")
    _clip(a, f"clone_ref_{HEX}.wav", 10)
    _clip(b, f"clone_ref_{HEX}.wav", 20)
    _point(a, "X", f"voice_refs/clone_ref_{HEX}.wav")
    body, clips = _ids(c)
    assert [t["title"] for t in body["titles"]] == ["B"] and clips[0]["size_bytes"] == 20


def test_only_known_kinds_directly_in_voice_refs(c):
    did = _drama()
    _clip(did, "notes.txt")
    _clip(did, "Ann.wav")                       # dub.extract_reference_clips output
    _clip(did, f"candidates/{HEX}.wav")
    _clip(did, f"clone_ref_{HEX}.txt")
    _clip(did, f"clone_pick_{HEX}.mp3")
    with open(os.path.join(db.drama_dir(did), f"clone_ref_{HEX}.wav"), "wb") as fh:
        fh.write(b"x")                          # right name, wrong folder
    assert _ids(c)[1] == []


def test_symlinked_clip_and_folder_are_ignored(c, tmp_path):
    did = _drama()
    outside = tmp_path / "x.wav"
    outside.write_bytes(b"secret")
    folder = os.path.join(db.drama_dir(did), "voice_refs")
    os.makedirs(folder)
    try:
        os.symlink(outside, os.path.join(folder, f"clone_ref_{HEX}.wav"))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert _ids(c)[1] == []


@pytest.mark.parametrize("job_id", ["dub_{d}", "narration_{d}", "audiobook_{d}"])
def test_running_job_hides_title_and_refuses_removal(c, job_id):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav")
    _, clips = _ids(c)
    cid = clips[0]["id"]
    background_jobs._jobs[job_id.format(d=did)] = {"status": "running"}
    try:
        body, clips = _ids(c)
        assert clips == [] and body["titles_in_use"] == 1
        r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": cid, "expected_size_bytes": 100}],
                                             "confirm": True})
        assert r.status_code == 409
        assert os.path.exists(path)
    finally:
        background_jobs._jobs.pop(job_id.format(d=did), None)


def test_job_record_from_another_process_skips_the_clip(c):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav")
    db.save_job_record(f"dub_{did}", "running")
    body, clips = _ids(c)
    assert clips == [] and body["titles_in_use"] == 1 and os.path.exists(path)


def test_move_to_trash_and_restore(c):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav", 321)
    _, clips = _ids(c)
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": clips[0]["id"],
                                                    "expected_size_bytes": 321}], "confirm": True})
    assert r.status_code == 200, r.text
    assert r.json() == {"moved_count": 1, "moved_bytes": 321, "skipped": []}
    assert not os.path.exists(path)
    assert _ids(c)[1] == []
    trash = c.get("/api/data-usage/trash").json()["items"]
    assert len(trash) == 1 and trash[0]["restorable"]
    r = c.post("/api/data-usage/trash/restore", json={"id": trash[0]["id"], "confirm": True})
    assert r.status_code == 200 and os.path.exists(path)


def test_confirm_and_sizes_required(c):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav")
    cid = _ids(c)[1][0]["id"]
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": cid, "expected_size_bytes": 100}]})
    assert r.status_code == 422
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": cid}], "confirm": True})
    assert r.status_code == 422
    r = c.post(f"{BASE}/to-trash", json={"clips": [], "confirm": True})
    assert r.status_code == 422
    assert os.path.exists(path)


def test_changed_size_is_skipped(c):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav", 100)
    cid = _ids(c)[1][0]["id"]
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": cid, "expected_size_bytes": 99}],
                                         "confirm": True})
    assert r.status_code == 200
    assert r.json()["moved_count"] == 0 and r.json()["skipped"][0]["reason"] == "changed"
    assert os.path.exists(path)


def test_clip_picked_between_list_and_remove_is_kept(c):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav")
    cid = _ids(c)[1][0]["id"]
    _point(did, "A", f"voice_refs/clone_ref_{HEX}.wav")
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": cid, "expected_size_bytes": 100}],
                                         "confirm": True})
    assert r.status_code == 200
    assert r.json()["moved_count"] == 0
    assert r.json()["skipped"] == [{"id": cid, "reason": "no_longer_unused"}]
    assert os.path.exists(path)


def test_clip_picked_during_the_move_is_put_back(c, monkeypatch):
    did = _drama()
    path = _clip(did, f"clone_ref_{HEX}.wav")
    cid = _ids(c)[1][0]["id"]
    real = dus._rename

    def pick_then_rename(src, dst):
        real(src, dst)
        _point(did, "A", f"voice_refs/clone_ref_{HEX}.wav")
    monkeypatch.setattr(dus, "_rename", pick_then_rename)
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": cid, "expected_size_bytes": 100}],
                                         "confirm": True})
    assert r.status_code == 200 and r.json()["moved_count"] == 0
    assert os.path.exists(path)
    assert c.get("/api/data-usage/trash").json()["item_count"] == 0


def test_ids_are_not_guessable_or_portable(c):
    a, b = _drama("A"), _drama("B")
    pa = _clip(a, f"clone_ref_{HEX}.wav")
    pb = _clip(b, f"clone_ref_{HEX}.wav")
    body = c.get(BASE).json()
    ids = {t["title"]: t["clips"][0]["id"] for t in body["titles"]}
    assert ids["A"] != ids["B"]
    junk = ["../" * 3 + "x", "1", f"clone_ref_{HEX}.wav", "library/dramas/1/voice_refs/x.wav",
            "0" * 32, HEX]
    for bad in junk:
        r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": bad, "expected_size_bytes": 100}],
                                             "confirm": True})
        assert r.status_code in (200, 422), r.text
        assert r.json().get("moved_count", 0) == 0
    assert os.path.exists(pa) and os.path.exists(pb)
    # an id only removes the file of the title it was issued for
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": ids["A"], "expected_size_bytes": 100}],
                                         "confirm": True})
    assert r.json()["moved_count"] == 1
    assert not os.path.exists(pa) and os.path.exists(pb)


def test_ids_change_with_a_new_process_key(c, monkeypatch):
    did = _drama()
    _clip(did, f"clone_ref_{HEX}.wav")
    old = _ids(c)[1][0]["id"]
    monkeypatch.setattr(dus, "_CLIP_KEY", b"k" * 32)
    r = c.post(f"{BASE}/to-trash", json={"clips": [{"id": old, "expected_size_bytes": 100}],
                                         "confirm": True})
    assert r.json()["skipped"][0]["reason"] == "no_longer_unused"


def test_responses_name_no_file_or_path(c, tmp_path):
    did = _drama("Show")
    _clip(did, "voicebank_3_Secret Ann.mp3")
    _clip(did, f"clone_pick_{HEX2}.wav")
    texts = [c.get(BASE).text]
    clips = _ids(c)[1]
    r = c.post(f"{BASE}/to-trash", json={
        "clips": [{"id": x["id"], "expected_size_bytes": x["size_bytes"]} for x in clips]
                 + [{"id": HEX, "expected_size_bytes": 1}], "confirm": True})
    texts.append(r.text)
    for t in texts:
        for leak in ("Secret", "voicebank", "clone_pick", "clone_ref", "voice_refs",
                     os.path.dirname(db.LIBRARY_DIR), db.LIBRARY_DIR):
            assert leak not in t


def test_empty_library_and_remote_refusal(c):
    assert c.get(BASE).json()["total_count"] == 0
    remote = TestClient(create_app(ApiSettings(auth_mode="off", serve_frontend=False)),
                        base_url="https://baihe.example.com", client=("203.0.113.5", 5000),
                        raise_server_exceptions=False)
    assert remote.get(BASE).status_code in (401, 403, 404)
    assert remote.post(f"{BASE}/to-trash", json={"clips": [], "confirm": True}).status_code in (
        401, 403, 404, 422)
