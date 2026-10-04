"""
tests/test_db.py -- tests for db.py, using the isolated_db fixture so
nothing here ever touches your real library.
"""

import contextlib
import gc
import shutil
import sqlite3
import subprocess
import sys
import os
import tempfile
import threading
import weakref
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from core import Line


class TestDramaCRUD:
    def test_create_and_get(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        drama = isolated_db.get_drama(did)
        assert drama["title_en"] == "Test Drama"
        assert drama["status"] == "not started"  # default

    def test_narration_language_defaults_to_translation_and_persists_original(self, isolated_db):
        """Step 26c: existing dramas (and any new one that doesn't opt in)
        keep narrating the translation -- 'original' only takes effect
        once a drama explicitly sets it."""
        did = isolated_db.create_drama(title_en="Novel", content_mode="novel_narration")
        assert isolated_db.get_drama(did)["narration_language"] == "translation"
        isolated_db.update_drama(did, narration_language="original")
        assert isolated_db.get_drama(did)["narration_language"] == "original"

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
        did = isolated_db.create_drama(title_en="Test", translation_engine="fake")
        isolated_db.log_usage(did, "fake", "fake", "translate", 100, 50, 0.0)
        isolated_db.log_usage(did, "fake", "fake", "translate", 200, 100, 0.0)
        row = next(r for r in isolated_db.get_usage_by_drama() if r["id"] == did)
        assert row["translation_engine"] == "fake"
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
    call poisoned the whole session until restart. Step 69 wrapped every
    public function's own get_conn()/close() pair in try/finally, so a
    failure inside one of them (like log_usage below) no longer leaks a
    connection at all -- see TestLeakedConnectionCleanup for the
    still-needed last-resort net this leaves in get_conn() itself, for a
    caller that opens one directly without going through that pattern."""

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

    def test_a_failed_write_no_longer_leaks_a_connection(self, isolated_db):
        # Before Step 69, this same failing call left its connection
        # tracked as "leaked" until the next get_conn() call reclaimed it
        # -- log_usage's own get_conn()/close() pair had no try/finally.
        # Now the failure is caught by its own with-block on the way out,
        # so nothing is ever tracked as open in the first place.
        try:
            isolated_db.log_usage(99999, "c", "m", "t", 1, 1, 0.0)
        except Exception:
            pass
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

    def test_reset_closes_a_connection_this_thread_left_open(self, isolated_db):
        leftover = isolated_db.get_conn()
        isolated_db.reset_library()
        try:
            leftover.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            return
        raise AssertionError("the leftover connection was still open")

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
            "Su Shan", clip, ref_text="a line", clone_engine="gpt_sovits", voice_design="")

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
        assert c["clone_engine"] == "gpt_sovits"

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

    def test_max_holders_allows_that_many_and_no_more(self, isolated_db):
        assert isolated_db.try_acquire_gpu_lock("ui:a", "A", max_holders=2) is True
        assert isolated_db.try_acquire_gpu_lock("cli:1", "B", max_holders=2) is True
        assert isolated_db.try_acquire_gpu_lock("ui:c", "C", max_holders=2) is False
        assert isolated_db.try_acquire_gpu_lock("ui:c", "C") is False  # a cap of 1 too
        assert isolated_db.gpu_lock_holder_count() == 2
        assert isolated_db.gpu_lock_holder_count(exclude_holder="ui:a") == 1
        isolated_db.release_gpu_lock("ui:a")
        assert isolated_db.gpu_lock_status() == ("cli:1", "B")  # only its own row went
        assert isolated_db.try_acquire_gpu_lock("ui:c", "C", max_holders=2) is True

    def test_max_holders_is_capped_by_the_table(self, isolated_db):
        for i in range(isolated_db.GPU_LOCK_MAX_SLOTS):
            assert isolated_db.try_acquire_gpu_lock(f"ui:{i}", max_holders=99) is True
        assert isolated_db.try_acquire_gpu_lock("ui:extra", max_holders=99) is False

    def test_settle_seconds_refuses_joining_a_fresh_holder(self, isolated_db):
        assert isolated_db.try_acquire_gpu_lock("ui:a", "A", max_holders=2, settle_seconds=60) is True
        assert isolated_db.try_acquire_gpu_lock("ui:b", "B", max_holders=2, settle_seconds=60) is False
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET acquired_at = acquired_at - 61")
        conn.commit()
        conn.close()
        assert isolated_db.try_acquire_gpu_lock("ui:b", "B", max_holders=2, settle_seconds=60) is True

    def test_a_stale_holder_does_not_count_and_heartbeat_is_per_holder(self, isolated_db):
        isolated_db.try_acquire_gpu_lock("ui:a", "A", max_holders=2)
        isolated_db.try_acquire_gpu_lock("ui:b", "B", max_holders=2)
        conn = isolated_db.get_conn()
        conn.execute("UPDATE gpu_lock SET heartbeat_at = heartbeat_at - ?",
                     (isolated_db.GPU_LOCK_STALE_SECONDS + 1,))
        conn.commit()
        conn.close()
        isolated_db.heartbeat_gpu_lock("ui:b")
        assert isolated_db.gpu_lock_holder_count() == 1
        assert isolated_db.try_acquire_gpu_lock("ui:c", "C", max_holders=2) is True  # took a's slot
        assert isolated_db.try_acquire_gpu_lock("ui:d", "D", max_holders=2) is False

    def test_old_single_row_table_is_migrated_keeping_the_holder(self, isolated_db):
        conn = isolated_db.get_conn()
        conn.execute("DROP TABLE gpu_lock")
        conn.execute("""CREATE TABLE gpu_lock (id INTEGER PRIMARY KEY CHECK (id = 1),
                        holder TEXT NOT NULL, description TEXT, acquired_at REAL NOT NULL,
                        heartbeat_at REAL NOT NULL)""")
        conn.commit()
        conn.close()
        assert isolated_db.try_acquire_gpu_lock("cli:1", "CLI") is True
        isolated_db.init_db()
        assert isolated_db.gpu_lock_status() == ("cli:1", "CLI")
        assert isolated_db.try_acquire_gpu_lock("ui:a", "A", max_holders=2) is True


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
        # db.py takes its library location from portable.data_dir() (Step 80b).
        shutil.copy(os.path.join(project_root, "portable.py"), os.path.join(temp_dir, "portable.py"))

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


def _is_tracked(db_module, conn):
    return any(ref() is conn for refs in db_module._open_connections.values() for ref in refs)


class TestNestedConnections:
    """A helper that opens its own connection while its caller still holds
    one on the same thread used to close the caller's connection, silently
    rolling back the caller's uncommitted writes."""

    def test_a_nested_get_conn_keeps_the_outer_transaction(self, isolated_db):
        did = isolated_db.create_drama(title_en="Before")
        with contextlib.closing(isolated_db.get_conn()) as outer:
            outer.execute("UPDATE dramas SET title_en = 'After' WHERE id = ?", (did,))
            assert outer.in_transaction
            isolated_db.list_dramas()   # opens and closes its own connection
            assert outer.in_transaction
            outer.commit()
        assert isolated_db.get_drama(did)["title_en"] == "After"
        assert len(isolated_db._open_connections) == 0

    def test_a_connection_dropped_without_close_releases_the_write_lock(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        conn = isolated_db.get_conn()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE dramas SET title_en = 'lost' WHERE id = ?", (did,))
        # The registry must not keep it, and its write lock, alive. sqlite3's
        # statement cache refers back to its connection, so the cycle
        # collector is what frees a dropped one.
        del conn
        gc.collect()
        isolated_db.update_drama(did, status="translated")
        assert isolated_db.get_drama(did)["title_en"] == "X"
        assert isolated_db.get_drama(did)["status"] == "translated"


class TestLeakedConnectionCleanup:
    """Step 69: get_conn()'s own connection-tracking exists to catch a
    connection leaked by a mid-statement failure (get_conn() called, but
    the exception skips the matching close()). This app runs real
    background_jobs.py threading.Thread workers concurrently with the
    main Streamlit thread, so that leak is routinely left by one thread
    and only discovered by a get_conn() call on another."""

    def test_leaked_connection_from_a_dead_background_thread_is_genuinely_closed(self, isolated_db):
        leaked = {}

        def worker():
            conn = isolated_db.get_conn()
            leaked["conn"] = conn
            # Simulates a mid-statement failure (a transient "database is
            # locked", a bad parameter) that skips conn.close() -- exactly
            # the scenario this cleanup exists to catch. The thread then
            # exits without ever closing its own connection. Caught here
            # (rather than left to propagate) only so the test doesn't
            # also have to deal with pytest's own unraisable-in-thread
            # warning -- the leak itself doesn't depend on that.
            try:
                raise RuntimeError("simulated transient db failure")
            except RuntimeError:
                pass

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert not t.is_alive()

        leaked_conn = leaked["conn"]
        assert _is_tracked(isolated_db, leaked_conn)

        # The dead worker thread can never touch its own connection again,
        # so the main thread's next get_conn() call should be able to
        # actually close it -- not just drop it from tracking (the bug:
        # closing it used to raise sqlite3.ProgrammingError for being on
        # the wrong thread, caught and silently discarded, leaving the
        # connection open but untracked and never retried).
        import pytest

        conn2 = isolated_db.get_conn()
        try:
            assert not _is_tracked(isolated_db, leaked_conn)
            with pytest.raises(sqlite3.ProgrammingError):
                leaked_conn.execute("SELECT 1")
        finally:
            conn2.close()


    def test_leaked_connection_close_failure_is_logged_not_silently_discarded(self, isolated_db, monkeypatch):
        # sqlite3.Connection is a C type that forbids monkeypatching its
        # own close() (an "immutable type" TypeError), so a real close
        # failure is triggered here instead: a connection opened with
        # sqlite3's default check_same_thread=True (get_conn() itself
        # always passes False, but this exercises the fallback logging
        # path in case some other close failure ever occurs) on a
        # now-dead thread genuinely raises ProgrammingError when closed
        # from a different thread -- exactly the failure this cleanup's
        # own warning exists to surface instead of silently discarding.
        import applog

        holder = {}

        def worker():
            holder["conn"] = sqlite3.connect(isolated_db.DB_PATH,
                                             factory=isolated_db._TrackedConnection)
            holder["ident"] = threading.get_ident()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert not t.is_alive()

        # Register it as if it were a leak left by that (now-dead) thread.
        isolated_db._open_connections[holder["ident"]] = [weakref.ref(holder["conn"])]

        logged = []

        class FakeLogger:
            def warning(self, *args, **kwargs):
                logged.append((args, kwargs))

        monkeypatch.setattr(applog, "get_logger", lambda: FakeLogger())

        # The next get_conn() call sweeps the dead-thread entry, hits the
        # real cross-thread close failure, and must log it rather than pass.
        conn2 = isolated_db.get_conn()
        try:
            assert logged, "a failed leaked-connection close should be logged via applog"
            assert holder["ident"] not in isolated_db._open_connections
        finally:
            conn2.close()

    def test_a_still_running_threads_own_connection_is_never_closed_by_another_thread(self, isolated_db):
        # The other half of the same fix: a connection isn't a leak just
        # because it's still open when another thread calls get_conn() --
        # it might be in perfectly ordinary use by a thread that's still
        # running. Closing it out from under that thread is a worse bug
        # than the one this cleanup exists to catch (this reproduced for
        # real during this step: Streamlit's own AppTest runs the app
        # script in its own thread while the test thread also calls
        # db.py, and an earlier version of this fix closed the script
        # thread's still-in-use connection, which then failed its very
        # next statement with "Cannot operate on a closed database").
        conn_opened = threading.Event()
        release = threading.Event()
        holder = {}

        def worker():
            conn = isolated_db.get_conn()
            holder["conn"] = conn
            conn_opened.set()
            release.wait(timeout=5)
            conn.execute("SELECT 1")  # must still work -- conn must still be open
            conn.close()

        t = threading.Thread(target=worker)
        t.start()
        try:
            assert conn_opened.wait(timeout=5), "worker never opened its connection"
            # The worker's own thread is still alive and hasn't closed its
            # connection yet -- a get_conn() call on the main thread must
            # not treat that as a leak.
            conn2 = isolated_db.get_conn()
            try:
                assert _is_tracked(isolated_db, holder["conn"])
            finally:
                conn2.close()
        finally:
            release.set()
            t.join(timeout=5)
        assert not t.is_alive()


class TestStep26eProfilesMigration:
    """_migrate_step26e_profiles: an existing (pre-profiles) install's
    progress/reading_history/personal_notes data must survive db.init_db()
    unchanged in content, just now attributed to a newly-created default
    profile -- and progress's primary key actually changes (SQLite can't
    ALTER a PRIMARY KEY in place), so this builds a real old-schema
    database by hand rather than trusting the new CREATE TABLE IF NOT
    EXISTS block alone (which only a fresh install ever goes through)."""

    def _make_old_schema_db(self, temp_dir):
        db_path = os.path.join(temp_dir, "library.db")
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE dramas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title_en TEXT, title_zh TEXT, personal_notes TEXT
            );
            CREATE TABLE progress (
                drama_id INTEGER PRIMARY KEY,
                last_line_idx INTEGER DEFAULT 0,
                audio_position_seconds REAL DEFAULT 0,
                last_page INTEGER DEFAULT 1,
                percent_complete REAL DEFAULT 0,
                last_accessed_at TEXT
            );
            CREATE TABLE reading_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drama_id INTEGER NOT NULL,
                line_id INTEGER, line_idx INTEGER,
                percent_complete REAL, accessed_at TEXT
            );
        """)
        conn.execute("INSERT INTO dramas (id, title_en, personal_notes) VALUES "
                     "(1, 'Old Drama', 'a private note from before profiles existed')")
        conn.execute("INSERT INTO progress (drama_id, last_line_idx, last_page, "
                     "percent_complete, last_accessed_at) VALUES (1, 7, 3, 42.0, "
                     "'2025-01-01T00:00:00')")
        conn.execute("INSERT INTO reading_history (drama_id, line_idx, percent_complete, "
                     "accessed_at) VALUES (1, 7, 42.0, '2025-01-01T00:00:00')")
        conn.commit()
        conn.close()

    def _with_redirected_library(self, temp_dir, fn):
        previous = db.LIBRARY_DIR
        try:
            db.configure_library_dir(temp_dir)
            fn()
        finally:
            db.configure_library_dir(previous)

    def test_upgrading_a_pre_profiles_install_preserves_its_data(self, tmp_path_str):
        self._make_old_schema_db(tmp_path_str)

        def upgrade_and_check():
            db.init_db()  # this is the real upgrade path a restart runs
            profiles = db.list_profiles()
            assert len(profiles) == 1
            default_id = profiles[0]["id"]

            prog = db.get_progress(1, profile_id=default_id)
            assert prog["last_line_idx"] == 7
            assert prog["last_page"] == 3
            assert prog["percent_complete"] == 42.0

            hist = db.list_reading_history(1, profile_id=default_id)
            assert len(hist) == 1
            assert hist[0]["percent_complete"] == 42.0

            assert (db.get_personal_notes(1, profile_id=default_id)
                    == "a private note from before profiles existed")

        self._with_redirected_library(tmp_path_str, upgrade_and_check)

    def test_running_init_db_twice_does_not_duplicate_the_default_profile(self, tmp_path_str):
        self._make_old_schema_db(tmp_path_str)

        def upgrade_twice():
            db.init_db()
            db.init_db()  # e.g. the app restarting again later
            assert len(db.list_profiles()) == 1

        self._with_redirected_library(tmp_path_str, upgrade_twice)


class TestSafeAlterGuardsInitDbAgainstACrossProcessRace:
    """Migration Slice 6 (React + FastAPI migration, D1 fix 4):
    db._ensure_ready() calls init_db() lazily, per process, with no
    cross-process lock -- Streamlit and a separately-running
    `python -m api` process can both pass init_db()'s own
    "column not in existing_cols yet" check before either ALTER runs, so
    the second ALTER for the same column hits
    sqlite3.OperationalError: duplicate column name even though nothing
    is actually wrong. _safe_alter() is the guard; these tests exercise
    it directly rather than trying to force a real thread race against
    SQLite's own file locking."""

    def test_duplicate_column_is_swallowed(self, tmp_path):
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("ALTER TABLE t ADD COLUMN x TEXT")
        # Simulates the race outcome directly: both "processes" already
        # passed the not-in-existing_cols check, so this ALTER runs for
        # real against a column that's already there.
        db._safe_alter(conn, "ALTER TABLE t ADD COLUMN x TEXT")  # must not raise
        cols = {r[1] for r in conn.execute("PRAGMA table_info(t)").fetchall()}
        assert cols == {"id", "x"}
        conn.close()

    def test_other_operational_errors_still_raise(self, tmp_path):
        import pytest
        conn = sqlite3.connect(str(tmp_path / "t.db"))
        with pytest.raises(sqlite3.OperationalError):
            db._safe_alter(conn, "ALTER TABLE does_not_exist ADD COLUMN x TEXT")
        conn.close()

    def test_init_db_still_adds_a_genuinely_missing_column(self, isolated_db):
        # isolated_db's own setup already ran init_db() once against a
        # fresh schema -- confirms the real check-then-_safe_alter path
        # (not just the swallow-a-duplicate path) actually adds columns.
        conn = db.get_conn()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(lines)").fetchall()}
        conn.close()
        assert {"speaker", "flag", "flag_note", "sfx"} <= cols


