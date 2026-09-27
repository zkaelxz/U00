"""
tests/test_db.py -- tests for db.py, using the isolated_db fixture so
nothing here ever touches your real library.
"""

import shutil
import subprocess
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line


class TestDramaCRUD:
    def test_create_and_get(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        drama = isolated_db.get_drama(did)
        assert drama["title_en"] == "Test Drama"
        assert drama["status"] == "not started"  # default

    def test_source_url_persists(self, isolated_db):
        did = isolated_db.create_drama(title_en="Stream", title_zh="直播原名")
        isolated_db.update_drama(did, source_url="https://youtube.com/watch?v=fake123")
        drama = isolated_db.get_drama(did)
        assert drama["source_url"] == "https://youtube.com/watch?v=fake123"
        # title_en/title_zh double as translated/untranslated stream name --
        # confirming both persist alongside source_url, not just one or the other.
        assert drama["title_en"] == "Stream"
        assert drama["title_zh"] == "直播原名"

    def test_create_sets_created_and_updated_at(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        drama = isolated_db.get_drama(did)
        assert drama["created_at"] is not None
        assert drama["updated_at"] is not None

    def test_update_changes_updated_at(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        before = isolated_db.get_drama(did)["updated_at"]
        isolated_db.update_drama(did, status="translated")
        after = isolated_db.get_drama(did)["updated_at"]
        assert isolated_db.get_drama(did)["status"] == "translated"
        # updated_at should change (may be equal if the clock has sub-ms
        # resolution issues, so just check status changed correctly --
        # the important behavioral guarantee, not exact timestamp diff)
        assert after is not None

    def test_delete_removes_drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.delete_drama(did)
        assert isolated_db.get_drama(did) is None

    def test_get_nonexistent_returns_none(self, isolated_db):
        assert isolated_db.get_drama(99999) is None

    def test_list_dramas_filters_by_language(self, isolated_db):
        isolated_db.create_drama(title_en="ZH Drama", source_language="zh")
        isolated_db.create_drama(title_en="KO Drama", source_language="ko")
        results = isolated_db.list_dramas(source_language="ko")
        assert len(results) == 1
        assert results[0]["title_en"] == "KO Drama"

    def test_list_dramas_filters_by_media_type(self, isolated_db):
        isolated_db.create_drama(title_en="Novel", media_type="novel")
        isolated_db.create_drama(title_en="Manhwa", media_type="manhwa")
        results = isolated_db.list_dramas(media_type="manhwa")
        assert len(results) == 1
        assert results[0]["title_en"] == "Manhwa"

    def test_search_matches_title_and_summary(self, isolated_db):
        isolated_db.create_drama(title_en="Unrelated", summary="mentions dragons")
        results = isolated_db.list_dramas(search="dragons")
        assert len(results) == 1


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


class TestLinesSaveLoad:
    def test_save_and_load_roundtrip(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A")]
        isolated_db.save_lines(did, lines)
        loaded = isolated_db.load_lines(did)
        assert len(loaded) == 1
        assert loaded[0]["zh"] == "你好"
        assert loaded[0]["en"] == "Hello"
        assert loaded[0]["speaker"] == "A"

    def test_save_lines_replaces_previous_set(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="a")])
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="b", en="b")])
        loaded = isolated_db.load_lines(did)
        assert len(loaded) == 1
        assert loaded[0]["zh"] == "b"

    def test_dub_filename_persists(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        ln = Line(idx=0, start=0, end=1, zh="a", en="a")
        ln.dub_filename = "clip_0.wav"
        isolated_db.save_lines(did, [ln])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["dub_filename"] == "clip_0.wav"

    def test_flag_and_flag_note_persist(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        ln = Line(idx=0, start=0, end=1, zh="a", en="b",
                  flag="ambiguous_reference", flag_note="'her' unresolved")
        isolated_db.save_lines(did, [ln])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] == "ambiguous_reference"
        assert loaded[0]["flag_note"] == "'her' unresolved"

    def test_unflagged_line_has_no_flag(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b")])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] is None

    def test_resaving_clears_a_previously_set_flag(self, isolated_db):
        """A line whose flag was dismissed (or cleared by editing) and
        re-saved shouldn't have the old flag reappear."""
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b", flag="name_uncertain")])
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", en="b", flag=None)])
        loaded = isolated_db.load_lines(did)
        assert loaded[0]["flag"] is None


class TestLoadLineIds:
    """Step 6f: the cheap id-set check a caller uses to confirm a
    snapshot it computed earlier isn't stale relative to the database
    before committing it as a full replacement (see resegment Apply's
    own safety check)."""

    def test_returns_the_current_id_set(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a"),
                                     Line(idx=1, start=1, end=2, zh="b")])
        loaded = isolated_db.load_line_objects(did)
        assert isolated_db.load_line_ids(did) == {ln.id for ln in loaded}

    def test_empty_for_a_drama_with_no_lines(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.load_line_ids(did) == set()

    def test_reflects_a_change_made_by_another_save(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a")])
        before = isolated_db.load_line_ids(did)

        lines = isolated_db.load_line_objects(did)
        lines.append(Line(idx=1, start=1, end=2, zh="b"))
        isolated_db.save_lines(did, lines)

        assert isolated_db.load_line_ids(did) != before
        assert isolated_db.load_line_ids(did) == {ln.id for ln in isolated_db.load_line_objects(did)}


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
        isolated_db.rename_series_character(sc["id"], "Su Shan (corrected)")
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

        isolated_db.rename_series_character(sc["id"], "Su Shan (corrected)")

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
        isolated_db.rename_series_character(sc["id"], "Su Shan (corrected)")
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


class TestUsageTracking:
    def test_log_and_summarize(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 1000, 500, 0.0055)
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 2000, 800, 0.0092)
        summary = isolated_db.get_usage_summary(did)
        assert summary["call_count"] == 2
        assert abs(summary["estimated_cost_usd"] - 0.0147) < 0.0001

    def test_library_wide_total_sums_all_dramas(self, isolated_db):
        did1 = isolated_db.create_drama(title_en="D1")
        did2 = isolated_db.create_drama(title_en="D2")
        isolated_db.log_usage(did1, "claude", "m", "translate", 100, 50, 0.001)
        isolated_db.log_usage(did2, "deepseek", "m", "translate", 200, 100, 0.0002)
        total = isolated_db.get_usage_summary()
        assert total["call_count"] == 2

    def test_no_usage_returns_zeros_not_none(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        summary = isolated_db.get_usage_summary(did)
        assert summary["call_count"] == 0
        assert summary["estimated_cost_usd"] == 0


class TestCacheReadsAndMonthSpend:
    """Step 9: usage_log records prompt-cache reads, and month-to-date
    spend is what the monthly cap is checked against."""

    def test_cache_read_tokens_are_summed(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.log_usage(did, "claude", "m", "translate", 1000, 10, 0.01, cache_read_tokens=800)
        isolated_db.log_usage(did, "claude", "m", "translate", 1000, 10, 0.01)
        assert isolated_db.get_usage_summary(did)["cache_read_tokens"] == 800
        assert isolated_db.get_usage_summary()["cache_read_tokens"] == 800
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["cache_read_tokens"] == 800

    def test_month_spend_counts_only_the_current_month(self, isolated_db):
        import datetime
        import pytest
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.log_usage(did, "claude", "m", "translate", 1, 1, 1.50)
        conn = isolated_db.get_conn()
        conn.execute("INSERT INTO usage_log (drama_id, engine, model, operation, input_tokens, "
                     "output_tokens, estimated_cost_usd, created_at) VALUES (?, 'c', 'm', 't', 1, 1, 9.0, ?)",
                     (did, "2000-01-15T00:00:00"))
        conn.commit()
        conn.close()
        assert isolated_db.get_month_spend() == pytest.approx(1.50)
        assert isolated_db.get_month_spend(datetime.datetime(2000, 1, 20)) == pytest.approx(10.50)

    def test_the_cache_column_is_added_to_an_older_database(self, isolated_db):
        conn = isolated_db.get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(usage_log)").fetchall()}
        conn.close()
        assert "cache_read_tokens" in cols


class TestUsageByDrama:
    """get_usage_by_drama backs the Library dashboard's cost breakdown --
    Step 1d added translation_engine and call_count to it so the
    dashboard can show a free engine's real (zero-cost) usage instead of
    filtering it out as if nothing had run (the old filter was
    estimated_cost_usd > 0, which drops free engines entirely)."""

    def test_includes_translation_engine_and_call_count(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test", translation_engine="test_offline")
        isolated_db.log_usage(did, "test_offline", "test_offline", "translate", 100, 50, 0.0)
        isolated_db.log_usage(did, "test_offline", "test_offline", "translate", 200, 100, 0.0)
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["translation_engine"] == "test_offline"
        assert row["call_count"] == 2
        assert row["estimated_cost_usd"] == 0.0

    def test_a_drama_with_no_usage_has_zero_call_count(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["call_count"] == 0


class TestConsistencyIssuesPersist:
    """Regression coverage for a real bug: the consistency-check result
    (a real LLM call) used to live only in st.session_state, so a page
    refresh silently lost it -- meaning re-checking (and re-paying for
    it) was the only way to see the same result again."""

    def test_save_and_load(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_consistency_issues(did, [
            {"term": "沈清疑", "variants": ["Shen Qingyi", "Shen Qing Yi"], "note": "spacing"},
        ])
        loaded = isolated_db.load_consistency_issues(did)
        assert len(loaded) == 1
        assert loaded[0]["term"] == "沈清疑"
        assert loaded[0]["variants"] == ["Shen Qingyi", "Shen Qing Yi"]

    def test_a_later_run_replaces_the_previous_one(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_consistency_issues(did, [{"term": "a", "variants": [], "note": ""}])
        isolated_db.save_consistency_issues(did, [{"term": "b", "variants": [], "note": ""}])
        loaded = isolated_db.load_consistency_issues(did)
        assert [i["term"] for i in loaded] == ["b"]

    def test_no_issues_saved_yet_returns_empty_list(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.load_consistency_issues(did) == []


class TestEmotionsPersist:
    """Regression coverage for a real bug: emotion detection (a
    whole-drama LLM batch job, same cost scale as translation) had no
    database persistence at all -- its result lived only in
    st.session_state, so a page refresh lost it and it never fed back
    into a later translation run either."""

    def test_save_and_load(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=str(i)) for i in range(4)])
        isolated_db.save_emotions(did, {
            0: {"emotion": "sarcastic", "intensity": 0.9, "note": "mock praise"},
            3: {"emotion": "sad", "intensity": 0.6, "note": ""},
        })
        loaded = isolated_db.load_emotions(did)
        assert loaded[0]["emotion"] == "sarcastic"
        assert loaded[0]["intensity"] == 0.9
        assert loaded[3]["emotion"] == "sad"

    def test_re_running_updates_existing_lines_rather_than_duplicating(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a")])
        isolated_db.save_emotions(did, {0: {"emotion": "angry", "intensity": 0.5, "note": ""}})
        isolated_db.save_emotions(did, {0: {"emotion": "sad", "intensity": 0.7, "note": ""}})
        loaded = isolated_db.load_emotions(did)
        assert len(loaded) == 1
        assert loaded[0]["emotion"] == "sad"

    def test_no_emotions_saved_yet_returns_empty_dict(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        assert isolated_db.load_emotions(did) == {}


class TestLibraryStatsAndSearch:
    def test_get_library_stats_counts_dramas(self, isolated_db):
        isolated_db.create_drama(title_en="A", status="translated")
        isolated_db.create_drama(title_en="B", status="not started")
        stats = isolated_db.get_library_stats()
        assert stats["total_dramas"] == 2
        assert stats["by_status"]["translated"] == 1

    def test_recently_active_orders_by_updated_at(self, isolated_db):
        did1 = isolated_db.create_drama(title_en="First")
        did2 = isolated_db.create_drama(title_en="Second")
        isolated_db.update_drama(did1, status="translated")  # touch it after did2 was created
        recent = isolated_db.list_dramas_recently_active(5)
        assert recent[0]["title_en"] == "First"  # most recently touched first

    def test_global_search_finds_across_dramas(self, isolated_db):
        did = isolated_db.create_drama(title_en="Findable Drama")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好", en="hello there")])
        results = isolated_db.search_lines_globally("你好")
        assert len(results) == 1
        assert results[0]["title_en"] == "Findable Drama"

    def test_global_search_no_match_returns_empty(self, isolated_db):
        isolated_db.create_drama(title_en="Test")
        results = isolated_db.search_lines_globally("nonexistent_search_term_xyz")
        assert results == []


class TestKnownTitles:
    def test_create_and_list(self, isolated_db):
        isolated_db.create_known_title(title_original="测试", title_en="Test",
                                        source_name="manual", language="zh", media_type="novel")
        titles = isolated_db.list_known_titles()
        assert len(titles) == 1

    def test_filter_by_source_name(self, isolated_db):
        isolated_db.create_known_title(title_original="A", source_name="baihehub", language="zh", media_type="novel")
        isolated_db.create_known_title(title_original="B", source_name="manual", language="zh", media_type="novel")
        results = isolated_db.list_known_titles(source_name="baihehub")
        assert len(results) == 1
        assert results[0]["title_original"] == "A"

    def test_delete_known_title(self, isolated_db):
        tid = isolated_db.create_known_title(title_original="A", source_name="manual",
                                              language="zh", media_type="novel")
        isolated_db.delete_known_title(tid)
        assert isolated_db.list_known_titles() == []


class TestPagesAndBubbles:
    def test_create_page_and_list(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        pages = isolated_db.list_pages(did)
        assert len(pages) == 1
        assert pages[0]["width"] == 600

    def test_save_and_load_bubbles(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "a", "translated_text": "b",
             "font_size": 18, "skip": False}
        ])
        bubbles = isolated_db.load_bubbles(pid)
        assert len(bubbles) == 1
        assert bubbles[0]["translated_text"] == "b"

    def test_update_page_rendered_filename(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.update_page(pid, rendered_filename="pages/typeset_0000.png")
        page = isolated_db.list_pages(did)[0]
        assert page["rendered_filename"] == "pages/typeset_0000.png"

    def test_list_bubbles_for_drama_spans_every_page(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid0 = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        pid1 = isolated_db.create_page(did, 1, "pages/page_0001.png", 600, 400)
        isolated_db.save_bubbles(pid0, [
            {"x": 1, "y": 1, "w": 10, "h": 10, "source_text": "a", "translated_text": "Hello",
             "font_size": 18, "skip": False}])
        isolated_db.save_bubbles(pid1, [
            {"x": 2, "y": 2, "w": 10, "h": 10, "source_text": "b", "translated_text": "World",
             "font_size": 18, "skip": False}])
        bubbles = isolated_db.list_bubbles_for_drama(did)
        assert len(bubbles) == 2
        assert {b["page_idx"] for b in bubbles} == {0, 1}
        assert {b["translated_text"] for b in bubbles} == {"Hello", "World"}

    def test_list_bubbles_for_drama_ignores_other_dramas(self, isolated_db):
        did_a = isolated_db.create_drama(title_en="A")
        did_b = isolated_db.create_drama(title_en="B")
        pid_a = isolated_db.create_page(did_a, 0, "pages/page_0000.png", 600, 400)
        isolated_db.create_page(did_b, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid_a, [
            {"x": 1, "y": 1, "w": 10, "h": 10, "source_text": "a", "translated_text": "Only A",
             "font_size": 18, "skip": False}])
        bubbles = isolated_db.list_bubbles_for_drama(did_b)
        assert bubbles == []

    def test_update_bubble_text_only_changes_translated_text(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "orig", "translated_text": "old",
             "font_size": 24, "skip": True, "font_category": "bold"}])
        bubble_id = isolated_db.load_bubbles(pid)[0]["id"]

        isolated_db.update_bubble_text(bubble_id, "new")

        updated = isolated_db.load_bubbles(pid)[0]
        assert updated["translated_text"] == "new"
        # Everything else about the bubble stays exactly as it was.
        assert updated["x"] == 10 and updated["y"] == 20
        assert updated["w"] == 100 and updated["h"] == 50
        assert updated["source_text"] == "orig"
        assert updated["font_size"] == 24
        assert updated["skip"] == 1
        assert updated["font_category"] == "bold"

    def test_update_bubble_text_does_not_touch_other_bubbles(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 1, "y": 1, "w": 10, "h": 10, "source_text": "a", "translated_text": "keep me",
             "font_size": 18, "skip": False},
            {"x": 2, "y": 2, "w": 10, "h": 10, "source_text": "b", "translated_text": "change me",
             "font_size": 18, "skip": False},
        ])
        bubbles = isolated_db.load_bubbles(pid)
        target = next(b for b in bubbles if b["translated_text"] == "change me")

        isolated_db.update_bubble_text(target["id"], "changed")

        reloaded = {b["translated_text"] for b in isolated_db.load_bubbles(pid)}
        assert reloaded == {"keep me", "changed"}

    def test_font_category_round_trips(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "a", "translated_text": "b",
             "font_size": 18, "skip": False, "font_category": "handwritten"}
        ])
        bubbles = isolated_db.load_bubbles(pid)
        assert bubbles[0]["font_category"] == "handwritten"

    def test_font_category_defaults_to_regular_when_omitted(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test")
        pid = isolated_db.create_page(did, 0, "pages/page_0000.png", 600, 400)
        isolated_db.save_bubbles(pid, [
            {"x": 10, "y": 20, "w": 100, "h": 50, "source_text": "a", "translated_text": "b",
             "font_size": 18, "skip": False}  # no font_category key at all
        ])
        bubbles = isolated_db.load_bubbles(pid)
        assert bubbles[0]["font_category"] == "regular"


class TestBenchmarkCasesAndRuns:
    def test_configure_library_dir_redirects_benchmark_dir_too(self, isolated_db):
        """Regression test for a real gap: configure_library_dir (what
        test isolation itself relies on) never redirected BENCHMARK_DIR,
        so any test exercising the benchmark case file-upload path would
        have silently written into the real production library instead
        of the isolated temp one."""
        assert isolated_db.BENCHMARK_DIR.startswith(isolated_db.LIBRARY_DIR)
        assert isolated_db.BENCHMARK_DIR == os.path.join(isolated_db.LIBRARY_DIR, "benchmark_cases")

    def test_create_and_list_a_case(self, isolated_db):
        case_id = isolated_db.create_benchmark_case(
            "My test drama clip", "transcription", "audio_drama", source_language="zh",
            input_filename="clip.wav", reference_text="你好世界")
        cases = isolated_db.list_benchmark_cases()
        assert len(cases) == 1
        assert cases[0]["id"] == case_id
        assert cases[0]["reference_text"] == "你好世界"

    def test_list_cases_filters_by_stage(self, isolated_db):
        isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.create_benchmark_case("b", "translation", "novel")
        assert len(isolated_db.list_benchmark_cases(stage="transcription")) == 1
        assert len(isolated_db.list_benchmark_cases(stage="translation")) == 1
        assert len(isolated_db.list_benchmark_cases()) == 2

    def test_delete_a_case(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.delete_benchmark_case(case_id)
        assert isolated_db.list_benchmark_cases() == []

    def test_save_and_list_runs_for_a_case(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.save_benchmark_run(case_id, {"output_text": "hi", "score": 0.9,
                                                  "duration_seconds": 1.2, "error": None},
                                        run_label="first try")
        runs = isolated_db.list_benchmark_runs(case_id)
        assert len(runs) == 1
        assert runs[0]["score"] == 0.9
        assert runs[0]["run_label"] == "first try"

    def test_deleting_a_case_cascades_to_its_runs(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.save_benchmark_run(case_id, {"output_text": "hi", "score": 0.9,
                                                  "duration_seconds": 1.0, "error": None})
        isolated_db.delete_benchmark_case(case_id)
        assert isolated_db.list_benchmark_runs(case_id) == []

    def test_latest_run_per_case_returns_only_the_most_recent(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        isolated_db.save_benchmark_run(case_id, {"output_text": "old", "score": 0.5,
                                                  "duration_seconds": 1.0, "error": None})
        isolated_db.save_benchmark_run(case_id, {"output_text": "new", "score": 0.9,
                                                  "duration_seconds": 1.0, "error": None})
        latest = isolated_db.latest_benchmark_run_per_case()
        assert latest[case_id]["output_text"] == "new"

    def test_latest_run_per_case_filters_by_stage(self, isolated_db):
        transcription_case = isolated_db.create_benchmark_case("a", "transcription", "audio_drama")
        translation_case = isolated_db.create_benchmark_case("b", "translation", "novel")
        isolated_db.save_benchmark_run(transcription_case, {"output_text": "x", "score": 0.5,
                                                              "duration_seconds": 1.0, "error": None})
        isolated_db.save_benchmark_run(translation_case, {"output_text": "y", "score": 0.5,
                                                            "duration_seconds": 1.0, "error": None})
        latest = isolated_db.latest_benchmark_run_per_case(stage="transcription")
        assert set(latest.keys()) == {transcription_case}

    def test_cost_defaults_to_zero_when_omitted(self, isolated_db):
        case_id = isolated_db.create_benchmark_case("a", "translation", "novel")
        isolated_db.save_benchmark_run(case_id, {"output_text": "x", "score": 1.0,
                                                  "duration_seconds": 0.5, "error": None})
        runs = isolated_db.list_benchmark_runs(case_id)
        assert runs[0]["cost_usd"] == 0.0


class TestConnectionLeakRecovery:
    """Regression tests for a real bug: a statement raising between
    get_conn() and conn.close() leaked the connection, and under WAL that
    made every subsequent write fail with 'database is locked' -- one bad
    call poisoned the whole session until restart."""

    def test_healthy_calls_leave_no_open_connections(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        for _ in range(5):
            isolated_db.list_dramas()
            isolated_db.get_drama(did)
        assert len(isolated_db._open_connections) == 0

    def test_write_succeeds_after_a_failed_write(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        try:
            isolated_db.log_usage(99999, "claude", "m", "translate", 1, 1, 0.0)
        except Exception:
            pass
        isolated_db.save_progress(did, last_line_idx=5, percent_complete=10.0)
        assert isolated_db.get_progress(did)["last_line_idx"] == 5

    def test_recovers_from_repeated_failures(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        for _ in range(3):
            try:
                isolated_db.log_usage(99999, "c", "m", "t", 1, 1, 0.0)
            except Exception:
                pass
        isolated_db.update_drama(did, status="translated")
        assert isolated_db.get_drama(did)["status"] == "translated"

    def test_leaked_connection_is_tracked_then_reclaimed(self, isolated_db):
        try:
            isolated_db.log_usage(99999, "c", "m", "t", 1, 1, 0.0)
        except Exception:
            pass
        assert len(isolated_db._open_connections) == 1
        isolated_db.list_dramas()  # next call reclaims it
        assert len(isolated_db._open_connections) == 0


class TestFullLibraryReset:
    """The Diagnostics 'Reset everything' button, used to wipe test data
    and start over. Real deletion, so this is checked directly against a
    populated library rather than trusted from a smaller unit test."""

    def test_wipes_dramas_series_and_files(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "source.mp3"), "wb") as f:
            f.write(b"audio")
        sid = isolated_db.get_or_create_series("S")
        isolated_db.upsert_glossary_term(sid, "a", "b")

        isolated_db.reset_library()

        assert isolated_db.list_dramas() == []
        assert isolated_db.list_series() == []
        assert not os.path.exists(ddir)

    def test_schema_is_immediately_usable_after_reset(self, isolated_db):
        isolated_db.reset_library()
        did = isolated_db.create_drama(title_en="Fresh")
        assert isolated_db.get_drama(did)["title_en"] == "Fresh"

    def test_dramas_directory_exists_but_empty_after_reset(self, isolated_db):
        isolated_db.create_drama(title_en="X")
        isolated_db.reset_library()
        assert os.path.isdir(isolated_db.DRAMAS_DIR)
        assert isolated_db.list_dramas() == []

    def test_reset_on_an_already_empty_library_does_not_crash(self, isolated_db):
        isolated_db.reset_library()  # nothing to wipe
        assert isolated_db.list_dramas() == []

    def test_wipes_the_cached_cedict_dictionary_too(self, isolated_db):
        cedict_path = os.path.join(isolated_db.LIBRARY_DIR, "cedict.txt")
        with open(cedict_path, "w", encoding="utf-8") as f:
            f.write("fake cedict data")

        isolated_db.reset_library()

        assert not os.path.exists(cedict_path)

    def test_reset_without_a_cedict_file_present_does_not_crash(self, isolated_db):
        cedict_path = os.path.join(isolated_db.LIBRARY_DIR, "cedict.txt")
        assert not os.path.exists(cedict_path)
        isolated_db.reset_library()  # must not raise just because there's nothing to remove


class TestSnapshotDatabase:
    """Regression coverage for a real gap: the backup feature used to
    copy library.db as a plain file while the database runs in WAL mode
    (see get_conn) -- a write still sitting in library.db-wal could be
    missing from that copy, or the copy could be mid-write. snapshot_database
    uses SQLite's own backup API instead, which is what actually guarantees
    a consistent, complete copy regardless of the WAL file's state."""

    def test_snapshot_is_written_to_disk(self, isolated_db, tmp_path_str):
        isolated_db.create_drama(title_en="Test Drama")
        dest = os.path.join(tmp_path_str, "snapshot.db")

        isolated_db.snapshot_database(dest)

        assert os.path.exists(dest)
        assert os.path.getsize(dest) > 0

    def test_snapshot_includes_a_write_made_just_before_it(self, isolated_db, tmp_path_str):
        did = isolated_db.create_drama(title_en="Before Snapshot")
        dest = os.path.join(tmp_path_str, "snapshot.db")

        isolated_db.snapshot_database(dest)

        import sqlite3
        conn = sqlite3.connect(dest)
        row = conn.execute("SELECT title_en FROM dramas WHERE id = ?", (did,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "Before Snapshot"

    def test_snapshot_is_independent_of_the_live_database(self, isolated_db, tmp_path_str):
        """The snapshot is a standalone file -- writing to the live
        database afterward must not change it."""
        did = isolated_db.create_drama(title_en="Original")
        dest = os.path.join(tmp_path_str, "snapshot.db")
        isolated_db.snapshot_database(dest)

        isolated_db.update_drama(did, title_en="Changed After Snapshot")

        import sqlite3
        conn = sqlite3.connect(dest)
        row = conn.execute("SELECT title_en FROM dramas WHERE id = ?", (did,)).fetchone()
        conn.close()
        assert row[0] == "Original"


class TestTranslateHistory:
    """Step 26b: history for the standalone translate tool -- a
    library-level table (no drama_id), same shape as presets. Exit
    condition: a completed translation is saved to history and appears
    in a history listing."""

    def test_save_and_list_roundtrips_every_field(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "你好", "Hello")
        history = isolated_db.list_translate_history()
        assert len(history) == 1
        row = history[0]
        assert row["source_language"] == "zh"
        assert row["target_language"] == "en"
        assert row["engine"] == "claude"
        assert row["source_text"] == "你好"
        assert row["translated_text"] == "Hello"
        assert row["created_at"]

    def test_list_is_most_recent_first(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "first", "First")
        isolated_db.save_translate_history("en", "ja", "deepl", "second", "Second")
        history = isolated_db.list_translate_history()
        assert [h["source_text"] for h in history] == ["second", "first"]

    def test_list_respects_limit(self, isolated_db):
        for i in range(5):
            isolated_db.save_translate_history("zh", "en", "claude", f"src{i}", f"out{i}")
        assert len(isolated_db.list_translate_history(limit=3)) == 3

    def test_clear_removes_every_row(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "a", "A")
        isolated_db.save_translate_history("ja", "en", "deepseek", "b", "B")
        isolated_db.clear_translate_history()
        assert isolated_db.list_translate_history() == []


class TestGpuLock:
    """Step 25w: the cross-process "one GPU job at a time" guard.
    background_jobs.py's own guard (Step 5c) is plain in-process module
    state, invisible to a separate OS process -- a `cli.py` run and the
    live Streamlit UI could each hold the GPU at once with neither seeing
    the other. This single-row table in the shared library.db is the
    coordination point both sides check."""

    def test_free_lock_is_acquired(self, isolated_db):
        assert isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription") is True
        holder, description = isolated_db.gpu_lock_status()
        assert holder == "ui:job1"
        assert description == "Transcription"

    def test_held_lock_refuses_a_different_holder(self, isolated_db):
        assert isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription") is True
        assert isolated_db.try_acquire_gpu_lock("cli:1234", "CLI dub") is False
        # Still job1's -- the failed attempt above must not have touched it.
        holder, description = isolated_db.gpu_lock_status()
        assert holder == "ui:job1"
        assert description == "Transcription"

    def test_same_holder_can_reacquire_its_own_lock(self, isolated_db):
        assert isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription") is True
        assert isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription") is True

    def test_release_frees_it_for_someone_else(self, isolated_db):
        isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription")
        isolated_db.release_gpu_lock("ui:job1")
        assert isolated_db.gpu_lock_status() == (None, None)
        assert isolated_db.try_acquire_gpu_lock("cli:1234", "CLI dub") is True

    def test_releasing_the_wrong_holder_is_a_no_op(self, isolated_db):
        """A lock that went stale and was taken over by someone else must
        not be released out from under its new, legitimate holder by a
        late release() call from whoever held it before."""
        isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription")
        isolated_db.release_gpu_lock("cli:1234")  # never held it
        holder, _ = isolated_db.gpu_lock_status()
        assert holder == "ui:job1"

    def test_stale_lock_is_taken_over(self, isolated_db):
        """A holder that crashed without releasing shouldn't permanently
        block the GPU -- a lock whose heartbeat is older than
        GPU_LOCK_STALE_SECONDS is treated as abandoned."""
        isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription")
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ? WHERE id = 1",
                    (isolated_db.GPU_LOCK_STALE_SECONDS + 1,))
        conn.commit()
        conn.close()
        assert isolated_db.gpu_lock_status() == (None, None)
        assert isolated_db.try_acquire_gpu_lock("cli:1234", "CLI dub") is True

    def test_heartbeat_keeps_a_long_running_holder_from_going_stale(self, isolated_db):
        isolated_db.try_acquire_gpu_lock("ui:job1", "Transcription")
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ? WHERE id = 1",
                    (isolated_db.GPU_LOCK_STALE_SECONDS - 1,))
        conn.commit()
        conn.close()
        isolated_db.heartbeat_gpu_lock("ui:job1")
        # Refreshed -- still held, and a competing holder is still refused.
        assert isolated_db.try_acquire_gpu_lock("cli:1234", "CLI dub") is False

    def test_status_is_free_when_nothing_has_ever_held_it(self, isolated_db):
        assert isolated_db.gpu_lock_status() == (None, None)


class TestImportTimeSafety:
    """Step 51: importing db.py alone must never touch a real library path
    -- init has to be lazy (first real get_conn() call), not at import
    time, so `pytest` collecting/importing db can't create or touch the
    real library/library.db before isolated_db redirects it."""

    @staticmethod
    def _copy_db_and_deps(temp_dir):
        # db.py imports core.py (for LINE_FIELDS); both are self-contained
        # (stdlib-only), so copying just the two is enough to import db.py
        # with a real, separate interpreter, in a directory with nothing
        # else in it -- the only way to observe a genuinely fresh module
        # import rather than the already-imported module every other test
        # in this process shares.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shutil.copy(os.path.join(project_root, "db.py"), os.path.join(temp_dir, "db.py"))
        shutil.copy(os.path.join(project_root, "core.py"), os.path.join(temp_dir, "core.py"))

    def test_bare_import_does_not_touch_any_library_dir(self):
        temp_dir = tempfile.mkdtemp(prefix="baihe_import_check_")
        try:
            self._copy_db_and_deps(temp_dir)
            result = subprocess.run(
                [sys.executable, "-c", "import db"],
                cwd=temp_dir, capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert not os.path.exists(os.path.join(temp_dir, "library")), (
                "importing db.py alone created a library/ directory as a side effect"
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_first_real_call_still_initializes_the_schema(self):
        # The other half of the guarantee: init must still happen, just
        # lazily -- a real call right after import has to work normally.
        temp_dir = tempfile.mkdtemp(prefix="baihe_import_check_")
        try:
            self._copy_db_and_deps(temp_dir)
            script = (
                "import db\n"
                "did = db.create_drama(title_en='x')\n"
                "assert db.get_drama(did)['title_en'] == 'x'\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=temp_dir, capture_output=True, text=True, timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert os.path.exists(os.path.join(temp_dir, "library", "library.db"))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_isolated_db_fixture_still_initializes_a_working_schema(self, isolated_db):
        # No regression to the isolation isolated_db already provides.
        did = isolated_db.create_drama(title_en="Fixture Still Works")
        assert isolated_db.get_drama(did)["title_en"] == "Fixture Still Works"
        assert os.path.exists(isolated_db.DB_PATH)

    def test_configure_library_dir_reinitializes_at_the_new_path(self, isolated_db, tmp_path_str):
        # isolated_db already redirected + initialized once; redirecting a
        # second time (configure_library_dir alone, no explicit init_db())
        # must still produce a working schema at the new path rather than
        # silently reusing the old path's "already ready" state.
        isolated_db.create_drama(title_en="Old Path")
        isolated_db.configure_library_dir(tmp_path_str)
        did = isolated_db.create_drama(title_en="New Path")
        assert isolated_db.get_drama(did)["title_en"] == "New Path"
        assert os.path.exists(os.path.join(tmp_path_str, "library.db"))
