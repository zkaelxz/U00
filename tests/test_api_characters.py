"""
Tests for the /api/characters endpoints (Migration Slice 42): a thin
layer over services/characters_service.py, so these cover the HTTP
contract -- body-keyed speaker labels, partial-update semantics, status
codes, error shape and the no-path rule. Uses TestClient against an
`isolated_db` library.
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import dub
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


def _drama(db, speakers=("A", "B"), **fields):
    did = db.create_drama(title_en="D", **fields)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 0.5, zh="你", speaker=s)
                        for i, s in enumerate(speakers)])
    return did


def _post(client, did, label, **fields):
    return client.post(f"/api/characters/dramas/{did}/character",
                       json={"speaker_label": label, **fields})


def _by_label(items, label):
    return next(c for c in items if c["speaker_label"] == label)


def _bank_entry(db, tmp_path):
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"RIFF")
    return db.save_voice_bank_entry("Voice1", str(clip), ref_text="hi", clone_engine="f5tts")


class TestList:
    def test_lists_speakers_and_character_row(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("A", "B", "A"))
        isolated_db.upsert_character(did, "A", character_name="Mei", pronouns="she/her")
        r = client.get(f"/api/characters/dramas/{did}")
        assert r.status_code == 200
        items = r.json()
        assert [c["speaker_label"] for c in items] == ["A", "B"]
        a = _by_label(items, "A")
        assert a["character_name"] == "Mei" and a["pronouns"] == "she/her"
        assert a["line_count"] == 2 and a["has_ref_audio"] is False
        assert _by_label(items, "B")["character_name"] == ""

    def test_unknown_drama_404(self, client):
        r = client.get("/api/characters/dramas/999")
        assert r.status_code == 404
        assert _error(r)["code"] == "not_found"

    def test_error_body_shape_422(self, client, isolated_db):
        did = _drama(isolated_db)
        r = _post(client, did, "A", clone_engine="nope")
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"


class TestUpdate:
    def test_partial_update_leaves_other_fields(self, client, isolated_db):
        did = _drama(isolated_db)
        _post(client, did, "A", character_name="Mei", pronouns="she/her")
        r = _post(client, did, "A", tts_voice="v1")
        assert r.status_code == 200
        body = r.json()
        assert body["tts_voice"] == "v1"
        assert body["character_name"] == "Mei" and body["pronouns"] == "she/her"

    def test_empty_string_clears(self, client, isolated_db):
        did = _drama(isolated_db)
        _post(client, did, "A", pronouns="she/her", tts_voice="v1")
        body = _post(client, did, "A", pronouns="").json()
        assert body["pronouns"] == "" and body["tts_voice"] == "v1"

    def test_explicit_null_is_not_passed(self, client, isolated_db):
        did = _drama(isolated_db)
        _post(client, did, "A", pronouns="she/her")
        r = _post(client, did, "A", pronouns=None, tts_voice="v1")
        assert r.status_code == 200 and r.json()["pronouns"] == "she/her"

    def test_extra_field_rejected(self, client, isolated_db):
        did = _drama(isolated_db)
        r = _post(client, did, "A", ref_audio_filename="x.wav")
        assert r.status_code == 422
        _error(r)

    def test_label_with_spaces_unicode_slash_roundtrips(self, client, isolated_db):
        label = "说话人 1/旁白"
        did = _drama(isolated_db, speakers=(label, "B"))
        r = _post(client, did, label, character_name="Narrator")
        assert r.status_code == 200 and r.json()["speaker_label"] == label
        got = _by_label(client.get(f"/api/characters/dramas/{did}").json(), label)
        assert got["character_name"] == "Narrator"

    def test_unknown_speaker_404(self, client, isolated_db):
        did = _drama(isolated_db)
        r = _post(client, did, "ZZ", pronouns="he/him")
        assert r.status_code == 404 and _error(r)["code"] == "not_found"

    def test_unknown_drama_404(self, client):
        r = _post(client, 999, "A", pronouns="he/him")
        assert r.status_code == 404 and _error(r)["code"] == "not_found"

    def test_missing_speaker_label_422(self, client, isolated_db):
        did = _drama(isolated_db)
        r = client.post(f"/api/characters/dramas/{did}/character", json={"pronouns": "x"})
        assert r.status_code == 422
        _error(r)

    def test_cross_drama_isolation(self, client, isolated_db):
        d1, d2 = _drama(isolated_db), _drama(isolated_db)
        _post(client, d1, "A", character_name="One", tts_voice="v1")
        b = _by_label(client.get(f"/api/characters/dramas/{d2}").json(), "A")
        assert b["character_name"] == "" and b["tts_voice"] == ""


@pytest.mark.parametrize("lang", ["zh", "ja", "ko"])
class TestCloneLanguageRule:
    def test_update_enforces_rule(self, client, isolated_db, lang):
        did = _drama(isolated_db, source_language=lang)
        for engine in dub.CLONE_ENGINES:
            r = _post(client, did, "A", clone_engine=engine)
            if dub.clone_engine_supports_language(engine, lang):
                assert r.status_code == 200 and r.json()["clone_engine"] == engine
            else:
                assert r.status_code == 422
                assert _error(r)["code"] == "validation_error"

    def test_clone_engines_match_capability(self, client, isolated_db, lang):
        did = _drama(isolated_db, source_language=lang)
        body = client.get(f"/api/characters/dramas/{did}/clone-engines").json()
        expected = {e for e in dub.CLONE_ENGINES if dub.clone_engine_supports_language(e, lang)}
        assert body["source_language"] == lang
        assert {e["id"] for e in body["engines"]} == expected


class TestCloneEngines:
    def test_shape(self, client, isolated_db):
        did = _drama(isolated_db)
        body = client.get(f"/api/characters/dramas/{did}/clone-engines").json()
        assert set(body) == {"source_language", "default_engine", "engines"}
        assert body["engines"]
        for e in body["engines"]:
            assert set(e) == {"id", "label", "is_default", "language_gated", "local_model"}
            assert all(isinstance(e[k], bool) for k in ("is_default", "language_gated", "local_model"))

    def test_unknown_drama_404(self, client):
        assert client.get("/api/characters/dramas/999/clone-engines").status_code == 404


class TestSeries:
    def test_list(self, client, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.upsert_series_character(sid, "Mei", gender="she/her")
        r = client.get(f"/api/characters/series/{sid}/characters")
        assert r.status_code == 200
        [c] = r.json()
        assert c["character_name"] == "Mei" and c["pronouns"] == "she/her"
        assert set(c) == {"id", "character_name", "aliases", "notes", "pronouns"}

    def test_unknown_series_empty_and_bad_id_422(self, client):
        assert client.get("/api/characters/series/999/characters").json() == []
        assert client.get("/api/characters/series/0/characters").status_code == 422


class TestVoiceBank:
    def test_list_has_no_path_or_clip(self, client, isolated_db, tmp_path):
        _bank_entry(isolated_db, tmp_path)
        r = client.get("/api/characters/voice-bank")
        assert r.status_code == 200
        [e] = r.json()
        assert e["name"] == "Voice1" and e["ref_text_present"] is True
        assert "clip_filename" not in e
        assert ".wav" not in r.text and str(tmp_path) not in r.text

    def test_apply_scoped_to_one_drama(self, client, isolated_db, tmp_path):
        eid = _bank_entry(isolated_db, tmp_path)
        d1, d2 = _drama(isolated_db), _drama(isolated_db)
        r = client.post(f"/api/characters/dramas/{d1}/voice-bank/apply",
                        json={"speaker_label": "A", "voice_bank_id": eid})
        assert r.status_code == 200
        assert r.json()["has_ref_audio"] is True and r.json()["clone_engine"] == "f5tts"
        assert ".wav" not in r.text and str(isolated_db.LIBRARY_DIR) not in r.text
        other = _by_label(client.get(f"/api/characters/dramas/{d2}").json(), "A")
        assert other["has_ref_audio"] is False

    def test_apply_not_found(self, client, isolated_db, tmp_path):
        eid = _bank_entry(isolated_db, tmp_path)
        did = _drama(isolated_db)
        url = f"/api/characters/dramas/{did}/voice-bank/apply"
        assert client.post("/api/characters/dramas/999/voice-bank/apply",
                           json={"speaker_label": "A", "voice_bank_id": eid}).status_code == 404
        assert client.post(url, json={"speaker_label": "ZZ", "voice_bank_id": eid}).status_code == 404
        r = client.post(url, json={"speaker_label": "A", "voice_bank_id": 999})
        assert r.status_code == 404 and _error(r)["code"] == "not_found"

    def test_apply_bad_id_422(self, client, isolated_db):
        did = _drama(isolated_db)
        r = client.post(f"/api/characters/dramas/{did}/voice-bank/apply",
                        json={"speaker_label": "A", "voice_bank_id": 0})
        assert r.status_code == 422
        _error(r)


def test_no_filesystem_path_in_responses(client, isolated_db, tmp_path):
    lib = str(isolated_db.LIBRARY_DIR)
    eid = _bank_entry(isolated_db, tmp_path)
    did = _drama(isolated_db)
    client.post(f"/api/characters/dramas/{did}/voice-bank/apply",
                json={"speaker_label": "A", "voice_bank_id": eid})
    for path in (f"/api/characters/dramas/{did}", f"/api/characters/dramas/{did}/clone-engines",
                 "/api/characters/voice-bank", "/api/characters/dramas/999"):
        assert lib not in client.get(path).text
    assert lib not in _post(client, did, "ZZ", pronouns="x").text
