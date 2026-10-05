"""
Tests for the /api/characters endpoints (Migration Slice 42): a thin
layer over services/characters_service.py, so these cover the HTTP
contract -- body-keyed speaker labels, partial-update semantics, status
codes, error shape and the no-path rule. Uses TestClient against an
`isolated_db` library.
"""

import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import dub
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services.service_errors import NotFoundError


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


# --- Hardening H1 -----------------------------------------------------------

def test_h1_hostile_text_never_echoed(client, isolated_db):
    did = _drama(isolated_db)
    hostile = "evil\n" + "x" * 5000 + "\u202e\u2603"
    for payload in ({"speaker_label": hostile, "pronouns": "x"},
                    {"speaker_label": "A", "clone_engine": hostile},
                    {"speaker_label": "A", "clone_engine": "chatterbox"}):
        r = client.post(f"/api/characters/dramas/{did}/character", json=payload)
        assert r.status_code in (404, 422)
        assert "evil" not in r.text and "xxxx" not in r.text and "chatterbox" not in r.text
    r = client.post(f"/api/characters/dramas/{did}/voice-bank/apply",
                    json={"speaker_label": hostile, "voice_bank_id": 1})
    assert r.status_code in (404, 422) and "evil" not in r.text


def test_h1_oversized_ids_never_500(client, isolated_db):
    big = 10**30
    did = _drama(isolated_db)
    assert client.get(f"/api/characters/dramas/{big}").status_code == 422
    assert client.get(f"/api/characters/dramas/{big}/clone-engines").status_code == 422
    assert client.get(f"/api/characters/series/{big}/characters").status_code == 422
    assert client.post(f"/api/characters/dramas/{big}/character",
                       json={"speaker_label": "A", "pronouns": "x"}).status_code == 422
    assert client.post(f"/api/characters/dramas/{did}/voice-bank/apply",
                       json={"speaker_label": "A", "voice_bank_id": big}).status_code == 422


