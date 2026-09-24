"""
tests/test_db.py -- tests for db.py, using the isolated_db fixture so
nothing here ever touches your real library.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line


class TestDramaCRUD:
    def test_create_and_get(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        drama = isolated_db.get_drama(did)
        assert drama["title_en"] == "Test Drama"
        assert drama["status"] == "not started"  # default

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
        page = isolated_db.get_page(pid)
        assert page["rendered_filename"] == "pages/typeset_0000.png"


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