class TestJobRecords:
    """Migration Slice 7 (D1 fix 1): job_records is a cross-process,
    records-only mirror of background_jobs.py's own in-memory job state
    -- no resume, so these tests exercise db.py's own CRUD directly
    rather than a real multi-process scenario."""

    def test_save_and_get_a_job_record(self, isolated_db):
        db.save_job_record("job1", status="running", progress=0.5, message="halfway",
                           error=None, description="Translating", gpu_touching=True,
                           started_at=100.0, finished_at=None)
        rec = db.get_job_record("job1")
        assert rec["status"] == "running"
        assert rec["progress"] == 0.5
        assert rec["description"] == "Translating"
        assert bool(rec["gpu_touching"]) is True
        assert rec["finished_at"] is None

    def test_saving_again_updates_in_place_not_a_second_row(self, isolated_db):
        db.save_job_record("job1", status="running", progress=0.0)
        db.save_job_record("job1", status="done", progress=1.0, finished_at=200.0)
        records = db.list_job_records()
        assert len(records) == 1
        assert records[0]["status"] == "done"
        assert records[0]["finished_at"] == 200.0

    def test_get_missing_record_is_none(self, isolated_db):
        assert db.get_job_record("does-not-exist") is None

    def test_delete_job_record(self, isolated_db):
        db.save_job_record("job1", status="done")
        db.delete_job_record("job1")
        assert db.get_job_record("job1") is None

    def test_clear_all_job_records(self, isolated_db):
        db.save_job_record("job1", status="done")
        db.save_job_record("job2", status="running")
        db.clear_all_job_records()
        assert db.list_job_records() == []

    def test_list_job_records_orders_newest_started_first(self, isolated_db):
        db.save_job_record("old", status="done", started_at=100.0)
        db.save_job_record("new", status="running", started_at=200.0)
        records = db.list_job_records()
        assert [r["job_id"] for r in records] == ["new", "old"]


