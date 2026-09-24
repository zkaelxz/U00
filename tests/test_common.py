"""
tests/test_common.py -- common.py's synced_api_key_input() helper.

Regression coverage for a real, previously-unnoticed bug affecting
every duplicated API-key field in the app (Workspace's HF token box,
Reader's Q&A/Story tools keys, Scanlate's engine key, the Settings
sidebar itself, and others): Streamlit only applies a widget's `value=`
argument on its very first render in a session -- a widget with its own
explicit `key` freezes to whatever it last held after that, ignoring
`value=` forever afterward. Typing an API key into the Settings sidebar
never reached any of these per-tab duplicate fields, even though the
underlying settings_<name> session-state value was genuinely updated.

st.text_input() itself can't be meaningfully unit tested outside a real
Streamlit script run -- called "bare" (as any pytest run is), it always
returns "" regardless of session_state -- so these tests exercise
_pull_canonical_settings_value(), the actual session_state fix, directly.
"""
import os
import sys

import pytest
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import _pull_canonical_settings_value


@pytest.fixture(autouse=True)
def clean_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


class TestPullCanonicalSettingsValue:
    def test_fills_a_blank_widget_from_the_canonical_value(self):
        st.session_state["settings_hf_token"] = "hf_abc123"
        _pull_canonical_settings_value("hf_token", "workspace_hf_token_input")
        assert st.session_state["workspace_hf_token_input"] == "hf_abc123"

    def test_returns_the_settings_state_key(self):
        result = _pull_canonical_settings_value("hf_token", "workspace_hf_token_input")
        assert result == "settings_hf_token"

    def test_does_not_overwrite_a_widget_that_already_has_its_own_value(self):
        st.session_state["settings_hf_token"] = "from_settings_sidebar"
        st.session_state["workspace_hf_token_input"] = "typed_directly_here"
        _pull_canonical_settings_value("hf_token", "workspace_hf_token_input")
        assert st.session_state["workspace_hf_token_input"] == "typed_directly_here"

    def test_a_blank_canonical_value_leaves_the_widget_untouched(self):
        _pull_canonical_settings_value("hf_token", "workspace_hf_token_input")
        assert "workspace_hf_token_input" not in st.session_state

    def test_works_with_a_dynamic_engine_scoped_key(self):
        # e.g. Scanlate's sc_api_key, keyed on whichever engine is selected.
        st.session_state["settings_deepseek"] = "sk-deepseek-xyz"
        _pull_canonical_settings_value("deepseek", "sc_api_key")
        assert st.session_state["sc_api_key"] == "sk-deepseek-xyz"
