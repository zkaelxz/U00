"""
tests/test_live_tab.py -- tabs/live_tab.py's own UI wiring (the pipeline
itself is exercised in test_live_translate.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs


class TestCookieSettingsReachTheStartButton:
    """Step 9b.4: the cookies setting from Settings must reach
    live_translate.run_live_job the same way it reaches the Workspace
    downloader -- closing the loop at the real click site, not just
    proving run_live_job itself forwards the params (test_live_translate.py)."""

    def _run(self, **session_state):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.live_tab as lt
            lt.render_live_tab()

        at = AppTest.from_function(_render)
        for k, v in session_state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        return at

    def test_cookies_from_settings_reach_run_live_job(self, monkeypatch):
        background_jobs.clear_job("live_capture")
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)

        at = self._run(live_url="https://example.com/live",
                       live_engine_choice="test_offline",
                       settings_cookies_browser="chrome",
                       settings_cookies_file="")
        [b for b in at.button if b.label == "▶️ Start"][0].click()
        at.run(timeout=30)

        assert captured.get("cookies_browser") == "chrome"
        assert captured.get("cookies_file") is None

    def test_no_cookies_configured_passes_none(self, monkeypatch):
        background_jobs.clear_job("live_capture")
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)

        at = self._run(live_url="https://example.com/live", live_engine_choice="test_offline")
        [b for b in at.button if b.label == "▶️ Start"][0].click()
        at.run(timeout=30)

        assert captured.get("cookies_browser") is None
        assert captured.get("cookies_file") is None

    def test_cookies_file_takes_priority_when_both_set(self, monkeypatch):
        background_jobs.clear_job("live_capture")
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)

        at = self._run(live_url="https://example.com/live", live_engine_choice="test_offline",
                       settings_cookies_browser="chrome", settings_cookies_file="/tmp/c.txt")
        [b for b in at.button if b.label == "▶️ Start"][0].click()
        at.run(timeout=30)

        assert captured.get("cookies_browser") == "chrome"
        assert captured.get("cookies_file") == "/tmp/c.txt"