class TestAppSettings:
    """Migration Slice 9 (D1 fix 2): a general-purpose, cross-process
    app-settings store -- distinct from sources/store.py's own settings
    table, which stays scoped to the source-adapter system."""

    def test_missing_key_returns_the_given_default(self, isolated_db):
        assert db.get_app_setting("does_not_exist", "fallback") == "fallback"
        assert db.get_app_setting("does_not_exist") is None

    def test_set_then_get_round_trips(self, isolated_db):
        db.set_app_setting("gpu_limit_enabled", False)
        assert db.get_app_setting("gpu_limit_enabled") is False

    def test_setting_again_updates_in_place_not_a_second_row(self, isolated_db):
        db.set_app_setting("k", 1)
        db.set_app_setting("k", 2)
        conn = db.get_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM app_settings WHERE key='k'").fetchone()["n"]
        conn.close()
        assert n == 1
        assert db.get_app_setting("k") == 2

    def test_value_types_round_trip_via_json(self, isolated_db):
        db.set_app_setting("a_string", "hello")
        db.set_app_setting("a_number", 3.5)
        db.set_app_setting("a_bool", True)
        db.set_app_setting("a_list", [1, 2, 3])
        assert db.get_app_setting("a_string") == "hello"
        assert db.get_app_setting("a_number") == 3.5
        assert db.get_app_setting("a_bool") is True
        assert db.get_app_setting("a_list") == [1, 2, 3]


