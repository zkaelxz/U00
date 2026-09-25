"""
tests/test_settings_tab.py -- _load_env_defaults(), the .env -> Settings
sidebar loader.

Regression coverage for a real reported bug: adding a key to .env while
the app was already running never took effect until a full process
restart, because the loader only ever ran once per session. Fixed by
dropping that once-per-session gate; these tests lock in both that fix
and the "already-set values always win" protection it still needs to
provide instead.
"""
import os
import sys

import pytest
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tabs.settings_tab import _load_env_defaults


@pytest.fixture(autouse=True)
def clean_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


def _write_env(path, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return str(path)


class TestLoadsFromEnvFile:
    def test_loads_a_recognized_key(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "BAIHE_HF_TOKEN=hf_abc123\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "hf_abc123"

    def test_strips_quotes_and_whitespace(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", '  BAIHE_HF_TOKEN = "hf_abc123"  \n')
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "hf_abc123"

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        env_path = _write_env(tmp_path / ".env",
                               "# a comment\n\nBAIHE_HF_TOKEN=hf_abc123\n# BAIHE_DEEPL_KEY=unused\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "hf_abc123"
        assert not st.session_state.get("settings_deepl")

    def test_first_matching_name_wins(self, tmp_path):
        # hf_token checks BAIHE_HF_TOKEN, then HF_TOKEN, then HUGGINGFACE_TOKEN --
        # the more specific BAIHE_-prefixed name should win when both are present.
        env_path = _write_env(tmp_path / ".env",
                               "HF_TOKEN=generic\nBAIHE_HF_TOKEN=specific\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "specific"

    def test_missing_file_does_not_crash(self, tmp_path):
        _load_env_defaults(str(tmp_path / "does_not_exist.env"))
        assert not st.session_state.get("settings_hf_token")

    def test_malformed_file_does_not_crash(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "this is not a valid env line at all")
        _load_env_defaults(env_path)  # must not raise
        assert not st.session_state.get("settings_hf_token")

    def test_a_utf8_bom_on_the_first_line_does_not_break_that_variable(self, tmp_path):
        """Regression test for a real reported failure: a .env saved by
        Notepad (the default editor a non-technical Windows user would
        reach for) writes a UTF-8 byte-order-mark at the very start of
        the file by default. Read as plain "utf-8", that BOM attaches
        itself to the first key's name ("﻿HF_TOKEN" instead of
        "HF_TOKEN"), so it silently never matches -- while a variable
        placed on a LATER line in the same file works fine, which is
        exactly what made this so confusing to diagnose from a bug
        report alone (the fix must actually write the raw BOM bytes,
        not go through _write_env()'s plain-text helper, which doesn't
        add one)."""
        env_path = str(tmp_path / ".env")
        with open(env_path, "wb") as f:
            f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
            f.write(b"HF_TOKEN=hf_abc123\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "hf_abc123"

    def test_loads_gemini_key(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "GEMINI_API_KEY=g_abc123\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_gemini") == "g_abc123"

    def test_gemini_does_not_fall_back_to_google_api_key(self, tmp_path):
        # GOOGLE_API_KEY belongs to the separate Google Translate engine --
        # a Cloud Translation key isn't guaranteed to also work as a Gemini
        # key, so gemini must not silently pick it up.
        env_path = _write_env(tmp_path / ".env", "GOOGLE_API_KEY=translate_key_only\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_google") == "translate_key_only"
        assert not st.session_state.get("settings_gemini")


class TestAlreadySetValuesWin:
    def test_manually_typed_value_is_not_overwritten(self, tmp_path):
        st.session_state["settings_hf_token"] = "typed-by-hand"
        env_path = _write_env(tmp_path / ".env", "BAIHE_HF_TOKEN=from_env\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "typed-by-hand"

    def test_value_loaded_from_env_on_an_earlier_call_still_wins(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "BAIHE_HF_TOKEN=first_value\n")
        _load_env_defaults(env_path)
        _write_env(tmp_path / ".env", "BAIHE_HF_TOKEN=second_value\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "first_value"


class TestGeminiFreeTierCheckbox:
    """Step 1d item 4: a "My Gemini key is free-tier" checkbox in
    Settings, feeding translate_engines.engine_picker_label /
    get_engine(free_tier=...) everywhere Gemini is picked."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _checkbox(self, at):
        matches = [c for c in at.checkbox if c.label == "My Gemini key is free-tier"]
        assert matches, "checkbox not found in the sidebar"
        return matches[0]

    def test_checkbox_defaults_to_off(self):
        at = self._run()
        assert self._checkbox(at).value is False
        assert at.session_state.get("gemini_free_tier") is False

    def test_ticking_it_sets_session_state(self):
        at = self._run()
        self._checkbox(at).set_value(True).run()
        assert at.session_state.get("gemini_free_tier") is True

    def test_stays_on_across_a_rerun(self):
        at = self._run()
        self._checkbox(at).set_value(True).run()
        at.run(timeout=30)
        assert self._checkbox(at).value is True


class TestLimitOneGpuJobToggle:
    """Step 5c: a "Limit to one GPU job at a time" checkbox, defaulting on,
    that syncs into background_jobs' own module-level flag so start_job()
    and gpu_slot() calls made anywhere in the same script run see the
    current value -- background_jobs deliberately doesn't import
    streamlit, so this sidebar is what keeps it in sync."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _checkbox(self, at):
        matches = [c for c in at.checkbox if c.label == "Limit to one GPU job at a time"]
        assert matches, "checkbox not found in the sidebar"
        return matches[0]

    def test_defaults_to_on(self):
        import background_jobs
        at = self._run()
        assert self._checkbox(at).value is True
        assert background_jobs.gpu_limit_enabled() is True

    def test_turning_it_off_syncs_to_background_jobs(self):
        import background_jobs
        at = self._run()
        try:
            self._checkbox(at).set_value(False).run()
            assert background_jobs.gpu_limit_enabled() is False
        finally:
            background_jobs.set_gpu_limit_enabled(True)  # don't leak into other tests


class TestCookieBasedLoginSettings:
    """Step 9b.4: a Settings field for cookie-based login (browser or
    cookies file), previously only ever mentioned inside a YouTube-
    specific error message -- now something the user can actually turn
    on, for TikTok/Instagram in particular."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _browser_box(self, at):
        matches = [b for b in at.selectbox if b.label == "Pull cookies from this browser"]
        assert matches, "browser selectbox not found"
        return matches[0]

    def _file_input(self, at):
        matches = [t for t in at.text_input
                  if t.label.startswith("...or a cookies.txt file path")]
        assert matches, "cookies file text_input not found"
        return matches[0]

    def test_defaults_to_none(self):
        at = self._run()
        assert self._browser_box(at).value == "-- none --"
        assert at.session_state.get("settings_cookies_browser") is None
        assert at.session_state.get("settings_cookies_file") == ""

    def test_picking_a_browser_sets_session_state(self):
        at = self._run()
        self._browser_box(at).set_value("chrome").run()
        assert at.session_state.get("settings_cookies_browser") == "chrome"

    def test_setting_a_cookies_file_path(self):
        at = self._run()
        self._file_input(at).set_value("/home/me/cookies.txt").run()
        assert at.session_state.get("settings_cookies_file") == "/home/me/cookies.txt"

    def test_offered_browsers_match_video_download_module(self):
        import video_download
        at = self._run()
        assert self._browser_box(at).options[1:] == video_download.COOKIE_BROWSERS


class TestRepeatedCallsPickUpLateEdits:
    """The actual bug fix: editing .env while the app is running (no
    restart) must take effect on the next call, as long as the session
    hasn't already set that specific key."""

    def test_a_key_added_to_env_after_the_first_call_is_picked_up_on_the_second(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "BAIHE_CLAUDE_KEY=sk-ant-x\n")
        _load_env_defaults(env_path)
        assert not st.session_state.get("settings_hf_token")  # not in .env yet

        _write_env(tmp_path / ".env", "BAIHE_CLAUDE_KEY=sk-ant-x\nBAIHE_HF_TOKEN=hf_added_later\n")
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_hf_token") == "hf_added_later"
