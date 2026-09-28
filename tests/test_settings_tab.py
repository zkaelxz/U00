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

from tabs.settings_tab import _load_env_defaults, save_key_to_env


@pytest.fixture(autouse=True)
def clean_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


@pytest.fixture(autouse=True)
def _isolated_library(isolated_db):
    """Every render_settings_sidebar() call now touches the database --
    Step 26e's profile picker calls db.list_profiles() unconditionally,
    which this file's own sidebar tests never exercised before it existed
    (this file didn't import db at all). Autouse so every test here is
    isolated from the real library automatically, without each one
    having to remember to request isolated_db by hand -- a real, if
    minor, incident this file's own tests caused before this fixture
    existed: a stray "Me" profile row written into the actual project
    library/library.db from an unisolated AppTest run."""
    yield


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
    calls made anywhere in the same script run see the current value --
    background_jobs deliberately doesn't import streamlit, so this
    sidebar is what keeps it in sync."""

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
        assert background_jobs.get_gpu_limit_enabled() is True

    def test_turning_it_off_syncs_to_background_jobs(self):
        import background_jobs
        at = self._run()
        try:
            self._checkbox(at).set_value(False).run()
            assert background_jobs.get_gpu_limit_enabled() is False
        finally:
            background_jobs.set_gpu_limit_enabled(True)  # don't leak into other tests


class TestNotifyOnJobDoneToggle:
    """Step 23c item 4: the Settings toggle for a desktop notification
    when a background job finishes, synced into background_jobs's own
    module-level flag -- same pattern as TestLimitOneGpuJobToggle above,
    since background_jobs deliberately doesn't import streamlit either."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _checkbox(self, at):
        matches = [c for c in at.checkbox
                   if c.label == "🔔 Desktop notification when a background job finishes"]
        assert matches, "checkbox not found in the sidebar"
        return matches[0]

    def test_defaults_to_off(self):
        import background_jobs
        at = self._run()
        assert self._checkbox(at).value is False
        assert background_jobs.get_notify_on_completion() is False

    def test_turning_it_on_syncs_to_background_jobs(self):
        import background_jobs
        at = self._run()
        try:
            self._checkbox(at).set_value(True).run()
            assert background_jobs.get_notify_on_completion() is True
        finally:
            background_jobs.set_notify_on_completion(False)  # don't leak into other tests


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


class TestSaveKeyToEnv:
    """Step 16 item 6: a "Save to .env" action next to each API-key field,
    writing/updating the matching BAIHE_<NAME>_KEY line in place -- so a
    typed key survives a restart without requiring the user to hand-edit
    .env themselves."""

    def test_creates_the_file_when_it_does_not_exist_yet(self, tmp_path):
        env_path = str(tmp_path / ".env")
        var_name = save_key_to_env("hf_token", "hf_new_value", env_path)
        assert var_name == "BAIHE_HF_TOKEN"
        with open(env_path, encoding="utf-8") as f:
            content = f.read()
        assert content == "BAIHE_HF_TOKEN=hf_new_value\n"

    def test_appends_a_new_line_when_the_file_exists_but_lacks_that_key(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "BAIHE_CLAUDE_KEY=sk-ant-x\n")
        save_key_to_env("hf_token", "hf_new_value", env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines == ["BAIHE_CLAUDE_KEY=sk-ant-x\n", "BAIHE_HF_TOKEN=hf_new_value\n"]

    def test_updates_an_existing_line_in_place_rather_than_appending_a_duplicate(self, tmp_path):
        env_path = _write_env(
            tmp_path / ".env",
            "BAIHE_CLAUDE_KEY=sk-ant-old\nBAIHE_HF_TOKEN=hf_old\n")
        save_key_to_env("claude", "sk-ant-new", env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines == ["BAIHE_CLAUDE_KEY=sk-ant-new\n", "BAIHE_HF_TOKEN=hf_old\n"]
        assert lines.count("BAIHE_CLAUDE_KEY=sk-ant-new\n") == 1

    def test_preserves_comments_and_other_lines(self, tmp_path):
        env_path = _write_env(
            tmp_path / ".env",
            "# a comment\nBAIHE_CLAUDE_KEY=sk-ant-old\n\nBAIHE_HF_TOKEN=hf_old\n")
        save_key_to_env("claude", "sk-ant-new", env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines == [
            "# a comment\n", "BAIHE_CLAUDE_KEY=sk-ant-new\n", "\n", "BAIHE_HF_TOKEN=hf_old\n"]

    def test_round_trips_through_load_env_defaults(self, tmp_path):
        env_path = str(tmp_path / ".env")
        save_key_to_env("deepl", "dl-abc123", env_path)
        _load_env_defaults(env_path)
        assert st.session_state.get("settings_deepl") == "dl-abc123"


class TestApiKeySaveToEnvButton:
    """UI wiring for TestSaveKeyToEnv's underlying function -- clicking
    the button calls save_key_to_env with the field's current value,
    without ever touching a real file (save_key_to_env itself is
    monkeypatched, since the button under test intentionally targets
    the real project .env path when not given one)."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def test_clicking_save_calls_save_key_to_env_with_the_typed_value(self, monkeypatch):
        import tabs.settings_tab as settings_tab
        calls = []
        monkeypatch.setattr(
            settings_tab, "save_key_to_env",
            lambda key, value, env_path=None: calls.append((key, value)) or "BAIHE_CLAUDE_KEY")

        at = self._run()
        claude_input = [t for t in at.text_input if t.key == "settings_input_claude"][0]
        claude_input.set_value("sk-ant-typed").run(timeout=30)
        save_btn = [b for b in at.button if b.key == "save_env_claude"][0]
        save_btn.click().run(timeout=30)

        assert calls == [("claude", "sk-ant-typed")]


class TestOcrDefaultBackendSetting:
    """Step 16: the OCR default-backend picker moves from being only in
    Scanlate to living in Settings' OCR section, so it has a home even
    for someone who hasn't opened Scanlate yet."""

    def _run(self):
        from streamlit.testing.v1 import AppTest

        def _render():
            from tabs.settings_tab import render_settings_sidebar
            render_settings_sidebar()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        return at

    def _backend_box(self, at):
        matches = [b for b in at.selectbox if b.label == "Default OCR backend"]
        assert matches, "Default OCR backend selectbox not found in Settings"
        return matches[0]

    def test_defaults_to_auto(self):
        at = self._run()
        assert self._backend_box(at).value == "auto"
        assert at.session_state.get("settings_ocr_backend") == "auto"

    def test_picking_a_backend_sets_session_state(self):
        at = self._run()
        self._backend_box(at).set_value("manga_ocr").run(timeout=30)
        assert at.session_state.get("settings_ocr_backend") == "manga_ocr"

    def test_prefer_paddle_vl_manga_checkbox_present_and_off_by_default(self):
        at = self._run()
        matches = [c for c in at.checkbox
                   if c.label == "For Japanese, Auto prefers PaddleOCR-VL-For-Manga over manga_ocr"]
        assert matches, "checkbox not found in Settings' OCR section"
        assert matches[0].value is False
        assert at.session_state.get("settings_ocr_prefer_paddle_vl_manga") is False


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
