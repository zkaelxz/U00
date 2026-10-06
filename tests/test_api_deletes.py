"""PC-only delete routes (migration handoff "Next queue" item 2):
services/delete_service.py + api/routers/delete_routes.py. Mocked; no
network, models or real jobs."""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service

YES = {"confirm": True}


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _drama(**kw):
    return db.create_drama(title_en="T", content_mode="audio_drama", **kw)


def _folder(did):
    return db.drama_dir(did)


def _media_drama():
    did = _drama()
    for name in ("audio.wav", "source.mp4"):
        with open(os.path.join(_folder(did), name), "wb") as f:
            f.write(b"x")
    db.update_drama(did, audio_filename="audio.wav", source_video_filename="source.mp4")
    return did


def _raw_novel_drama():
    did = _drama()
    with open(os.path.join(_folder(did), "raw_novel_context.txt"), "w", encoding="utf-8") as f:
        f.write("原文")
    return did


def _version(did, active=False):
    return db.save_translation_version(did, [], "v1", "google", "", make_active=active)


def _series_char():
    sid = db.get_or_create_series("S")
    db.upsert_series_character(sid, "Lan")
    cid = db.list_series_characters(sid)[0]["id"]
    return sid, cid


def _preset():
    return db.save_preset("P1", translation_engine="google")


def _voice(tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    return db.save_voice_bank_entry("V1", str(clip))


def _no_leak(body, *needles):
    text = str(body)
    assert db.LIBRARY_DIR not in text and os.sep + "dramas" not in text
    for n in needles:
        assert n not in text


def _code(r):
    return r.json()["error"]["code"]


# --- success ----------------------------------------------------------------

def test_remove_media(client):
    did = _media_drama()
    r = client.post(f"/api/media/dramas/{did}/remove", json=YES)
    assert r.status_code == 200, r.text
    assert r.json() == {"drama_id": did, "removed": True, "audio_file_removed": True,
                        "video_file_removed": True, "has_audio": False, "has_video": False}
    _no_leak(r.json(), "audio.wav", "source.mp4")
    d = db.get_drama(did)
    assert d["audio_filename"] is None and d["source_video_filename"] is None
    assert not os.listdir(_folder(did))


def test_remove_media_keeps_lines(client):
    did = _media_drama()
    import core
    db.save_lines(did, [core.Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hi")])
    assert client.post(f"/api/media/dramas/{did}/remove", json=YES).status_code == 200
    assert [ln["en"] for ln in db.load_lines(did)] == ["Hi"]


def test_remove_media_with_stored_path_outside_folder_is_not_followed(client, tmp_path):
    did = _drama()
    outside = tmp_path / "keep.wav"
    outside.write_bytes(b"x")
    db.update_drama(did, audio_filename=str(outside), source_video_filename="../../x.mp4")
    r = client.post(f"/api/media/dramas/{did}/remove", json=YES)
    assert r.status_code == 200
    assert r.json()["audio_file_removed"] is False and outside.exists()
    assert db.get_drama(did)["audio_filename"] is None
    _no_leak(r.json(), str(outside))


def test_remove_media_missing_file_still_clears_fields(client):
    did = _drama()
    db.update_drama(did, audio_filename="gone.wav")
    r = client.post(f"/api/media/dramas/{did}/remove", json=YES)
    assert r.status_code == 200 and r.json()["audio_file_removed"] is False
    assert db.get_drama(did)["audio_filename"] is None


def test_remove_raw_novel(client):
    did = _raw_novel_drama()
    r = client.post(f"/api/novel/dramas/{did}/raw-novel/remove", json=YES)
    assert r.status_code == 200
    assert r.json() == {"drama_id": did, "removed": True, "has_raw_novel_context": False}
    assert not os.path.exists(os.path.join(_folder(did), "raw_novel_context.txt"))
    _no_leak(r.json(), "raw_novel_context.txt")


def test_delete_version(client):
    did = _drama()
    keep = _version(did)
    vid = _version(did, active=True)
    r = client.post(f"/api/review/dramas/{did}/versions/{vid}/delete", json=YES)
    assert r.status_code == 200
    assert r.json() == {"drama_id": did, "version_id": vid, "deleted": True, "was_active": True}
    assert [v["id"] for v in db.list_translation_versions(did)] == [keep]


def test_delete_series_character_unlinks_drama_characters(client):
    sid, cid = _series_char()
    did = _drama(series_id=sid)
    db.upsert_character(did, "SPEAKER_00", character_name="Lan", series_character_id=cid)
    r = client.post(f"/api/characters/series/{sid}/characters/{cid}/delete", json=YES)
    assert r.status_code == 200
    assert r.json() == {"series_id": sid, "character_id": cid, "deleted": True}
    assert db.list_series_characters(sid) == []
    ch = db.list_characters_with_series_names(did)[0]
    assert ch["character_name"] == "Lan" and ch["series_character_id"] is None


def test_delete_preset(client):
    pid = _preset()
    r = client.post(f"/api/library/presets/{pid}/delete", json=YES)
    assert r.status_code == 200 and r.json() == {"preset_id": pid, "deleted": True}
    assert db.list_presets() == []


def test_delete_voice_bank_entry_removes_clip(client, tmp_path):
    eid = _voice(tmp_path)
    clip = os.path.join(db.VOICE_BANK_DIR, db.get_voice_bank_entry(eid)["clip_filename"])
    assert os.path.exists(clip)
    r = client.post(f"/api/library/voice-bank/{eid}/delete", json=YES)
    assert r.status_code == 200 and r.json() == {"entry_id": eid, "deleted": True}
    assert db.get_voice_bank_entry(eid) is None and not os.path.exists(clip)
    _no_leak(r.json(), os.path.basename(clip))


# --- 422: confirm missing / false / not strict --------------------------------

def _all_urls(tmp_path):
    did = _media_drama()
    rid = _raw_novel_drama()
    vid = _version(did)
    sid, cid = _series_char()
    return [f"/api/media/dramas/{did}/remove",
            f"/api/novel/dramas/{rid}/raw-novel/remove",
            f"/api/review/dramas/{did}/versions/{vid}/delete",
            f"/api/characters/series/{sid}/characters/{cid}/delete",
            f"/api/library/presets/{_preset()}/delete",
            f"/api/library/voice-bank/{_voice(tmp_path)}/delete"]


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1},
                                  {"confirm": True, "extra": 1}, None])
