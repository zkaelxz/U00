"""The Series page routes (api/routers/series_routes.py): list, page,
rename, delete (PC only) and copy glossary terms between series."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from tests.api_helpers import loopback_client

REMOTE = "https://baihe.example.com"


def _app(auth="on"):
    return create_app(ApiSettings(auth_mode=auth, serve_frontend=False))


def _remote(app):
    return TestClient(app, base_url=REMOTE, raise_server_exceptions=False)



def _login(email, admin=False):
    u = auth_service.grant_admin_local(email) if admin else auth_service.add_user(email)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def world(isolated_db):
    a_id, a = _login("ann@example.com")
    b_id, b = _login("bo@example.com")
    w = {"a": a, "b": b, "a_id": a_id, "b_id": b_id}
    w["saga"] = db.create_series("Saga", owner_user_id=a_id)
    w["ep1"] = db.create_drama(title_en="Ep 1", source_language="zh", series_id=w["saga"],
                               owner_user_id=a_id)
    db.upsert_glossary_term(w["saga"], "林", "Lin")
    w["other"] = db.create_series("Other", owner_user_id=a_id)
    db.upsert_glossary_term(w["other"], "林", "Forest")
    db.upsert_glossary_term(w["other"], "苏", "Su")
    w["secret"] = db.create_series("Secret", owner_user_id=b_id, is_private=True)
    db.upsert_glossary_term(w["secret"], "秘", "Hidden")
    return w


class TestReadRoutes:
    def test_list_and_page(self, world):
        c = _remote(_app())
        items = c.get("/api/series", headers=world["a"]).json()["items"]
        names = {s["name"] for s in items}
        assert {"Saga", "Other"} <= names and "Secret" not in names
        page = c.get(f"/api/series/{world['saga']}", headers=world["a"])
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["titles"][0]["id"] == world["ep1"]
        assert body["glossary_term_count"] == 1
        assert c.get(f"/api/series/{world['secret']}", headers=world["a"]).status_code == 404

    def test_response_never_carries_paths_or_owner_ids(self, world):
        c = _remote(_app())
        text = c.get(f"/api/series/{world['saga']}", headers=world["a"]).text
        assert "owner_user_id" not in text and db.LIBRARY_DIR not in text


class TestRename:
    def test_rename_and_conflict(self, world):
        c = _remote(_app())
        r = c.post(f"/api/series/{world['saga']}/rename", headers=world["a"],
                   json={"name": " Saga II "})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Saga II"
        r = c.post(f"/api/series/{world['saga']}/rename", headers=world["a"],
                   json={"name": "other"})
        assert r.status_code == 409

    def test_a_hidden_series_name_answers_exactly_like_a_visible_one(self, world):
        c = _remote(_app())
        url = f"/api/series/{world['saga']}/rename"
        visible = c.post(url, headers=world["a"], json={"name": "Other"})
        hidden = c.post(url, headers=world["a"], json={"name": "Secret"})
        assert visible.status_code == hidden.status_code == 409
        assert visible.content == hidden.content
        assert b"Other" not in visible.content

    def test_other_users_private_series_is_404(self, world):
        c = _remote(_app())
        r = c.post(f"/api/series/{world['secret']}/rename", headers=world["a"],
                   json={"name": "Mine now"})
        assert r.status_code == 404


class TestDelete:
    def test_remote_delete_is_refused(self, world):
        c = _remote(_app())
        r = c.post(f"/api/series/{world['saga']}/delete", headers=world["a"],
                   json={"confirm": True, "confirm_text": "DELETE"})
        assert r.status_code == 403
        assert db.get_item_ownership("series", world["saga"]) is not None

    def test_preview_then_delete_at_the_pc(self, world):
        c = loopback_client(_app())
        p = c.get(f"/api/series/{world['saga']}/delete-preview", headers=world["a"]).json()
        assert p["title_count"] == 1 and p["needs_typed_confirm"] is True
        r = c.post(f"/api/series/{world['saga']}/delete", json={"confirm": True})
        assert r.status_code == 422
        r = c.post(f"/api/series/{world['saga']}/delete",
                   json={"confirm": True, "confirm_text": "DELETE"})
        assert r.status_code == 200, r.text
        assert r.json()["titles_kept"] == 1
        assert db.get_drama(world["ep1"])["series_id"] is None


class TestCopyTerms:
    def test_preview_then_apply(self, world):
        c = _remote(_app())
        url = f"/api/series/{world['saga']}/copy-terms"
        p = c.get(f"{url}-preview?source_series_id={world['other']}", headers=world["a"])
        assert p.status_code == 200, p.text
        assert (p.json()["new_count"], p.json()["skipped_count"]) == (1, 1)
        r = c.post(url, headers=world["a"], json={"source_series_id": world["other"]})
        assert r.status_code == 200, r.text
        assert (r.json()["added"], r.json()["skipped"]) == (1, 1)
        terms = {t["term_original"]: t["term_translation"]
                 for t in db.list_glossary_terms(world["saga"])}
        assert terms == {"林": "Lin", "苏": "Su"}

    def test_private_source_of_another_user_is_404(self, world):
        c = _remote(_app())
        url = f"/api/series/{world['saga']}/copy-terms"
        p = c.get(f"{url}-preview?source_series_id={world['secret']}", headers=world["a"])
        assert p.status_code == 404
        r = c.post(url, headers=world["a"], json={"source_series_id": world["secret"]})
        assert r.status_code == 404
        assert len(db.list_glossary_terms(world["saga"])) == 1


class TestDefaults:
    def test_set_read_preview_and_apply(self, world):
        c = _remote(_app())
        url = f"/api/series/{world['saga']}/defaults"
        r = c.post(url, headers=world["a"], json={"settings": {"beam_size": 8,
                                                               "whisper_fast_mode": True}})
        assert r.status_code == 200, r.text
        body = c.get(url, headers=world["a"]).json()
        assert body["settings"] == {"beam_size": 8, "whisper_fast_mode": True}
        assert any(f["key"] == "beam_size" and f["type"] == "int" for f in body["fields"])
        p = c.get(f"{url}/apply-preview?mode=overwrite", headers=world["a"]).json()
        assert p["title_count"] == 1 and p["changed_titles"] == 1
        r = c.post(f"{url}/apply", headers=world["a"], json={"mode": "overwrite"})
        assert r.status_code == 200, r.text
        assert db.get_drama(world["ep1"])["beam_size"] == 8

    def test_bad_settings_are_422_and_nothing_is_stored(self, world):
        c = _remote(_app())
        url = f"/api/series/{world['saga']}/defaults"
        for bad in ({"api_key": "x"}, {"beam_size": "8"}, {"beam_size": 1000}):
            r = c.post(url, headers=world["a"], json={"settings": bad})
            assert r.status_code == 422, (bad, r.text)
        assert c.get(url, headers=world["a"]).json()["settings"] == {}
        assert c.get(f"{url}/apply-preview?mode=all", headers=world["a"]).status_code == 422

    def test_other_users_private_series_is_404(self, world):
        c = _remote(_app())
        url = f"/api/series/{world['secret']}/defaults"
        assert c.get(url, headers=world["a"]).status_code == 404
        assert c.post(url, headers=world["a"], json={"settings": {}}).status_code == 404
        assert c.post(f"{url}/apply", headers=world["a"], json={"mode": "fill"}).status_code == 404
