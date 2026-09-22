"""
tests/test_library_features.py -- tests for the library-experience layer:
progress tracking, reading history, translation versions, custom tags,
storage management, and time estimates.
"""

import sys
import os
import tempfile
import shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import story_context as sc
import storage as stg
from core import Line


class TestTimeEstimates:
    def test_reading_time_counts_words(self):
        lines = [Line(idx=0, start=0, end=1, zh="a", en="one two three four five")]
        assert sc.estimate_reading_time(lines)["word_count"] == 5

    def test_reading_time_scales_with_wpm(self):
        lines = [Line(idx=i, start=0, end=1, zh="a", en="w " * 100) for i in range(10)]
        slow = sc.estimate_reading_time(lines, wpm=100)["minutes"]
        fast = sc.estimate_reading_time(lines, wpm=400)["minutes"]
        assert slow > fast

    def test_listening_time_uses_last_timestamp(self):
        lines = [Line(idx=0, start=0, end=90.0, zh="a", en="b")]
        assert sc.estimate_listening_time(lines)["seconds"] == 90.0

    def test_empty_lines_no_crash(self):
        assert sc.estimate_listening_time([])["seconds"] == 0
        assert sc.estimate_reading_time([])["word_count"] == 0

    def test_percent_complete(self):
        assert sc.compute_percent_complete(9, 10) == 100.0
        assert sc.compute_percent_complete(4, 10) == 50.0
        assert sc.compute_percent_complete(0, 0) == 0.0

    def test_duration_formatting(self):
        assert sc._format_duration(0.4) == "under a minute"
        assert sc._format_duration(45) == "45m"
        assert sc._format_duration(120) == "2h"
        assert sc._format_duration(125) == "2h 5m"


class TestRelationshipMap:
    def test_mermaid_includes_nodes_and_edges(self):
        m = sc.relationship_map_to_mermaid({
            "characters": [{"name": "A", "role": "lead"}, {"name": "B", "role": "rival"}],
            "relationships": [{"from": "A", "to": "B", "relation": "rivals"}]})
        assert "graph TD" in m and "rivals" in m and "lead" in m

    def test_empty_map_returns_empty_string(self):
        assert sc.relationship_map_to_mermaid({"characters": [], "relationships": []}) == ""

    def test_relationship_missing_endpoint_skipped(self):
        m = sc.relationship_map_to_mermaid({
            "characters": [{"name": "A", "role": ""}],
            "relationships": [{"from": "A", "to": "", "relation": "x"}]})
        assert "-->" not in m

    def test_pure_mt_engine_returns_empty_map(self):
        class PureMT:
            supports_reference = False
        assert sc.build_relationship_map([], {}, PureMT()) == {"characters": [], "relationships": []}


