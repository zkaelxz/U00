"""
Tests for the Characters extras (inventory C02, C04, C07, C08):
recurring-voice suggestions (list/accept/reject), sample lines and the
series pronoun default on the characters list, custom pronouns, and
"remember as a known series character". Embeddings are written with
diarize.save_turns (plain JSON, no pyannote); no model runs.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import diarize
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import characters_service


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _series_drama(db, embedding=(1.0, 0.0), fingerprint=(1.0, 0.0), speakers=("SPEAKER_00",)):
    sid = db.get_or_create_series("Test Series")
    db.upsert_series_character(sid, "Su Shan", aliases="Shanshan", notes="calm")
    sc = db.list_series_characters(sid)[0]
    if fingerprint is not None:
        db.update_series_character_voice_fingerprint(sc["id"], list(fingerprint))
    did = db.create_drama(title_en="D", series_id=sid)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 0.5, zh="你好", speaker=s)
                        for i, s in enumerate(speakers)])
    if embedding is not None:
        diarize.save_turns(db.drama_dir(did), [], embeddings={"SPEAKER_00": list(embedding)})
    return did, sid, sc["id"]


def _suggest(client, did):
    r = client.get(f"/api/characters/dramas/{did}/voice-suggestions")
    assert r.status_code == 200, r.text
    return r.json()


class TestVoiceSuggestions:
    def test_matching_voice_is_suggested(self, client, isolated_db):
        did, _sid, sc_id = _series_drama(isolated_db)
        items = _suggest(client, did)
        assert items == [{"speaker_label": "SPEAKER_00", "series_character_id": sc_id,
                          "character_name": "Su Shan", "similarity": 1.0}]

    def test_below_threshold_is_empty(self, client, isolated_db):
        did, _sid, _ = _series_drama(isolated_db, embedding=(0.0, 1.0))
        assert _suggest(client, did) == []

    @pytest.mark.parametrize("kw", [{"embedding": None}, {"fingerprint": None}])
    def test_no_embeddings_or_fingerprint_is_empty_not_error(self, client, isolated_db, kw):
        did, _sid, _ = _series_drama(isolated_db, **kw)
        assert _suggest(client, did) == []

    def test_no_series_is_empty(self, client, isolated_db):
        did = isolated_db.create_drama(title_en="Solo")
        assert _suggest(client, did) == []

    def test_corrupt_embeddings_file_is_empty(self, client, isolated_db):
        did, _sid, _ = _series_drama(isolated_db)
        path = isolated_db.drama_dir(did)
        import os
        with open(os.path.join(path, diarize.TURNS_FILE), "w", encoding="utf-8") as f:
            f.write("{not json")
        assert _suggest(client, did) == []

    def test_unknown_drama_404(self, client):
        assert client.get("/api/characters/dramas/999/voice-suggestions").status_code == 404

    def test_named_speaker_gets_no_suggestion(self, client, isolated_db):
        did, _sid, _ = _series_drama(isolated_db)
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Someone")
        assert _suggest(client, did) == []

    def test_accept_names_links_and_blends_fingerprint(self, client, isolated_db):
        did, sid, sc_id = _series_drama(isolated_db)
        r = client.post(f"/api/characters/dramas/{did}/voice-suggestions/accept",
                        json={"speaker_label": "SPEAKER_00", "series_character_id": sc_id})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["character"]["character_name"] == "Su Shan"
        assert body["character"]["series_character_id"] == sc_id
        assert body["suggestions"] == []
        sc = isolated_db.list_series_characters(sid)[0]
        assert sc["voice_fingerprint_samples"] == 2
        assert sc["aliases"] == "Shanshan" and sc["notes"] == "calm"
        assert isolated_db.list_dismissed_voice_suggestions(did) == set()

    def test_accept_of_pair_not_offered_is_404_and_writes_nothing(self, client, isolated_db):
        did, sid, sc_id = _series_drama(isolated_db, embedding=(0.0, 1.0))
        r = client.post(f"/api/characters/dramas/{did}/voice-suggestions/accept",
                        json={"speaker_label": "SPEAKER_00", "series_character_id": sc_id})
        assert r.status_code == 404
        assert _error(r)["code"] == "not_found"
        assert isolated_db.list_characters_with_series_names(did) == []
        assert isolated_db.list_series_characters(sid)[0]["voice_fingerprint_samples"] == 1

    def test_reject_dismisses_only(self, client, isolated_db):
        did, sid, sc_id = _series_drama(isolated_db)
        r = client.post(f"/api/characters/dramas/{did}/voice-suggestions/reject",
                        json={"speaker_label": "SPEAKER_00", "series_character_id": sc_id})
        assert r.status_code == 200, r.text
        assert r.json() == {"character": None, "suggestions": []}
        assert isolated_db.list_dismissed_voice_suggestions(did) == {("SPEAKER_00", sc_id)}
        assert isolated_db.list_characters_with_series_names(did) == []
        assert isolated_db.list_series_characters(sid)[0]["voice_fingerprint_samples"] == 1
        assert _suggest(client, did) == []
        # Idempotent.
        r = client.post(f"/api/characters/dramas/{did}/voice-suggestions/reject",
                        json={"speaker_label": "SPEAKER_00", "series_character_id": sc_id})
        assert r.status_code == 200

    def test_reject_other_series_character_or_speaker_404(self, client, isolated_db):
        did, _sid, sc_id = _series_drama(isolated_db)
        other = isolated_db.get_or_create_series("Other")
        isolated_db.upsert_series_character(other, "Stranger")
        stranger = isolated_db.list_series_characters(other)[0]["id"]
        url = f"/api/characters/dramas/{did}/voice-suggestions/reject"
        assert client.post(url, json={"speaker_label": "SPEAKER_00",
                                      "series_character_id": stranger}).status_code == 404
        assert client.post(url, json={"speaker_label": "NOPE",
                                      "series_character_id": sc_id}).status_code == 404
        assert isolated_db.list_dismissed_voice_suggestions(did) == set()

    @pytest.mark.parametrize("body", [
        {"speaker_label": "", "series_character_id": 1},
        {"speaker_label": "A", "series_character_id": 0},
        {"speaker_label": "A", "series_character_id": 2**40},
        {"speaker_label": "x" * 101, "series_character_id": 1},
        {"speaker_label": "A", "series_character_id": 1, "extra": 1},
    ])
    def test_bad_bodies_422(self, client, isolated_db, body):
        did, _sid, _ = _series_drama(isolated_db)
        for action in ("accept", "reject"):
            r = client.post(f"/api/characters/dramas/{did}/voice-suggestions/{action}", json=body)
            assert r.status_code == 422, (action, body, r.text)


class TestSamplesAndSeriesPronouns:
    def test_first_and_middle_sample_capped(self, client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        texts = ["one", "   ", "two", "three " * 100, "four"]
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 0.5, zh=t, speaker="A")
                                     for i, t in enumerate(texts)]
                               + [Line(idx=9, start=9, end=9.5, zh="solo", speaker="B")])
        items = {c["speaker_label"]: c for c in client.get(f"/api/characters/dramas/{did}").json()}
        a = items["A"]["sample_lines"]
        assert a[0] == "one" and len(a) == 2
        assert a[1].startswith("three") and len(a[1]) == characters_service.MAX_SAMPLE_CHARS
        assert a[1].endswith("…")
        assert items["B"]["sample_lines"] == ["solo"]

    def test_speaker_without_lines_has_no_samples(self, client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.upsert_character(did, "Ghost", character_name="G")
        (c,) = client.get(f"/api/characters/dramas/{did}").json()
        assert c["sample_lines"] == [] and c["line_count"] == 0

    def test_series_pronouns_shown_not_copied(self, client, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.upsert_series_character(sid, "Mei", gender="female")
        sc_id = isolated_db.list_series_characters(sid)[0]["id"]
        did = isolated_db.create_drama(title_en="D", series_id=sid)
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你", speaker="A")])
        isolated_db.upsert_character(did, "A", character_name="Mei", series_character_id=sc_id)
        (c,) = client.get(f"/api/characters/dramas/{did}").json()
        assert c["series_pronouns"] == "she/her"
        assert c["pronouns"] == ""

    def test_custom_pronouns_saved_and_capped(self, client, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你", speaker="A")])
        url = f"/api/characters/dramas/{did}/character"
        r = client.post(url, json={"speaker_label": "A", "pronouns": " xe/xem "})
        assert r.status_code == 200 and r.json()["pronouns"] == "xe/xem"
        r = client.post(url, json={"speaker_label": "A", "pronouns": "x" * 41})
        assert r.status_code == 422
        assert isolated_db.list_characters_with_series_names(did)[0]["pronouns"] == "xe/xem"


class TestRemember:
    def _drama(self, db, series=True):
        sid = db.get_or_create_series("S") if series else None
        did = db.create_drama(title_en="D", series_id=sid)
        db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你", speaker="A"),
                            Line(idx=1, start=1, end=2, zh="好", speaker="B")])
        return did, sid

    def _post(self, client, did, label):
        return client.post(f"/api/characters/dramas/{did}/remember-series-character",
                           json={"speaker_label": label})

    def test_creates_and_links_with_pronouns(self, client, isolated_db):
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_character(did, "A", character_name="Lin", pronouns="xe/xem")
        r = self._post(client, did, "A")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] is True
        assert body["series_character"]["character_name"] == "Lin"
        assert body["series_character"]["pronouns"] == "xe/xem"
        assert body["character"]["series_character_id"] == body["series_character"]["id"]
        (sc,) = isolated_db.list_series_characters(sid)
        assert sc["voice_fingerprint"] is None
        # Only speaker A was touched.
        assert [c["speaker_label"] for c in isolated_db.list_characters_with_series_names(did)] == ["A"]

    def test_existing_name_links_without_overwriting(self, client, isolated_db):
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_series_character(sid, "Lin", aliases="L", notes="n", gender="she/her")
        isolated_db.upsert_character(did, "A", character_name="Lin", pronouns="he/him")
        r = self._post(client, did, "A")
        assert r.status_code == 200 and r.json()["created"] is False
        (sc,) = isolated_db.list_series_characters(sid)
        assert (sc["aliases"], sc["notes"], sc["gender"]) == ("L", "n", "she/her")

    def test_blends_voice_embedding_when_present(self, client, isolated_db):
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_character(did, "A", character_name="Lin")
        diarize.save_turns(isolated_db.drama_dir(did), [], embeddings={"A": [0.5, 0.5]})
        assert self._post(client, did, "A").status_code == 200
        (sc,) = isolated_db.list_series_characters(sid)
        assert sc["voice_fingerprint_samples"] == 1

    def test_second_remember_refused_without_reblending(self, client, isolated_db):
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_character(did, "A", character_name="Lin")
        diarize.save_turns(isolated_db.drama_dir(did), [], embeddings={"A": [0.5, 0.5]})
        first = self._post(client, did, "A")
        assert first.status_code == 200, first.text
        linked_id = first.json()["series_character"]["id"]
        (before,) = isolated_db.list_series_characters(sid)
        r = self._post(client, did, "A")
        assert r.status_code == 409
        assert _error(r)["code"] == "conflict"
        (after,) = isolated_db.list_series_characters(sid)
        assert after["voice_fingerprint_samples"] == before["voice_fingerprint_samples"] == 1
        assert after["voice_fingerprint"] == before["voice_fingerprint"]
        (row,) = isolated_db.list_characters_with_series_names(did)
        assert row["series_character_id"] == linked_id

    def test_already_linked_speaker_not_relinked_to_matching_name(self, client, isolated_db):
        # Speaker linked (e.g. via an accepted suggestion) to "Lin"; the
        # series also holds "Su" and the speaker's saved name is now "Su".
        # Remember must refuse rather than relink or blend into "Su".
        did, sid = self._drama(isolated_db)
        isolated_db.upsert_series_character(sid, "Lin")
        isolated_db.upsert_series_character(sid, "Su")
        by_name = {sc["character_name"]: sc for sc in isolated_db.list_series_characters(sid)}
        isolated_db.upsert_character(did, "A", character_name="Su",
                                     series_character_id=by_name["Lin"]["id"])
        diarize.save_turns(isolated_db.drama_dir(did), [], embeddings={"A": [0.5, 0.5]})
        r = self._post(client, did, "A")
        assert r.status_code == 409
        after = {sc["character_name"]: sc for sc in isolated_db.list_series_characters(sid)}
        assert after["Su"]["voice_fingerprint"] is None
        assert after["Su"]["voice_fingerprint_samples"] in (0, None)
        (row,) = isolated_db.list_characters_with_series_names(did)
        assert row["series_character_id"] == by_name["Lin"]["id"]

    def test_no_series_refused_clearly(self, client, isolated_db):
        did, _ = self._drama(isolated_db, series=False)
        isolated_db.upsert_character(did, "A", character_name="Lin")
        r = self._post(client, did, "A")
        assert r.status_code == 422
        assert "series" in _error(r)["message"]

    def test_unsaved_name_refused(self, client, isolated_db):
        did, sid = self._drama(isolated_db)
        r = self._post(client, did, "A")
        assert r.status_code == 422
        assert isolated_db.list_series_characters(sid) == []

    def test_unknown_speaker_and_drama_404(self, client, isolated_db):
        did, _ = self._drama(isolated_db)
        assert self._post(client, did, "Z").status_code == 404
        assert self._post(client, 999, "A").status_code == 404

    def test_bad_body_422(self, client, isolated_db):
        did, _ = self._drama(isolated_db)
        r = client.post(f"/api/characters/dramas/{did}/remember-series-character",
                        json={"speaker_label": "A", "name": "X"})
        assert r.status_code == 422
