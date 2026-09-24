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