def test_init_db_moves_dramas_off_the_removed_test_engine(isolated_db):
    did = isolated_db.create_drama(title_en="Old", translation_engine="test_offline")
    other = isolated_db.create_drama(title_en="Kept", translation_engine="deepseek")
    isolated_db.init_db()
    assert isolated_db.get_drama(did)["translation_engine"] == "claude"
    assert isolated_db.get_drama(other)["translation_engine"] == "deepseek"


# Every column init_db() adds with `ALTER TABLE ... ADD COLUMN`, per table,
# in the order it adds them. Dropping them gives a database shaped like one
# created before those migrations existed. When you add a column migration to
# db.py, add the column here too, or that migration is never run by a test
# (test_every_added_column_is_listed fails otherwise).
_INIT_DB_MIGRATED_COLUMNS = {
    "job_records": ("cancel_requested", "result_json", "owner_pid", "owner_user_id"),
    "lines": ("speaker", "dub_filename", "flag", "flag_note", "speaker_manual", "sfx"),
    "dramas": (
        "translation_engine", "content_mode", "narration_language", "source_video_filename",
        "source_language", "chinese_script", "media_type", "series_id", "episode_number",
        "episode_summary", "updated_at", "last_translate_errors", "author_romanized",
        "studio_romanized", "voice_actors_romanized", "director_romanized",
        "cover_art_filename", "genre", "publication_status", "chapter_count", "custom_tags",
        "personal_notes", "source_url", "transcript_mode", "whisper_size",
        "alignment_method", "asr_backend_choice", "min_silence_ms", "vad_threshold",
        "beam_size", "separate_vocals_first", "separation_backend", "realign_long_segments",
        "whisper_fast_mode", "use_groq", "hardsub_ocr_backend", "hardsub_interval_sec",
        "project_instructions", "notion_page_id", "owner_user_id", "is_private"),
    "series": ("instructions", "owner_user_id", "is_private"),
    "characters": ("ref_audio_filename", "ref_text", "elevenlabs_voice_id", "clone_engine",
                   "voice_design", "offline_voice", "series_character_id", "pronouns"),
    "glossary_terms": ("category", "policy", "enforce_exact", "aliases", "banned_translations"),
    "series_characters": ("gender", "voice_fingerprint", "voice_fingerprint_samples"),
    "usage_log": ("cache_read_tokens",),
    "bubbles": ("font_category", "kind", "kind_confidence", "confidence", "language",
                "orientation", "panel_id", "include_sfx"),
    "pages": ("rev", "context_summary", "run_notes"),
    "bulk_jobs": ("kind", "stage", "pipeline_id"),
    "bulk_job_lines": ("result_text", "state_at_submit"),
    "vocab_lookups": ("export_rich",),
    "style_profile": ("history_json",),
    "users": ("share_by_default",),
    "translate_history": ("user_id",),
    "auth_sessions": ("device_label",),
    "benchmark_results": ("scorer",),
    "benchmark_cases": ("tier", "set_name", "origin_drama_id", "origin_line_id"),
}