class TestRenameSpeaker:
    def _rename(self, client, did, label, name):
        return client.post(f"/api/characters/dramas/{did}/rename-speaker",
                           json={"speaker_label": label, "new_name": name})

    def test_renames_every_line_and_moves_the_character_row(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2", "Speaker 1"))
        isolated_db.upsert_character(did, "Speaker 1", pronouns="she/her", tts_voice="v1")
        lines = isolated_db.load_line_objects(did)
        lines[0].en = "Hello"
        isolated_db.save_lines(did, lines, fields=("en",))
        r = self._rename(client, did, "Speaker 1", "  Mei ")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["renamed"] == 2
        mei = _by_label(body["characters"], "Mei")
        assert (mei["character_name"], mei["pronouns"], mei["tts_voice"], mei["line_count"]) == \
            ("Mei", "she/her", "v1", 2)
        assert all(c["speaker_label"] != "Speaker 1" for c in body["characters"])
        saved = isolated_db.load_line_objects(did)
        assert [(ln.speaker, ln.speaker_manual) for ln in saved] == \
            [("Mei", True), ("Speaker 2", False), ("Mei", True)]
        assert saved[0].en == "Hello"

    def test_undo_restores_labels_flags_and_row(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2", "Speaker 1"))
        lines = isolated_db.load_line_objects(did)
        lines[2].speaker_manual = True
        isolated_db.save_lines(did, lines, fields=("speaker_manual",))
        isolated_db.upsert_character(did, "Speaker 1", tts_voice="v1")
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 200, r.text
        assert r.json()["undo"] is None
        saved = isolated_db.load_line_objects(did)
        assert [(ln.speaker, ln.speaker_manual) for ln in saved] == \
            [("Speaker 1", False), ("Speaker 2", False), ("Speaker 1", True)]
        row = _by_label(isolated_db.list_characters(did), "Speaker 1")
        assert row["tts_voice"] == "v1" and row["character_name"] is None
        assert not [c for c in isolated_db.list_characters(did) if c["speaker_label"] == "Mei"]

    def test_undo_refused_when_a_line_was_edited_since(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 1"))
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        lines = isolated_db.load_line_objects(did)
        lines[1].speaker = "Someone"
        isolated_db.save_lines(did, lines, fields=("speaker",))
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Mei", "Someone"]

    def test_refuses_a_name_another_speaker_has(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2"))
        r = self._rename(client, did, "Speaker 1", "Speaker 2")
        assert r.status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Speaker 1", "Speaker 2"]

    def test_blank_unchanged_and_unknown(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        assert self._rename(client, did, "Speaker 1", "   ").status_code == 422
        assert self._rename(client, did, "Speaker 1", "Speaker 1").status_code == 422
        assert self._rename(client, did, "Nobody", "Mei").status_code == 404
        assert self._rename(client, 9999, "Speaker 1", "Mei").status_code == 404

    def test_refused_while_a_job_runs(self, client, isolated_db, monkeypatch):
        from services import drama_service
        monkeypatch.setattr(drama_service, "job_running_for_drama", lambda *_a, **_k: True)
        did = _drama(isolated_db, speakers=("Speaker 1",))
        assert self._rename(client, did, "Speaker 1", "Mei").status_code == 409
        assert isolated_db.load_line_objects(did)[0].speaker == "Speaker 1"

    def test_undo_with_a_foreign_line_id_touches_nothing(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        other = _drama(isolated_db, speakers=("Mei",))
        self._rename(client, did, "Speaker 1", "Mei")
        undo = {"speaker_label": "Mei", "previous_label": "Speaker 1", "previous_character_name": None,
                "previous": [{"id": isolated_db.load_line_objects(other)[0].id,
                              "speaker": "Speaker 1", "speaker_manual": False}]}
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 409
        assert isolated_db.load_line_objects(other)[0].speaker == "Mei"
        assert isolated_db.load_line_objects(did)[0].speaker == "Mei"
        labels = [c["speaker_label"] for c in isolated_db.list_characters(did)]
        assert "Mei" in labels and "Speaker 1" not in labels

    def test_undo_after_new_lines_carry_the_name_is_refused(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 1"))
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=2, start=9, end=10, zh="再", speaker="Mei"))
        isolated_db.save_lines(did, lines)
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Mei"] * 3
        assert any(c["speaker_label"] == "Mei" for c in isolated_db.list_characters(did))

    @staticmethod
    def _fail_on(monkeypatch, db, fragment):
        import sqlite3
        real = db.get_conn

        class Proxy:
            def __init__(self, conn):
                self._c = conn

            def execute(self, sql, *a):
                if fragment in sql:
                    raise sqlite3.OperationalError("database is locked")
                return self._c.execute(sql, *a)

            def __getattr__(self, name):
                return getattr(self._c, name)
        monkeypatch.setattr(db, "get_conn", lambda: Proxy(real()))

    def _state(self, db, did):
        return ([(ln.speaker, ln.speaker_manual) for ln in db.load_line_objects(did)],
                sorted((c["speaker_label"], c["character_name"]) for c in db.list_characters(did)))

    @pytest.mark.parametrize("fragment", ["UPDATE characters SET", "UPDATE lines SET speaker"])
    def test_a_failure_anywhere_changes_nothing(self, client, isolated_db, monkeypatch, fragment):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2"))
        isolated_db.upsert_character(did, "Speaker 1", tts_voice="v")
        before = self._state(isolated_db, did)
        self._fail_on(monkeypatch, isolated_db, fragment)
        assert self._rename(client, did, "Speaker 1", "Mei").status_code >= 400
        monkeypatch.undo()
        assert self._state(isolated_db, did) == before

    @pytest.mark.parametrize("fragment", ["UPDATE characters SET", "UPDATE lines SET speaker"])
    def test_an_undo_failure_changes_nothing(self, client, isolated_db, monkeypatch, fragment):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2"))
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        before = self._state(isolated_db, did)
        self._fail_on(monkeypatch, isolated_db, fragment)
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code >= 400
        monkeypatch.undo()
        assert self._state(isolated_db, did) == before

    def test_a_compare_and_set_miss_changes_nothing(self, client, isolated_db, monkeypatch):
        from services import characters_service as cs
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 1"))
        real = cs._lines_with_label

        def racing(drama_id, label):
            found = real(drama_id, label)
            lines = isolated_db.load_line_objects(drama_id)
            lines[1].speaker = "Someone"
            isolated_db.save_lines(drama_id, lines, fields=("speaker",))
            return found
        monkeypatch.setattr(cs, "_lines_with_label", racing)
        r = self._rename(client, did, "Speaker 1", "Mei")
        assert r.status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Speaker 1", "Someone"]

    def test_undo_restores_each_original_manual_flag(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 1"))
        lines = isolated_db.load_line_objects(did)
        lines[0].speaker_manual = True
        isolated_db.save_lines(did, lines, fields=("speaker_manual",))
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert [ln.speaker_manual for ln in isolated_db.load_line_objects(did)] == [True, False]

    def test_a_current_label_undo_could_not_restore_is_refused(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("a/b", "x\u200by"))
        assert self._rename(client, did, "a/b", "Mei").status_code == 422
        assert self._rename(client, did, "x\u200by", "Mei").status_code == 422
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["a/b", "x\u200by"]

    def test_undo_is_not_a_free_rename(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2"))
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        url = f"/api/characters/dramas/{did}/rename-speaker/undo"
        assert client.post(url, json={"undo": {**undo, "previous_label": "speaker  2", "previous": [{**undo["previous"][0], "speaker": "speaker  2"}]}}).status_code == 409
        assert client.post(url, json={"undo": {**undo, "previous_label": "a/b"}}).status_code == 422
        assert client.post(url, json={"undo": {**undo, "previous_label": "a\u202eb"}}).status_code == 422

    def test_a_series_name_on_another_speaker_is_taken(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2"))
        sid = isolated_db.insert_series_character(isolated_db.create_series("S"), "Lin")
        isolated_db.upsert_character(did, "Speaker 2", series_character_id=sid)
        assert self._rename(client, did, "Speaker 1", "lin").status_code == 409

    def test_zero_width_characters_do_not_dodge_the_clash(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2"))
        assert self._rename(client, did, "Speaker 1", "Speaker\u200b 2").status_code == 422

    def test_a_speaker_with_no_lines_can_be_renamed_and_undone(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        isolated_db.upsert_character(did, "Ghost", tts_voice="v")
        r = self._rename(client, did, "Ghost", "Mei")
        assert r.status_code == 200 and r.json()["renamed"] == 0
        assert [c["speaker_label"] for c in r.json()["characters"]] == ["Mei", "Speaker 1"]
        assert self._rename(client, did, "Speaker 1", "mei").status_code == 409
        undo = r.json()["undo"]
        assert undo["previous"] == []
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 200
        assert "Ghost" in [c["speaker_label"] for c in r.json()["characters"]]

    def test_a_zero_line_undo_is_refused_once_lines_carry_the_name(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        isolated_db.upsert_character(did, "Ghost")
        undo = self._rename(client, did, "Ghost", "Mei").json()["undo"]
        lines = isolated_db.load_line_objects(did)
        lines[0].speaker = "Mei"
        isolated_db.save_lines(did, lines, fields=("speaker",))
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 409

    def test_names_compare_normalised_against_labels_and_names(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 2", "Speaker 3"))
        isolated_db.upsert_character(did, "Speaker 2", character_name="Ａｎｎａ")
        assert self._rename(client, did, "Speaker 1", " anna ").status_code == 409
        assert self._rename(client, did, "Speaker 1", "speaker   3").status_code == 409

    def test_bad_names_are_422(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        for bad in ("a/b", "a\\b", "x..y", "a\x07b", "a\u200bb", "a\u2028b", "n" * 101):
            assert self._rename(client, did, "Speaker 1", bad).status_code == 422, bad

    def test_series_linked_speaker_is_refused(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        sid = isolated_db.insert_series_character(isolated_db.create_series("S"), "Lin")
        isolated_db.upsert_character(did, "Speaker 1", series_character_id=sid)
        assert self._rename(client, did, "Speaker 1", "Mei").status_code == 409
        assert isolated_db.load_line_objects(did)[0].speaker == "Speaker 1"

    def test_a_lone_surrogate_is_422_not_500(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker",
                        content=b'{"speaker_label":"Speaker 1","new_name":"a\\ud800b"}',
                        headers={"content-type": "application/json"})
        assert r.status_code == 422
        assert isolated_db.load_line_objects(did)[0].speaker == "Speaker 1"

    def test_in_transaction_name_taken_changes_nothing(self, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        isolated_db.upsert_character(did, "Speaker 1")
        isolated_db.upsert_character(did, "Mei")
        line = isolated_db.load_line_objects(did)[0]
        r = isolated_db.rename_speaker_atomic(did, "Speaker 1", "Mei", "Mei", [
            {"id": line.id, "expect_speaker": "Speaker 1", "expect_manual": False,
             "speaker": "Mei", "manual": True}])
        assert r == "name_taken"
        assert isolated_db.load_line_objects(did)[0].speaker == "Speaker 1"

    def test_a_line_outside_the_update_on_either_label_blocks_it(self, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 1", "Mei"))
        lines = isolated_db.load_line_objects(did)
        up = [{"id": lines[0].id, "expect_speaker": "Speaker 1", "expect_manual": False,
               "speaker": "Mei", "manual": True}]
        assert isolated_db.rename_speaker_atomic(did, "Speaker 1", "Mei", "Mei", up) == "changed"
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Speaker 1", "Speaker 1", "Mei"]

    def test_undo_payload_with_a_duplicate_or_extra_id_is_refused(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Speaker 1"))
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        url = f"/api/characters/dramas/{did}/rename-speaker/undo"
        first = undo["previous"][0]
        dup = {**undo, "previous": undo["previous"] + [first]}
        extra = {**undo, "previous": undo["previous"] + [{**first, "id": 9999}]}
        assert client.post(url, json={"undo": dup}).status_code == 409
        assert client.post(url, json={"undo": extra}).status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["Mei", "Mei"]

    def test_dismissed_voice_matches_move_with_the_speaker(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        sid = isolated_db.insert_series_character(isolated_db.create_series("S"), "Lin")
        isolated_db.dismiss_voice_suggestion(did, "Speaker 1", sid)
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        assert isolated_db.list_dismissed_voice_suggestions(did) == {("Mei", sid)}
        client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert isolated_db.list_dismissed_voice_suggestions(did) == {("Speaker 1", sid)}

    def test_an_existing_label_with_a_zero_width_char_still_clashes(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1", "Spe\u200baker 2"))
        assert self._rename(client, did, "Speaker 1", "Speaker 2").status_code == 409

    def test_a_stored_name_with_a_zwj_sequence_is_restored_by_undo(self, client, isolated_db):
        did = _drama(isolated_db, speakers=("Speaker 1",))
        name = "Mei \U0001F469\u200d\U0001F4BB"
        assert _post(client, did, "Speaker 1", character_name=name).status_code == 200
        undo = self._rename(client, did, "Speaker 1", "Mei").json()["undo"]
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker/undo", json={"undo": undo})
        assert r.status_code == 200, r.text
        assert _by_label(r.json()["characters"], "Speaker 1")["character_name"] == name


class TestMergeSpeakers:
    FORBIDDEN_KEYS = {"ref_audio_filename", "ref_text", "snapshot", "source_row", "target_row"}

    def _merge(self, client, did, source, target):
        return client.post(f"/api/characters/dramas/{did}/merge-speakers",
                           json={"source_label": source, "target_label": target, "confirm": True})

    def _undo(self, client, did, undo):
        undo_id = undo["undo_id"] if isinstance(undo, dict) else undo
        return client.post(f"/api/characters/dramas/{did}/merge-speakers/undo",
                           json={"undo_id": undo_id})

    def _setup(self, db):
        did = _drama(db, speakers=("S1", "S3", "S1", "S2"))
        lines = db.load_line_objects(did)
        for ln in lines:
            ln.en = f"en{ln.idx}"
        lines[0].speaker_manual = True
        db.save_lines(did, lines, fields=("en", "speaker_manual"))
        return did

    def _clip(self, db, did, name):
        path = os.path.join(db.drama_dir(did), name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"RIFF")
        return path

    def _rows(self, db, did):
        return sorted(({k: v for k, v in x.items() if k != "id"} for x in db.list_characters(did)),
                      key=lambda x: x["speaker_label"])

    def test_moves_lines_and_folds_the_row_without_touching_other_fields(self, client, isolated_db):
        did = self._setup(isolated_db)
        isolated_db.upsert_character(did, "S1", character_name="Mei", tts_voice="v1")
        isolated_db.upsert_character(did, "S3", pronouns="she/her", tts_voice="v3")
        r = self._merge(client, did, "S3", "S1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["moved"] == 1
        labels = [c["speaker_label"] for c in body["characters"]]
        assert labels == ["S1", "S2"]
        mei = _by_label(body["characters"], "S1")
        assert (mei["character_name"], mei["tts_voice"], mei["pronouns"], mei["line_count"]) == \
            ("Mei", "v1", "she/her", 3)
        assert [r["speaker_label"] for r in isolated_db.list_characters(did)].count("S3") == 0
        saved = isolated_db.load_line_objects(did)
        assert [(ln.speaker, ln.en) for ln in saved] == \
            [("S1", "en0"), ("S1", "en1"), ("S1", "en2"), ("S2", "en3")]
        assert saved[0].speaker_manual is True and saved[1].speaker_manual is True

    def test_response_never_carries_rows_clip_names_or_paths(self, client, isolated_db):
        did = self._setup(isolated_db)
        clip = "voice_refs/clone_ref_" + "a" * 32 + ".wav"
        self._clip(isolated_db, did, clip)
        isolated_db.upsert_character(did, "S3", ref_audio_filename=clip, ref_text="secret words")
        def walk(x):
            if isinstance(x, dict):
                assert not (set(x) & self.FORBIDDEN_KEYS), x
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str):
                assert "voice_refs" not in x and "secret words" not in x and "clone_ref_" not in x
        resp = self._merge(client, did, "S3", "S1")
        undo = resp.json()["undo"]
        assert set(undo) == {"undo_id", "expires_in"}
        walk(resp.json())
        r = self._undo(client, did, undo)
        assert r.status_code == 200
        walk(r.json())

    def test_undo_restores_lines_flags_and_both_rows(self, client, isolated_db):
        did = self._setup(isolated_db)
        isolated_db.upsert_character(did, "S1", character_name="Mei")
        isolated_db.upsert_character(did, "S3", pronouns="she/her", tts_voice="v3")
        before_rows = self._rows(isolated_db, did)
        before_lines = [(ln.speaker, ln.speaker_manual) for ln in isolated_db.load_line_objects(did)]
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        r = self._undo(client, did, undo)
        assert r.status_code == 200, r.text
        assert r.json()["undo"] is None
        assert self._rows(isolated_db, did) == before_rows
        assert [(ln.speaker, ln.speaker_manual) for ln in isolated_db.load_line_objects(did)] == before_lines

    def test_undo_removes_a_target_row_the_merge_created(self, client, isolated_db):
        did = self._setup(isolated_db)
        isolated_db.upsert_character(did, "S3", tts_voice="v3")
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert self._undo(client, did, undo).status_code == 200
        assert [r["speaker_label"] for r in isolated_db.list_characters(did)] == ["S3"]

    def test_undo_is_single_use(self, client, isolated_db):
        did = self._setup(isolated_db)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert self._undo(client, did, undo).status_code == 200
        assert self._undo(client, did, undo).status_code == 404

    def test_forged_unknown_or_other_drama_undo_writes_nothing(self, client, isolated_db):
        did = self._setup(isolated_db)
        other = self._setup(isolated_db)
        isolated_db.upsert_character(did, "S3", tts_voice="v3")
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        after = self._rows(isolated_db, did)
        assert self._undo(client, did, "x" * 32).status_code == 404
        assert self._undo(client, other, undo).status_code == 404
        assert client.post(f"/api/characters/dramas/{did}/merge-speakers/undo",
                           json={"undo_id": "short"}).status_code == 422
        assert client.post(f"/api/characters/dramas/{did}/merge-speakers/undo",
                           json={"undo": {"source_row": {}}}).status_code == 422
        assert self._rows(isolated_db, did) == after
        assert self._undo(client, did, undo).status_code == 200  # the real id still works

    def test_undo_is_scoped_to_the_user_who_merged(self, client, isolated_db):
        did = self._setup(isolated_db)
        from services import characters_service
        out = characters_service.merge_speakers(did, "S3", "S1", user_id=7)
        with pytest.raises(NotFoundError):
            characters_service.undo_merge_speakers(did, out["undo"]["undo_id"], user_id=8)
        with pytest.raises(NotFoundError):
            characters_service.undo_merge_speakers(did, out["undo"]["undo_id"], user_id=None)
        assert characters_service.undo_merge_speakers(did, out["undo"]["undo_id"], user_id=7)["moved"] == 1

    def test_expired_undo_is_gone(self, client, isolated_db, monkeypatch):
        did = self._setup(isolated_db)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + undo["expires_in"] + 1)
        assert self._undo(client, did, undo).status_code == 404
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)][1] == "S1"

    def test_series_link_is_inherited_or_conflicts(self, client, isolated_db):
        sid = isolated_db.create_series("Show")
        did = _drama(isolated_db, speakers=("S1", "S3"), series_id=sid)
        a = isolated_db.insert_series_character(sid, "Mei")
        b = isolated_db.insert_series_character(sid, "Lan")
        isolated_db.upsert_character(did, "S3", series_character_id=a)
        r = self._merge(client, did, "S3", "S1")
        assert r.status_code == 200, r.text
        assert _by_label(r.json()["characters"], "S1")["series_character_id"] == a
        did2 = _drama(isolated_db, speakers=("S1", "S3"), series_id=sid)
        isolated_db.upsert_character(did2, "S1", series_character_id=a)
        isolated_db.upsert_character(did2, "S3", series_character_id=b)
        assert self._merge(client, did2, "S3", "S1").status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did2)] == ["S1", "S3"]

    def test_validation(self, client, isolated_db):
        did = self._setup(isolated_db)
        assert self._merge(client, did, "S1", "S1").status_code == 422
        assert self._merge(client, did, "Nobody", "S1").status_code == 404
        assert self._merge(client, did, "S1", "Nobody").status_code == 404
        assert self._merge(client, 9999, "S1", "S3").status_code == 404

    def test_refused_while_a_job_runs(self, client, isolated_db, monkeypatch):
        did = self._setup(isolated_db)
        from services import drama_service
        monkeypatch.setattr(drama_service, "job_running_for_drama", lambda _id: True)
        assert self._merge(client, did, "S3", "S1").status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)][1] == "S3"

    def test_undo_refused_while_a_job_runs_and_keeps_the_id(self, client, isolated_db, monkeypatch):
        did = self._setup(isolated_db)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        from services import drama_service
        monkeypatch.setattr(drama_service, "job_running_for_drama", lambda _id: True)
        assert self._undo(client, did, undo).status_code == 409
        monkeypatch.undo()
        assert self._undo(client, did, undo).status_code == 200

    def test_undo_refused_when_a_moved_line_changed_or_label_reused(self, client, isolated_db):
        did = self._setup(isolated_db)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        lines = isolated_db.load_line_objects(did)
        lines[1].speaker = "S2"
        isolated_db.save_lines(did, lines, fields=("speaker",))
        assert self._undo(client, did, undo).status_code == 409
        assert self._undo(client, did, undo).status_code == 404  # a 409 spends the id
        undo = self._merge(client, did, "S2", "S1").json()["undo"]
        lines = isolated_db.load_line_objects(did)
        lines[3].speaker = "S2"
        isolated_db.save_lines(did, lines, fields=("speaker",))
        assert self._undo(client, did, undo).status_code == 409

    def test_undo_after_the_target_was_renamed_is_refused_and_writes_nothing(self, client, isolated_db):
        did = self._setup(isolated_db)
        isolated_db.upsert_character(did, "S1", tts_voice="v1")
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        r = client.post(f"/api/characters/dramas/{did}/rename-speaker",
                        json={"speaker_label": "S1", "new_name": "Mei"})
        assert r.status_code == 200, r.text
        rows = self._rows(isolated_db, did)
        lines = [(ln.speaker, ln.speaker_manual) for ln in isolated_db.load_line_objects(did)]
        resp = self._undo(client, did, undo)
        assert resp.status_code == 409
        assert resp.json()["error"]["message"]
        assert self._rows(isolated_db, did) == rows
        assert [(ln.speaker, ln.speaker_manual) for ln in isolated_db.load_line_objects(did)] == lines
        assert "Mei" in [r["speaker_label"] for r in isolated_db.list_characters(did)]

    def test_undo_after_the_target_row_was_edited_is_refused(self, client, isolated_db):
        did = self._setup(isolated_db)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert _post(client, did, "S1", pronouns="he/him").status_code == 200
        assert self._undo(client, did, undo).status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)][1] == "S1"

    def test_target_row_edited_behind_the_services_back_is_a_409(self, client, isolated_db):
        did = self._setup(isolated_db)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        isolated_db.upsert_character(did, "S1", pronouns="he/him")
        before = self._rows(isolated_db, did)
        assert self._undo(client, did, undo).status_code == 409
        assert self._rows(isolated_db, did) == before

    def test_series_link_conflict_is_checked_inside_the_transaction(self, client, isolated_db, monkeypatch):
        sid = isolated_db.create_series("Show")
        did = _drama(isolated_db, speakers=("S1", "S3"), series_id=sid)
        a = isolated_db.insert_series_character(sid, "Mei")
        b = isolated_db.insert_series_character(sid, "Lan")
        isolated_db.upsert_character(did, "S1", series_character_id=a)
        real = isolated_db.merge_speakers_atomic

        def racing(*args, **kw):
            isolated_db.upsert_character(did, "S3", series_character_id=b)
            return real(*args, **kw)
        monkeypatch.setattr(isolated_db, "merge_speakers_atomic", racing)
        assert self._merge(client, did, "S3", "S1").status_code == 409
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["S1", "S3"]

    def test_lines_moved_are_the_ones_on_source_at_merge_time(self, client, isolated_db, monkeypatch):
        did = self._setup(isolated_db)
        real = isolated_db.merge_speakers_atomic

        def racing(*args, **kw):
            lines = isolated_db.load_line_objects(did)
            lines[3].speaker = "S3"
            isolated_db.save_lines(did, lines, fields=("speaker",))
            return real(*args, **kw)
        monkeypatch.setattr(isolated_db, "merge_speakers_atomic", racing)
        body = self._merge(client, did, "S3", "S1").json()
        assert body["moved"] == 2
        assert "S3" not in [ln.speaker for ln in isolated_db.load_line_objects(did)]
        assert self._undo(client, did, body["undo"]).status_code == 200
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)] == ["S1", "S3", "S1", "S3"]

    # --- the reference voice moves as one group ---------------------------------

    def test_voice_group_is_taken_whole_only_when_target_has_no_clip_or_design(self, client, isolated_db):
        did = self._setup(isolated_db)
        clip = "voice_refs/clone_ref_" + "b" * 32 + ".wav"
        self._clip(isolated_db, did, clip)
        isolated_db.upsert_character(did, "S3", ref_audio_filename=clip, ref_text="src words",
                                     clone_engine="f5tts")
        isolated_db.upsert_character(did, "S1", ref_text="target words", clone_engine="cosyvoice")
        assert self._merge(client, did, "S3", "S1").status_code == 200
        row = next(r for r in isolated_db.list_characters(did) if r["speaker_label"] == "S1")
        assert (row["ref_audio_filename"], row["ref_text"], row["clone_engine"]) == \
            (clip, "src words", "f5tts")

    def test_a_target_clip_keeps_its_own_transcript_and_engine(self, client, isolated_db):
        did = self._setup(isolated_db)
        mine = "voice_refs/clone_ref_" + "c" * 32 + ".wav"
        theirs = "voice_refs/clone_ref_" + "d" * 32 + ".wav"
        self._clip(isolated_db, did, mine)
        self._clip(isolated_db, did, theirs)
        isolated_db.upsert_character(did, "S1", ref_audio_filename=mine)
        isolated_db.upsert_character(did, "S3", ref_audio_filename=theirs, ref_text="their words",
                                     clone_engine="f5tts")
        assert self._merge(client, did, "S3", "S1").status_code == 200
        row = next(r for r in isolated_db.list_characters(did) if r["speaker_label"] == "S1")
        assert row["ref_audio_filename"] == mine
        assert not row["ref_text"] and not row["clone_engine"]

    def test_a_target_design_blocks_the_sources_clip(self, client, isolated_db):
        did = self._setup(isolated_db)
        clip = "voice_refs/clone_ref_" + "e" * 32 + ".wav"
        self._clip(isolated_db, did, clip)
        isolated_db.upsert_character(did, "S1", voice_design="warm and low")
        isolated_db.upsert_character(did, "S3", ref_audio_filename=clip, ref_text="words")
        assert self._merge(client, did, "S3", "S1").status_code == 200
        row = next(r for r in isolated_db.list_characters(did) if r["speaker_label"] == "S1")
        assert row["voice_design"] == "warm and low"
        assert not row["ref_audio_filename"] and not row["ref_text"]

    # --- real clip names, restored by the server ----------------------------------

    @pytest.mark.parametrize("name", ["voice_refs/clone_ref_" + "1" * 32 + ".wav",
                                      "voice_refs/clone_pick_" + "2" * 32 + ".wav"])
    def test_undo_restores_an_uploaded_or_picked_clip_link(self, client, isolated_db, name):
        did = self._setup(isolated_db)
        path = self._clip(isolated_db, did, name)
        isolated_db.upsert_character(did, "S3", ref_audio_filename=name, ref_text="hello")
        before = self._rows(isolated_db, did)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert self._undo(client, did, undo).status_code == 200
        assert self._rows(isolated_db, did) == before
        assert os.path.isfile(path)

    def test_undo_refused_when_the_stored_clip_vanished(self, client, isolated_db):
        did = self._setup(isolated_db)
        name = "voice_refs/clone_ref_" + "3" * 32 + ".wav"
        path = self._clip(isolated_db, did, name)
        isolated_db.upsert_character(did, "S3", ref_audio_filename=name, ref_text="hello")
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        os.remove(path)
        s1_after = self._rows(isolated_db, did)
        assert self._undo(client, did, undo).status_code == 409
        assert self._rows(isolated_db, did) == s1_after

    # --- dismissed voice matches ---------------------------------------------------

    def test_undo_brings_back_rejected_voice_matches_exactly(self, client, isolated_db):
        sid = isolated_db.create_series("Show")
        did = _drama(isolated_db, speakers=("S1", "S3"), series_id=sid)
        a = isolated_db.insert_series_character(sid, "Mei")
        b = isolated_db.insert_series_character(sid, "Lan")
        conn = isolated_db.get_conn()
        for label, sc in (("S1", a), ("S3", a), ("S3", b)):  # S1/a collides with S3/a
            conn.execute("INSERT INTO voice_suggestion_dismissals (drama_id, speaker_label, "
                         "series_character_id, created_at) VALUES (?, ?, ?, 't')", (did, label, sc))
        conn.commit()
        conn.close()

        def dismissals():
            c = isolated_db.get_conn()
            try:
                return sorted(tuple(r) for r in c.execute(
                    "SELECT speaker_label, series_character_id, created_at "
                    "FROM voice_suggestion_dismissals WHERE drama_id = ?", (did,)))
            finally:
                c.close()
        before = dismissals()
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert dismissals() == [("S1", a, "t"), ("S1", b, "t")]
        assert self._undo(client, did, undo).status_code == 200
        assert dismissals() == before

    # --- clip files: no merge, undo or edit path ever deletes one -------------------
    # Unused clips are listed and removed from the PC-only Disk usage view.

    def _unused_clip_setup(self, db, kind="clone_pick_"):
        did = self._setup(db)
        mine = "voice_refs/clone_ref_" + "4" * 32 + ".wav"
        theirs = f"voice_refs/{kind}" + "5" * 32 + ".wav"
        self._clip(db, did, mine)
        path = self._clip(db, did, theirs)
        db.upsert_character(did, "S1", ref_audio_filename=mine)
        db.upsert_character(did, "S3", ref_audio_filename=theirs)
        return did, path

    def _expire(self, monkeypatch, undo):
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + undo["expires_in"] + 1)

    @pytest.mark.parametrize("kind", ["clone_pick_", "clone_ref_"])
    @pytest.mark.parametrize("action", ["merge", "expire", "retire", "spend", "undo"])
    def test_no_path_deletes_a_clip_file(self, client, isolated_db, monkeypatch, kind, action):
        did, path = self._unused_clip_setup(isolated_db, kind=kind)
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert os.path.isfile(path)
        if action == "expire":
            self._expire(monkeypatch, undo)
        elif action == "spend":
            isolated_db.upsert_character(did, "S1", pronouns="he/him")
            assert self._undo(client, did, undo).status_code == 409
        elif action == "undo":
            assert self._undo(client, did, undo).status_code == 200
        if action != "merge":
            assert _post(client, did, "S1", pronouns="she/her").status_code == 200
            assert client.get(f"/api/characters/dramas/{did}").status_code == 200
        assert os.path.isfile(path)

    def test_live_undo_clips_are_listed_until_the_record_expires_or_is_retired(self, client, isolated_db, monkeypatch):
        did, _ = self._unused_clip_setup(isolated_db)
        rel = "voice_refs/clone_pick_" + "5" * 32 + ".wav"
        assert isolated_db.live_speaker_merge_undo_clips(did, time.time()) == set()
        undo = self._merge(client, did, "S3", "S1").json()["undo"]
        assert isolated_db.live_speaker_merge_undo_clips(did, time.time()) == {rel}
        assert isolated_db.live_speaker_merge_undo_clips(did + 1, time.time()) == set()
        assert isolated_db.live_speaker_merge_undo_clips(
            did, time.time() + undo["expires_in"] + 1) == set()
        assert _post(client, did, "S1", pronouns="he/him").status_code == 200  # retires it
        assert isolated_db.live_speaker_merge_undo_clips(did, time.time()) == set()

    def test_a_merge_that_fails_leaves_earlier_undos_usable(self, client, isolated_db):
        did = self._setup(isolated_db)
        sid = isolated_db.create_series("A")
        a = isolated_db.insert_series_character(sid, "X")
        b = isolated_db.insert_series_character(sid, "Y")
        isolated_db.upsert_character(did, "S1", series_character_id=a)
        isolated_db.upsert_character(did, "S2", series_character_id=b)
        first = self._merge(client, did, "S3", "S1").json()["undo"]
        assert self._merge(client, did, "S2", "S1").status_code == 409
        assert self._undo(client, did, first).status_code == 200

    def test_a_failing_bookkeeping_step_never_changes_the_outcome(self, client, isolated_db, monkeypatch):
        import sqlite3
        did = self._setup(isolated_db)
        def locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(isolated_db, "drop_speaker_merge_undos", locked)
        monkeypatch.setattr(isolated_db, "retire_speaker_merge_undos", locked)
        merged = self._merge(client, did, "S3", "S1")
        assert merged.status_code == 200
        assert _post(client, did, "S1", pronouns="he/him").status_code == 200  # committed edit, not a 500
        assert self._undo(client, did, merged.json()["undo"]).status_code == 409  # target edited after the merge
        assert self._undo(client, did, {"undo_id": "x" * 30}).status_code == 404  # the real answer

    def test_merge_needs_confirm(self, client, isolated_db):
        did = self._setup(isolated_db)
        for body in ({}, {"confirm": False}, {"confirm": "yes"}):
            r = client.post(f"/api/characters/dramas/{did}/merge-speakers",
                            json={"source_label": "S3", "target_label": "S1", **body})
            assert r.status_code == 422
        assert [ln.speaker for ln in isolated_db.load_line_objects(did)][1] == "S3"
