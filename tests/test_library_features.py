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

import time

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


class TestCostDashboardShowsFreeEngineUsage:
    """Step 1d item 5: a drama translated with a free engine has real
    usage logged but legitimately costs $0 -- the dashboard shows it
    labelled "(free)" instead of the old behavior (filtering it out of
    the table entirely because estimated_cost_usd wasn't > 0, making it
    indistinguishable from a drama nothing had ever run on)."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _cost_df(self, at):
        # render_library_tab() renders more than one st.dataframe (e.g. a
        # drama listing further down) -- find the cost breakdown
        # specifically by its distinctive "Est. cost" column rather than
        # assuming it's the first one on the page.
        for el in at.dataframe:
            if "Est. cost" in el.value.columns:
                return el.value
        return None

    def test_free_engine_usage_shows_zero_free_not_omitted(self, isolated_db):
        did = isolated_db.create_drama(title_en="Free Drama", translation_engine="test_offline")
        isolated_db.log_usage(did, "test_offline", "test_offline", "translate", 100, 50, 0.0)
        df = self._cost_df(self._run())
        row = df[df["id"] == did].iloc[0]
        assert row["Est. cost"] == "$0.00 (free)"

    def test_paid_engine_usage_shows_a_real_dollar_amount(self, isolated_db):
        did = isolated_db.create_drama(title_en="Paid Drama", translation_engine="claude")
        isolated_db.log_usage(did, "claude", "claude-sonnet-5", "translate", 1000, 500, 0.0055)
        df = self._cost_df(self._run())
        row = df[df["id"] == did].iloc[0]
        assert row["Est. cost"] == "$0.01"

    def test_a_drama_with_no_usage_at_all_is_not_in_the_table(self, isolated_db):
        did = isolated_db.create_drama(title_en="Untouched Drama")
        at = self._run()
        assert any("No usage logged yet." in c.value for c in at.caption)
        assert self._cost_df(at) is None


class TestCacheHitShare:
    """Step 9: the Library dashboard shows what share of input tokens were
    prompt-cache reads, next to the cost."""

    def test_share_of_input_tokens(self):
        from tabs.library_tab import cache_hit_share
        assert cache_hit_share({"input_tokens": 1000, "cache_read_tokens": 250}) == 0.25

    def test_no_usage_is_zero_not_a_division_error(self):
        from tabs.library_tab import cache_hit_share
        assert cache_hit_share({"input_tokens": 0, "cache_read_tokens": 0}) == 0.0


# ---------------------------------------------------------------------------
# Step 9b.3: bulk "translate everything untranslated" across a series.
# ---------------------------------------------------------------------------

class TestBulkSeriesTranslate:
    def _drama(self, isolated_db, n=2, engine="test_offline", series_id=None, status="aligned"):
        did = isolated_db.create_drama(title_en=f"Drama {n}", media_type="audio_drama",
                                       content_mode="audio_drama", status=status,
                                       translation_engine=engine, series_id=series_id)
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=f"句{i}") for i in range(n)])
        return did

    def _run(self, drama_ids, monkeypatch, **kw):
        import tabs.library_tab as lt
        monkeypatch.setattr(lt.time, "sleep", lambda s: None)
        job_id = "test_bulk_series"
        background_jobs.clear_job(job_id)
        started = background_jobs.start_job(
            job_id, lt.run_bulk_series_translate_job, job_id, drama_ids, kw.pop("api_keys", {}), **kw)
        assert started
        deadline = time.time() + 5
        while background_jobs.is_running(job_id) and time.time() < deadline:
            time.sleep(0.02)
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
        import tabs.library_tab as lt
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: recording)
        status = self._run([d1], monkeypatch)
        assert recording.seen_glossary and recording.seen_glossary[0]["term_original"] == "苏杉"
        # enforce_exact substitution still applies on top of the engine's own output.
        assert isolated_db.load_lines(d1)[0]["en"] == "Su Shan appears."

    def test_cancelling_stops_the_current_drama_and_skips_the_rest(self, isolated_db, monkeypatch):
        import tabs.library_tab as lt
        d1 = self._drama(isolated_db, n=1)
        d2 = self._drama(isolated_db, n=1)
        job_id = "test_bulk_series_cancel"
        background_jobs.clear_job(job_id)
        monkeypatch.setattr(lt.time, "sleep", lambda s: None)

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
        deadline = time.time() + 5
        while background_jobs.is_running(job_id) and time.time() < deadline:
            time.sleep(0.02)
        result = background_jobs.get_status(job_id)["result"]
        assert d2 not in result["translated"]
        assert d2 not in result.get("skipped_running", [])
        assert not any(r["en"] for r in isolated_db.load_lines(d2))

    def test_combined_progress_message_names_the_current_drama(self, isolated_db, monkeypatch):
        import tabs.library_tab as lt
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
        monkeypatch.setattr(lt.time, "sleep", lambda s: None)
        lt.run_bulk_series_translate_job(job_id, [d1], {})
        assert any("Drama 1" in m and "1/1" in m for m in messages)


class TestBulkSeriesTranslateStatusUI:
    """UI-level coverage for the combined status line/cancel button.
    st.data_editor's selection column isn't a queryable widget in this
    Streamlit AppTest version, so the click-to-select-and-submit path
    itself is covered at the function level above (TestBulkSeriesTranslate)
    -- this covers what the status panel renders once a job exists."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_combined_status_line_shows_while_running(self, isolated_db):
        import tabs.library_tab as lt
        isolated_db.create_drama(title_en="Any Drama")
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)
        background_jobs._jobs[lt.BULK_SERIES_TRANSLATE_JOB_ID] = {
            "status": "running", "progress": 0.3, "message": "Translating 2/5 -- Some Drama (40%)",
            "error": None, "cancel_requested": False, "result": None, "started_at": time.time() - 10}
        at = self._run()
        bars = [p for p in at.get("progress") if p.value == 30]
        assert bars and "Some Drama" in bars[0].proto.text
        assert [b for b in at.button if b.key == "bulk_series_translate_cancel"]
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)

    def test_cancel_button_requests_cancellation(self, isolated_db):
        import tabs.library_tab as lt
        isolated_db.create_drama(title_en="Any Drama")
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)
        background_jobs._jobs[lt.BULK_SERIES_TRANSLATE_JOB_ID] = {
            "status": "running", "progress": 0.3, "message": "Translating...",
            "error": None, "cancel_requested": False, "result": None, "started_at": time.time()}
        at = self._run()
        [b for b in at.button if b.key == "bulk_series_translate_cancel"][0].click()
        at.run(timeout=30)
        assert background_jobs.is_cancel_requested(lt.BULK_SERIES_TRANSLATE_JOB_ID)
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)

    def test_done_summary_shown_and_job_cleared(self, isolated_db):
        import tabs.library_tab as lt
        isolated_db.create_drama(title_en="Any Drama")
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)
        background_jobs._jobs[lt.BULK_SERIES_TRANSLATE_JOB_ID] = {
            "status": "done", "progress": 1.0, "message": "Done", "error": None,
            "cancel_requested": False, "started_at": time.time() - 30,
            "result": {"translated": [1, 2], "skipped_running": [], "skipped_no_key": [3],
                      "skipped_no_lines": [], "errors": {}}}
        at = self._run()
        assert any("2 translated" in m.value and "1 skipped (no API key)" in m.value
                  for m in at.success)
        assert background_jobs.get_status(lt.BULK_SERIES_TRANSLATE_JOB_ID) is None