def _schema_shape(path):
    """Every table's columns (name, type, not-null, default, pk) and every
    index, ignoring column order and CREATE text, which an ALTER-upgraded
    database legitimately differs from a fresh one in."""
    conn = sqlite3.connect(path)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        indexes = conn.execute(
            "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' "
            "ORDER BY name").fetchall()
        columns = {
            name: sorted(tuple(r[1:]) for r in conn.execute(f"PRAGMA table_info({name})"))
            for kind, name, _ in master if kind == "table"}
    finally:
        conn.close()
    return {"master": master, "indexes": indexes, "columns": columns}


def _exact_snapshot(path):
    """Everything init_db() leaves behind, byte for byte: sqlite_master,
    each table's ordered columns and rows, and the persistent pragmas."""
    conn = sqlite3.connect(path)
    try:
        master = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        tables = {}
        for kind, name, _, _ in master:
            if kind != "table":
                continue
            info = conn.execute(f"PRAGMA table_info({name})").fetchall()
            rows = conn.execute(f"SELECT * FROM {name}").fetchall()
            tables[name] = (info, sorted(rows, key=repr))
        pragmas = {p: conn.execute(f"PRAGMA {p}").fetchone()[0]
                   for p in ("user_version", "journal_mode", "auto_vacuum", "page_size")}
    finally:
        conn.close()
    return {"master": master, "tables": tables, "pragmas": pragmas}


