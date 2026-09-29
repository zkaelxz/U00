"""Streamlit widget, AppTest and tab-source tests split out of tests/test_diagnostics_and_export.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_diagnostics_and_export.py."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import debug_view
from core import Line


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestBenchmarkRunnerEnginePassesOllamaUrlAndFreeTier:
    """Step 25d item 7: same gap Step 5b item 1 already fixed elsewhere --
    this call used to always build the engine with no base_url/free_tier
    at all, so it ignored a custom Ollama URL and always billed Gemini as
    paid-tier."""

    def _run(self, **state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        for k, v in state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_ollama_base_url_is_passed_through(self, isolated_db, monkeypatch):
        import translate_engines
        isolated_db.create_benchmark_case("Case", "translation", "audio_drama", source_text="你好")
        seen = {}

        def spy(engine_name, api_key, *a, **kw):
            seen["engine"] = engine_name
            seen["base_url"] = kw.get("base_url")
            raise RuntimeError("stop before any real network call")
        monkeypatch.setattr(translate_engines, "get_engine", spy)

        at = self._run(settings_default_engine="ollama", settings_ollama="local",
                       settings_ollama_url="http://myhost:11434")
        [btn] = [b for b in at.button
                if b.label == "▶️ Run all cases through the current pipeline"]
        btn.click().run(timeout=30)

        assert seen.get("engine") == "ollama"
        assert seen.get("base_url") == "http://myhost:11434"


class TestRunningJobsPanelAutoRefresh:
    """Step 26c: the Running jobs panel auto-refreshes (st.fragment with
    run_every) so a job started from a second concurrent session/tab
    shows up here without a manual click. AppTest always reruns the
    whole script rather than reproducing a fragment-scoped timed rerun
    (see tests/test_gui_polish.py's own note on this), so the fragment
    scoping itself is checked statically and the actual rendered content
    is checked by driving a real AppTest render."""

    def _decorators(self, func_name):
        import ast
        with open(os.path.join(PROJECT_ROOT, "tabs/diagnostics_tab.py"), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        [fn] = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == func_name]
        return [ast.unparse(d) for d in fn.decorator_list]

    def test_panel_is_an_auto_refreshing_fragment(self):
        decorators = self._decorators("_render_running_jobs_panel")
        assert any("st.fragment" in d and "run_every" in d for d in decorators), decorators

    def _panel_app(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.diagnostics_tab import _render_running_jobs_panel
            _render_running_jobs_panel()
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_shows_nothing_running_caption_when_idle(self):
        import background_jobs
        background_jobs._jobs.clear()
        at = self._panel_app()
        assert not at.exception
        assert any("Nothing running" in c.value for c in at.caption)

    def test_shows_a_real_running_job_with_progress_and_cancel(self, isolated_db):
        import background_jobs
        did = isolated_db.create_drama(title_en="Concurrent Session Test")
        job_id = f"translate_{did}"
        background_jobs._jobs[job_id] = {
            "status": "running", "progress": 0.42, "message": "Line 5/12",
            "error": None, "cancel_requested": False, "result": None,
        }
        try:
            at = self._panel_app()
            assert not at.exception
            progress_els = at.get("progress")
            assert len(progress_els) == 1
            assert progress_els[0].value == 42  # st.progress stores 0-100, not the 0.0-1.0 given
            assert "Concurrent Session Test" in progress_els[0].proto.text
            assert "Line 5/12" in progress_els[0].proto.text
            assert len(at.button) == 1  # Cancel only -- the manual Refresh button was removed
            assert "Cancel" in at.button[0].label
        finally:
            background_jobs._jobs.pop(job_id, None)

    def test_cancel_button_requests_cancellation(self, isolated_db, monkeypatch):
        import background_jobs
        did = isolated_db.create_drama(title_en="Cancel Me")
        job_id = f"translate_{did}"
        background_jobs._jobs[job_id] = {
            "status": "running", "progress": 0.1, "message": "",
            "error": None, "cancel_requested": False, "result": None,
        }
        requested = []
        monkeypatch.setattr(background_jobs, "request_cancel", lambda jid: requested.append(jid))
        try:
            at = self._panel_app()
            at.button[0].click().run()
            assert requested == [job_id]
        finally:
            background_jobs._jobs.pop(job_id, None)


class TestPiperVoicesPanelUI:
    """UI-level: Diagnostics' disk-management panel actually surfaces
    what scan_piper_voices() finds in the real library/piper_voices dir
    (isolated_db redirects db.LIBRARY_DIR, same as everywhere else)."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_shows_a_downloaded_piper_voice(self, isolated_db):
        import dub
        voices_dir = dub.piper_voices_dir()
        os.makedirs(voices_dir, exist_ok=True)
        with open(os.path.join(voices_dir, "en_US-amy-medium.onnx"), "wb") as f:
            f.write(b"x" * 5000)

        at = self._run()
        assert any("en_US-amy-medium" in c.value for c in at.caption)

    def test_nothing_shown_when_no_piper_voices_downloaded(self, isolated_db):
        at = self._run()
        assert not any("Piper voices" in c.value for c in at.caption)


class TestBugBundleDeleteNeedsConfirmation:
    """Step 71: "Delete bundle" used to fire on a single click with no
    confirmation, unlike every other destructive action in the app."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _bundle(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        isolated_db.save_lines(did, lines)
        return debug_view.save_bug_bundle(
            did, lines[0], lines, "claude", None, glossary_terms=[], locale="en-US")

    def test_delete_bundle_disabled_until_confirmed(self, isolated_db):
        report_id = self._bundle(isolated_db)
        at = self._run()

        assert [b for b in at.button if b.key == f"bug_delete_{report_id}"][0].disabled
        at.checkbox(key=f"confirm_bug_delete_{report_id}").set_value(True).run(timeout=30)
        assert not [b for b in at.button if b.key == f"bug_delete_{report_id}"][0].disabled

    def test_delete_bundle_removes_it_once_confirmed(self, isolated_db):
        report_id = self._bundle(isolated_db)
        at = self._run()

        at.checkbox(key=f"confirm_bug_delete_{report_id}").set_value(True).run(timeout=30)
        [b for b in at.button if b.key == f"bug_delete_{report_id}"][0].click().run(timeout=30)

        assert isolated_db.get_bug_report(report_id) is None


class TestDiagnosticsTabSupportReport:
    """UI-level: the 'Copy diagnostics for support' button actually wires
    format_diagnostics_report + redact_for_support together, so a path/
    username that would appear in the raw diagnostics never reaches the
    copyable box."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.diagnostics_tab as dt
            dt.render_diagnostics_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_report_appears_with_no_path_or_username(self, isolated_db, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "realuserabc")
        at = self._run()
        [b for b in at.button if b.key == "build_support_report"][0].click()
        at.run(timeout=30)
        # Find the specific code block holding the report (there's also
        # the log-tail code block on this page).
        codes = [c.value for c in at.code]
        report = next((c for c in codes if "Python:" in c), None)
        assert report is not None
        assert "realuserabc" not in report
        assert os.path.dirname(os.path.dirname(os.path.abspath(
            "tabs/diagnostics_tab.py"))) not in report
        assert "Model/engine versions" in report
        assert "Whisper (faster-whisper)" in report
