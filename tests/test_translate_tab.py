"""
tests/test_translate_tab.py -- tabs/translate_tab.py's own UI wiring
(translate_engines.standalone_translate itself, and the direction-support/
chunking logic it calls, are exercised in test_standalone_translate.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(session_state=None):
    from streamlit.testing.v1 import AppTest

    def _render():
        import tabs.translate_tab as tt
        tt.render_translate_tab()

    at = AppTest.from_function(_render)
    for k, v in (session_state or {}).items():
        at.session_state[k] = v
    at.run(timeout=30)
    return at


class TestTranslateTab:
    def test_renders_without_error(self, isolated_db):
        at = _run()
        assert not at.exception

    def test_translating_with_test_offline_saves_history(self, isolated_db):
        at = _run({"translate_tab_engine": "test_offline"})
        at.text_area(key="translate_tab_text").set_value("你好世界").run()
        [b for b in at.button if "Translate" in b.label][0].click().run()
        assert not at.exception
        history = isolated_db.list_translate_history()
        assert len(history) == 1
        assert history[0]["source_text"] == "你好世界"
        assert history[0]["engine"] == "test_offline"
        assert history[0]["source_language"] == "zh"
        assert history[0]["target_language"] == "en"

    def test_english_to_chinese_direction_saves_the_right_languages(self, isolated_db):
        at = _run({"translate_tab_engine": "test_offline",
                   "translate_tab_direction": "from_english"})
        at.text_area(key="translate_tab_text").set_value("Hello there.").run()
        [b for b in at.button if "Translate" in b.label][0].click().run()
        assert not at.exception
        history = isolated_db.list_translate_history()
        assert len(history) == 1
        assert history[0]["source_language"] == "en"
        assert history[0]["target_language"] == "zh"

    def test_libretranslate_english_to_cjk_shows_a_refusal_and_saves_nothing(self, isolated_db):
        """Exit condition: an engine that doesn't support the requested
        direction is refused with a clear reason rather than silently
        attempted -- checked here at the actual UI entry point, not just
        the underlying function."""
        at = _run({"translate_tab_engine": "libretranslate",
                   "translate_tab_direction": "from_english"})
        assert not at.exception
        assert any("libretranslate" in e.value.lower() for e in at.error)
        assert isolated_db.list_translate_history() == []

    def test_clear_history_button_disabled_until_confirmed(self, isolated_db):
        """Step 25z: destructive actions need an explicit confirmation step,
        defaulting to unchecked, before the button itself is enabled."""
        isolated_db.save_translate_history("zh", "en", "claude", "a", "A")
        at = _run()
        assert [b for b in at.button if "Clear history" in b.label][0].disabled
        assert isolated_db.list_translate_history() != []

    def test_clear_history_button_empties_it_once_confirmed(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "a", "A")
        at = _run()
        at.checkbox(key="confirm_clear_translate_history").set_value(True).run(timeout=30)
        [b for b in at.button if "Clear history" in b.label][0].click().run()
        assert isolated_db.list_translate_history() == []

    def test_engine_setup_failure_never_shows_a_raw_key_in_the_error(self, isolated_db, monkeypatch):
        """Regression: an engine-setup failure must run its exception text
        through translate_engines.redact_secrets before showing it, the
        same as every other error path in the app (e.g. workspace_tab's
        bulk-submission errors) -- an invalid-key error can otherwise
        echo the key straight back into the UI."""
        import translate_engines as te

        leaked_key = "sk-should-not-appear-1234567890"

        def _boom(*a, **kw):
            raise RuntimeError(f"invalid api key: {leaked_key}")

        monkeypatch.setattr(te, "get_engine", _boom)

        at = _run({"translate_tab_engine": "claude",
                   "translate_tab_api_key_claude": "whatever-was-typed"})
        at.text_area(key="translate_tab_text").set_value("你好").run()
        [b for b in at.button if "Translate" in b.label][0].click().run()

        assert not at.exception
        assert not any(leaked_key in e.value for e in at.error)
        assert any("[REDACTED]" in e.value for e in at.error)

    def test_translation_failure_never_shows_a_raw_key_in_the_error(self, isolated_db, monkeypatch):
        """Same regression as above, for a failure raised by
        standalone_translate itself (e.g. the provider rejects the key
        mid-request) rather than by get_engine."""
        import translate_engines as te

        leaked_key = "sk-should-not-appear-0987654321"

        def _boom(*a, **kw):
            raise RuntimeError(f"invalid api key: {leaked_key}")

        monkeypatch.setattr(te, "standalone_translate", _boom)

        at = _run({"translate_tab_engine": "claude",
                   "translate_tab_api_key_claude": "whatever-was-typed"})
        at.text_area(key="translate_tab_text").set_value("你好").run()
        [b for b in at.button if "Translate" in b.label][0].click().run()

        assert not at.exception
        assert not any(leaked_key in e.value for e in at.error)
        assert any("[REDACTED]" in e.value for e in at.error)