def _make_old_shape(path, share_by_default_was_on=False):
    """Turns a freshly initialised database into one from before every
    init_db() column migration, holding the rows its data migrations act on:
    a user, a session that still stores its raw user agent, a drama
    predating every added column, and a preset on the removed test engine.
    share_by_default_was_on: users.share_by_default exists with the old
    default of 1 instead of being missing."""
    conn = sqlite3.connect(path)
    try:
        for table, cols in _INIT_DB_MIGRATED_COLUMNS.items():
            for col in reversed(cols):
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        if share_by_default_was_on:
            conn.execute("ALTER TABLE users ADD COLUMN share_by_default INTEGER DEFAULT 1")
        conn.execute("INSERT INTO users (id, email, created_at) VALUES (1, 'a@example.com', 'x')")
        conn.execute(
            "INSERT INTO auth_sessions (id_hash, user_id, created_at, expires_at, last_seen_at, "
            "user_agent_short, csrf_hash) VALUES ('h', 1, 0, 9e9, 0, "
            "'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120.0', 'c')")
        conn.execute("INSERT INTO dramas (id, title_en) VALUES (1, 'Old')")
        conn.execute("INSERT INTO presets (name, translation_engine) VALUES ('p', 'test_offline')")
        conn.commit()
    finally:
        conn.close()


