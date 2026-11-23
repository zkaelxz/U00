"""
Tests for the drama create/update endpoints (Migration Slice 35):
POST /api/dramas and POST /api/dramas/{id}/metadata. FastAPI TestClient
against an `isolated_db` library -- no server process, no network.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient's transport


import db
from api.api_config import ApiSettings
from api.server import create_app


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
    resp = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T", "series_id": sid,
                                            "new_series_name": "X"})
    assert resp.status_code == 422
    _error(resp)
    assert _count() == 0


def test_create_unknown_series_and_preset_404(client):
    r1 = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T", "series_id": 999})
    assert r1.status_code == 404 and _error(r1)["code"] == "not_found"
    r2 = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T", "preset_id": 999})
    assert r2.status_code == 404 and _error(r2)["code"] == "not_found"
    assert _count() == 0


def test_create_unknown_key_422(client):
    resp = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T", "status": "exported"})
    assert resp.status_code == 422
    assert _count() == 0


def test_create_with_new_series(client):
    resp = client.post("/api/dramas", json={"source_language": "ja", "title_en": "T", "new_series_name": "Saga"})
    assert resp.status_code == 201
    assert [s["name"] for s in db.list_series()] == ["Saga"]
    assert resp.json()["series_id"] == db.list_series()[0]["id"]


def test_create_with_preset_returns_exactly_five_defaults(client):
    pid = db.save_preset("P", translation_engine="gemini", engine_model="m",
                         style_preset="casual", locale="en-GB",
                         default_female_pronouns=True, include_genre_notes=False)
    resp = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T", "preset_id": pid})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["preset_defaults"] == {"style_preset": "casual", "locale": "en-GB",
                                       "default_female_pronouns": True,
                                       "include_genre_notes": False,
                                       "engine_model": "m"}
    assert body["translation_engine"] == "gemini"


def _make(client, **kw):
    return client.post("/api/dramas", json={"source_language": "zh", "title_en": "T", **kw}).json()["id"]


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
    from lib.errors import InvalidInputError
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
    r1 = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"})
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
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    for f in ("chapter_count", "episode_number", "series_id"):
        assert client.post(f"/api/dramas/{did}/metadata", json={f: big}).status_code == 422
    assert client.post(f"/api/dramas/{big}/metadata", json={"title_en": "x"}).status_code == 422


def test_h1_caps_and_url_scheme(client):
    assert client.post("/api/dramas", json={"source_language": "zh",
                                            "title_en": "a" * 301}).status_code == 422
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
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


def test_metadata_new_series_name_and_unassign(client):
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    r = client.post(f"/api/dramas/{did}/metadata", json={"new_series_name": "Saga"})
    assert r.status_code == 200, r.text
    sid = db.list_series()[0]["id"]
    assert r.json()["series_id"] == sid
    r = client.post(f"/api/dramas/{did}/metadata", json={"series_id": 0})
    assert r.status_code == 200 and r.json()["series_id"] is None
    r = client.post(f"/api/dramas/{did}/metadata",
                    json={"series_id": sid, "new_series_name": "Other"})
    assert r.status_code == 422 and "not both" in _error(r)["message"]
    assert client.post(f"/api/dramas/{did}/metadata",
                       json={"new_series_name": "x" * 301}).status_code == 422
    assert client.post(f"/api/dramas/{did}/metadata",
                       json={"series_id": -1}).status_code == 422
    assert [s["name"] for s in db.list_series()] == ["Saga"]


def test_metadata_p10_fields_round_trip_through_the_detail(client):
    # Parity P10: the Workspace's Edit details reads these back to show them.
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    body = {"genre": "xianxia", "publication_status": "ongoing", "chapter_count": 120,
            "episode_number": 3, "source_url": "https://example.com/d/1",
            "episode_summary": "Key events.", "episode_brief": "Lin is the boss."}
    r = client.post(f"/api/dramas/{did}/metadata", json=body)
    assert r.status_code == 200, r.text
    assert {k: r.json()[k] for k in body} == body
    got = client.get(f"/api/library/dramas/{did}").json()
    assert {k: got[k] for k in body} == body
    r = client.post(f"/api/dramas/{did}/metadata", json={"chapter_count": 0, "episode_number": 0,
                                                         "source_url": "", "episode_summary": "",
                                                         "episode_brief": ""})
    assert r.status_code == 200, r.text
    assert (r.json()["chapter_count"], r.json()["episode_number"]) == (None, None)
    assert r.json()["source_url"] is None and r.json()["episode_summary"] == ""


def test_detail_source_url_never_returns_a_query_or_userinfo(client):
    # URL download stores the pasted link as given; it can carry a token.
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    db.update_drama(did, source_url="https://u:p@cdn.example.com:8443/v/1.mp4?X-Amz-Signature=abc&token=t#f")
    for body in (client.get(f"/api/library/dramas/{did}").json(),
                 client.post(f"/api/dramas/{did}/metadata", json={"genre": "x"}).json()):
        assert body["source_url"] == "https://cdn.example.com:8443/v/1.mp4"
    db.update_drama(did, source_url="example.com/novel")   # old free text: not a URL
    assert client.get(f"/api/library/dramas/{did}").json()["source_url"] is None



@pytest.mark.parametrize("stored,shown", [
    ("https://host/v.mp4;jsessionid=ABC", "https://host/v.mp4"),
    ("https://api.telegram.org/file/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/videos/f.mp4",
     "https://api.telegram.org/"),
    ("https://customer-x.cloudflarestream.com/eyJhbGciOiJSUzI1NiIsImtpZCI6IjEyMyJ9abcdefgh/downloads/d.mp4",
     "https://customer-x.cloudflarestream.com/"),
    ("https://[2001:db8::1]:8443/p", "https://[2001:db8::1]:8443/p"),
    ("file://localhost/C:/Users/me/Videos/ep1.mp4", None),
    ("javascript://host/%0Aalert(1)", None),
    ("https://www.example.com/drama/tgcf-123", "https://www.example.com/drama/tgcf-123"),
])
def test_detail_source_url_drops_path_tokens_and_non_http(client, stored, shown):
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    db.update_drama(did, source_url=stored)
    assert client.get(f"/api/library/dramas/{did}").json()["source_url"] == shown


def test_translate_toggles_are_saved_through_the_metadata_update(client):
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    assert db.get_drama(did)["default_female_pronouns"] is None
    r = client.post(f"/api/dramas/{did}/metadata",
                    json={"default_female_pronouns": True, "include_genre_notes": False})
    assert r.status_code == 200, r.text
    row = db.get_drama(did)
    assert (row["default_female_pronouns"], row["include_genre_notes"]) == (1, 0)
    assert client.post(f"/api/dramas/{did}/metadata",
                       json={"default_female_pronouns": "yes"}).status_code == 422


@pytest.mark.parametrize("titles", [{}, {"title_en": ""}, {"title_en": "  ", "title_zh": "\t"}])
def test_create_needs_a_non_blank_title(client, titles):
    resp = client.post("/api/dramas", json={"source_language": "zh", **titles})
    assert resp.status_code == 422
    assert _count() == 0


def test_metadata_title_with_nul_is_422(client):
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    resp = client.post(f"/api/dramas/{did}/metadata", json={"title_en": "a\u0000b"})
    assert resp.status_code == 422


def test_metadata_blanking_the_only_title_is_422(client):
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "T"}).json()["id"]
    resp = client.post(f"/api/dramas/{did}/metadata", json={"title_en": "   "})
    assert resp.status_code == 422
    assert client.get(f"/api/library/dramas/{did}").json()["title_en"] == "T"


def test_release_date_create_read_update_and_reject(client):
    made = client.post("/api/dramas", json={"source_language": "ja", "title_en": "Stream",
                                            "release_date": "2025-03-04"})
    assert made.status_code == 201, made.text
    did = made.json()["id"]
    assert made.json()["release_date"] == "2025-03-04"
    assert client.get(f"/api/library/dramas/{did}").json()["release_date"] == "2025-03-04"
    listed = client.get("/api/library/dramas").json()["items"]
    assert [i["release_date"] for i in listed] == ["2025-03-04"]

    bad = client.post(f"/api/dramas/{did}/metadata", json={"release_date": "2025-02-30"})
    assert bad.status_code == 422, bad.text
    _error(bad)
    cleared = client.post(f"/api/dramas/{did}/metadata", json={"release_date": ""})
    assert cleared.status_code == 200 and cleared.json()["release_date"] is None


# ---------------------------------------------------------------------------
# POST /api/dramas/{id}/folder/open
# ---------------------------------------------------------------------------

LOCAL = {"X-Baihe-Local": "1"}


@pytest.fixture
def opened(monkeypatch):
    from lib import file_manager
    calls = []
    monkeypatch.setattr(file_manager, "open_folder", calls.append)
    return calls


def test_open_folder_opens_the_titles_own_folder(client, opened):
    import os
    drama_id = db.create_drama(title_en="A")
    r = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    assert r.status_code == 200 and r.json() == {"opened": True, "folder": None}
    folder = os.path.realpath(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    assert opened == [folder] and os.path.isdir(folder)
    assert str(folder) not in r.text


def test_open_folder_unknown_title_is_404_and_opens_nothing(client, opened):
    assert client.post("/api/dramas/999/folder/open", headers=LOCAL).status_code == 404
    assert client.post("/api/dramas/0/folder/open", headers=LOCAL).status_code == 422
    assert opened == []


def test_open_folder_refuses_a_folder_that_leaves_the_library(client, opened, tmp_path):
    import os
    drama_id = db.create_drama(title_en="A")
    os.makedirs(db.DRAMAS_DIR, exist_ok=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    os.symlink(outside, os.path.join(db.DRAMAS_DIR, str(drama_id)), target_is_directory=True)
    r = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    assert r.status_code == 422 and opened == []


def test_open_folder_without_a_file_manager_is_400(client, monkeypatch):
    from lib import file_manager

    def broken(folder):
        raise OSError("no xdg-open")
    monkeypatch.setattr(file_manager, "open_folder", broken)
    drama_id = db.create_drama(title_en="A")
    r = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    assert r.status_code == 400
    assert _error(r)["message"] == "This PC has no file manager the app can open."


def test_open_folder_when_its_name_is_taken_is_409(client, opened):
    import os
    drama_id = db.create_drama(title_en="A")
    os.makedirs(db.DRAMAS_DIR, exist_ok=True)
    with open(os.path.join(db.DRAMAS_DIR, str(drama_id)), "w") as f:
        f.write("not a folder")
    r = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    assert r.status_code == 409 and opened == []
    assert db.DRAMAS_DIR not in r.text


def test_open_folder_takes_the_frontends_json_post_and_refuses_a_bare_one(client, opened):
    drama_id = db.create_drama(title_en="A")
    # pcOnlyFetch sends a JSON body; a form-style POST with neither is refused.
    assert client.post(f"/api/dramas/{drama_id}/folder/open", json={}).status_code == 200
    assert client.post(f"/api/dramas/{drama_id}/folder/open").status_code == 403
    assert len(opened) == 1


def test_open_folder_route_is_pc_only():
    from api.auth import iter_route_declarations
    decls = {(path, tuple(sorted(methods))): d
             for _r, path, methods, d in iter_route_declarations(create_app(ApiSettings()))}
    assert decls[("/api/dramas/{drama_id}/folder/open", ("POST",))] == [("local_only", None)]


def test_open_folder_from_a_background_service_returns_the_folder_to_open_by_hand(client, monkeypatch):
    from lib import file_manager

    def no_desktop(folder):
        raise file_manager.NoDesktopError("no desktop session")
    monkeypatch.setattr(file_manager, "open_folder", no_desktop)
    drama_id = db.create_drama(title_en="A")
    r = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    folder = os.path.realpath(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    assert r.status_code == 200 and r.json() == {"opened": False, "folder": folder}
    again = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    assert again.json() == {"opened": False, "folder": folder}


def test_open_folder_without_a_desktop_names_the_same_folder_for_a_comic(client, monkeypatch):
    from lib import file_manager

    def no_desktop(folder):
        raise file_manager.NoDesktopError("no desktop session")
    monkeypatch.setattr(file_manager, "open_folder", no_desktop)
    drama_id = db.create_drama(title_en="C", media_type="comic")
    r = client.post(f"/api/dramas/{drama_id}/folder/open", headers=LOCAL)
    assert r.json()["folder"] == os.path.realpath(db.drama_dir(drama_id))


def test_duplicate_makes_an_independent_copy(client):
    src = client.post("/api/dramas", json={"source_language": "zh", "title_en": "Orig"}).json()
    resp = client.post(f"/api/dramas/{src['id']}/duplicate", json={})
    assert resp.status_code == 201, resp.text
    assert resp.json()["title_en"] == "Orig (copy)" and resp.json()["id"] != src["id"]
    assert client.post("/api/dramas/987654/duplicate", json={}).status_code == 404


def test_capture_notes_route_lists_a_titles_notes_and_404s_for_a_missing_title(client):
    import db
    did = client.post("/api/dramas", json={"source_language": "zh", "title_en": "Cap"}).json()["id"]
    assert client.get(f"/api/library/dramas/{did}/capture-notes").json() == {"items": []}
    db.add_capture_note(did, "Saved 3 of 3 pages.")
    items = client.get(f"/api/library/dramas/{did}/capture-notes").json()["items"]
    assert [i["summary"] for i in items] == ["Saved 3 of 3 pages."]
    assert client.get("/api/library/dramas/999999/capture-notes").status_code == 404



def _away_client():
    from fastapi.testclient import TestClient
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                      raise_server_exceptions=False)


def _signed_in(email, admin=False):
    from api import auth as api_auth
    from services import auth_service
    u = auth_service.grant_admin_local(email) if admin else auth_service.add_user(email)
    s = auth_service.create_session(u["id"])
    return u, {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}", api_auth.CSRF_HEADER: s["csrf_token"]}


_DELETE = {"confirm": True, "confirm_text": "DELETE"}


def _made_by(user, **kw):
    return db.create_drama(owner_user_id=user["id"], created_by_user_id=user["id"], **kw)


def test_a_member_deletes_a_title_they_made_from_away_and_nothing_else(isolated_db):
    from services import auth_service
    away = _away_client()
    kim, kim_h = _signed_in("kim@example.com")
    _, bo_h = _signed_in("bo@example.com")
    _, admin_h = _signed_in("admin@example.com", admin=True)
    mine = _made_by(kim, title_en="Mine", is_private=1)
    kept = _made_by(kim, title_en="Kept")
    shared = _made_by(kim, title_en="Shared", is_private=0)
    pc_made = db.create_drama(title_en="PC", is_private=0)

    def delete(did, h, **over):
        return away.post(f"/api/dramas/{did}/delete-mine", json={**_DELETE, **over}, headers=h)

    assert delete(mine, kim_h, confirm_text="delete").status_code == 422
    assert delete(mine, bo_h).status_code == 404              # private: not even seen
    assert delete(shared, bo_h).status_code == 403            # seen and editable, but not theirs
    assert delete(pc_made, kim_h).status_code == 403
    assert delete(pc_made, admin_h).status_code == 403        # an admin away: only what they made too
    assert away.post(f"/api/dramas/{mine}/delete-mine", json=_DELETE,
                     headers={"Cookie": kim_h["Cookie"]}).status_code == 403   # no CSRF token
    assert db.get_drama(mine) and db.get_drama(pc_made) and db.get_drama(shared)
    r = delete(mine, kim_h)
    assert r.status_code == 200 and r.json() == {"deleted": True, "drama_id": mine}
    assert db.get_drama(mine) is None
    row = next(a for a in auth_service.list_audit(limit=10) if a["action"] == "title.delete")
    assert row["user_id"] == kim["id"] and row["detail_redacted"] == f"title {mine}"
    listed = away.get("/api/library/dramas", headers=kim_h).json()["items"]
    assert {d["id"]: d["made_by_me"] for d in listed} == {kept: True, shared: True, pc_made: False}


def test_giving_yourself_a_title_from_away_does_not_make_it_deletable(isolated_db):
    """The owner can be changed from away (Settings > Sharing); only who made the title counts."""
    away = _away_client()
    admin, admin_h = _signed_in("admin@example.com", admin=True)
    pc_made = db.create_drama(title_en="PC", is_private=0)
    r = away.post(f"/api/sharing/dramas/{pc_made}/owner", json={"owner_user_id": admin["id"]}, headers=admin_h)
    assert r.status_code == 200, r.text
    assert db.get_drama(pc_made)["owner_user_id"] == admin["id"]
    assert away.post(f"/api/dramas/{pc_made}/delete-mine", json=_DELETE, headers=admin_h).status_code == 403
    assert db.get_drama(pc_made) is not None
    # An admin's own title made from away is theirs to delete.
    own = _made_by(admin, title_en="Phone")
    assert away.post(f"/api/dramas/{own}/delete-mine", json=_DELETE, headers=admin_h).status_code == 200


def test_a_title_given_away_after_it_was_made_is_no_longer_deletable_by_its_maker(isolated_db):
    away = _away_client()
    kim, kim_h = _signed_in("kim@example.com")
    did = _made_by(kim, title_en="Mine")
    db.update_drama(did, owner_user_id=None)
    assert away.post(f"/api/dramas/{did}/delete-mine", json=_DELETE, headers=kim_h).status_code in (403, 404)
    assert db.get_drama(did) is not None


def test_an_owner_change_during_the_delete_wins(isolated_db, monkeypatch):
    from services import drama_service
    kim, _ = _signed_in("kim@example.com")
    did = _made_by(kim, title_en="Mine")
    monkeypatch.setattr(drama_service, "uploads_held", _owner_changes_first(did))
    from lib.errors import ForbiddenError
    with pytest.raises(ForbiddenError):
        drama_service.delete_own_drama({"user_id": kim["id"], "is_admin": False, "permissions": ["lines.edit"]},
                                       did, confirm=True, confirm_text="DELETE")
    assert db.get_drama(did) is not None


def _owner_changes_first(did):
    import contextlib

    # The admin at the PC moves the title in Settings > Sharing just as the member presses Delete.
    @contextlib.contextmanager
    def held(drama_id):
        db.update_drama(did, owner_user_id=None)
        yield
    return held


def test_a_title_with_a_bulk_batch_waiting_is_not_deleted_from_away(isolated_db):
    away = _away_client()
    kim, kim_h = _signed_in("kim@example.com")
    did = _made_by(kim, title_en="Mine")
    db.create_bulk_job(did, "claude", "claude-sonnet", "submitted", [], provider_batch_id="b1", estimated_usd=0.5)
    r = away.post(f"/api/dramas/{did}/delete-mine", json=_DELETE, headers=kim_h)
    assert r.status_code == 409 and "bulk translation" in r.text
    assert db.get_drama(did) is not None
