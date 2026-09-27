"""
tests/test_page_server_settings.py -- Step 32's Settings expander: the
opt-in switch, the token display, and the bridge that carries translation
settings to the endpoint's background thread.

The bridge is the part worth testing. The endpoint runs on a thread with
no `st.session_state`, and this repo deliberately keeps API keys out of
the database, so the only way a key reaches it is the UI handing it over
in memory on each render -- the same shape
`background_jobs.set_gpu_limit_enabled` already uses. A silent break here
would look like "the extension stopped translating" with nothing in a
log to explain it.

No real port is ever opened: `ensure_server_started` is monkeypatched.
"""
import os
import sys

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import page_server


@pytest.fixture(autouse=True)
def no_real_server(monkeypatch):
    started = []
    monkeypatch.setattr(page_server, "ensure_server_started",
                        lambda *a, **kw: started.append(True) or True)
    monkeypatch.setattr(page_server, "server_running", lambda: bool(started))
    monkeypatch.setattr(page_server, "server_port", lambda: page_server.DEFAULT_PORT)
    page_server.set_translation_config(engine=None, api_key="", base_url=None,
                                       free_tier=False, hf_token=None,
                                       tesseract_cmd=None)
    return started


def _render(enabled, session=None):
    """Renders just the extension expander and returns the AppTest.

    The persisted flag is set out here, not inside the script: AppTest
    re-executes the function's source in a fresh module namespace, so the
    script can't close over a test-local value. It reads the same
    `sources.db` because `isolated_db` has already redirected
    `db.LIBRARY_DIR` for this whole process.
    """
    from sources import store as src_store
    src_store.set_setting("page_server_enabled", bool(enabled))

    def script():
        from tabs.settings_tab import _render_browser_extension_settings
        _render_browser_extension_settings()

    at = AppTest.from_function(script)
    for key, value in (session or {}).items():
        at.session_state[key] = value
    at.run(timeout=30)
    return at


class TestTheOptInSwitch:
    def test_off_by_default_and_nothing_is_started(self, isolated_db, no_real_server):
        at = _render(False)
        assert no_real_server == []
        # No token is shown while it's off -- nothing to leak on screen.
        assert not [t for t in at.text_input if "Token" in (t.label or "")]

    def test_turning_it_on_starts_the_endpoint_and_shows_the_token(self, isolated_db,
                                                                   no_real_server):
        at = _render(True)
        assert no_real_server == [True]
        tokens = [t for t in at.text_input if "Token" in (t.label or "")]
        assert len(tokens) == 1
        assert tokens[0].value == page_server.load_or_create_token()

    def test_the_persisted_flag_survives_a_restart(self, isolated_db, no_real_server):
        """A new browser session must not silently turn the endpoint off
        again -- the sidebar's own settings are session-only, which is
        why this one flag lives in the persisted store."""
        from sources import store as src_store
        _render(True)
        assert src_store.get_setting("page_server_enabled") is True


class TestTheConfigBridge:
    def test_the_chosen_engines_own_key_is_handed_to_the_endpoint(self, isolated_db,
                                                                 no_real_server):
        _render(True, session={"settings_page_server_engine": "deepseek",
                               "settings_deepseek": "ds-key-123"})
        config = page_server.get_translation_config()
        assert config["engine"] == "deepseek"
        assert config["api_key"] == "ds-key-123"

    def test_another_engines_key_is_not_handed_over(self, isolated_db, no_real_server):
        """Only the selected engine's key crosses the bridge."""
        _render(True, session={"settings_page_server_engine": "deepseek",
                               "settings_claude": "claude-key",
                               "settings_deepseek": "ds-key"})
        assert page_server.get_translation_config()["api_key"] == "ds-key"

    def test_ocr_settings_cross_the_bridge_too(self, isolated_db, no_real_server):
        _render(True, session={"settings_page_server_engine": "claude",
                               "settings_claude": "k",
                               "settings_hf_token": "hf_x",
                               "settings_tesseract_cmd": "/usr/bin/tesseract"})
        config = page_server.get_translation_config()
        assert config["hf_token"] == "hf_x"
        assert config["tesseract_cmd"] == "/usr/bin/tesseract"

    def test_a_missing_key_leaves_the_endpoint_without_an_engine(self, isolated_db,
                                                                no_real_server):
        """Rather than a half-configured engine that fails per request."""
        _render(True, session={"settings_page_server_engine": "claude"})
        config = page_server.get_translation_config()
        assert config["engine"] == "claude" and config["api_key"] == ""
        assert page_server._build_engine(config) is None


class TestSecretsStayOutOfTheStore:
    def test_no_api_key_and_no_token_reaches_the_settings_database(self, isolated_db,
                                                                  no_real_server):
        """This repo's rule: keys are never written to the database. The
        endpoint's own token is a file in the library dir, not a row."""
        from sources import store as src_store
        _render(True, session={"settings_page_server_engine": "claude",
                               "settings_claude": "claude-key-should-not-persist"})
        stored = repr(src_store.all_settings())
        assert "claude-key-should-not-persist" not in stored
        assert page_server.load_or_create_token() not in stored