def test_missing_or_bad_confirm_is_422_and_nothing_changes(client, tmp_path, body):
    urls = _all_urls(tmp_path)
    before = (db.list_dramas(), db.list_translation_versions(1), db.list_presets(),
              db.list_voice_bank_entries())
    for url in urls:
        r = client.post(url, json=body) if body is not None else client.post(url)
        # A body-less POST is not JSON and has no X-Baihe-Local header, so
        # local_only's cross-site rule refuses it (403) before validation.
        assert r.status_code == (403 if body is None else 422), (url, r.text)
    assert (db.list_dramas(), db.list_translation_versions(1), db.list_presets(),
            db.list_voice_bank_entries()) == before
    assert os.path.exists(os.path.join(db.DRAMAS_DIR, "1", "audio.wav"))


# --- 404: unknown, foreign or nothing to remove ------------------------------

def test_unknown_ids_404(client):
    for url in ("/api/media/dramas/999/remove", "/api/novel/dramas/999/raw-novel/remove",
                "/api/review/dramas/999/versions/1/delete",
                "/api/characters/series/999/characters/1/delete",
                "/api/library/presets/999/delete",
                "/api/library/voice-bank/999/delete"):
        r = client.post(url, json=YES)
        assert r.status_code == 404, url
        assert _code(r) == "not_found"


def test_nothing_to_remove_404(client):
    did = _drama()
    assert client.post(f"/api/media/dramas/{did}/remove", json=YES).status_code == 404
    assert client.post(f"/api/novel/dramas/{did}/raw-novel/remove", json=YES).status_code == 404


def test_version_of_another_drama_is_404(client):
    a, b = _drama(), _drama()
    vid = _version(a)
    r = client.post(f"/api/review/dramas/{b}/versions/{vid}/delete", json=YES)
    assert r.status_code == 404
    assert [v["id"] for v in db.list_translation_versions(a)] == [vid]


def test_character_of_another_series_is_404(client):
    sid, cid = _series_char()
    other = db.get_or_create_series("Other")
    r = client.post(f"/api/characters/series/{other}/characters/{cid}/delete", json=YES)
    assert r.status_code == 404
    assert len(db.list_series_characters(sid)) == 1


