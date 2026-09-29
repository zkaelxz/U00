"""
tests/test_library_features.py -- tests for the library-experience layer:
progress tracking, reading history, translation versions, custom tags,
storage management, and time estimates.
"""

import sys
import os
import io
import zipfile
import tempfile
import shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import pytest

import background_jobs
import story_context as sc
import storage as stg
import translate_engines
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
        assert sc.build_relationship_map([], PureMT()) == {"characters": [], "relationships": []}


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


class TestProfiles:
    """Step 26e: household profiles (Jellyfin-style) -- one shared
    library, but each profile's own reading position, history and notes.
    isolated_db's own init_db() call always creates a default profile
    (see _migrate_step26e_profiles), so every test here already has one
    before it starts."""

    def test_init_db_creates_a_default_profile(self, isolated_db):
        profiles = isolated_db.list_profiles()
        assert len(profiles) == 1
        assert profiles[0]["name"] == "Me"

    def test_create_list_get(self, isolated_db):
        pid = isolated_db.create_profile("Alex", color="#ff0000")
        names = {p["name"] for p in isolated_db.list_profiles()}
        assert names == {"Me", "Alex"}
        assert isolated_db.get_profile(pid)["color"] == "#ff0000"

    def test_get_unknown_profile_returns_none(self, isolated_db):
        assert isolated_db.get_profile(99999) is None

    def test_rename(self, isolated_db):
        pid = isolated_db.create_profile("Alex")
        isolated_db.rename_profile(pid, "Alexandra")
        assert isolated_db.get_profile(pid)["name"] == "Alexandra"

    def test_delete_a_second_profile(self, isolated_db):
        pid = isolated_db.create_profile("Alex")
        isolated_db.delete_profile(pid)
        names = {p["name"] for p in isolated_db.list_profiles()}
        assert names == {"Me"}

    def test_cannot_delete_the_last_remaining_profile(self, isolated_db):
        [default_profile] = isolated_db.list_profiles()
        with pytest.raises(ValueError):
            isolated_db.delete_profile(default_profile["id"])
        assert len(isolated_db.list_profiles()) == 1

    def test_deleting_a_profile_drops_its_reading_history(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        pid = isolated_db.create_profile("Alex")
        isolated_db.save_progress(did, profile_id=pid, last_line_idx=1, percent_complete=10.0)
        assert len(isolated_db.list_reading_history(did, profile_id=pid)) == 1
        isolated_db.delete_profile(pid)
        # profile_id no longer exists -- calling with it now falls back to
        # the (only remaining) default profile's own, separate history
        assert isolated_db.list_reading_history(did) == []

    def test_progress_is_isolated_per_profile(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        [me] = isolated_db.list_profiles()
        alex = isolated_db.create_profile("Alex")

        isolated_db.save_progress(did, profile_id=me["id"], last_page=12, percent_complete=50.0)
        isolated_db.save_progress(did, profile_id=alex, last_page=3, percent_complete=10.0)

        assert isolated_db.get_progress(did, profile_id=me["id"])["last_page"] == 12
        assert isolated_db.get_progress(did, profile_id=alex)["last_page"] == 3
        # the exact bug the old schema (drama_id alone as primary key)
        # made structurally impossible to avoid -- two profiles reading
        # the same drama would silently overwrite each other's page
        assert isolated_db.get_progress(did, profile_id=me["id"])["last_page"] == 12

    def test_continue_shelf_is_isolated_per_profile(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        [me] = isolated_db.list_profiles()
        alex = isolated_db.create_profile("Alex")
        isolated_db.save_progress(did, profile_id=me["id"], percent_complete=40.0)

        assert len(isolated_db.list_continue_reading(profile_id=me["id"])) == 1
        assert isolated_db.list_continue_reading(profile_id=alex) == []

    def test_reading_history_is_isolated_per_profile(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        [me] = isolated_db.list_profiles()
        alex = isolated_db.create_profile("Alex")
        isolated_db.save_progress(did, profile_id=me["id"], last_line_idx=1, percent_complete=10.0)
        isolated_db.save_progress(did, profile_id=alex, last_line_idx=1, percent_complete=10.0)
        isolated_db.save_progress(did, profile_id=alex, last_line_idx=2, percent_complete=20.0)

        assert len(isolated_db.list_reading_history(did, profile_id=me["id"])) == 1
        assert len(isolated_db.list_reading_history(did, profile_id=alex)) == 2

    def test_clear_reading_history_only_clears_that_profile(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        [me] = isolated_db.list_profiles()
        alex = isolated_db.create_profile("Alex")
        isolated_db.save_progress(did, profile_id=me["id"], last_line_idx=1, percent_complete=10.0)
        isolated_db.save_progress(did, profile_id=alex, last_line_idx=1, percent_complete=10.0)

        isolated_db.clear_reading_history(did, profile_id=me["id"])
        assert isolated_db.list_reading_history(did, profile_id=me["id"]) == []
        assert len(isolated_db.list_reading_history(did, profile_id=alex)) == 1

    def test_personal_notes_are_isolated_per_profile(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        [me] = isolated_db.list_profiles()
        alex = isolated_db.create_profile("Alex")

        isolated_db.save_personal_notes(did, "My private note", profile_id=me["id"])
        isolated_db.save_personal_notes(did, "Alex's own note", profile_id=alex)

        assert isolated_db.get_personal_notes(did, profile_id=me["id"]) == "My private note"
        assert isolated_db.get_personal_notes(did, profile_id=alex) == "Alex's own note"

    def test_personal_notes_default_to_empty_string_not_none(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        assert isolated_db.get_personal_notes(did) == ""

    def test_calls_without_profile_id_all_resolve_to_the_same_default(self, isolated_db):
        """Every pre-Step-26e caller (and every test that predates
        profiles) never passes profile_id at all -- this is what keeps
        them all working unchanged, against one consistent profile."""
        did = isolated_db.create_drama(title_en="T")
        isolated_db.save_progress(did, last_line_idx=1, percent_complete=10.0)
        isolated_db.save_progress(did, last_line_idx=2, percent_complete=20.0)
        [default_profile] = isolated_db.list_profiles()
        assert isolated_db.get_progress(did, profile_id=default_profile["id"])["last_line_idx"] == 2
        assert len(isolated_db.list_reading_history(did, profile_id=default_profile["id"])) == 2


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


# ---------------------------------------------------------------------------
# Step 9b.3: bulk "translate everything untranslated" across a series.
# ---------------------------------------------------------------------------

def _fast_poll(module, monkeypatch):
    """Shorten the coordinator's poll sleep without patching the global
    `time` module: a process-wide no-op sleep makes background_jobs'
    job-heartbeat daemon spin and flood job_records with writes."""
    import types
    real_time = module.time
    stub = types.SimpleNamespace(**{n: getattr(real_time, n) for n in dir(real_time)
                                    if not n.startswith("__")})
    stub.sleep = lambda s: real_time.sleep(0.001)
    monkeypatch.setattr(module, "time", stub)


def _wait_for_job(job_id, timeout=60):
    """Wait for the job to finish (not a short deadline, so a slow CI box
    doesn't read a still-running job as its result)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = (background_jobs.get_status(job_id) or {}).get("status")
        if status not in ("running", "queued"):
            return
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} still running after {timeout}s")


class TestBulkSeriesTranslate:
    def _drama(self, isolated_db, n=2, engine="test_offline", series_id=None, status="aligned"):
        did = isolated_db.create_drama(title_en=f"Drama {n}", media_type="audio_drama",
                                       content_mode="audio_drama", status=status,
                                       translation_engine=engine, series_id=series_id)
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(n)])
        return did

    def _run(self, drama_ids, monkeypatch, **kw):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        _fast_poll(lt, monkeypatch)
        job_id = "test_bulk_series"
        background_jobs.clear_job(job_id)
        started = background_jobs.start_job(
            job_id, lt.run_bulk_series_translate_job, job_id, drama_ids, kw.pop("api_keys", {}), **kw)
        assert started
        _wait_for_job(job_id)
        return background_jobs.get_status(job_id)

    def test_translates_every_eligible_drama_with_its_own_saved_engine(self, isolated_db, monkeypatch):
        d1 = self._drama(isolated_db, n=1, engine="test_offline")
        d2 = self._drama(isolated_db, n=1, engine="test_offline")
        status = self._run([d1, d2], monkeypatch)
        result = status["result"]
        assert sorted(result["translated"]) == sorted([d1, d2])
        assert all(r["en"] for r in isolated_db.load_lines(d1))
        assert all(r["en"] for r in isolated_db.load_lines(d2))
        assert isolated_db.get_drama(d1)["status"] == "translated"

    def test_a_drama_already_running_is_skipped_not_queued(self, isolated_db, monkeypatch):
        d1 = self._drama(isolated_db)
        background_jobs.clear_job(f"translate_{d1}")
        background_jobs._jobs[f"translate_{d1}"] = {
            "status": "running", "progress": 0.5, "message": "", "error": None,
            "cancel_requested": False, "result": None, "started_at": time.time()}
        status = self._run([d1], monkeypatch)
        assert status["result"]["skipped_running"] == [d1]
        assert not any(r["en"] for r in isolated_db.load_lines(d1))
        background_jobs.clear_job(f"translate_{d1}")

    def test_a_drama_needing_a_key_with_none_supplied_is_skipped(self, isolated_db, monkeypatch):
        d1 = self._drama(isolated_db, engine="claude")
        status = self._run([d1], monkeypatch, api_keys={})
        assert status["result"]["skipped_no_key"] == [d1]
        assert not any(r["en"] for r in isolated_db.load_lines(d1))

    def test_a_drama_with_no_lines_is_skipped(self, isolated_db, monkeypatch):
        d1 = isolated_db.create_drama(title_en="Empty", status="aligned",
                                      translation_engine="test_offline")
        status = self._run([d1], monkeypatch)
        assert status["result"]["skipped_no_lines"] == [d1]

    def test_each_drama_uses_its_own_series_glossary(self, isolated_db, monkeypatch):
        sid = isolated_db.get_or_create_series("Test Series")
        isolated_db.upsert_glossary_term(sid, "苏杉", "Su Shan", enforce_exact=True, notes="Su Xian")
        d1 = self._drama(isolated_db, n=1, series_id=sid)

        class RecordingEngine:
            name = "test_offline"
            supports_reference = True

            def __init__(self):
                self.last_usage = {}
                self.seen_glossary = None

            def translate_batch(self, zh_lines, context):
                self.seen_glossary = context.get("glossary_terms")
                return ["Su Xian appears." for _ in zh_lines]

        recording = RecordingEngine()
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: recording)
        status = self._run([d1], monkeypatch)
        assert recording.seen_glossary and recording.seen_glossary[0]["term_original"] == "苏杉"
        # enforce_exact substitution still applies on top of the engine's own output.
        assert isolated_db.load_lines(d1)[0]["en"] == "Su Shan appears."

    def test_cancelling_stops_the_current_drama_and_skips_the_rest(self, isolated_db, monkeypatch):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        d1 = self._drama(isolated_db, n=1)
        d2 = self._drama(isolated_db, n=1)
        job_id = "test_bulk_series_cancel"
        background_jobs.clear_job(job_id)
        _fast_poll(lt, monkeypatch)

        class SlowEngine:
            name = "test_offline"
            supports_reference = True

            def __init__(self):
                self.last_usage = {}

            def translate_batch(self, zh_lines, context):
                background_jobs.request_cancel(job_id)
                background_jobs.request_cancel(f"translate_{d1}")
                return [f"EN:{z}" for z in zh_lines]

        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: SlowEngine())
        assert background_jobs.start_job(job_id, lt.run_bulk_series_translate_job, job_id, [d1, d2], {})
        _wait_for_job(job_id)
        result = background_jobs.get_status(job_id)["result"]
        assert d2 not in result["translated"]
        assert d2 not in result.get("skipped_running", [])
        assert not any(r["en"] for r in isolated_db.load_lines(d2))

    def test_monthly_cap_already_reached_skips_the_drama_without_translating(self, isolated_db, monkeypatch):
        """Step 25d item 1: this coordinator used to ignore the monthly
        spending cap entirely -- Workspace's Translate button and
        `cli.py translate` both already enforce it."""
        d1 = self._drama(isolated_db, n=1, engine="claude")
        isolated_db.log_usage(d1, "claude", "claude-sonnet-5", "translate", 1000, 500, 50.0)
        status = self._run([d1], monkeypatch, api_keys={"claude": "sk-ant-fake"}, monthly_cap=5.0)
        assert status["result"]["skipped_cap"] == [d1]
        assert not any(r["en"] for r in isolated_db.load_lines(d1))

    def test_monthly_cap_not_reached_still_translates(self, isolated_db, monkeypatch):
        d1 = self._drama(isolated_db, n=1, engine="test_offline")
        status = self._run([d1], monkeypatch, monthly_cap=100.0)
        assert status["result"]["translated"] == [d1]

    def test_a_novel_narration_drama_uses_the_novel_style_preset_not_audio_drama(
            self, isolated_db, monkeypatch):
        """Step 25d item 1: this used to hardcode "audio_drama" even for a
        novel-narration drama -- same per-content-mode default Step 25c's
        shared helper and `cli.py translate` already use."""
        d1 = isolated_db.create_drama(title_en="Novel Drama", status="aligned",
                                      content_mode="novel_narration",
                                      translation_engine="test_offline")
        isolated_db.save_lines(d1, [Line(idx=0, start=0, end=1, zh="句0")])

        calls = []
        real_start_job = background_jobs.start_job

        def spy(job_id, target, *args, **kwargs):
            if job_id == f"translate_{d1}":
                calls.append(args)
            return real_start_job(job_id, target, *args, **kwargs)
        monkeypatch.setattr(background_jobs, "start_job", spy)

        self._run([d1], monkeypatch)
        assert calls, "the per-drama translate job was never started"
        style_preset = calls[0][12]
        assert style_preset == "novel"

    def test_uses_the_settings_configured_model_not_the_engines_bare_default(
            self, isolated_db, monkeypatch):
        """Step 25d item 1: this used to always pass model=None, so every
        drama got whichever model get_engine() defaults to, ignoring
        whatever model Settings has configured for that engine."""
        d1 = self._drama(isolated_db, n=1, engine="claude")
        seen = {}
        real_get_engine = translate_engines.get_engine

        def spy(engine_name, api_key, model=None, **kw):
            seen["model"] = model
            return real_get_engine(engine_name, api_key, model, **kw)
        monkeypatch.setattr(translate_engines, "get_engine", spy)

        self._run([d1], monkeypatch, api_keys={"claude": "sk-ant-fake"},
                 models={"claude": "claude-opus-4"})
        assert seen["model"] == "claude-opus-4"

    def test_an_ollama_drama_is_gpu_touching(self, isolated_db, monkeypatch):
        """Step 25d item 1: this never set gpu_touching=True for an Ollama
        engine, risking it running outside Step 5c's GPU-job guard."""
        d1 = self._drama(isolated_db, n=1, engine="ollama")
        self._run([d1], monkeypatch)
        job = background_jobs.get_status(f"translate_{d1}")
        assert job is not None and job.get("gpu_touching") is True
        background_jobs.clear_job(f"translate_{d1}")

    def test_combined_progress_message_names_the_current_drama(self, isolated_db, monkeypatch):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        d1 = self._drama(isolated_db, n=1)
        job_id = "test_bulk_series_progress"
        background_jobs.clear_job(job_id)
        messages = []
        real_update = background_jobs.update_progress

        def spy(jid, frac, message=""):
            if jid == job_id:
                messages.append(message)
            real_update(jid, frac, message)
        monkeypatch.setattr(background_jobs, "update_progress", spy)
        _fast_poll(lt, monkeypatch)
        lt.run_bulk_series_translate_job(job_id, [d1], {})
        assert any("Drama 1" in m and "1/1" in m for m in messages)


class TestListDramasBySeries:
    """Step 22: the series-view query -- every drama for a given
    series_id, regardless of media_type."""

    def test_returns_every_media_type_in_the_series(self, isolated_db):
        sid = isolated_db.get_or_create_series("A Series")
        d1 = isolated_db.create_drama(title_en="The Show", series_id=sid, media_type="video_drama")
        d2 = isolated_db.create_drama(title_en="The Manga", series_id=sid, media_type="manga")
        d3 = isolated_db.create_drama(title_en="The Novel", series_id=sid, media_type="novel")
        isolated_db.create_drama(title_en="Unrelated", media_type="novel")  # no series -- excluded

        result = isolated_db.list_dramas_by_series(sid)
        assert {d["id"] for d in result} == {d1, d2, d3}

    def test_a_different_series_id_is_not_included(self, isolated_db):
        sid_a = isolated_db.get_or_create_series("Series A")
        sid_b = isolated_db.get_or_create_series("Series B")
        isolated_db.create_drama(title_en="In B", series_id=sid_b)
        assert isolated_db.list_dramas_by_series(sid_a) == []

    def test_empty_for_a_series_with_no_dramas(self, isolated_db):
        sid = isolated_db.get_or_create_series("Empty Series")
        assert isolated_db.list_dramas_by_series(sid) == []

    def test_falls_back_to_created_at_when_no_episode_numbers_set(self, isolated_db):
        """Step 74: an existing series that's never used episode_number
        keeps its old newest-first order -- this must never silently
        reorder it just because the column now exists."""
        sid = isolated_db.get_or_create_series("Unnumbered Series")
        d1 = isolated_db.create_drama(title_en="First created", series_id=sid)
        d2 = isolated_db.create_drama(title_en="Second created", series_id=sid)
        result = isolated_db.list_dramas_by_series(sid)
        assert [d["id"] for d in result] == [d2, d1]  # created_at DESC, unchanged

    def test_orders_by_episode_number_once_any_drama_in_series_has_one(self, isolated_db):
        """Step 74: once episode_number is in use anywhere in the series,
        it becomes the real ordering signal -- ascending (reading order),
        not the created_at newest-first default."""
        sid = isolated_db.get_or_create_series("Numbered Series")
        d3 = isolated_db.create_drama(title_en="Ep 3", series_id=sid, episode_number=3)
        d1 = isolated_db.create_drama(title_en="Ep 1", series_id=sid, episode_number=1)
        d2 = isolated_db.create_drama(title_en="Ep 2", series_id=sid, episode_number=2)
        result = isolated_db.list_dramas_by_series(sid)
        assert [d["id"] for d in result] == [d1, d2, d3]

    def test_unnumbered_sibling_sorts_after_numbered_ones_without_crashing(self, isolated_db):
        """Step 74's own exit condition: a partially-numbered series
        doesn't crash or get its unnumbered member silently interleaved
        into the numbered ordering."""
        sid = isolated_db.get_or_create_series("Partially Numbered")
        d1 = isolated_db.create_drama(title_en="Ep 1", series_id=sid, episode_number=1)
        d_unset = isolated_db.create_drama(title_en="Not numbered yet", series_id=sid)
        d2 = isolated_db.create_drama(title_en="Ep 2", series_id=sid, episode_number=2)
        result = isolated_db.list_dramas_by_series(sid)
        assert [d["id"] for d in result] == [d1, d2, d_unset]


class TestPreviousEpisodeSummary:
    """Step 74: _DRAMA_SELECT's previous_episode_summary -- the
    immediately preceding episode's stored running summary, resolved via
    dramas.episode_number, surfaced on every get_drama()/list_dramas()
    row so drama_meta always carries it forward with no extra lookup."""

    def test_reaches_the_next_episode_by_episode_number(self, isolated_db):
        sid = isolated_db.get_or_create_series("Continuity Series")
        isolated_db.create_drama(title_en="Ep 1", series_id=sid, episode_number=1,
                                 episode_summary="Xiaoling found the letter.")
        d2 = isolated_db.create_drama(title_en="Ep 2", series_id=sid, episode_number=2)
        assert isolated_db.get_drama(d2)["previous_episode_summary"] == "Xiaoling found the letter."

    def test_only_the_immediately_preceding_episode_reaches_forward(self, isolated_db):
        """Not every earlier episode's summary -- only the one directly
        before this one, by episode_number."""
        sid = isolated_db.get_or_create_series("Continuity Series")
        isolated_db.create_drama(title_en="Ep 1", series_id=sid, episode_number=1,
                                 episode_summary="Episode 1 events.")
        isolated_db.create_drama(title_en="Ep 2", series_id=sid, episode_number=2,
                                 episode_summary="Episode 2 events.")
        d3 = isolated_db.create_drama(title_en="Ep 3", series_id=sid, episode_number=3)
        assert isolated_db.get_drama(d3)["previous_episode_summary"] == "Episode 2 events."

    def test_empty_with_no_episode_number_set(self, isolated_db):
        sid = isolated_db.get_or_create_series("No Ordering Series")
        isolated_db.create_drama(title_en="Ep A", series_id=sid,
                                 episode_summary="Should not reach forward.")
        d2 = isolated_db.create_drama(title_en="Ep B", series_id=sid)
        assert not isolated_db.get_drama(d2)["previous_episode_summary"]

    def test_empty_for_the_first_episode(self, isolated_db):
        sid = isolated_db.get_or_create_series("Continuity Series")
        d1 = isolated_db.create_drama(title_en="Ep 1", series_id=sid, episode_number=1)
        assert not isolated_db.get_drama(d1)["previous_episode_summary"]

    def test_editing_the_stored_summary_changes_what_is_fed_forward(self, isolated_db):
        """Same 'suggestion, editable, not silently auto-applied' pattern
        as glossary/translation memory -- fixing a bad auto-summary must
        actually change what the next episode's translation prompt sees."""
        sid = isolated_db.get_or_create_series("Editable Series")
        d1 = isolated_db.create_drama(title_en="Ep 1", series_id=sid, episode_number=1,
                                      episode_summary="Wrong auto-summary.")
        d2 = isolated_db.create_drama(title_en="Ep 2", series_id=sid, episode_number=2)
        assert isolated_db.get_drama(d2)["previous_episode_summary"] == "Wrong auto-summary."

        isolated_db.update_drama(d1, episode_summary="Corrected summary.")
        assert isolated_db.get_drama(d2)["previous_episode_summary"] == "Corrected summary."


class TestRestoreFromBackupValidatesBeforeDestroying:
    """Step 25k: "Restore from backup" used to shutil.rmtree() the whole
    library BEFORE checking whether the uploaded zip was even a real
    backup, so a corrupted download or the wrong file entirely wiped the
    existing library and then showed an error. restore_library_backup()
    must validate (opens as a zip, contains library.db, no corrupt
    member) and extract to a staging directory first, only touching the
    real library_dir once that's confirmed good."""

    def _make_zip_bytes(self, members: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def _db_bytes(self) -> bytes:
        """A minimal real SQLite library.db (restore now opens it and
        requires a dramas table)."""
        import sqlite3
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE dramas (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            with open(path, "rb") as f:
                return f.read()
        finally:
            os.remove(path)

    def test_not_a_zip_at_all_leaves_existing_library_intact(self, tmp_path_str):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)

        marker = os.path.join(tmp_path_str, "dramas", "existing_drama.txt")
        os.makedirs(os.path.dirname(marker))
        with open(marker, "w") as f:
            f.write("original data")

        with pytest.raises(Exception):
            lt.restore_library_backup(b"this is not a zip file at all", tmp_path_str)

        assert os.path.exists(marker)
        with open(marker) as f:
            assert f.read() == "original data"

    def test_valid_zip_missing_library_db_leaves_existing_library_intact(self, tmp_path_str):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)

        marker = os.path.join(tmp_path_str, "dramas", "existing_drama.txt")
        os.makedirs(os.path.dirname(marker))
        with open(marker, "w") as f:
            f.write("original data")

        bad_zip = self._make_zip_bytes({"some_random_file.txt": "not a real backup"})
        with pytest.raises(ValueError, match="doesn't look like"):
            lt.restore_library_backup(bad_zip, tmp_path_str)

        assert os.path.exists(marker)
        with open(marker) as f:
            assert f.read() == "original data"

    def test_valid_backup_zip_still_restores_correctly(self, tmp_path_str):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)

        marker = os.path.join(tmp_path_str, "dramas", "old_drama.txt")
        os.makedirs(os.path.dirname(marker))
        with open(marker, "w") as f:
            f.write("stale data that should be replaced")

        good_zip = self._make_zip_bytes({
            "library.db": self._db_bytes(),
            "dramas/new_drama.txt": "restored data",
        })
        lt.restore_library_backup(good_zip, tmp_path_str)

        assert os.path.exists(os.path.join(tmp_path_str, "library.db"))
        assert os.path.exists(os.path.join(tmp_path_str, "dramas", "new_drama.txt"))
        assert not os.path.exists(marker)

    def test_restoring_into_a_library_dir_that_does_not_exist_yet_works(self, tmp_path_str):
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)

        library_dir = os.path.join(tmp_path_str, "brand_new_library")
        good_zip = self._make_zip_bytes({"library.db": self._db_bytes()})
        lt.restore_library_backup(good_zip, library_dir)

        assert os.path.exists(os.path.join(library_dir, "library.db"))

    def test_backup_over_total_size_limit_is_rejected_before_extraction(self, tmp_path_str, monkeypatch):
        """Step 52: a zip whose members would expand past the total-size
        limit must be rejected before extractall() ever runs, not partway
        through with a full disk -- and the existing library survives."""
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        from services import workspace_job_service

        monkeypatch.setattr(workspace_job_service, "_MAX_RESTORE_TOTAL_BYTES", 10)

        marker = os.path.join(tmp_path_str, "dramas", "existing_drama.txt")
        os.makedirs(os.path.dirname(marker))
        with open(marker, "w") as f:
            f.write("original data")

        oversized_zip = self._make_zip_bytes({"library.db": "x" * 100})
        with pytest.raises(ValueError, match="expand to more than"):
            lt.restore_library_backup(oversized_zip, tmp_path_str)

        assert os.path.exists(marker)
        with open(marker) as f:
            assert f.read() == "original data"

    def test_backup_with_oversized_single_member_is_rejected(self, tmp_path_str, monkeypatch):
        """Step 52: the per-file limit catches one huge member even if the
        total-size limit wouldn't (e.g. it's the only file in the zip)."""
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        from services import workspace_job_service

        monkeypatch.setattr(workspace_job_service, "_MAX_RESTORE_MEMBER_BYTES", 10)

        oversized_zip = self._make_zip_bytes({"library.db": "x" * 100})
        with pytest.raises(ValueError, match="per-file limit"):
            lt.restore_library_backup(oversized_zip, tmp_path_str)

    def test_backup_over_member_count_limit_is_rejected(self, tmp_path_str, monkeypatch):
        """Step 52: a zip with too many members is rejected up front,
        without ever calling extractall()."""
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)
        from services import workspace_job_service

        monkeypatch.setattr(workspace_job_service, "_MAX_RESTORE_MEMBERS", 1)

        many_files_zip = self._make_zip_bytes({
            "library.db": self._db_bytes(),
            "dramas/extra_file.txt": "one file too many",
        })
        with pytest.raises(ValueError, match="file limit"):
            lt.restore_library_backup(many_files_zip, tmp_path_str)

    def test_backup_within_limits_still_restores_normally(self, tmp_path_str):
        """Step 52's new checks shouldn't reject an ordinary, legitimate
        backup -- the limits are generous by design."""
        import services.workspace_job_service as lt  # was tabs.library_tab (a re-export)

        good_zip = self._make_zip_bytes({
            "library.db": self._db_bytes(),
            "dramas/new_drama.txt": "restored data",
        })
        lt.restore_library_backup(good_zip, tmp_path_str)

        assert os.path.exists(os.path.join(tmp_path_str, "library.db"))
        assert os.path.exists(os.path.join(tmp_path_str, "dramas", "new_drama.txt"))


class TestOrganizationalTags:
    """Step 24 item 3: Favorite / On Hold / Plan to Translate, stored in
    custom_tags and filterable in Library -- a personal layer that never
    touches dramas.status (pipeline progress). The "Add selected to list"
    button itself isn't driven here for the same reason as
    TestBulkSeriesTranslateStatusUI: data_editor selection isn't settable
    in AppTest; it just calls set_custom_tag per selected drama."""

    def test_set_and_clear_leaves_other_tags_and_status_alone(self, isolated_db):
        did = isolated_db.create_drama(title_en="A", custom_tags="slow burn", status="translated")
        isolated_db.set_custom_tag(did, "Favorite", True)
        d = isolated_db.get_drama(did)
        assert d["custom_tags"] == "slow burn, Favorite"
        assert d["status"] == "translated"
        isolated_db.set_custom_tag(did, "Favorite", False)
        d = isolated_db.get_drama(did)
        assert d["custom_tags"] == "slow burn"
        assert d["status"] == "translated"

    def test_setting_twice_or_hand_typed_lowercase_doesnt_duplicate(self, isolated_db):
        did = isolated_db.create_drama(title_en="A", custom_tags="favorite")
        assert isolated_db.has_custom_tag(isolated_db.get_drama(did), "Favorite")
        isolated_db.set_custom_tag(did, "Favorite", True)
        isolated_db.set_custom_tag(did, "Favorite", True)
        assert isolated_db.get_drama(did)["custom_tags"] == "Favorite"

    def test_has_custom_tag_needs_the_whole_tag(self, isolated_db):
        assert not isolated_db.has_custom_tag({"custom_tags": "Hold"}, "On Hold")
        assert not isolated_db.has_custom_tag({"custom_tags": None}, "Favorite")

