import os

import pytest

import dub
from core import Line
from services import characters_service as cs
from services.service_errors import InvalidInputError, NotFoundError


def _drama(db, speakers=("A", "B"), **fields):
    did = db.create_drama(title_en="D", **fields)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 0.5, zh="你", speaker=s)
                        for i, s in enumerate(speakers)])
    return did


def _by_label(items, label):
    return next(c for c in items if c["speaker_label"] == label)


class TestList:
    def test_unknown_drama(self, isolated_db):
        with pytest.raises(NotFoundError):
            cs.list_characters(999)

    def test_lists_line_speakers_with_counts_and_blank_fields(self, isolated_db):
        did = _drama(isolated_db, speakers=("A", "A", "B"))
        out = cs.list_characters(did)
        assert [c["speaker_label"] for c in out] == ["A", "B"]
        a = _by_label(out, "A")
        assert a["line_count"] == 2 and a["character_name"] == ""
        assert a["has_ref_audio"] is False and a["ref_text_present"] is False

    def test_no_paths_or_ref_filename(self, isolated_db):
        did = _drama(isolated_db)
        isolated_db.upsert_character(did, "A", ref_audio_filename="secret_clip.wav",
                                     ref_text="hello")
        out = cs.list_characters(did)
        assert _by_label(out, "A")["has_ref_audio"] is True
        assert _by_label(out, "A")["ref_text_present"] is True
        assert "secret_clip" not in repr(out)
        assert not any("ref_audio_filename" in c for c in out)

    def test_series_name_wins(self, isolated_db):
        sid = isolated_db.get_or_create_series("S")
        isolated_db.upsert_series_character(sid, "Mei", gender="she/her")
        scid = isolated_db.list_series_characters(sid)[0]["id"]
        did = _drama(isolated_db)
        isolated_db.upsert_character(did, "A", character_name="old", series_character_id=scid)
        a = _by_label(cs.list_characters(did), "A")
        assert a["character_name"] == "Mei" and a["series_character_id"] == scid
        assert cs.list_series_characters(sid) == [{
            "id": scid, "character_name": "Mei", "aliases": "", "notes": "",
            "pronouns": "she/her"}]


class TestUpdate:
    def test_unknown_drama_and_speaker(self, isolated_db):
        with pytest.raises(NotFoundError):
            cs.update_character(999, "A", character_name="x")
        did = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            cs.update_character(did, "ZZ", character_name="x")

    def test_partial_update_leaves_other_fields(self, isolated_db):
        did = _drama(isolated_db)
        cs.update_character(did, "A", character_name="Mei", tts_voice="v1", pronouns="she/her")
        out = cs.update_character(did, "A", offline_voice="off")
        assert out["character_name"] == "Mei" and out["tts_voice"] == "v1"
        assert out["pronouns"] == "she/her" and out["offline_voice"] == "off"

    def test_none_vs_empty(self, isolated_db):
        did = _drama(isolated_db)
        cs.update_character(did, "A", pronouns="they/them", voice_design="calm")
        out = cs.update_character(did, "A", pronouns=None, voice_design="")
        assert out["pronouns"] == "they/them" and out["voice_design"] == ""

    def test_custom_pronouns_allowed_and_stripped(self, isolated_db):
        did = _drama(isolated_db)
        assert cs.update_character(did, "A", pronouns=" xe/xem ")["pronouns"] == "xe/xem"

    def test_validation(self, isolated_db):
        did = _drama(isolated_db)
        for kw in ({"character_name": "   "}, {"character_name": ""},
                   {"tts_voice": "x" * 1000}, {"pronouns": "p" * 100},
                   {"clone_engine": "nope"}):
            with pytest.raises(InvalidInputError):
                cs.update_character(did, "A", **kw)

    def test_write_never_touches_other_drama(self, isolated_db):
        d1, d2 = _drama(isolated_db), _drama(isolated_db)
        isolated_db.upsert_character(d2, "A", character_name="Other", tts_voice="keep")
        cs.update_character(d1, "A", character_name="Mine", tts_voice="mine")
        b = _by_label(cs.list_characters(d2), "A")
        assert b["character_name"] == "Other" and b["tts_voice"] == "keep"
        assert _by_label(cs.list_characters(d1), "A")["character_name"] == "Mine"

    def test_clear_clone_engine_allowed(self, isolated_db):
        did = _drama(isolated_db)
        cs.update_character(did, "A", clone_engine="f5tts")
        assert cs.update_character(did, "A", clone_engine="")["clone_engine"] == ""


@pytest.mark.parametrize("lang", ["zh", "ja", "ko"])
class TestCloneLanguageRule:
    def test_options_match_capability_function(self, isolated_db, lang):
        did = _drama(isolated_db, source_language=lang)
        opts = cs.get_clone_engine_options(did)
        expected = {e for e in dub.CLONE_ENGINES if dub.clone_engine_supports_language(e, lang)}
        assert opts["source_language"] == lang
        assert {e["id"] for e in opts["engines"]} == expected

    def test_update_enforces_rule(self, isolated_db, lang):
        did = _drama(isolated_db, source_language=lang)
        for engine in dub.CLONE_ENGINES:
            if dub.clone_engine_supports_language(engine, lang):
                assert cs.update_character(did, "A", clone_engine=engine)["clone_engine"] == engine
            else:
                with pytest.raises(InvalidInputError):
                    cs.update_character(did, "A", clone_engine=engine)


def test_clone_options_unknown_drama(isolated_db):
    with pytest.raises(NotFoundError):
        cs.get_clone_engine_options(999)


class TestVoiceBank:
    def _entry(self, db, tmp_path):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"RIFF")
        return db.save_voice_bank_entry("Voice1", str(clip), ref_text="hi", clone_engine="f5tts")

    def test_list_has_no_paths(self, isolated_db, tmp_path):
        self._entry(isolated_db, tmp_path)
        out = cs.list_voice_bank()
        assert out[0]["name"] == "Voice1" and out[0]["ref_text_present"] is True
        assert "clip_filename" not in out[0] and ".wav" not in repr(out)

    def test_apply_scoped_to_drama(self, isolated_db, tmp_path):
        eid = self._entry(isolated_db, tmp_path)
        d1, d2 = _drama(isolated_db), _drama(isolated_db)
        out = cs.apply_voice_bank_entry(d1, "A", eid)
        assert out["has_ref_audio"] is True and out["clone_engine"] == "f5tts"
        assert ".wav" not in repr(out)
        assert os.path.exists(os.path.join(isolated_db.drama_dir(d1), f"voicebank_{eid}_A.wav"))
        assert _by_label(cs.list_characters(d2), "A")["has_ref_audio"] is False

    def test_apply_not_found(self, isolated_db, tmp_path):
        eid = self._entry(isolated_db, tmp_path)
        did = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            cs.apply_voice_bank_entry(999, "A", eid)
        with pytest.raises(NotFoundError):
            cs.apply_voice_bank_entry(did, "ZZ", eid)
        with pytest.raises(NotFoundError):
            cs.apply_voice_bank_entry(did, "A", 999)
