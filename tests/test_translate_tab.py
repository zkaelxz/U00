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

    def test_clear_history_button_empties_it(self, isolated_db):
        isolated_db.save_translate_history("zh", "en", "claude", "a", "A")
        at = _run()
        [b for b in at.button if "Clear history" in b.label][0].click().run()
        assert isolated_db.list_translate_history() == []
