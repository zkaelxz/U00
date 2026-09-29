"""Streamlit widget, AppTest and tab-source tests split out of tests/test_library_features.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_library_features.py."""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
from core import Line


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


class TestAnimeInLibraryTypeFilter:
    """Step 22b: "anime" filterable in the Library, same as manhwa/manga/
    manhua already are."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_anime_is_a_type_filter_option(self, isolated_db):
        at = self._run()
        type_filter = [s for s in at.selectbox if s.label == "Type"][0]
        assert "Anime" in type_filter.options

    def test_filtering_by_anime_shows_only_anime_dramas(self, isolated_db):
        isolated_db.create_drama(title_en="An Anime", media_type="anime")
        isolated_db.create_drama(title_en="A Novel", media_type="novel")
        at = self._run()
        [s for s in at.selectbox if s.label == "Type"][0].select("Anime").run()
        assert any(c.value == "1 drama(s)" for c in at.caption)


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

        p = next(p for p in isolated_db.list_presets() if p["id"] == pid)
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

        at.checkbox(key=f"confirm_delete_preset_{pid2}").set_value(True).run(timeout=30)
        [b for b in at.button if b.key == f"delete_preset_{pid2}"][0].click().run()

        assert [p["id"] for p in isolated_db.list_presets()] == [pid1]

    def test_deleting_a_preset_does_not_change_a_drama_it_was_applied_to(self, isolated_db):
        pid = isolated_db.save_preset("Series defaults", translation_engine="deepseek")
        did = isolated_db.create_drama(title_en="A Drama", translation_engine="deepseek")
        before = isolated_db.get_drama(did)
        at = self._run()

        at.checkbox(key=f"confirm_delete_preset_{pid}").set_value(True).run(timeout=30)
        [b for b in at.button if b.key == f"delete_preset_{pid}"][0].click().run()

        assert not any(p["id"] == pid for p in isolated_db.list_presets())
        assert isolated_db.get_drama(did) == before

    def test_delete_button_disabled_until_confirmed(self, isolated_db):
        """Step 71: this preset delete used to fire on a single click with
        no confirmation, unlike the rest of the app's own established
        pattern (Library's bulk-drama-delete checkbox, Step 25z's Workspace
        deletes)."""
        pid = isolated_db.save_preset("Delete me")
        at = self._run()

        assert [b for b in at.button if b.key == f"delete_preset_{pid}"][0].disabled
        at.checkbox(key=f"confirm_delete_preset_{pid}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"delete_preset_{pid}"][0].disabled


class TestVoiceBankDeleteConfirm:
    """Step 77: the voice bank entry delete button had the identical
    no-confirmation gap Step 71 fixed everywhere else (Step 71 correctly
    scoped itself to the spots it named, and this one was added by Step 26
    after Step 71's own roadmap review pass was already written)."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.library_tab as lt
            lt.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _make_clip(self, tmp_path_str, name="ref.wav"):
        p = os.path.join(tmp_path_str, name)
        with open(p, "wb") as f:
            f.write(b"fake wav bytes")
        return p

    def test_delete_button_disabled_until_confirmed(self, isolated_db, tmp_path_str):
        eid = isolated_db.save_voice_bank_entry("Delete me", self._make_clip(tmp_path_str))
        at = self._run()

        assert [b for b in at.button if b.key == f"delete_vb_{eid}"][0].disabled
        at.checkbox(key=f"confirm_delete_vb_{eid}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"delete_vb_{eid}"][0].disabled

    def test_delete_removes_only_that_entry_once_confirmed(self, isolated_db, tmp_path_str):
        eid1 = isolated_db.save_voice_bank_entry("Keep me", self._make_clip(tmp_path_str, "a.wav"))
        eid2 = isolated_db.save_voice_bank_entry("Delete me", self._make_clip(tmp_path_str, "b.wav"))
        at = self._run()

        at.checkbox(key=f"confirm_delete_vb_{eid2}").set_value(True).run(timeout=30)
        [b for b in at.button if b.key == f"delete_vb_{eid2}"][0].click().run()

        assert [e["id"] for e in isolated_db.list_voice_bank_entries()] == [eid1]


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


class TestLibraryEntryPointsClearStaleWidgetState:
    """Step 25j: Library's "▶️ Resume" and a series' "Open" button both set
    active_drama_id/lines directly instead of going through Workspace's own
    drama-picker, so neither one triggered its _clear_line_widget_state()
    call. By the time Workspace's picker later rendered, picked_id already
    matched the newly-set active_drama_id, so its own "did the selection
    change" guard never fired either -- a second, unpatched entry point to
    the Step 4j stale-widget bug class."""

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

    def test_resume_button_clears_stale_line_widget_state(self, isolated_db):
        did_a = isolated_db.create_drama(title_en="Drama A", media_type="audio_drama")
        did_b = isolated_db.create_drama(title_en="Drama B", media_type="audio_drama")
        isolated_db.save_progress(did_b, last_page=2, percent_complete=40.0)

        at = self._run(active_drama_id=did_a, lines=None, zh_0="Drama A's stale text")
        [b for b in at.button if b.key == f"resume_{did_b}"][0].click().run(timeout=30)

        assert at.session_state.active_drama_id == did_b
        assert at.session_state.lines is None
        assert "zh_0" not in at.session_state

    def test_series_open_button_clears_stale_line_widget_state(self, isolated_db):
        sid = isolated_db.get_or_create_series("Jump Series")
        did_a = isolated_db.create_drama(title_en="First", series_id=sid)
        did_b = isolated_db.create_drama(title_en="Second", series_id=sid)

        at = self._run(active_drama_id=did_a, lines=None, zh_0="Drama A's stale text")
        [b for b in at.button if b.key == f"series_open_{did_b}"][0].click().run(timeout=30)

        assert at.session_state.active_drama_id == did_b
        assert at.session_state.lines is None
        assert "zh_0" not in at.session_state


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
        for label in ("📊 Dashboard", "🎭 Series", "🔍 Search across all dramas", "📚 All dramas",
                      "🗄️ Storage", "📜 Reading history", "💾 Backup & restore", "🎛️ Presets"):
            assert label in labels, f"missing expander: {label}"

    def test_dashboard_and_search_and_filter_default_open(self, isolated_db):
        at = self._run()
        by_label = {e.label: e for e in at.expander}
        assert by_label["📊 Dashboard"].proto.expanded is True
        assert by_label["🔍 Search across all dramas"].proto.expanded is True
        assert by_label["📚 All dramas"].proto.expanded is True

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


class TestBulkExportClampsOverlappingCues:
    """Step 25d item 6: Workspace's own export already promises "never
    export an overlapping (invalid) cue" (subtitle_formats.clamp_overlaps)
    -- Library's "Export all" used to skip that clamp entirely."""

    def test_overlapping_cues_are_clamped_before_srt_generation(self, isolated_db, monkeypatch):
        from streamlit.testing.v1 import AppTest
        import tabs.library_tab as lt

        did = isolated_db.create_drama(title_en="Overlap Drama", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0.0, end=2.0, zh="a", en="Hello"),
            Line(idx=1, start=1.5, end=3.0, zh="b", en="World"),
        ])

        calls = []
        real_lines_to_srt = lt.lines_to_srt

        def spy(lines, *a, **k):
            calls.append(list(lines))
            return real_lines_to_srt(lines, *a, **k)
        monkeypatch.setattr(lt, "lines_to_srt", spy)

        def _render():
            import tabs.library_tab as lt2
            lt2.render_library_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        [btn] = [b for b in at.button if b.label.startswith("📦 Export all")]
        btn.click().run(timeout=30)

        assert calls, "lines_to_srt was never called"
        exported_lines = calls[0]
        # Clamped to the next line's start, not the original overlapping end.
        assert exported_lines[0].end == 1.5


