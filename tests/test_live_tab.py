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


class TestOverlapSettingReachesTheStartButton:
    """Step 9b item 6: the Live tab's "Chunk overlap" slider reaches
    live_translate.run_live_job as overlap_seconds."""

    def _start(self, monkeypatch, **session_state):
        from streamlit.testing.v1 import AppTest
        import live_translate
        background_jobs.clear_job("live_capture")
        captured = {}
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda job_id, target, *a, **kw: captured.update(kw) or True)

        def _render():
            import tabs.live_tab as lt
            lt.render_live_tab()

        at = AppTest.from_function(_render)
        for k, v in session_state.items():
            at.session_state[k] = v
        at.run(timeout=30)
        [b for b in at.button if b.label == "▶️ Start"][0].click()
        at.run(timeout=30)
        return captured, live_translate

    def test_default_overlap_is_passed(self, monkeypatch):
        captured, live_translate = self._start(
            monkeypatch, live_url="https://example.com/live", live_engine_choice="test_offline")
        assert captured.get("overlap_seconds") == live_translate.DEFAULT_OVERLAP_SECONDS

    def test_chosen_overlap_is_passed(self, monkeypatch):
        captured, _ = self._start(
            monkeypatch, live_url="https://example.com/live", live_engine_choice="test_offline",
            live_overlap_seconds=0)
        assert captured.get("overlap_seconds") == 0


class TestQueuedJobCancelButton:
    """Step 9f item 3: cancelling a still-queued job clears it outright;
    one that got promoted to running in the gap between the render and
    the click falls back to the real "Stop" path instead of leaving it
    running invisibly with no job record left to reach."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.live_tab as lt
            lt.render_live_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_cancel_clears_a_still_queued_job(self):
        background_jobs._jobs["live_capture"] = {
            "status": "queued", "progress": 0.0, "message": "Waiting -- GPU busy.",
            "error": None, "cancel_requested": False, "result": None,
            "gpu_touching": True, "started_at": 0.0,
        }
        at = self._run()
        [b for b in at.button if b.label == "✖️ Cancel"][0].click()
        at.run(timeout=30)

        assert background_jobs.get_status("live_capture") is None

    def test_cancel_falls_back_to_a_real_stop_when_promoted_in_the_gap(self, monkeypatch):
        import live_translate
        background_jobs._jobs["live_capture"] = {
            "status": "queued", "progress": 0.0, "message": "Waiting -- GPU busy.",
            "error": None, "cancel_requested": False, "result": None,
            "gpu_touching": True, "started_at": 0.0,
        }
        monkeypatch.setattr(background_jobs, "cancel_queued", lambda job_id: False)
        bumped = []
        monkeypatch.setattr(live_translate, "bump_generation", lambda job_id: bumped.append(job_id))

        at = self._run()
        [b for b in at.button if b.label == "✖️ Cancel"][0].click()
        at.run(timeout=30)

        assert bumped == ["live_capture"]
        assert background_jobs.is_cancel_requested("live_capture") is True
        background_jobs.clear_job("live_capture")
