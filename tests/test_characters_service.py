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
        cs.update_character(did, "A", character_name="Mei", voice_design="v1", pronouns="she/her")
        out = cs.update_character(did, "A", voice_actor="off")
        assert out["character_name"] == "Mei" and out["voice_design"] == "v1"
        assert out["pronouns"] == "she/her" and out["voice_actor"] == "off"

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
                   {"voice_design": "x" * 1001}, {"pronouns": "p" * 100},
                   {"clone_engine": "nope"}):
            with pytest.raises(InvalidInputError):
                cs.update_character(did, "A", **kw)

    def test_write_never_touches_other_drama(self, isolated_db):
        d1, d2 = _drama(isolated_db), _drama(isolated_db)
        isolated_db.upsert_character(d2, "A", character_name="Other", voice_design="keep")
        cs.update_character(d1, "A", character_name="Mine", voice_design="mine")
        b = _by_label(cs.list_characters(d2), "A")
        assert b["character_name"] == "Other" and b["voice_design"] == "keep"
        assert _by_label(cs.list_characters(d1), "A")["character_name"] == "Mine"

    def test_clear_clone_engine_allowed(self, isolated_db):
        did = _drama(isolated_db)
        cs.update_character(did, "A", clone_engine="omnivoice")
        assert cs.update_character(did, "A", clone_engine="")["clone_engine"] == ""

    def test_a_removed_engine_cannot_be_picked_but_a_stored_one_lists_as_removed(self, isolated_db):
        did = _drama(isolated_db)
        with pytest.raises(InvalidInputError,
                           match="The F5-TTS engine was removed. Pick another voice engine in Dub."):
            cs.update_character(did, "A", clone_engine="f5tts")
        isolated_db.upsert_character(did, "A", clone_engine="f5tts")  # as saved before the removal
        out = cs.list_characters(did)
        entry = next(c for c in out if c["speaker_label"] == "A")
        assert entry["clone_engine"] == "f5tts"
        assert entry["clone_engine_removed"] == "The F5-TTS engine was removed. Pick another voice engine in Dub."
        # picking another engine replaces it; the same call clears the notice
        assert cs.update_character(did, "A", clone_engine="omnivoice")["clone_engine_removed"] == ""


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


# --- Hardening H1 -----------------------------------------------------------

BIG = 10**30
HOSTILE = "evil\n" + "x" * 5000 + "\u202e\u2603"


def test_h1_no_echo_in_service_errors(isolated_db):
    did = _drama(isolated_db)
    calls = [lambda: cs.update_character(did, HOSTILE, pronouns="x"),
             lambda: cs.update_character(did, "A", clone_engine=HOSTILE),
             lambda: cs.apply_voice_bank_entry(did, HOSTILE, 1)]
    for call in calls:
        with pytest.raises((InvalidInputError, NotFoundError)) as e:
            call()
        assert "evil" not in str(e.value) and "xxxx" not in str(e.value)


def test_h1_unsupported_engine_message_has_no_engine_name(isolated_db):
    did = _drama(isolated_db, source_language="zh")
    with pytest.raises(InvalidInputError) as e:
        cs.update_character(did, "A", clone_engine="nope_engine")
    assert "nope_engine" not in str(e.value)


@pytest.mark.parametrize("key,label", [("tada", "TADA"), ("chatterbox", "Chatterbox"),
                                       ("gpt_sovits", "GPT-SoVITS")])
def test_a_removed_engine_cannot_be_chosen_but_a_stored_one_loads(isolated_db, key, label):
    did = _drama(isolated_db, source_language="zh")
    removed = f"The {label} engine was removed. Pick another voice engine in Dub."
    with pytest.raises(InvalidInputError) as e:
        cs.update_character(did, "A", clone_engine=key)
    assert str(e.value) == removed
    isolated_db.upsert_character(did, "A", clone_engine=key, ref_audio_filename="a.wav", ref_text="t")
    entry = _by_label(cs.list_characters(did), "A")
    assert (entry["clone_engine"], entry["clone_engine_removed"]) == (key, removed)
    # picking another engine clears it; the clip and transcript stay
    cs.update_character(did, "A", clone_engine="omnivoice")
    entry = _by_label(cs.list_characters(did), "A")
    assert entry["clone_engine_removed"] == "" and entry["has_ref_audio"] is True


def test_h1_oversized_ids(isolated_db):
    did = _drama(isolated_db)
    for call in (lambda: cs.list_characters(BIG), lambda: cs.get_clone_engine_options(BIG),
                 lambda: cs.update_character(BIG, "A", pronouns="x"),
                 lambda: cs.list_series_characters(BIG),
                 lambda: cs.apply_voice_bank_entry(did, "A", BIG),
                 lambda: cs.apply_voice_bank_entry(BIG, "A", 1)):
        with pytest.raises(InvalidInputError):
            call()


class TestVoiceBankHardening:
    def _entry(self, db, tmp_path, engine="f5tts"):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"RIFF")
        return db.save_voice_bank_entry("V", str(clip), clone_engine=engine)

    @pytest.mark.parametrize("label", ["../x", "a/b", "a\\b", "a..b", "a\x00b", "a\nb",
                                       "L" * 101])
    def test_unsafe_labels_rejected(self, isolated_db, tmp_path, label):
        eid = self._entry(isolated_db, tmp_path)
        did = _drama(isolated_db, speakers=(label,))  # even if it's a real speaker
        with pytest.raises(InvalidInputError) as e:
            cs.apply_voice_bank_entry(did, label, eid)
        assert "L" * 20 not in str(e.value)

    def test_missing_clip_is_clean_not_found(self, isolated_db, tmp_path):
        eid = self._entry(isolated_db, tmp_path)
        entry = isolated_db.get_voice_bank_entry(eid)
        os.remove(os.path.join(isolated_db.VOICE_BANK_DIR, entry["clip_filename"]))
        did = _drama(isolated_db)
        with pytest.raises(NotFoundError):
            cs.apply_voice_bank_entry(did, "A", eid)

    def test_entry_engine_language_rule(self, isolated_db, tmp_path):
        eid = self._entry(isolated_db, tmp_path, engine="omnivoice")
        did = _drama(isolated_db, source_language="en")
        with pytest.raises(InvalidInputError):
            cs.apply_voice_bank_entry(did, "A", eid)
        assert _by_label(cs.list_characters(did), "A")["has_ref_audio"] is False

    def test_an_entry_saved_with_a_removed_engine_still_applies_and_reads_as_removed(
            self, isolated_db, tmp_path):
        eid = self._entry(isolated_db, tmp_path, engine="chatterbox")
        did = _drama(isolated_db, source_language="zh")
        entry = cs.apply_voice_bank_entry(did, "A", eid)
        assert entry["has_ref_audio"] is True and entry["clone_engine"] == "chatterbox"
        assert entry["clone_engine_removed"] == (
            "The Chatterbox engine was removed. Pick another voice engine in Dub.")