class TestOrganizationalTags:
    """Step 24 item 3: Favorite / On Hold / Plan to Translate, stored in
    custom_tags and filterable in Library -- a personal layer that never
    touches dramas.status (pipeline progress). The "Add selected to list"
    button itself isn't driven here for the same reason as
    TestBulkSeriesTranslateStatusUI: data_editor selection isn't settable
    in AppTest; it just calls set_custom_tag per selected drama."""

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

    def _listed_ids(self, at):
        for el in at.dataframe:
            if "tags" in el.value.columns:
                return sorted(el.value["id"].tolist())
        return []

    def test_quick_filter_shows_only_that_list_with_real_status(self, isolated_db):
        fav = isolated_db.create_drama(title_en="Fav", custom_tags="Favorite", status="dubbed")
        hold = isolated_db.create_drama(title_en="Hold", custom_tags="On Hold, Favorite")
        plan = isolated_db.create_drama(title_en="Plan", custom_tags="Plan to Translate")
        isolated_db.create_drama(title_en="None")

        at = self._run(library_org_filter="Favorite")
        assert self._listed_ids(at) == sorted([fav, hold])
        at = self._run(library_org_filter="Plan to Translate")
        assert self._listed_ids(at) == [plan]
        at = self._run()
        assert len(self._listed_ids(at)) == 4
        assert isolated_db.get_drama(fav)["status"] == "dubbed"
