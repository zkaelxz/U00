"""
tests/test_settings_env_file.py -- the .env key loader and writer in
services/settings_service.py (resolve_key, set_engine_key).

Moved from tests/test_settings_tab.py (Streamlit retirement,
docs/streamlit-retirement-plan.md section 9, guardrail 4), where the same
cases ran against the Settings tab's _load_env_defaults/save_key_to_env and
asserted st.session_state. They now assert the service's return values.

Regression coverage for a real reported bug: adding a key to .env while the
app was already running never took effect until a full restart. The service
re-reads .env on every call.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import settings_service


@pytest.fixture(autouse=True)
def _no_real_env_keys(monkeypatch):
    """resolve_key falls back to real environment variables after .env, so
    a key set on the machine running the tests must not leak in."""
    for names in settings_service.ENV_NAMES.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)


def _write_env(path, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return str(path)


class TestLoadsFromEnvFile:
    def test_strips_quotes_and_whitespace(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", '  BAIHE_HF_TOKEN = "hf_abc123"  \n')
        assert settings_service.resolve_key("hf_token", env_path) == "hf_abc123"

    def test_ignores_comments_and_blank_lines(self, tmp_path):
        env_path = _write_env(tmp_path / ".env",
                               "# a comment\n\nBAIHE_HF_TOKEN=hf_abc123\n# BAIHE_GROQ_KEY=unused\n")
        assert settings_service.resolve_key("hf_token", env_path) == "hf_abc123"
        assert not settings_service.resolve_key("groq", env_path)

    def test_malformed_file_does_not_crash(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "this is not a valid env line at all")
        assert not settings_service.resolve_key("hf_token", env_path)  # must not raise

    def test_loads_gemini_key(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "GEMINI_API_KEY=g_abc123\n")
        assert settings_service.resolve_key("gemini", env_path) == "g_abc123"

    def test_gemini_does_not_fall_back_to_google_api_key(self, tmp_path):
        # A key under GOOGLE_API_KEY isn't guaranteed to also work as a
        # Gemini key, so gemini must not silently pick it up.
        env_path = _write_env(tmp_path / ".env", "GOOGLE_API_KEY=other_google_key\n")
        assert not settings_service.resolve_key("gemini", env_path)


class TestSaveKeyToEnv:
    """Step 16 item 6: saving a typed key writes/updates the matching
    BAIHE_<NAME>_KEY line in place, so it survives a restart without the
    user hand-editing .env."""

    def test_creates_the_file_when_it_does_not_exist_yet(self, tmp_path):
        env_path = str(tmp_path / ".env")
        result = settings_service.set_engine_key("hf_token", "hf_new_value", env_path)
        assert result == {"engine": "hf_token", "configured": True}
        with open(env_path, encoding="utf-8") as f:
            content = f.read()
        assert content == "BAIHE_HF_TOKEN=hf_new_value\n"

    def test_appends_a_new_line_when_the_file_exists_but_lacks_that_key(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "BAIHE_CLAUDE_KEY=sk-ant-x\n")
        settings_service.set_engine_key("hf_token", "hf_new_value", env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines == ["BAIHE_CLAUDE_KEY=sk-ant-x\n", "BAIHE_HF_TOKEN=hf_new_value\n"]

    def test_updates_an_existing_line_in_place_rather_than_appending_a_duplicate(self, tmp_path):
        env_path = _write_env(
            tmp_path / ".env",
            "BAIHE_CLAUDE_KEY=sk-ant-old\nBAIHE_HF_TOKEN=hf_old\n")
        settings_service.set_engine_key("claude", "sk-ant-new", env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines == ["BAIHE_CLAUDE_KEY=sk-ant-new\n", "BAIHE_HF_TOKEN=hf_old\n"]
        assert lines.count("BAIHE_CLAUDE_KEY=sk-ant-new\n") == 1

    def test_preserves_comments_and_other_lines(self, tmp_path):
        env_path = _write_env(
            tmp_path / ".env",
            "# a comment\nBAIHE_CLAUDE_KEY=sk-ant-old\n\nBAIHE_HF_TOKEN=hf_old\n")
        settings_service.set_engine_key("claude", "sk-ant-new", env_path)
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert lines == [
            "# a comment\n", "BAIHE_CLAUDE_KEY=sk-ant-new\n", "\n", "BAIHE_HF_TOKEN=hf_old\n"]

    def test_round_trips_through_resolve_key(self, tmp_path):
        env_path = str(tmp_path / ".env")
        settings_service.set_engine_key("groq", "gq-abc123", env_path)
        assert settings_service.resolve_key("groq", env_path) == "gq-abc123"


class TestRepeatedCallsPickUpLateEdits:
    """The actual bug fix: editing .env while the app is running (no
    restart) must take effect on the next call."""

    def test_a_key_added_to_env_after_the_first_call_is_picked_up_on_the_second(self, tmp_path):
        env_path = _write_env(tmp_path / ".env", "BAIHE_CLAUDE_KEY=sk-ant-x\n")
        assert not settings_service.resolve_key("hf_token", env_path)  # not in .env yet
        _write_env(tmp_path / ".env", "BAIHE_CLAUDE_KEY=sk-ant-x\nBAIHE_HF_TOKEN=hf_added_later\n")
        assert settings_service.resolve_key("hf_token", env_path) == "hf_added_later"