class TestProgressTracking:
    def test_save_and_get(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_progress(did, last_line_idx=5, percent_complete=30.0)
        p = isolated_db.get_progress(did)
        assert p["last_line_idx"] == 5 and p["percent_complete"] == 30.0

    def test_partial_update_preserves_other_fields(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_progress(did, last_line_idx=5, percent_complete=30.0)
        isolated_db.save_progress(did, audio_position_seconds=99.5)
        p = isolated_db.get_progress(did)
        assert p["last_line_idx"] == 5
        assert p["percent_complete"] == 30.0
        assert p["audio_position_seconds"] == 99.5

    def test_no_progress_returns_none(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        assert isolated_db.get_progress(did) is None

    def test_continue_shelf_excludes_finished(self, isolated_db):
        d1 = isolated_db.create_drama(title_en="Partial")
        d2 = isolated_db.create_drama(title_en="Done")
        isolated_db.save_progress(d1, percent_complete=40.0)
        isolated_db.save_progress(d2, percent_complete=100.0)
        titles = [d["title_en"] for d in isolated_db.list_continue_reading()]
        assert "Partial" in titles and "Done" not in titles

    def test_continue_shelf_excludes_untouched(self, isolated_db):
        isolated_db.create_drama(title_en="Untouched")
        assert isolated_db.list_continue_reading() == []

    def test_history_records_entries(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_progress(did, last_line_idx=1, percent_complete=10.0)
        isolated_db.save_progress(did, last_line_idx=2, percent_complete=20.0)
        assert len(isolated_db.list_reading_history(did)) == 2

    def test_clear_history(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_progress(did, last_line_idx=1, percent_complete=10.0)
        isolated_db.clear_reading_history(did)
        assert isolated_db.list_reading_history(did) == []


class TestTranslationVersions:
    def test_save_and_list(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        lines = [Line(idx=0, start=0, end=1, zh="原", en="v1")]
        isolated_db.save_translation_version(did, lines, "First", "claude", "m", make_active=True)
        versions = isolated_db.list_translation_versions(did)
        assert len(versions) == 1 and versions[0]["is_active"] == 1

    def test_only_one_active_at_a_time(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        lines = [Line(idx=0, start=0, end=1, zh="原", en="v")]
        v1 = isolated_db.save_translation_version(did, lines, "A", make_active=True)
        v2 = isolated_db.save_translation_version(did, lines, "B", make_active=True)
        versions = {v["id"]: v["is_active"] for v in isolated_db.list_translation_versions(did)}
        assert versions[v1] == 0 and versions[v2] == 1

    def test_content_preserved_across_versions(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        v1 = isolated_db.save_translation_version(
            did, [Line(idx=0, start=0, end=1, zh="原", en="claude text")], "A")
        isolated_db.save_translation_version(
            did, [Line(idx=0, start=0, end=1, zh="原", en="deepseek text")], "B")
        assert isolated_db.get_translation_version(v1)["lines"][0]["en"] == "claude text"

    def test_switching_active_version(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        lines = [Line(idx=0, start=0, end=1, zh="原", en="v")]
        v1 = isolated_db.save_translation_version(did, lines, "A", make_active=True)
        v2 = isolated_db.save_translation_version(did, lines, "B")
        isolated_db.set_active_translation_version(did, v2)
        versions = {v["id"]: v["is_active"] for v in isolated_db.list_translation_versions(did)}
        assert versions[v2] == 1 and versions[v1] == 0

    def test_delete_version(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        v = isolated_db.save_translation_version(
            did, [Line(idx=0, start=0, end=1, zh="a", en="b")], "A")
        isolated_db.delete_translation_version(v)
        assert isolated_db.list_translation_versions(did) == []


class TestCustomTagsAndMetadata:
    def test_distinct_tags_split_and_deduped(self, isolated_db):
        isolated_db.create_drama(title_en="A", custom_tags="favorite, slow burn")
        isolated_db.create_drama(title_en="B", custom_tags="favorite, angst")
        assert isolated_db.distinct_custom_tags() == ["angst", "favorite", "slow burn"]

    def test_no_tags_returns_empty(self, isolated_db):
        isolated_db.create_drama(title_en="A")
        assert isolated_db.distinct_custom_tags() == []

    def test_metadata_fields_persist(self, isolated_db):
        did = isolated_db.create_drama(
            title_en="T", genre="historical", publication_status="ongoing",
            chapter_count=42, personal_notes="note")
        d = isolated_db.get_drama(did)
        assert d["genre"] == "historical"
        assert d["publication_status"] == "ongoing"
        assert d["chapter_count"] == 42
        assert d["personal_notes"] == "note"


class TestStorage:
    def _make_dir(self):
        d = tempfile.mkdtemp(prefix="stg_test_")
        os.makedirs(os.path.join(d, "dub_clips"), exist_ok=True)
        with open(os.path.join(d, "dub_clips", "line_0.wav"), "wb") as f:
            f.write(b"x" * 5000)
        with open(os.path.join(d, "source.mp3"), "wb") as f:
            f.write(b"y" * 10000)
        with open(os.path.join(d, "ocr_page1.png"), "wb") as f:
            f.write(b"z" * 3000)
        return d

    def test_scan_categorizes_sizes(self):
        d = self._make_dir()
        try:
            scan = stg.scan_drama_storage(d)
            assert scan["categories"]["dub_clips"] == 5000
            assert scan["categories"]["ocr_temp"] == 3000
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cleanup_never_removes_source(self):
        d = self._make_dir()
        try:
            stg.clean_drama_storage(d, ["dub_clips", "ocr_temp", "temp_files"])
            assert os.path.exists(os.path.join(d, "source.mp3"))
            assert not os.path.exists(os.path.join(d, "dub_clips"))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cleanup_reports_freed_bytes(self):
        d = self._make_dir()
        try:
            res = stg.clean_drama_storage(d, ["dub_clips"])
            assert res["freed_bytes"] == 5000
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_nonexistent_dir_no_crash(self):
        assert stg.scan_drama_storage("/nonexistent/xyz")["total_bytes"] == 0
        assert stg.clean_drama_storage("/nonexistent/xyz", ["dub_clips"])["freed_bytes"] == 0

    def test_format_bytes(self):
        assert stg.format_bytes(512) == "512 B"
        assert stg.format_bytes(1536) == "1.5 KB"

    def test_archival_preset_keeps_regenerables(self):
        cats = stg.categories_for_preset("archival")
        assert "dub_clips" not in cats and "temp_files" in cats

    def test_minimal_preset_cleans_everything_regenerable(self):
        cats = stg.categories_for_preset("minimal")
        for expected in ("dub_clips", "ocr_temp", "typeset_pages"):
            assert expected in cats

    def test_all_cleanable_categories_are_regenerable(self):
        # nothing marked cleanable should ever be irreplaceable
        for key, cfg in stg.CLEANABLE_CATEGORIES.items():
            assert cfg["regenerable"] is True


class TestResumeHandoff:
    """Resume sets a target in one tab that another tab has to honour.
    Two bugs lived here: the reader's keyed selectbox ignored the target
    entirely (so Resume opened the wrong drama), and the Library claimed
    to have navigated when Streamlit's st.tabs offers no way to do that."""

    def _options(self):
        dramas = [{"id": 3, "title_en": "Other", "last_page": 1},
                  {"id": 4, "title_en": "Test", "last_page": 7}]
        return dramas, {f"#{d['id']} — {d['title_en']}": d for d in dramas}

    def _apply_pending(self, session, options):
        pending = session.pop("reader_resume_pending", None)
        if pending:
            for label, d in options.items():
                if d["id"] == pending:
                    session["reader_drama_pick"] = label
                    session["reader_resume_banner"] = label
                    break
        return session

    def test_resume_overrides_a_stale_selection(self):
        dramas, options = self._options()
        session = {"reader_drama_pick": "#3 — Other", "reader_resume_pending": 4}
        self._apply_pending(session, options)
        assert session["reader_drama_pick"] == "#4 — Test"

    def test_resume_sets_a_one_time_banner(self):
        _, options = self._options()
        session = {"reader_resume_pending": 4}
        self._apply_pending(session, options)
        assert session.pop("reader_resume_banner", None) == "#4 — Test"
        assert session.pop("reader_resume_banner", None) is None

    def test_manual_selection_survives_when_no_resume_pending(self):
        _, options = self._options()
        session = {"reader_drama_pick": "#3 — Other"}
        self._apply_pending(session, options)
        assert session["reader_drama_pick"] == "#3 — Other"

    def test_pending_flag_is_consumed_not_sticky(self):
        _, options = self._options()
        session = {"reader_resume_pending": 4}
        self._apply_pending(session, options)
        assert "reader_resume_pending" not in session

    def test_unknown_drama_id_leaves_selection_alone(self):
        _, options = self._options()
        session = {"reader_drama_pick": "#3 — Other", "reader_resume_pending": 999}
        self._apply_pending(session, options)
        assert session["reader_drama_pick"] == "#3 — Other"

    def test_resume_page_carries_the_saved_position(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_progress(did, last_page=7, percent_complete=40.0)
        entry = isolated_db.list_continue_reading()[0]
        assert entry["last_page"] == 7
