"""
tests/test_discover_tab.py -- Discover tab's baihehub search.

Step 25u item 2: with Ollama selected and no API key set, `bh_api_key`
being empty made `engine = None`, skipping `title_library.translate_query_to_zh`
entirely -- so the raw English query got sent straight to baihehub's
Chinese-language search instead of being translated first.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import title_library
import translate_engines


class _LLMEngine:
    supports_reference = True


def _run(monkeypatch, isolated_db, engine_name, api_key=""):
    from streamlit.testing.v1 import AppTest

    translate_calls = []

    def fake_translate_query_to_zh(query, engine):
        translate_calls.append((query, engine))
        return "翻译后的查询"
    monkeypatch.setattr(title_library, "translate_query_to_zh", fake_translate_query_to_zh)
    monkeypatch.setattr(title_library, "search_baihehub", lambda query, **kw: [])
    monkeypatch.setattr(title_library, "search_url_fallback", lambda query: f"https://example.com/?q={query}")

    def fake_get_engine(name, api_key=None, model=None, free_tier=False, base_url=None):
        return _LLMEngine()
    monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)

    def _render():
        import tabs.discover_tab as dt
        dt.render_discover_tab()

    at = AppTest.from_function(_render)
    at.run(timeout=30)
    [query_box] = [t for t in at.text_input if t.key == "bh_query"]
    query_box.set_value("some english title")
    [engine_picker] = [s for s in at.selectbox if s.key == "bh_engine"]
    engine_picker.set_value(engine_name)
    at.run(timeout=30)
    if api_key:
        [key_box] = [t for t in at.text_input if t.key == "bh_api_key"]
        key_box.set_value(api_key)
        at.run(timeout=30)
    [button] = [b for b in at.button if b.label == "Search baihehub"]
    button.click().run(timeout=30)
    return at, translate_calls


class TestBaihehubSearch:
    def test_ollama_with_no_key_still_translates_query(self, monkeypatch, isolated_db):
        at, translate_calls = _run(monkeypatch, isolated_db, "ollama")
        assert not at.exception
        assert not at.error, [e.value for e in at.error]
        assert len(translate_calls) == 1
        assert translate_calls[0][0] == "some english title"
        assert translate_calls[0][1] is not None
        assert any("翻译后的查询" in c.value for c in at.caption)

    def test_non_local_engine_without_key_falls_back_to_raw_query(self, monkeypatch, isolated_db):
        """Sanity check: an engine that *does* need a key isn't used for
        translation when no key was provided -- unlike Ollama, this is
        still expected to search the raw, untranslated query."""
        at, translate_calls = _run(monkeypatch, isolated_db, "claude")
        assert not at.exception
        assert translate_calls == []
        assert any("some english title" in c.value for c in at.caption)

    def test_engine_with_key_translates_query(self, monkeypatch, isolated_db):
        at, translate_calls = _run(monkeypatch, isolated_db, "claude", api_key="test-key")
        assert not at.exception
        assert len(translate_calls) == 1
        assert translate_calls[0][1] is not None