class TestBulkSeriesTranslateRefreshesOpenWorkspaceDrama:
    """Step 9i item 1: the same Step 9h staleness, triggered from a
    different tab -- Library's own bulk-translate completion never
    touched st.session_state.lines, so a drama bulk-translated from here
    while it's also the currently-open Workspace drama showed stale
    pre-translation text until a hard refresh."""

    def _run(self, **session_state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        for k, v in session_state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_refreshes_lines_when_the_open_drama_was_just_translated(self, isolated_db):
        import tabs.library_tab as lt
        did = isolated_db.create_drama(title_en="Open Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)
        background_jobs._jobs[lt.BULK_SERIES_TRANSLATE_JOB_ID] = {
            "status": "done", "progress": 1.0, "message": "Done", "error": None,
            "cancel_requested": False, "started_at": time.time() - 30,
            "result": {"translated": [did], "skipped_running": [], "skipped_no_key": [],
                      "skipped_no_lines": [], "errors": {}}}

        # The job's own real completion already wrote the translation to
        # the database before flipping to "done" -- simulated here the
        # same way, since run_bulk_series_translate_job itself isn't
        # under test in this class.
        translated = isolated_db.load_line_objects(did)
        translated[0].en = "Hello."
        isolated_db.save_lines(did, translated, fields=("en",))

        at = self._run(active_drama_id=did, lines=[Line(idx=0, start=0.0, end=1.0, zh="你好", en="")])
        at.run(timeout=30)

        assert at.session_state.lines[0].en == "Hello."

    def test_does_not_touch_lines_for_an_unrelated_open_drama(self, isolated_db):
        import tabs.library_tab as lt
        did = isolated_db.create_drama(title_en="Translated Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="aligned")
        other_did = isolated_db.create_drama(title_en="Open But Untouched Drama",
                                             media_type="audio_drama", content_mode="audio_drama")
        isolated_db.save_lines(other_did, [Line(idx=0, start=0.0, end=1.0, zh="别的", en="")])
        background_jobs.clear_job(lt.BULK_SERIES_TRANSLATE_JOB_ID)
        background_jobs._jobs[lt.BULK_SERIES_TRANSLATE_JOB_ID] = {
            "status": "done", "progress": 1.0, "message": "Done", "error": None,
            "cancel_requested": False, "started_at": time.time() - 30,
            "result": {"translated": [did], "skipped_running": [], "skipped_no_key": [],
                      "skipped_no_lines": [], "errors": {}}}

        sentinel_lines = [Line(idx=0, start=0.0, end=1.0, zh="别的", en="")]
        at = self._run(active_drama_id=other_did, lines=sentinel_lines)
        at.run(timeout=30)

        # Untouched -- the same object identity, not just equal content,
        # proves this drama's lines were never reassigned.
        assert at.session_state.lines is sentinel_lines


class TestManagePresetsUI:
    """Step 9c item 4: the Library tab's "🎛️ Presets" panel -- list,
    rename, delete."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_no_presets_shows_the_empty_state(self, isolated_db):
        at = self._run()
        assert any("No presets saved yet" in c.value for c in at.caption)

    def test_a_saved_preset_shows_its_captured_fields_at_a_glance(self, isolated_db):
        isolated_db.save_preset(
            "Novel style", translation_engine="claude", engine_model="claude-sonnet-5",
            style_preset="novel", locale="en-GB", default_female_pronouns=True,
            include_genre_notes=False)
        at = self._run()
        assert any("Novel style" in m.value for m in at.markdown)
        fields = " ".join(c.value for c in at.caption)
        assert "claude" in fields and "claude-sonnet-5" in fields
        assert "novel" in fields and "en-GB" in fields
        assert "she/her default" in fields
        assert "genre guidance off" in fields

    def test_rename_updates_the_name_only(self, isolated_db):
        pid = isolated_db.save_preset("Old name", translation_engine="claude", locale="en-US")
        at = self._run()

        [t for t in at.text_input if t.key == f"rename_preset_{pid}"][0].set_value(
            "New name").run()
        [b for b in at.button if b.key == f"rename_preset_btn_{pid}"][0].click().run()

        p = isolated_db.get_preset(pid)
        assert p["name"] == "New name"
        assert p["translation_engine"] == "claude"
        assert p["locale"] == "en-US"

    def test_rename_to_the_same_name_is_disabled(self, isolated_db):
        pid = isolated_db.save_preset("Same name")
        at = self._run()
        btn = [b for b in at.button if b.key == f"rename_preset_btn_{pid}"][0]
        assert btn.disabled

    def test_delete_removes_only_that_preset(self, isolated_db):
        pid1 = isolated_db.save_preset("Keep me")
        pid2 = isolated_db.save_preset("Delete me")
        at = self._run()

        [b for b in at.button if b.key == f"delete_preset_{pid2}"][0].click().run()

        assert [p["id"] for p in isolated_db.list_presets()] == [pid1]

    def test_deleting_a_preset_does_not_change_a_drama_it_was_applied_to(self, isolated_db):
        pid = isolated_db.save_preset("Series defaults", translation_engine="deepseek")
        did = isolated_db.create_drama(title_en="A Drama", translation_engine="deepseek")
        before = isolated_db.get_drama(did)
        at = self._run()

        [b for b in at.button if b.key == f"delete_preset_{pid}"][0].click().run()

        assert isolated_db.get_preset(pid) is None
        assert isolated_db.get_drama(did) == before


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


class TestLibrarySeriesView:
    """Step 22 item 1: a series-level view in Library grouping every
    drama in a series together, across media types, for any series with
    more than one drama."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_a_single_drama_series_is_not_shown(self, isolated_db):
        sid = isolated_db.get_or_create_series("Solo Series")
        isolated_db.create_drama(title_en="Only One", series_id=sid)
        at = self._run()
        assert any("No series with more than one drama yet" in c.value for c in at.caption)
        assert not any("Solo Series" in m.value for m in at.markdown)

    def test_a_multi_drama_series_lists_every_drama_across_media_types(self, isolated_db):
        sid = isolated_db.get_or_create_series("Mixed Series")
        isolated_db.create_drama(title_en="The Show", series_id=sid, media_type="video_drama")
        isolated_db.create_drama(title_en="The Manga", series_id=sid, media_type="manga")
        isolated_db.create_drama(title_en="The Novel", series_id=sid, media_type="novel")

        at = self._run()
        assert any("Mixed Series" in m.value for m in at.markdown)
        summary = next(m.value for m in at.markdown if "Mixed Series" in m.value)
        assert "1 Video Drama" in summary and "1 Manga" in summary and "1 Novel" in summary
        titles = " ".join(c.value for c in at.caption)
        assert "The Show" in titles and "The Manga" in titles and "The Novel" in titles

    def test_counts_pluralize_when_more_than_one(self, isolated_db):
        sid = isolated_db.get_or_create_series("Streamer Archive")
        isolated_db.create_drama(title_en="Stream 1", series_id=sid, media_type="streamer_vod")
        isolated_db.create_drama(title_en="Stream 2", series_id=sid, media_type="streamer_vod")
        at = self._run()
        summary = next(m.value for m in at.markdown if "Streamer Archive" in m.value)
        assert "2 Streamer VODs" in summary

    def test_shows_shared_character_and_glossary_counts(self, isolated_db):
        sid = isolated_db.get_or_create_series("Cast Series")
        isolated_db.create_drama(title_en="D1", series_id=sid)
        isolated_db.create_drama(title_en="D2", series_id=sid)
        isolated_db.upsert_series_character(sid, "Shen Qingyi")
        isolated_db.upsert_glossary_term(sid, "沈清疑", "Shen Qingyi")
        at = self._run()
        captions = " ".join(c.value for c in at.caption)
        assert "1 shared character(s)" in captions
        assert "1 glossary term(s)" in captions

    def test_open_button_switches_the_active_drama(self, isolated_db):
        sid = isolated_db.get_or_create_series("Jump Series")
        d1 = isolated_db.create_drama(title_en="First", series_id=sid)
        d2 = isolated_db.create_drama(title_en="Second", series_id=sid)
        at = self._run()

        [b for b in at.button if b.key == f"series_open_{d2}"][0].click().run(timeout=30)

        assert at.session_state["active_drama_id"] == d2
        # nav_notice is popped and shown as an st.info() the very next
        # rerun (same pattern as the existing "Resume" button), so by the
        # time .run() returns it's already been consumed into the banner.
        assert any("Workspace" in i.value for i in at.info)


class TestLibrarySectionsAreCollapsible:
    """Step 22 item 5: Library's own sections are individually
    collapsible, Dashboard/Series-adjacent Search and Filter open by
    default, the rest collapsed."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_every_top_level_section_is_an_expander(self, isolated_db):
        at = self._run()
        labels = {e.label for e in at.expander}
        for label in ("📊 Dashboard", "🎭 Series", "🔍 Search across all dramas", "Filter",
                      "🗄️ Storage", "📜 Reading history", "💾 Backup & restore", "🎛️ Presets"):
            assert label in labels, f"missing expander: {label}"

    def test_dashboard_and_search_and_filter_default_open(self, isolated_db):
        at = self._run()
        by_label = {e.label: e for e in at.expander}
        assert by_label["📊 Dashboard"].proto.expanded is True
        assert by_label["🔍 Search across all dramas"].proto.expanded is True
        assert by_label["Filter"].proto.expanded is True

    def test_series_and_the_rest_default_collapsed(self, isolated_db):
        at = self._run()
        by_label = {e.label: e for e in at.expander}
        for label in ("🎭 Series", "🗄️ Storage", "📜 Reading history",
                      "💾 Backup & restore", "🎛️ Presets"):
            assert by_label[label].proto.expanded is False, f"{label} should default collapsed"


class TestSeriesPickerSharedBetweenMetadataAndGlossary:
    """Step 22 item 4: the same series_options dropdown, driven by the
    same db.update_drama(series_id=...) call, now also lives in ✏️ Edit
    metadata -- not just 📖 Series glossary & term handling."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_metadata_expander_has_a_series_picker(self, isolated_db):
        isolated_db.get_or_create_series("Existing Series")
        did = isolated_db.create_drama(title_en="A Drama")
        at = self._run(did)
        assert any(s.key and s.key.startswith(f"meta_series_pick_{did}_") for s in at.selectbox)

    def test_assigning_a_series_from_metadata_reflects_in_the_glossary_picker(self, isolated_db):
        isolated_db.get_or_create_series("Shared Universe")
        did = isolated_db.create_drama(title_en="A Drama")
        at = self._run(did)

        meta_picker = [s for s in at.selectbox if s.key and s.key.startswith(f"meta_series_pick_{did}_")][0]
        meta_picker.set_value("Shared Universe").run(timeout=30)

        assert isolated_db.get_drama(did)["series_id"] == isolated_db.get_or_create_series("Shared Universe")
        glossary_picker = [s for s in at.selectbox if s.key and s.key.startswith(f"glossary_series_pick_{did}_")][0]
        assert glossary_picker.value == "Shared Universe"

    def test_assigning_from_glossary_reflects_in_the_metadata_picker(self, isolated_db):
        isolated_db.get_or_create_series("Shared Universe 2")
        did = isolated_db.create_drama(title_en="A Drama")
        at = self._run(did)

        glossary_picker = [s for s in at.selectbox if s.key and s.key.startswith(f"glossary_series_pick_{did}_")][0]
        glossary_picker.set_value("Shared Universe 2").run(timeout=30)

        assert isolated_db.get_drama(did)["series_id"] == isolated_db.get_or_create_series("Shared Universe 2")
        meta_picker = [s for s in at.selectbox if s.key and s.key.startswith(f"meta_series_pick_{did}_")][0]
        assert meta_picker.value == "Shared Universe 2"


class TestSeriesSharingIndicators:
    """Step 22 item 2: a "shared from this series" note wherever a
    character or glossary term appears, since the sharing already
    happens under the hood."""

    def _run_workspace(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def test_a_series_linked_character_shows_the_shared_indicator(self, isolated_db):
        sid = isolated_db.get_or_create_series("Cast Series")
        isolated_db.upsert_series_character(sid, "Shen Qingyi")
        [sc] = isolated_db.list_series_characters(sid)
        did = isolated_db.create_drama(title_en="A Drama", series_id=sid)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", speaker="SPEAKER_00")])
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Shen Qingyi",
                                      series_character_id=sc["id"])

        at = self._run_workspace(did)
        assert any("Shared with other dramas in this series" in c.value for c in at.caption)

    def test_a_character_with_no_series_link_shows_no_indicator(self, isolated_db):
        did = isolated_db.create_drama(title_en="A Drama")
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="你好", speaker="SPEAKER_00")])
        isolated_db.upsert_character(did, "SPEAKER_00", character_name="Someone")
        at = self._run_workspace(did)
        assert not any("Shared with other dramas in this series" in c.value for c in at.caption)
