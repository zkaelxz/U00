"""
Tests for the Voices screen: services/voices_service.py and api/routers/voices_routes.py.
isolated_db only (a scratch library, never the real one); embeddings are written through diarize.save_turns.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import diarize
from api.api_config import ApiSettings
from api.server import create_app
from lib.errors import ConflictError, InvalidInputError, NotFoundError
from services import auth_service, voices_service as svc
from tests.api_helpers import loopback_client, session_headers


def _drama(db, title, embeddings, series_id):
    did = db.create_drama(title_en=title, series_id=series_id)
    diarize.save_turns(db.drama_dir(did), [{"speaker": k, "start": 0.0, "end": 1.0} for k in embeddings],
                       embeddings=embeddings)
    return did


def _person(db, name="Mei"):
    sid = db.get_or_create_series("S")
    return sid, db.insert_series_character(sid, name)


def _link(db, did, label, cid):
    db.upsert_character(did, label, character_name="Mei", series_character_id=cid)


@pytest.fixture
def enrolled(isolated_db):
    """Mei with two linked speakers and a print of both blended in."""
    db = isolated_db
    sid, cid = _person(db)
    a = _drama(db, "A", {"SPEAKER_00": [1.0, 0.0]}, sid)
    b = _drama(db, "B", {"SPEAKER_01": [0.0, 1.0]}, sid)
    for did, label, vec in ((a, "SPEAKER_00", [1.0, 0.0]), (b, "SPEAKER_01", [0.0, 1.0])):
        _link(db, did, label, cid)
        db.update_series_character_voice_fingerprint(cid, vec)
    return db, sid, cid, a, b


def _sc(db, sid, cid):
    return next(c for c in db.list_series_characters(sid) if c["id"] == cid)


class TestList:
    def test_lists_only_enrolled_people_with_count_and_match(self, enrolled):
        db, sid, cid, a, _ = enrolled
        db.insert_series_character(sid, "Nobody")
        (v,) = svc.list_voices()
        assert (v["character_name"], v["series_name"], v["sample_count"]) == ("Mei", "S", 2)
        assert {s["speaker_label"] for s in v["linked"]} == {"SPEAKER_00", "SPEAKER_01"}
        assert v["rebuildable"] == 2
        assert v["lowest_similarity"] == pytest.approx(0.7071, abs=1e-3)

    def test_speaker_without_stored_embedding_has_no_score(self, enrolled):
        db, sid, cid, a, _ = enrolled
        c = db.create_drama(title_en="C", series_id=sid)
        _link(db, c, "SPEAKER_00", cid)
        (v,) = svc.list_voices()
        assert next(s for s in v["linked"] if s["drama_id"] == c)["similarity"] is None
        assert v["rebuildable"] == 2


class TestChanges:
    def test_clear_needs_confirm_and_keeps_the_person(self, enrolled):
        db, sid, cid, a, _ = enrolled
        with pytest.raises(InvalidInputError):
            svc.clear_voice(sid, cid)
        assert _sc(db, sid, cid)["voice_fingerprint"]
        svc.clear_voice(sid, cid, confirm=True)
        sc = _sc(db, sid, cid)
        assert sc["voice_fingerprint"] is None and sc["voice_fingerprint_samples"] == 0
        assert sc["character_name"] == "Mei"
        assert db.list_series_character_links(cid)
        assert svc.list_voices() == []

    def test_retrain_rebuilds_the_mean_from_linked_speakers(self, enrolled):
        db, sid, cid, a, b = enrolled
        # A drifted print, as after a wrong accept that was later unlinked by hand.
        db.set_series_character_voice_fingerprint(cid, [5.0, 5.0], 9)
        with pytest.raises(InvalidInputError):
            svc.retrain_voice(sid, cid)
        assert svc.retrain_voice(sid, cid, confirm=True)["sample_count"] == 2
        sc = _sc(db, sid, cid)
        import json
        assert json.loads(sc["voice_fingerprint"]) == [0.5, 0.5]
        assert sc["voice_fingerprint_samples"] == 2

    def test_retrain_with_nothing_to_rebuild_from_keeps_the_print(self, isolated_db):
        db = isolated_db
        sid, cid = _person(db)
        db.update_series_character_voice_fingerprint(cid, [1.0, 0.0])
        with pytest.raises(ConflictError):
            svc.retrain_voice(sid, cid, confirm=True)
        assert _sc(db, sid, cid)["voice_fingerprint"]

    def test_remove_sample_unlinks_and_rebuilds_without_it(self, enrolled):
        db, sid, cid, a, b = enrolled
        with pytest.raises(InvalidInputError):
            svc.remove_sample(sid, cid, b, "SPEAKER_01")
        out = svc.remove_sample(sid, cid, b, "SPEAKER_01", confirm=True)
        assert out["sample_count"] == 1
        import json
        assert json.loads(_sc(db, sid, cid)["voice_fingerprint"]) == [1.0, 0.0]
        row = next(r for r in db.list_characters(b) if r["speaker_label"] == "SPEAKER_01")
        assert row["series_character_id"] is None and row["character_name"] == "Mei"
        with pytest.raises(NotFoundError):
            svc.remove_sample(sid, cid, b, "SPEAKER_01", confirm=True)

    def test_removing_the_last_sample_clears_the_print(self, enrolled):
        db, sid, cid, a, b = enrolled
        svc.remove_sample(sid, cid, a, "SPEAKER_00", confirm=True)
        svc.remove_sample(sid, cid, b, "SPEAKER_01", confirm=True)
        assert _sc(db, sid, cid)["voice_fingerprint"] is None

    def test_unknown_or_unenrolled_person(self, isolated_db):
        sid, cid = _person(isolated_db)
        with pytest.raises(ConflictError):
            svc.clear_voice(sid, cid, confirm=True)
        with pytest.raises(NotFoundError):
            svc.clear_voice(sid, cid + 99, confirm=True)

    def test_mismatched_dimension_is_left_out_of_a_retrain(self, enrolled):
        db, sid, cid, a, b = enrolled
        c = _drama(db, "C", {"SPEAKER_02": [1.0, 0.0, 0.0]}, sid)
        _link(db, c, "SPEAKER_02", cid)
        assert svc.retrain_voice(sid, cid, confirm=True)["sample_count"] == 2


class TestApi:
    def test_routes(self, enrolled):
        db, sid, cid, a, b = enrolled
        client = loopback_client(create_app(ApiSettings(auth_mode="off")))
        items = client.get("/api/voices").json()["items"]
        assert items[0]["character_id"] == cid and items[0]["sample_count"] == 2
        base = f"/api/voices/{sid}/{cid}"
        assert client.post(base + "/clear", json={}).status_code == 422
        r = client.post(base + "/remove-sample",
                        json={"drama_id": b, "speaker_label": "SPEAKER_01", "confirm": True})
        assert r.status_code == 200 and r.json()["sample_count"] == 1
        assert client.post(base + "/retrain", json={"confirm": True}).json()["sample_count"] == 1
        assert client.post(base + "/clear", json={"confirm": True}).status_code == 200
        assert client.get("/api/voices").json()["items"] == []

    def test_remote_member_can_read_but_not_change(self, enrolled):
        db, sid, cid, a, b = enrolled
        remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                            raise_server_exceptions=False)
        assert remote.get("/api/voices").status_code == 401
        u = auth_service.add_user("r@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        auth_service.grant_permission(u["id"], "library.read")
        headers = session_headers(auth_service.create_session(u["id"], "pytest", "203.0.113.9"))
        assert remote.get("/api/voices", headers=headers).status_code == 200
        r = remote.post(f"/api/voices/{sid}/{cid}/clear", json={"confirm": True}, headers=headers)
        assert r.status_code == 403
        assert _sc(db, sid, cid)["voice_fingerprint"]