@pytest.mark.parametrize("bad", ["0", "-1", str(2**31), str(2**63), "abc"])
def test_out_of_range_ids_422(client, bad):
    assert client.post(f"/api/library/presets/{bad}/delete", json=YES).status_code == 422
    assert client.post(f"/api/media/dramas/{bad}/remove", json=YES).status_code == 422


# --- 409: a job running for the drama -----------------------------------------

def test_running_job_refuses_media_novel_version(client, monkeypatch):
    did = _media_drama()
    with open(os.path.join(_folder(did), "raw_novel_context.txt"), "w") as f:
        f.write("x")
    vid = _version(did)
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: d == did)
    for url in (f"/api/media/dramas/{did}/remove", f"/api/novel/dramas/{did}/raw-novel/remove",
                f"/api/review/dramas/{did}/versions/{vid}/delete"):
        r = client.post(url, json=YES)
        assert r.status_code == 409, url
        assert _code(r) == "conflict"
    assert db.get_drama(did)["audio_filename"] == "audio.wav"
    assert os.path.exists(os.path.join(_folder(did), "raw_novel_context.txt"))
    assert [v["id"] for v in db.list_translation_versions(did)] == [vid]


def test_job_on_another_drama_does_not_block(client, monkeypatch):
    did = _media_drama()
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda d: d != did)
    assert client.post(f"/api/media/dramas/{did}/remove", json=YES).status_code == 200


# --- local_only: remote refused, loopback allowed (auth on) ------------------

def _auth_app():
    return create_app(ApiSettings(auth_mode="on"))


def _admin_headers():
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_remote_refused_even_for_admin(isolated_db, tmp_path):
    urls = _all_urls(tmp_path)
    remote = TestClient(_auth_app(), base_url="https://baihe.example.com",
                        raise_server_exceptions=False)
    h = _admin_headers()
    for url in urls:
        assert remote.post(url, json=YES, headers=h).status_code == 403, url
        assert remote.post(url, json=YES).status_code in (401, 403), url
    # proxied loopback is not local either
    local = TestClient(_auth_app(), base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    for url in urls:
        assert local.post(url, json=YES, headers={"X-Forwarded-For": "1.2.3.4"}
                          ).status_code == 403, url
    assert db.get_drama(1)["audio_filename"] == "audio.wav"
    assert len(db.list_presets()) == 1 and len(db.list_voice_bank_entries()) == 1


def test_direct_loopback_allowed_with_auth_on(isolated_db, tmp_path):
    urls = _all_urls(tmp_path)
    local = TestClient(_auth_app(), base_url="http://127.0.0.1:8600", client=("127.0.0.1", 5000),
                       raise_server_exceptions=False)
    for url in urls:
        r = local.post(url, json=YES)
        assert r.status_code == 200, (url, r.text)
        _no_leak(r.json())
    assert db.list_presets() == [] and db.list_voice_bank_entries() == []


# --- job history (local_only, confirm) ----------------------------------------

def test_delete_job_routes(client):
    db.save_job_record("fin", status="done")
    db.save_job_record("run", status="running")
    assert client.post("/api/jobs/fin/delete", json={"confirm": False}).status_code == 422
    assert client.post("/api/jobs/run/delete", json=YES).status_code == 409
    assert client.post("/api/jobs/nope/delete", json=YES).status_code == 404
    assert client.post("/api/jobs/fin/delete", json=YES).json() == {"job_id": "fin", "deleted": True}
    assert db.get_job_record("fin") is None and db.get_job_record("run") is not None


def test_clear_finished_route_keeps_active(client):
    for jid, st in (("a", "done"), ("b", "error"), ("c", "queued")):
        db.save_job_record(jid, status=st)
    assert client.post("/api/jobs/clear-finished", json={}).status_code == 422
    assert client.post("/api/jobs/clear-finished", json=YES).json() == {"deleted_count": 2}
    assert [r["job_id"] for r in db.list_job_records()] == ["c"]


def test_job_deletes_refuse_a_proxied_request(client):
    db.save_job_record("fin", status="done")
    h = {"X-Forwarded-For": "203.0.113.5"}
    assert client.post("/api/jobs/fin/delete", json=YES, headers=h).status_code == 403
    assert client.post("/api/jobs/clear-finished", json=YES, headers=h).status_code == 403
    assert db.get_job_record("fin") is not None
