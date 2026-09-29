"""
Tests for the drama create/update endpoints (Migration Slice 35):
POST /api/dramas and POST /api/dramas/{id}/metadata. FastAPI TestClient
against an `isolated_db` library -- no server process, no network.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient's transport

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


def _count():
    return len(db.list_dramas())


def test_create_returns_201_and_is_retrievable(client):
    resp = client.post("/api/dramas", json={"source_language": "zh", "title_en": "Hello",
                                            "media_type": "anime"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title_en"] == "Hello" and body["media_type"] == "anime"
    assert body["source_language"] == "zh" and body["preset_defaults"] is None
    got = client.get(f"/api/library/dramas/{body['id']}")
    assert got.status_code == 200 and got.json()["title_en"] == "Hello"


def test_create_missing_source_language_422(client):
    resp = client.post("/api/dramas", json={"title_en": "x"})
    assert resp.status_code == 422
    _error(resp)
    assert _count() == 0


def test_create_invalid_language_422(client):
    resp = client.post("/api/dramas", json={"source_language": "fr"})
    assert resp.status_code == 422
    _error(resp)
    assert _count() == 0


def test_create_series_id_and_new_name_422(client):
    sid = db.get_or_create_series("S")
    resp = client.post("/api/dramas", json={"source_language": "zh", "series_id": sid,
                                            "new_series_name": "X"})
    assert resp.status_code == 422
    _error(resp)
    assert _count() == 0


def test_create_unknown_series_and_preset_404(client):
    r1 = client.post("/api/dramas", json={"source_language": "zh", "series_id": 999})
    assert r1.status_code == 404 and _error(r1)["code"] == "not_found"
    r2 = client.post("/api/dramas", json={"source_language": "zh", "preset_id": 999})
    assert r2.status_code == 404 and _error(r2)["code"] == "not_found"
    assert _count() == 0


def test_create_unknown_key_422(client):
    resp = client.post("/api/dramas", json={"source_language": "zh", "status": "exported"})
    assert resp.status_code == 422
    assert _count() == 0


def test_create_with_new_series(client):
    resp = client.post("/api/dramas", json={"source_language": "ja", "new_series_name": "Saga"})
    assert resp.status_code == 201
    assert [s["name"] for s in db.list_series()] == ["Saga"]
    assert resp.json()["series_id"] == db.list_series()[0]["id"]


def test_create_with_preset_returns_exactly_five_defaults(client):
    pid = db.save_preset("P", translation_engine="gemini", engine_model="m",
                         style_preset="casual", locale="en-GB",
                         default_female_pronouns=True, include_genre_notes=False)
    resp = client.post("/api/dramas", json={"source_language": "zh", "preset_id": pid})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["preset_defaults"] == {"style_preset": "casual", "locale": "en-GB",
                                       "default_female_pronouns": True,
                                       "include_genre_notes": False,
                                       "engine_model": "m"}
    assert body["translation_engine"] == "gemini"


def _make(client, **kw):
    return client.post("/api/dramas", json={"source_language": "zh", **kw}).json()["id"]


def test_update_partial_leaves_others_intact(client):
    did = _make(client, title_en="Keep", author="Au")
    resp = client.post(f"/api/dramas/{did}/metadata", json={"studio": "Studio X",
                                                            "chapter_count": 12})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["studio"] == "Studio X" and body["chapter_count"] == 12
    assert body["title_en"] == "Keep" and body["author"] == "Au"
    # An omitted field is "not passed": a second partial update keeps the first.
    body = client.post(f"/api/dramas/{did}/metadata", json={"summary": "S"}).json()
    assert body["studio"] == "Studio X" and body["summary"] == "S"


def test_update_empty_body_is_noop(client):
    did = _make(client, title_en="Keep")
    resp = client.post(f"/api/dramas/{did}/metadata", json={})
    assert resp.status_code == 200 and resp.json()["title_en"] == "Keep"


@pytest.mark.parametrize("field,value", [("status", "exported"),
                                         ("content_mode", "novel_narration"),
                                         ("audio_filename", "x.mp3"),
                                         ("source_language", "ja")])
def test_update_non_whitelisted_field_422_and_row_untouched(client, field, value):
    did = _make(client, title_en="Keep")
    before = db.get_drama(did)
    resp = client.post(f"/api/dramas/{did}/metadata", json={"title_en": "Changed", field: value})
    assert resp.status_code == 422
    _error(resp)
    assert db.get_drama(did) == before


def test_service_own_check_defence_in_depth(isolated_db):
    from services import drama_service
    from services.service_errors import InvalidInputError
    did = db.create_drama(source_language="zh", title_en="Keep")
    before = db.get_drama(did)
    with pytest.raises(InvalidInputError):
        drama_service.update_drama_metadata(did, status="exported")
    assert db.get_drama(did) == before


def test_update_bad_values_422(client):
    did = _make(client)
    assert client.post(f"/api/dramas/{did}/metadata",
                       json={"media_type": "bogus"}).status_code == 422
    assert client.post(f"/api/dramas/{did}/metadata",
                       json={"chapter_count": -1}).status_code == 422
    assert client.post(f"/api/dramas/{did}/metadata",
                       json={"title_en": 5}).status_code == 422


def test_update_unknown_drama_and_series_404(client):
    resp = client.post("/api/dramas/999/metadata", json={"title_en": "x"})
    assert resp.status_code == 404 and _error(resp)["code"] == "not_found"
    did = _make(client)
    resp = client.post(f"/api/dramas/{did}/metadata", json={"series_id": 999})
    assert resp.status_code == 404


def test_update_invalid_drama_id_422(client):
    assert client.post("/api/dramas/0/metadata", json={}).status_code == 422


def test_no_filesystem_path_in_responses(client, isolated_db):
    lib = str(isolated_db.LIBRARY_DIR)
    r1 = client.post("/api/dramas", json={"source_language": "zh"})
    did = r1.json()["id"]
    r2 = client.post(f"/api/dramas/{did}/metadata", json={"title_en": "x"})
    r3 = client.post("/api/dramas/999/metadata", json={})
    r4 = client.post("/api/dramas", json={"source_language": "xx"})
    for r in (r1, r2, r3, r4):
        assert lib not in r.text
    assert "filename" not in r1.text and "filename" not in r2.text


# --- Hardening H1 -----------------------------------------------------------

def test_h1_oversized_ints_are_422_never_500(client):
    big = 10**30
    for body in ({"source_language": "zh", "series_id": big},
                 {"source_language": "zh", "preset_id": big}):
        assert client.post("/api/dramas", json=body).status_code == 422
    did = client.post("/api/dramas", json={"source_language": "zh"}).json()["id"]
    for f in ("chapter_count", "episode_number", "series_id"):
        assert client.post(f"/api/dramas/{did}/metadata", json={f: big}).status_code == 422
    assert client.post(f"/api/dramas/{big}/metadata", json={"title_en": "x"}).status_code == 422


def test_h1_caps_and_url_scheme(client):
    assert client.post("/api/dramas", json={"source_language": "zh",
                                            "title_en": "a" * 301}).status_code == 422
    did = client.post("/api/dramas", json={"source_language": "zh"}).json()["id"]
    r = client.post(f"/api/dramas/{did}/metadata", json={"source_url": "javascript:alert(1)"})
    assert r.status_code == 422 and "javascript" not in r.text
    r = client.post(f"/api/dramas/{did}/metadata", json={"summary": "a" * 5001})
    assert r.status_code == 422
    r = client.post(f"/api/dramas/{did}/metadata", json={"source_url": "https://e.example"})
    assert r.status_code == 200

# ---- Slice 36: DELETE /api/dramas/{id} -----------------------------------
import os

import background_jobs


def _doomed():
    did = db.create_drama(title_en="Doomed", source_language="zh")
    refs = os.path.join(db.drama_dir(did), "voice_refs")
    os.makedirs(refs)
    with open(os.path.join(refs, "x"), "wb") as f:
        f.write(b"clip")
    return did, db.drama_dir(did)


def test_delete_unknown_404_without_confirm(client):
    resp = client.delete("/api/dramas/999")
    assert resp.status_code == 404
    assert _error(resp)["code"] == "not_found"


@pytest.mark.parametrize("qs", ["", "?confirm=true", "?confirm_text=DELETE",
                                "?confirm=true&confirm_text=delete",
                                "?confirm=true&confirm_text=DELETE%20",
                                "?confirm=true&confirm_text=",
                                "?confirm=false&confirm_text=DELETE"])
def test_delete_bad_confirmation_422(client, qs):
    did, folder = _doomed()
    resp = client.delete(f"/api/dramas/{did}{qs}")
    assert resp.status_code == 422
    _error(resp)
    assert db.get_drama(did) is not None
    assert os.path.isfile(os.path.join(folder, "voice_refs", "x"))


def test_delete_ok(client):
    did, folder = _doomed()
    resp = client.delete(f"/api/dramas/{did}?confirm=true&confirm_text=DELETE")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": True, "drama_id": did}
    assert db.get_drama(did) is None and not os.path.exists(folder)


def test_delete_running_job_409(client, monkeypatch):
    did, folder = _doomed()
    monkeypatch.setattr(background_jobs, "any_job_running_for_drama", lambda _id: True)
    resp = client.delete(f"/api/dramas/{did}?confirm=true&confirm_text=DELETE")
    assert resp.status_code == 409
    assert _error(resp)["code"] == "conflict"
    assert db.get_drama(did) is not None and os.path.isdir(folder)


def test_delete_fresh_job_record_409(client):
    did, folder = _doomed()
    db.save_job_record(f"dub_{did}", "queued")
    resp = client.delete(f"/api/dramas/{did}?confirm=true&confirm_text=DELETE")
    assert resp.status_code == 409
    assert db.get_drama(did) is not None and os.path.isdir(folder)