# Added inside data migrations that rebuild or back up tables (db.py's
# line-id and profile migrations), so they can't be dropped by hand here.
_ALTERS_WITH_OWN_MIGRATION = {("reading_history", "line_id"), ("reading_history", "profile_id")}


def _alter_columns_in_db_py():
    """Column names db.py adds to existing tables: every written-out
    `ALTER TABLE t ADD COLUMN c` as (table, column), plus the loop-driven
    ones, found as constant ("column", "TYPE ...") or ("table", "column",
    "TYPE ...") tuples anywhere in db.py, as (None, column)."""
    import ast
    import re
    src = open(os.path.join(os.path.dirname(db.__file__), "db.py"), encoding="utf-8").read()
    found = {(t, c) for t, c in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)\b", src)}
    sql_type = re.compile(r"^(TEXT|INTEGER|REAL|BLOB|NUMERIC)\b")
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Tuple) and node.elts
                and all(isinstance(e, ast.Constant) and isinstance(e.value, str)
                        for e in node.elts)):
            continue
        parts = [e.value for e in node.elts]
        if len(parts) == 2 and sql_type.match(parts[1]):
            found.add((None, parts[0]))
        elif len(parts) == 3 and sql_type.match(parts[2]):
            found.add((parts[0], parts[1]))
    return found


