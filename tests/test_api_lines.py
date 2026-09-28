"""
Tests for the Review per-line write endpoints (Migration Slice 43,
api/routers/lines_routes.py). TestClient against an `isolated_db` library.
Service logic is covered by tests/test_lines_service.py; these check the
HTTP contract.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    assert {"code", "message"} <= set(body["error"])
    return body["error"]


def _seed():
    did = db.create_drama(title_en="D", series_id=db.get_or_create_series("S"))
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A"),
        Line(idx=1, start=1.0, end=2.0, zh="再见", en="Bye", flag="uncertain", flag_note="hm"),
    ])
    return did, [r["id"] for r in db.load_lines(did)]


B = "/api/lines/dramas"


class TestPatch:
    def test_partial_edit(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/lines/{ids[1]}", json={"en": "Goodbye"})
        assert r.status_code == 200
        body = r.json()
        assert body["en"] == "Goodbye" and body["flag"] is None and body["id"] == ids[1]

    def test_explicit_null_is_not_passed(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/lines/{ids[0]}", json={"en": "X", "zh": None})
        assert r.status_code == 200 and r.json()["zh"] == "你好"

    def test_conflict_409(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/lines/{ids[0]}", json={"en": "X", "expected": {"en": "old"}})
        assert r.status_code == 409 and _error(r)["code"] == "conflict"
        assert db.load_lines(did)[0]["en"] == "Hello"

    def test_expected_match(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/lines/{ids[0]}",
                        json={"en": "X", "expected": {"en": "Hello", "start": 0.0}})
        assert r.status_code == 200

    def test_404s(self, client):
        did, ids = _seed()
        assert client.post(f"{B}/{did}/lines/99999", json={"en": "X"}).status_code == 404
        assert client.post(f"{B}/999/lines/{ids[0]}", json={"en": "X"}).status_code == 404
        assert len(db.load_lines(did)) == 2

    def test_validation_422(self, client):
        did, ids = _seed()
        for body in ({}, {"end": 0.0}, {"bogus": 1}, {"en": "x" * 2001}):
            r = client.post(f"{B}/{did}/lines/{ids[0]}", json=body)
            assert r.status_code == 422 and _error(r)["code"] == "validation_error", body


class TestOthers:
    def test_dismiss(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/lines/{ids[1]}/dismiss-flag")
        assert r.status_code == 200 and r.json()["flag"] is None
        assert client.post(f"{B}/{did}/lines/99999/dismiss-flag").status_code == 404

    def test_find_replace_apply(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/find-replace/apply", json={"matches": [
            {"id": ids[0], "old_text": "Hello", "new_text": "Howdy"},
            {"id": ids[1], "old_text": "stale", "new_text": "x"}]})
        assert r.status_code == 200
        body = r.json()
        assert (body["applied"], body["stale"], body["stale_ids"]) == (1, 1, [ids[1]])
        assert db.load_lines(did)[0]["en"] == "Howdy"
        assert client.post(f"{B}/{did}/find-replace/apply",
                           json={"matches": [], "x": 1}).status_code == 422

    def test_accept_tm(self, client):
        did, ids = _seed()
        sid = db.get_drama(did)["series_id"]
        db.record_translation_memory(sid, "你好", "Howdy")
        eid = db.list_translation_memory(sid)[0]["id"]
        r = client.post(f"{B}/{did}/lines/{ids[0]}/accept-tm", json={"entry_id": eid})
        assert r.status_code == 200 and r.json()["en"] == "Howdy"
        assert client.post(f"{B}/{did}/lines/{ids[0]}/accept-tm",
                           json={"entry_id": 9999}).status_code == 404

    def test_notes_add_delete(self, client):
        did, ids = _seed()
        r = client.post(f"{B}/{did}/notes", json={"line_id": ids[0], "term": "t",
                                                   "note_type": "idiom", "note": "n"})
        assert r.status_code == 200
        nid = r.json()["id"]
        other = db.create_drama(title_en="O")
        assert client.delete(f"{B}/{other}/notes/{nid}").status_code == 404
        assert len(db.list_translation_notes(did)) == 1
        r = client.delete(f"{B}/{did}/notes/{nid}")
        assert r.status_code == 200 and r.json() == {"deleted": True, "note_id": nid}
        r = client.post(f"{B}/{did}/notes", json={"line_id": ids[0], "term": "t",
                                                   "note_type": "nope", "note": "n"})
        assert r.status_code == 422
        r = client.post(f"{B}/{did}/notes", json={"line_id": 99999, "term": "t",
                                                   "note_type": "idiom", "note": "n"})
        assert r.status_code == 404
