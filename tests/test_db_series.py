"""Tests for characters, series, glossaries, presets, vocabulary and the voice bank in library.db."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


class TestCharacters:
    def test_upsert_creates_new_character(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Shi Qingyi")
        chars = isolated_db.list_characters(did)
        assert len(chars) == 1
        assert chars[0]["character_name"] == "Shi Qingyi"

    def test_upsert_preserves_existing_fields_when_not_overridden(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Name", voice_actor="VA1")
        isolated_db.upsert_character(did, "SPEAKER_00", tts_voice="en-US-AvaNeural")
        chars = isolated_db.list_characters(did)
        assert chars[0]["character_name"] == "Name"  # not clobbered
        assert chars[0]["voice_actor"] == "VA1"       # not clobbered
        assert chars[0]["tts_voice"] == "en-US-AvaNeural"

    def test_elevenlabs_voice_id_persists(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.upsert_character(did, "SPEAKER_00", elevenlabs_voice_id="abc123")
        chars = isolated_db.list_characters(did)
        assert chars[0]["elevenlabs_voice_id"] == "abc123"

    def test_voice_engine_and_description_persist_without_clobbering(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.upsert_character(did, "SPEAKER_00", clone_engine="omnivoice",
                                      voice_design="female, low pitch")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Aunt")  # both untouched
        [c] = isolated_db.list_characters(did)
        assert (c["clone_engine"], c["voice_design"]) == ("omnivoice", "female, low pitch")
        isolated_db.upsert_character(did, "SPEAKER_00", voice_design="")  # "" clears
        assert isolated_db.list_characters(did)[0]["voice_design"] == ""

    def test_existing_characters_get_no_engine_so_they_keep_f5tts(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.upsert_character(did, "SPEAKER_00", ref_audio_filename="a.wav")
        assert isolated_db.list_characters(did)[0]["clone_engine"] is None

    def test_series_character_id_persists(self, isolated_db):
        sid = isolated_db.get_or_create_series("A Streamer")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        did = isolated_db.create_drama(title_en="Test", series_id=sid)
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Su Shan",
                                      series_character_id=sc["id"])
        chars = isolated_db.list_characters(did)
        assert chars[0]["series_character_id"] == sc["id"]


def test_previous_episode_lookup_uses_the_series_index(isolated_db):
    """Every library list finds each title's previous episode by series; without
    the index that is a scan of every title for every title."""
    conn = db.get_conn()
    try:
        plan = " ".join(str(tuple(r)) for r in conn.execute(
            f"EXPLAIN QUERY PLAN {db.dramas._DRAMA_SELECT}").fetchall())
    finally:
        conn.close()
    assert "idx_dramas_series" in plan


class TestSeriesAndGlossary:
    def test_get_or_create_is_idempotent(self, isolated_db):
        sid1 = isolated_db.get_or_create_series("Series A")
        sid2 = isolated_db.get_or_create_series("Series A")
        assert sid1 == sid2

    def test_different_names_get_different_ids(self, isolated_db):
        sid1 = isolated_db.get_or_create_series("Series A")
        sid2 = isolated_db.get_or_create_series("Series B")
        assert sid1 != sid2

    def test_glossary_term_upsert_updates_translation(self, isolated_db):
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "拾", "Shi")
        isolated_db.upsert_glossary_term(sid, "拾", "Shí")
        terms = isolated_db.list_glossary_terms(sid)
        assert len(terms) == 1
        assert terms[0]["term_translation"] == "Shí"

    def test_glossary_term_aliases_and_banned_translations_round_trip(self, isolated_db):
        """Step 30: aliases and banned_translations are new pipe-separated
        columns on glossary_terms, stored and read back like any other
        field -- not touching the older enforce_exact/notes mechanism."""
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi",
                                          aliases="Shen Qing Yi|Shen Ching-yi",
                                          banned_translations="Chen Qingyi|Shen Qingyu")
        term = isolated_db.list_glossary_terms(sid)[0]
        assert term["aliases"] == "Shen Qing Yi|Shen Ching-yi"
        assert term["banned_translations"] == "Chen Qingyi|Shen Qingyu"

    def test_glossary_term_upsert_without_aliases_keeps_existing_value(self, isolated_db):
        """Same COALESCE behavior as category/policy: a re-upsert (e.g. from
        the extract-and-add flow, which knows nothing about aliases) must
        not silently wipe out a previously-recorded alias/banned list."""
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi",
                                          aliases="Shen Qing Yi", banned_translations="Chen Qingyi")
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi")
        term = isolated_db.list_glossary_terms(sid)[0]
        assert term["aliases"] == "Shen Qing Yi"
        assert term["banned_translations"] == "Chen Qingyi"

    def test_update_glossary_term_sets_aliases_and_banned_translations(self, isolated_db):
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi")
        term_id = isolated_db.list_glossary_terms(sid)[0]["id"]
        isolated_db.update_glossary_term(term_id, "沈清疑", "Shen Qingyi", aliases="Shen Qing Yi",
                                          banned_translations="Chen Qingyi")
        term = isolated_db.list_glossary_terms(sid)[0]
        assert term["aliases"] == "Shen Qing Yi"
        assert term["banned_translations"] == "Chen Qingyi"

    def test_two_dramas_share_a_series_glossary(self, isolated_db):
        sid = isolated_db.get_or_create_series("Shared Series")
        did1 = isolated_db.create_drama(title_en="Book 1", series_id=sid)
        did2 = isolated_db.create_drama(title_en="Book 2", series_id=sid)
        isolated_db.upsert_glossary_term(sid, "洛神", "Luo Shen")
        d1 = isolated_db.get_drama(did1)
        d2 = isolated_db.get_drama(did2)
        assert d1["series_id"] == d2["series_id"]
        terms_via_d1 = isolated_db.list_glossary_terms(d1["series_id"])
        assert len(terms_via_d1) == 1

    def test_delete_glossary_term(self, isolated_db):
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "拾", "Shi")
        term_id = isolated_db.list_glossary_terms(sid)[0]["id"]
        isolated_db.delete_glossary_term(term_id)
        assert isolated_db.list_glossary_terms(sid) == []

    def test_update_glossary_term_can_rename_the_original_text(self, isolated_db):
        """update_glossary_term() is keyed on the row's own id, unlike
        upsert_glossary_term() (keyed on term_original) -- this is what
        lets a typo in the original term itself be fixed without
        creating a second, orphaned row."""
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi", category="person_name",
                                          policy="keep_pinyin")
        term_id = isolated_db.list_glossary_terms(sid)[0]["id"]

        isolated_db.update_glossary_term(term_id, "沈清疑", "Shen Qing-yi", notes="hyphenated form",
                                          category="person_name", policy="keep_pinyin",
                                          enforce_exact=True)

        terms = isolated_db.list_glossary_terms(sid)
        assert len(terms) == 1
        assert terms[0]["term_translation"] == "Shen Qing-yi"
        assert terms[0]["notes"] == "hyphenated form"
        assert terms[0]["enforce_exact"] == 1

    def test_update_glossary_term_does_not_create_a_duplicate_row(self, isolated_db):
        sid = isolated_db.get_or_create_series("Series A")
        isolated_db.upsert_glossary_term(sid, "typo term", "Translation")
        term_id = isolated_db.list_glossary_terms(sid)[0]["id"]

        isolated_db.update_glossary_term(term_id, "corrected term", "Translation")

        terms = isolated_db.list_glossary_terms(sid)
        assert len(terms) == 1
        assert terms[0]["term_original"] == "corrected term"


class TestPresets:
    """Step 9c: library-level, reusable Workspace-configuration presets.
    Exit conditions: applying a preset sets all its captured fields with
    none frozen against later manual changes (that's an apply_preset_to_
    session/UI-level guarantee, see test_workspace_tab.py); deleting a
    preset never changes a drama it was previously applied to; renaming
    only changes the name."""

    def _get_preset(self, isolated_db, pid):
        return next((p for p in isolated_db.list_presets() if p["id"] == pid), None)

    def test_save_preset_creates_and_captures_every_field(self, isolated_db):
        pid = isolated_db.save_preset(
            "Audio drama defaults", translation_engine="claude",
            engine_model="claude-sonnet-5", style_preset="audio_drama", locale="en-US",
            default_female_pronouns=True, include_genre_notes=False)
        p = self._get_preset(isolated_db, pid)
        assert p["name"] == "Audio drama defaults"
        assert p["translation_engine"] == "claude"
        assert p["engine_model"] == "claude-sonnet-5"
        assert p["style_preset"] == "audio_drama"
        assert p["locale"] == "en-US"
        assert p["default_female_pronouns"] == 1
        assert p["include_genre_notes"] == 0

    def test_defaults_are_applied_when_not_given(self, isolated_db):
        pid = isolated_db.save_preset("Bare minimum")
        p = self._get_preset(isolated_db, pid)
        assert p["default_female_pronouns"] == 0
        assert p["include_genre_notes"] == 1  # matches the Workspace checkbox's own default

    def test_list_presets_is_sorted_case_insensitively_by_name(self, isolated_db):
        isolated_db.save_preset("zebra")
        isolated_db.save_preset("Apple")
        isolated_db.save_preset("banana")
        assert [p["name"] for p in isolated_db.list_presets()] == ["Apple", "banana", "zebra"]

    def test_saving_under_an_existing_name_overwrites_its_fields(self, isolated_db):
        pid = isolated_db.save_preset("My preset", translation_engine="claude", locale="en-US")
        pid2 = isolated_db.save_preset("My preset", translation_engine="gemini", locale="en-GB")
        assert pid2 == pid  # same row, not a second one
        assert isolated_db.list_presets() == [self._get_preset(isolated_db, pid)]
        p = self._get_preset(isolated_db, pid)
        assert p["translation_engine"] == "gemini"
        assert p["locale"] == "en-GB"

    def test_delete_preset_does_not_change_a_drama_it_was_applied_to(self, isolated_db):
        """Exit condition: deleting a preset doesn't change any drama it
        was previously applied to. There's no live link at all -- applying
        a preset only ever copies its fields onto a drama at that moment
        -- so this proves the drama is untouched by checking its full row
        is identical before and after the preset it came from is gone."""
        pid = isolated_db.save_preset("Series defaults", translation_engine="deepseek",
                                       style_preset="novel", locale="en-GB")
        did = isolated_db.create_drama(title_en="A Drama", translation_engine="deepseek")
        before = isolated_db.get_drama(did)

        isolated_db.delete_preset(pid)

        assert self._get_preset(isolated_db, pid) is None
        assert isolated_db.get_drama(did) == before

    def test_delete_preset_only_removes_that_one_preset(self, isolated_db):
        pid1 = isolated_db.save_preset("Keep me")
        pid2 = isolated_db.save_preset("Delete me")
        isolated_db.delete_preset(pid2)
        assert [p["id"] for p in isolated_db.list_presets()] == [pid1]

    def test_rename_preset_changes_only_the_name(self, isolated_db):
        pid = isolated_db.save_preset(
            "Old name", translation_engine="claude", engine_model="claude-sonnet-5",
            style_preset="audio_drama", locale="en-AU", default_female_pronouns=True,
            include_genre_notes=False)
        before = self._get_preset(isolated_db, pid)

        isolated_db.rename_preset(pid, "New name")

        after = self._get_preset(isolated_db, pid)
        assert after["name"] == "New name"
        for field in ("translation_engine", "engine_model", "style_preset", "locale",
                      "default_female_pronouns", "include_genre_notes"):
            assert after[field] == before[field]

    def test_rename_preset_does_not_affect_other_presets(self, isolated_db):
        pid1 = isolated_db.save_preset("Preset one")
        pid2 = isolated_db.save_preset("Preset two")
        isolated_db.rename_preset(pid1, "Renamed")
        assert self._get_preset(isolated_db, pid2)["name"] == "Preset two"


class TestSeriesCharacters:
    """A streamer's persistent cast (or a book series' recurring
    characters) -- independent of any one drama's diarization labels,
    which aren't stable across dramas. See the series_characters table
    comment in db.py's init_db()."""

    def test_upsert_creates_new(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan", aliases="SuSu",
                                             notes="calm, dry sarcasm")
        chars = isolated_db.list_series_characters(sid)
        assert len(chars) == 1
        assert chars[0]["character_name"] == "Su Shan"
        assert chars[0]["aliases"] == "SuSu"
        assert chars[0]["notes"] == "calm, dry sarcasm"

    def test_upsert_same_name_updates_not_duplicates(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan", notes="v1")
        isolated_db.upsert_series_character(sid, "Su Shan", notes="v2")
        chars = isolated_db.list_series_characters(sid)
        assert len(chars) == 1
        assert chars[0]["notes"] == "v2"

    def test_same_name_in_different_series_are_independent(self, isolated_db):
        sid1 = isolated_db.get_or_create_series("Streamer A")
        sid2 = isolated_db.get_or_create_series("Streamer B")
        isolated_db.upsert_series_character(sid1, "Guest", notes="A's guest")
        isolated_db.upsert_series_character(sid2, "Guest", notes="B's guest")
        assert isolated_db.list_series_characters(sid1)[0]["notes"] == "A's guest"
        assert isolated_db.list_series_characters(sid2)[0]["notes"] == "B's guest"

    def test_gender_can_be_set_and_read_back(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan", gender="female")
        [sc] = isolated_db.list_series_characters(sid)
        assert sc["gender"] == "female"

    def test_gender_defaults_to_unset(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        assert not sc["gender"]

    def test_updating_notes_without_passing_gender_does_not_clear_it(self, isolated_db):
        """A different flow (e.g. section 6's "remember this character")
        calls upsert_series_character() without a gender argument at all
        -- that must not silently wipe a gender set earlier elsewhere."""
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan", gender="female")
        isolated_db.upsert_series_character(sid, "Su Shan", notes="updated notes")
        [sc] = isolated_db.list_series_characters(sid)
        assert sc["gender"] == "female"
        assert sc["notes"] == "updated notes"

    def test_gender_can_be_explicitly_cleared(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan", gender="female")
        isolated_db.upsert_series_character(sid, "Su Shan", gender="")
        [sc] = isolated_db.list_series_characters(sid)
        assert not sc["gender"]

    def test_per_drama_pronouns_set_kept_and_cleared(self, isolated_db):
        did = isolated_db.create_drama(title_en="Standalone")
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Xiaoling", pronouns="they/them")
        isolated_db.upsert_character(did, "SPEAKER_00", voice_actor="VA")  # pronouns=None: untouched
        [c] = isolated_db.list_characters(did)
        assert c["pronouns"] == "they/them"
        isolated_db.upsert_character(did, "SPEAKER_00", pronouns="")
        [c] = isolated_db.list_characters(did)
        assert not c["pronouns"]

    def test_linked_series_pronouns_come_back_with_the_drama_character(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan", gender="she/her")
        [sc] = isolated_db.list_series_characters(sid)
        did = isolated_db.create_drama(title_en="Ep 1", series_id=sid)
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Su Shan",
                                      series_character_id=sc["id"])
        [c] = isolated_db.list_characters_with_series_names(did)
        assert c["series_pronouns"] == "she/her"

    def test_rename_updates_in_place(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        isolated_db.update_series_character(sc["id"], character_name="Su Shan (corrected)")
        chars = isolated_db.list_series_characters(sid)
        assert len(chars) == 1
        assert chars[0]["character_name"] == "Su Shan (corrected)"

    def test_rename_propagates_to_linked_drama_characters(self, isolated_db):
        """The whole point: fix the name once at the series level, and
        every drama that assigned this character shows the correction --
        no per-drama edit needed."""
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        did1 = isolated_db.create_drama(title_en="Stream 1", series_id=sid)
        did2 = isolated_db.create_drama(title_en="Stream 2", series_id=sid)
        isolated_db.upsert_character(did1, "SPEAKER_00", character_name="Su Shan",
                                      series_character_id=sc["id"])
        isolated_db.upsert_character(did2, "SPEAKER_02", character_name="Su Shan",
                                      series_character_id=sc["id"])

        isolated_db.update_series_character(sc["id"], character_name="Su Shan (corrected)")

        c1 = isolated_db.list_characters_with_series_names(did1)
        c2 = isolated_db.list_characters_with_series_names(did2)
        assert c1[0]["character_name"] == "Su Shan (corrected)"
        assert c2[0]["character_name"] == "Su Shan (corrected)"

    def test_unlinked_character_name_is_unaffected_by_rename(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        did = isolated_db.create_drama(title_en="Stream 1", series_id=sid)
        # A different character in the same drama, never linked to sc.
        isolated_db.upsert_character(did, "SPEAKER_01", character_name="Guest")
        isolated_db.update_series_character(sc["id"], character_name="Su Shan (corrected)")
        chars = isolated_db.list_characters_with_series_names(did)
        assert chars[0]["character_name"] == "Guest"

    def test_delete_unlinks_but_keeps_the_drama_characters_own_name(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        did = isolated_db.create_drama(title_en="Stream 1", series_id=sid)
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Su Shan",
                                      series_character_id=sc["id"])

        isolated_db.delete_series_character(sc["id"])

        assert isolated_db.list_series_characters(sid) == []
        chars = isolated_db.list_characters(did)
        assert chars[0]["character_name"] == "Su Shan"  # kept its own copy
        assert chars[0]["series_character_id"] is None   # link cleared

    def test_list_is_empty_for_a_series_with_no_characters_yet(self, isolated_db):
        sid = isolated_db.get_or_create_series("Fresh Streamer")
        assert isolated_db.list_series_characters(sid) == []


class TestVoiceFingerprint:
    """Step 8: series_characters keeps a running-average voice embedding,
    blended in only on an explicit Accept -- never automatically."""

    def test_defaults_to_unset(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        assert sc["voice_fingerprint"] is None
        assert sc["voice_fingerprint_samples"] in (0, None)

    def test_first_sample_is_stored_as_is(self, isolated_db):
        import json
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        isolated_db.update_series_character_voice_fingerprint(sc["id"], [1.0, 0.0, 0.0])
        [sc] = isolated_db.list_series_characters(sid)
        assert json.loads(sc["voice_fingerprint"]) == [1.0, 0.0, 0.0]
        assert sc["voice_fingerprint_samples"] == 1

    def test_second_sample_is_averaged_with_the_first(self, isolated_db):
        import json
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        isolated_db.update_series_character_voice_fingerprint(sc["id"], [1.0, 0.0])
        isolated_db.update_series_character_voice_fingerprint(sc["id"], [0.0, 1.0])
        [sc] = isolated_db.list_series_characters(sid)
        assert json.loads(sc["voice_fingerprint"]) == [0.5, 0.5]
        assert sc["voice_fingerprint_samples"] == 2

    def test_dimension_mismatch_restarts_from_the_new_sample(self, isolated_db):
        import json
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        isolated_db.update_series_character_voice_fingerprint(sc["id"], [1.0, 0.0, 0.0])
        isolated_db.update_series_character_voice_fingerprint(sc["id"], [0.5, 0.5])  # different length
        [sc] = isolated_db.list_series_characters(sid)
        assert json.loads(sc["voice_fingerprint"]) == [0.5, 0.5]
        assert sc["voice_fingerprint_samples"] == 1

    def test_unknown_series_character_id_is_a_no_op(self, isolated_db):
        isolated_db.update_series_character_voice_fingerprint(999999, [1.0, 0.0])  # must not raise


class TestVoiceSuggestionDismissals:
    def test_dismissed_pair_is_listed(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        isolated_db.dismiss_voice_suggestion(did, "SPEAKER_00", sc["id"])
        assert isolated_db.list_dismissed_voice_suggestions(did) == {("SPEAKER_00", sc["id"])}

    def test_a_different_candidate_for_the_same_speaker_is_not_dismissed(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        isolated_db.upsert_series_character(sid, "Rin")
        chars = {c["character_name"]: c["id"] for c in isolated_db.list_series_characters(sid)}
        isolated_db.dismiss_voice_suggestion(did, "SPEAKER_00", chars["Su Shan"])
        dismissed = isolated_db.list_dismissed_voice_suggestions(did)
        assert ("SPEAKER_00", chars["Su Shan"]) in dismissed
        assert ("SPEAKER_00", chars["Rin"]) not in dismissed

    def test_dismissing_the_same_pair_twice_does_not_raise(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        sid = isolated_db.get_or_create_series("Streamer A")
        isolated_db.upsert_series_character(sid, "Su Shan")
        [sc] = isolated_db.list_series_characters(sid)
        isolated_db.dismiss_voice_suggestion(did, "SPEAKER_00", sc["id"])
        isolated_db.dismiss_voice_suggestion(did, "SPEAKER_00", sc["id"])
        assert len(isolated_db.list_dismissed_voice_suggestions(did)) == 1

    def test_no_dismissals_yet_is_an_empty_set(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.list_dismissed_voice_suggestions(did) == set()


class TestVocabLookups:
    def test_save_and_list(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_vocab_lookup(did, "你好", "ni3 hao3", ["hello", "hi"], "zh", 0)
        vocab = isolated_db.list_vocab_lookups(did)
        assert len(vocab) == 1
        assert vocab[0]["definitions"] == ["hello", "hi"]

    def test_duplicate_word_does_not_overwrite(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_vocab_lookup(did, "你好", "first", ["a"], "zh")
        isolated_db.save_vocab_lookup(did, "你好", "second", ["b"], "zh")
        vocab = isolated_db.list_vocab_lookups(did)
        assert len(vocab) == 1
        assert vocab[0]["reading"] == "first"  # first insert wins

    def test_rich_export_flag_defaults_off_and_is_filterable(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_vocab_lookup(did, "你好", "ni3 hao3", ["hello"], "zh", 0)
        isolated_db.save_vocab_lookup(did, "再见", "zai4 jian4", ["goodbye"], "zh", 1)
        assert isolated_db.list_vocab_lookups(did, rich_only=True) == []

        isolated_db.set_vocab_export_rich(did, "你好", True)
        rich = isolated_db.list_vocab_lookups(did, rich_only=True)
        assert [r["word"] for r in rich] == ["你好"]
        assert len(isolated_db.list_vocab_lookups(did)) == 2  # unfiltered list unaffected

    def test_rich_export_flag_can_be_unset(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_vocab_lookup(did, "你好", "ni3 hao3", ["hello"], "zh", 0)
        isolated_db.set_vocab_export_rich(did, "你好", True)
        isolated_db.set_vocab_export_rich(did, "你好", False)
        assert isolated_db.list_vocab_lookups(did, rich_only=True) == []


class TestVoiceBank:
    """Step 26: reuse a cloned voice across projects. Exit conditions:
    saving a clone reference to the bank copies the clip (not a path into
    the source drama's folder); applying a bank entry to a character in a
    different drama copies the clip into that drama's folder and sets its
    clone fields; deleting the source drama afterward leaves the bank
    entry's own clip intact."""

    def _make_clip(self, tmp_path_str, name="ref.wav"):
        p = os.path.join(tmp_path_str, name)
        with open(p, "wb") as f:
            f.write(b"fake wav bytes")
        return p

    def test_save_copies_the_clip_not_a_reference(self, isolated_db, tmp_path_str):
        src_dir = os.path.join(tmp_path_str, "source_drama")
        os.makedirs(src_dir)
        clip = self._make_clip(src_dir)

        eid = isolated_db.save_voice_bank_entry(
            "Su Shan", clip, ref_text="hello there", clone_engine="f5_tts",
            voice_design="", language="zh", source_drama="Streamer Archive",
            source_speaker="SPEAKER_00")

        entry = isolated_db.get_voice_bank_entry(eid)
        assert entry["name"] == "Su Shan"
        assert entry["ref_text"] == "hello there"
        assert entry["clone_engine"] == "f5_tts"
        assert entry["language"] == "zh"
        assert entry["source_drama"] == "Streamer Archive"
        assert entry["source_speaker"] == "SPEAKER_00"
        clip_path = os.path.join(isolated_db.VOICE_BANK_DIR, entry["clip_filename"])
        # A real, separate file under the library's own voice_bank folder --
        # not a path back into the source drama's directory.
        assert os.path.exists(clip_path)
        assert not clip_path.startswith(src_dir)
        with open(clip_path, "rb") as f:
            assert f.read() == b"fake wav bytes"

    def test_list_is_sorted_by_name(self, isolated_db, tmp_path_str):
        isolated_db.save_voice_bank_entry("Zed", self._make_clip(tmp_path_str, "a.wav"))
        isolated_db.save_voice_bank_entry("Amy", self._make_clip(tmp_path_str, "b.wav"))
        assert [e["name"] for e in isolated_db.list_voice_bank_entries()] == ["Amy", "Zed"]

    def test_apply_copies_clip_into_the_new_drama_and_sets_clone_fields(self, isolated_db, tmp_path_str):
        clip = self._make_clip(tmp_path_str)
        eid = isolated_db.save_voice_bank_entry(
            "Su Shan", clip, ref_text="a line", clone_engine="omnivoice", voice_design="")

        did = isolated_db.create_drama(title_en="A New Drama")
        drama_dir = os.path.join(tmp_path_str, "new_drama")
        isolated_db.upsert_character(did, "SPEAKER_01")

        dest_filename = isolated_db.apply_voice_bank_entry(eid, drama_dir, did, "SPEAKER_01")

        dest_path = os.path.join(drama_dir, dest_filename)
        assert os.path.exists(dest_path)
        with open(dest_path, "rb") as f:
            assert f.read() == b"fake wav bytes"
        chars = {c["speaker_label"]: c for c in isolated_db.list_characters(did)}
        c = chars["SPEAKER_01"]
        assert c["ref_audio_filename"] == dest_filename
        assert c["ref_text"] == "a line"
        assert c["clone_engine"] == "omnivoice"

    def test_apply_writes_a_separate_copy_not_shared_with_the_bank_or_other_dramas(
            self, isolated_db, tmp_path_str):
        clip = self._make_clip(tmp_path_str)
        eid = isolated_db.save_voice_bank_entry("Su Shan", clip)
        bank_clip_path = os.path.join(
            isolated_db.VOICE_BANK_DIR, isolated_db.get_voice_bank_entry(eid)["clip_filename"])

        did1 = isolated_db.create_drama(title_en="Drama One")
        did2 = isolated_db.create_drama(title_en="Drama Two")
        dir1 = os.path.join(tmp_path_str, "drama_one")
        dir2 = os.path.join(tmp_path_str, "drama_two")
        isolated_db.upsert_character(did1, "SPEAKER_00")
        isolated_db.upsert_character(did2, "SPEAKER_00")

        f1 = isolated_db.apply_voice_bank_entry(eid, dir1, did1, "SPEAKER_00")
        f2 = isolated_db.apply_voice_bank_entry(eid, dir2, did2, "SPEAKER_00")

        assert os.path.join(dir1, f1) != os.path.join(dir2, f2)
        assert os.path.exists(bank_clip_path)  # the bank's own copy is untouched

    def test_deleting_the_source_drama_leaves_the_bank_entrys_clip_intact(self, isolated_db, tmp_path_str):
        src_dir = os.path.join(tmp_path_str, "source_drama")
        os.makedirs(src_dir)
        clip = self._make_clip(src_dir)
        did = isolated_db.create_drama(title_en="Source Drama")
        eid = isolated_db.save_voice_bank_entry("Su Shan", clip, source_drama="Source Drama")
        clip_path = os.path.join(
            isolated_db.VOICE_BANK_DIR, isolated_db.get_voice_bank_entry(eid)["clip_filename"])

        isolated_db.delete_drama(did)
        import shutil as _shutil
        _shutil.rmtree(src_dir, ignore_errors=True)  # what a real drama deletion also removes

        assert os.path.exists(clip_path)
        assert isolated_db.get_voice_bank_entry(eid) is not None

    def test_rename_changes_only_the_name(self, isolated_db, tmp_path_str):
        eid = isolated_db.save_voice_bank_entry("Old name", self._make_clip(tmp_path_str),
                                                 clone_engine="f5_tts", language="zh")
        isolated_db.rename_voice_bank_entry(eid, "New name")
        entry = isolated_db.get_voice_bank_entry(eid)
        assert entry["name"] == "New name"
        assert entry["clone_engine"] == "f5_tts"
        assert entry["language"] == "zh"

    def test_delete_removes_the_entry_and_its_clip_file(self, isolated_db, tmp_path_str):
        eid = isolated_db.save_voice_bank_entry("Doomed", self._make_clip(tmp_path_str))
        clip_path = os.path.join(
            isolated_db.VOICE_BANK_DIR, isolated_db.get_voice_bank_entry(eid)["clip_filename"])

        isolated_db.delete_voice_bank_entry(eid)

        assert isolated_db.get_voice_bank_entry(eid) is None
        assert not os.path.exists(clip_path)

    def test_delete_only_removes_that_one_entry(self, isolated_db, tmp_path_str):
        eid1 = isolated_db.save_voice_bank_entry("Keep me", self._make_clip(tmp_path_str, "a.wav"))
        eid2 = isolated_db.save_voice_bank_entry("Delete me", self._make_clip(tmp_path_str, "b.wav"))
        isolated_db.delete_voice_bank_entry(eid2)
        assert [e["id"] for e in isolated_db.list_voice_bank_entries()] == [eid1]

    def test_apply_raises_a_clear_error_for_an_unknown_entry_id(self, isolated_db, tmp_path_str):
        import pytest
        with pytest.raises(ValueError):
            isolated_db.apply_voice_bank_entry(99999, tmp_path_str, 1, "SPEAKER_00")

    def _outside_clip_names(self, isolated_db, tmp_path_str):
        """A '../' name and an absolute path, each naming a real file
        outside the voice-bank folder."""
        os.makedirs(isolated_db.VOICE_BANK_DIR, exist_ok=True)
        parent = os.path.dirname(os.path.abspath(isolated_db.VOICE_BANK_DIR))
        with open(os.path.join(parent, "victim_rel.wav"), "wb") as f:
            f.write(b"outside")
        absolute = os.path.join(tmp_path_str, "victim_abs.wav")
        with open(absolute, "wb") as f:
            f.write(b"outside")
        return [("../victim_rel.wav", os.path.join(parent, "victim_rel.wav")),
                (absolute, absolute)]

    def _set_clip_filename(self, isolated_db, entry_id, name):
        import sqlite3
        conn = sqlite3.connect(isolated_db.DB_PATH)
        conn.execute("UPDATE voice_bank SET clip_filename = ? WHERE id = ?", (name, entry_id))
        conn.commit()
        conn.close()

    def test_apply_refuses_a_clip_filename_outside_the_bank_folder(self, isolated_db, tmp_path_str):
        import pytest
        did = isolated_db.create_drama(title_en="Target")
        isolated_db.upsert_character(did, "SPEAKER_00")
        drama_dir = os.path.join(tmp_path_str, "target_drama")
        for name, _victim in self._outside_clip_names(isolated_db, tmp_path_str):
            eid = isolated_db.save_voice_bank_entry("Bad", self._make_clip(tmp_path_str))
            self._set_clip_filename(isolated_db, eid, name)
            with pytest.raises(ValueError):
                isolated_db.apply_voice_bank_entry(eid, drama_dir, did, "SPEAKER_00")
            assert not os.path.isdir(drama_dir) or os.listdir(drama_dir) == []
            char = {c["speaker_label"]: c for c in isolated_db.list_characters(did)}["SPEAKER_00"]
            assert not char.get("ref_audio_filename")

    def test_delete_leaves_a_file_outside_the_bank_folder_alone(self, isolated_db, tmp_path_str):
        for name, victim in self._outside_clip_names(isolated_db, tmp_path_str):
            eid = isolated_db.save_voice_bank_entry("Bad", self._make_clip(tmp_path_str))
            self._set_clip_filename(isolated_db, eid, name)
            isolated_db.delete_voice_bank_entry(eid)
            assert isolated_db.get_voice_bank_entry(eid) is None
            assert os.path.exists(victim)