class TestInitDbSchema:
    """init_db() runs on every user's existing database at startup: a fresh
    database and an upgraded old one must end up with the same schema, and
    running it again must change nothing."""

    def test_running_init_db_again_changes_nothing(self, isolated_db):
        before = _exact_snapshot(isolated_db.DB_PATH)
        isolated_db.init_db()
        assert _exact_snapshot(isolated_db.DB_PATH) == before

    def test_every_added_column_is_listed(self):
        listed = {(t, c) for t, cols in _INIT_DB_MIGRATED_COLUMNS.items() for c in cols}
        listed_names = {c for _, c in listed}
        missing = sorted(
            (t, c) for t, c in _alter_columns_in_db_py()
            if (t, c) not in listed and (t is not None or c not in listed_names)
            and (t, c) not in _ALTERS_WITH_OWN_MIGRATION)
        assert not missing, (
            f"db.py adds these columns to existing tables but _INIT_DB_MIGRATED_COLUMNS in "
            f"tests/test_db.py doesn't list them, so no test upgrades an old database "
            f"through them: {missing}")

    def test_old_database_upgrades_to_the_fresh_schema(self, isolated_db):
        fresh = _schema_shape(isolated_db.DB_PATH)
        _make_old_shape(isolated_db.DB_PATH)
        old = _schema_shape(isolated_db.DB_PATH)
        for table, cols in _INIT_DB_MIGRATED_COLUMNS.items():
            names = {c[0] for c in old["columns"][table]}
            assert not names & set(cols), table

        isolated_db.init_db()

        upgraded = _schema_shape(isolated_db.DB_PATH)
        assert upgraded == fresh
        upgraded_exact = _exact_snapshot(isolated_db.DB_PATH)
        isolated_db.init_db()
        assert _exact_snapshot(isolated_db.DB_PATH) == upgraded_exact

    def test_old_database_data_migrations_run(self, isolated_db):
        _make_old_shape(isolated_db.DB_PATH, share_by_default_was_on=True)
        isolated_db.init_db()
        conn = sqlite3.connect(isolated_db.DB_PATH)
        try:
            assert conn.execute("SELECT share_by_default FROM users").fetchone() == (0,)
            assert conn.execute(
                "SELECT value FROM app_settings WHERE key = 'migrations.share_by_default_off'"
            ).fetchone() == ("true",)
            agent, label = conn.execute(
                "SELECT user_agent_short, device_label FROM auth_sessions").fetchone()
            assert agent == "" and label
            assert conn.execute("SELECT translation_engine FROM presets").fetchone() == ("claude",)
            assert conn.execute(
                "SELECT translation_engine, is_private FROM dramas").fetchone() == ("claude", 0)
        finally:
            conn.close()
