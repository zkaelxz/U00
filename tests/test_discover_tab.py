"""
tests/test_discover_tab.py -- Discover tab: baihehub search, "Find a
title," and (Step 17) the folded-in site navigation helper section.

Step 25u item 2: with Ollama selected and no API key set, `bh_api_key`
being empty made `engine = None`, skipping `title_library.translate_query_to_zh`
entirely -- so the raw English query got sent straight to baihehub's
Chinese-language search instead of being translated first.

Step 17 item 3: Discover used to have four separate engine + API-key
pickers for the same job (baihehub search, bulk import, import-from-URL,
plus "Find a title" silently hard-coding the Claude key). They're now
one shared `discover_engine`/`discover_api_key` picker, reused below and
by the folded-in Navigator section (formerly `tabs/navigator_tab.py`,
tested here as TestSiteNavigationHelperButton -- see test_navigator.py
for navigator.py's own logic tests, unaffected by the tab merge).
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
import page_fetch
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
    [engine_picker] = [s for s in at.selectbox if s.key == "discover_engine"]
    engine_picker.set_value(engine_name)
    at.run(timeout=30)
    if api_key:
        [key_box] = [t for t in at.text_input if t.key == "discover_api_key"]
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


def _run_find_a_title(monkeypatch, isolated_db):
    """Step 25w: 'Find a title' translated find_query on every single
    Streamlit rerun -- unlike every other LLM call on this tab, it wasn't
    behind a button, so ANY unrelated widget interaction anywhere on the
    page (Streamlit reruns the whole script on any of them) re-translated
    an unchanged query, burning one real translate call each time.

    Step 17: this used to hard-code the Claude key from `settings_claude`
    regardless of engine choice -- now it goes through the shared
    discover_engine/discover_api_key picker like everything else on the
    tab, so the test drives that picker instead of pre-seeding session
    state."""
    from streamlit.testing.v1 import AppTest

    translate_calls = []

    def fake_translate_query_to_zh(query, engine):
        translate_calls.append(query)
        return "翻译后的标题"
    monkeypatch.setattr(title_library, "translate_query_to_zh", fake_translate_query_to_zh)

    def fake_get_engine(name, api_key=None, model=None, free_tier=False, base_url=None):
        return _LLMEngine()
    monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)

    def _render():
        import tabs.discover_tab as dt
        dt.render_discover_tab()

    at = AppTest.from_function(_render)
    at.run(timeout=30)
    [engine_picker] = [s for s in at.selectbox if s.key == "discover_engine"]
    engine_picker.set_value("claude")
    [key_box] = [t for t in at.text_input if t.key == "discover_api_key"]
    key_box.set_value("test-key")
    at.run(timeout=30)
    [query_box] = [t for t in at.text_input if t.key == "find_query"]
    query_box.set_value("some english title")
    at.run(timeout=30)
    return at, translate_calls


class TestFindATitleTranslationCaching:
    def test_translates_once_for_a_new_query(self, monkeypatch, isolated_db):
        at, translate_calls = _run_find_a_title(monkeypatch, isolated_db)
        assert not at.exception
        assert translate_calls == ["some english title"]
        assert any("翻译后的标题" in c.value for c in at.caption)

    def test_unrelated_rerun_does_not_retranslate_the_same_query(self, monkeypatch, isolated_db):
        at, translate_calls = _run_find_a_title(monkeypatch, isolated_db)
        assert len(translate_calls) == 1

        # An unrelated widget interaction elsewhere on the page -- Streamlit
        # reruns the whole script on ANY interaction, not just this box's
        # own. The query text itself hasn't changed.
        [format_picker] = [s for s in at.selectbox if s.key == "find_type"]
        format_picker.set_value("novel")
        at.run(timeout=30)

        assert translate_calls == ["some english title"]  # not called again

    def test_changing_the_query_translates_again(self, monkeypatch, isolated_db):
        at, translate_calls = _run_find_a_title(monkeypatch, isolated_db)
        assert len(translate_calls) == 1

        [query_box] = [t for t in at.text_input if t.key == "find_query"]
        query_box.set_value("a different english title")
        at.run(timeout=30)

        assert translate_calls == ["some english title", "a different english title"]

    def test_uses_the_chosen_engine_not_a_hardcoded_claude_key(self, monkeypatch, isolated_db):
        """Step 17 item 3: previously ignored the user's engine choice
        entirely and only ever worked via a hard-coded `settings_claude`
        lookup. Picking Ollama (no key needed) must still translate."""
        from streamlit.testing.v1 import AppTest

        translate_calls = []

        def fake_translate_query_to_zh(query, engine):
            translate_calls.append(query)
            return "翻译后的标题"
        monkeypatch.setattr(title_library, "translate_query_to_zh", fake_translate_query_to_zh)

        def fake_get_engine(name, api_key=None, model=None, free_tier=False, base_url=None):
            return _LLMEngine()
        monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)

        def _render():
            import tabs.discover_tab as dt
            dt.render_discover_tab()

        at = AppTest.from_function(_render)
        at.run(timeout=30)
        [engine_picker] = [s for s in at.selectbox if s.key == "discover_engine"]
        engine_picker.set_value("ollama")
        at.run(timeout=30)
        [query_box] = [t for t in at.text_input if t.key == "find_query"]
        query_box.set_value("some english title")
        at.run(timeout=30)

        assert not at.exception
        assert translate_calls == ["some english title"]


PAGE = """
<html><head><style>.x{}</style><script>var a = "not a label";</script></head>
<body>
  <div><a href="/">首页</a> <a href="/ranking">排行榜</a></div>
  <h1>广播剧</h1>
  <button>搜索</button>
  <a href="/">首页</a>
  <p>This long paragraph body text is not a navigation label at all.</p>
</body></html>
"""


@pytest.fixture
def fake_page(monkeypatch):
    pytest.importorskip("bs4")
    fetched = []

    def fake_fetch_static(url, timeout=20):
        fetched.append((url, timeout))
        return PAGE, ""
    monkeypatch.setattr(page_fetch, "fetch_static", fake_fetch_static)
    return fetched


def _fake_llm(monkeypatch, label_answer):
    """call_llm_json stand-in: the label-translation prompt gets
    label_answer(prompt), the steps prompt gets fixed steps text."""
    prompts = []

    def fake_call(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
        prompts.append(prompt)
        if "UI labels" in prompt:
            return label_answer(prompt)
        return "1. Click 广播剧 (Audio dramas).\n2. Use 搜索 (Search)."
    monkeypatch.setattr(translate_engines, "call_llm_json", fake_call)
    return prompts


class TestSiteNavigationHelperButton:
    """The folded-in Navigator section (Step 17) -- formerly
    tabs/navigator_tab.py's TestNavigatorButton in test_navigator.py,
    updated for the shared discover_engine/discover_api_key picker."""

    def _run(self, monkeypatch, engine_name, session=None):
        from streamlit.testing.v1 import AppTest
        got = {}

        def fake_get_engine(name, api_key=None, model=None, free_tier=False, base_url=None):
            got.update(name=name, api_key=api_key, free_tier=free_tier, base_url=base_url)
            return _LLMEngine()
        monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)

        def _render():
            import tabs.discover_tab as dt
            dt.render_discover_tab()

        at = AppTest.from_function(_render)
        for k, v in (session or {}).items():
            at.session_state[k] = v
        at.run(timeout=30)
        [engine_picker] = [s for s in at.selectbox if s.key == "discover_engine"]
        engine_picker.set_value(engine_name)
        [url_box] = [t for t in at.text_input if t.key == "nav_url"]
        url_box.set_value("https://example.com")
        [goal_box] = [t for t in at.text_area if t.key == "nav_goal"]
        goal_box.set_value("find the audio drama section")
        [key_box] = [t for t in at.text_input if t.key == "discover_api_key"]
        key_box.set_value("test-key")
        at.run(timeout=30)
        [button] = [b for b in at.button if b.label == "🧭 Translate page + get navigation steps"]
        button.click().run(timeout=30)
        return at, got

    def test_runs_end_to_end(self, monkeypatch, isolated_db, fake_page):
        def answer(prompt):
            return json.dumps({"1": "Home", "2": "Ranking", "3": "Audio dramas", "4": "Search"})
        _fake_llm(monkeypatch, answer)
        at, got = self._run(monkeypatch, "claude")
        assert not at.exception
        assert not at.error, [e.value for e in at.error]
        assert any("1. Click 广播剧" in m.value for m in at.markdown)
        assert any(c.value == "广播剧 → Audio dramas" for c in at.caption)
        assert got["name"] == "claude" and got["api_key"] == "test-key"

    def test_passes_gemini_free_tier(self, monkeypatch, isolated_db, fake_page):
        _fake_llm(monkeypatch, lambda p: "{}")
        _, got = self._run(monkeypatch, "gemini", {"gemini_free_tier": True})
        assert got["free_tier"] is True

    def test_passes_ollama_url(self, monkeypatch, isolated_db, fake_page):
        _fake_llm(monkeypatch, lambda p: "{}")
        _, got = self._run(monkeypatch, "ollama",
                           {"settings_ollama_url": "http://gpu-box:11434"})
        assert got["base_url"] == "http://gpu-box:11434"

    def test_button_enabled_with_ollama_and_no_api_key(self, monkeypatch, isolated_db, fake_page):
        """Step 25u: Ollama needs no key, so the button shouldn't stay
        disabled on "an API key" just because none was typed in."""
        _fake_llm(monkeypatch, lambda p: "{}")

        def fake_get_engine(name, api_key=None, model=None, free_tier=False, base_url=None):
            return _LLMEngine()
        monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)

        def _render():
            import tabs.discover_tab as dt
            dt.render_discover_tab()

        from streamlit.testing.v1 import AppTest
        at = AppTest.from_function(_render)
        at.run(timeout=30)
        [engine_picker] = [s for s in at.selectbox if s.key == "discover_engine"]
        engine_picker.set_value("ollama")
        [url_box] = [t for t in at.text_input if t.key == "nav_url"]
        url_box.set_value("https://example.com")
        [goal_box] = [t for t in at.text_area if t.key == "nav_goal"]
        goal_box.set_value("find the audio drama section")
        at.run(timeout=30)
        [key_box] = [t for t in at.text_input if t.key == "discover_api_key"]
        assert key_box.value in (None, "")
        [button] = [b for b in at.button if b.label == "🧭 Translate page + get navigation steps"]
        assert not button.disabled
        button.click().run(timeout=30)
        assert not at.exception
        assert not at.error, [e.value for e in at.error]


def _render_discover():
    def _render():
        import tabs.discover_tab as dt
        dt.render_discover_tab()
    from streamlit.testing.v1 import AppTest
    return AppTest.from_function(_render)


class TestDiscoverConsolidation:
    """Step 17 item 3's own exit conditions: one shared engine/key
    picker (not four), one empty-library notice (not two), and an
    idempotent "Import to my Library" (not a duplicate drama per click)."""

    def test_only_one_engine_and_key_picker_on_the_page(self, isolated_db):
        at = _render_discover()
        at.run(timeout=30)
        assert not at.exception
        assert [s.key for s in at.selectbox if s.key and "engine" in s.key] == ["discover_engine"]
        assert [t.key for t in at.text_input if t.key and "api_key" in t.key] == ["discover_api_key"]

    def test_empty_library_shows_exactly_one_notice(self, isolated_db):
        at = _render_discover()
        at.run(timeout=30)
        assert not at.exception
        empty_notices = [i for i in at.info if "empty" in i.value.lower()]
        assert len(empty_notices) == 1

    def test_import_to_library_is_idempotent(self, isolated_db):
        db.create_known_title(title_original="女将军", title_en="The General",
                               author="Someone", language="zh", media_type="audio_drama")

        at = _render_discover()
        at.run(timeout=30)
        [button] = [b for b in at.button if b.label == "➕ Import to my Library"]
        button.click().run(timeout=30)
        assert not at.exception
        assert len(db.list_dramas()) == 1

        # A fresh render of the same (now-imported) known title should
        # show "already imported" instead of offering the button again.
        at2 = _render_discover()
        at2.run(timeout=30)
        assert not any(b.label == "➕ Import to my Library" for b in at2.button)
        assert any("Already in your Library" in c.value for c in at2.caption)


class TestBulkImportUrlPatternGenerator:
    """Step 17 item 3: bulk_import.paginate_urls existed but nothing in
    the UI called it -- the bulk-import box made the user paste every
    page URL by hand. "Generate URLs" now fills the page-URL box from a
    {page}-pattern + a start/end range."""

    def test_generates_urls_into_the_bulk_urls_box(self, isolated_db):
        at = _render_discover()
        at.run(timeout=30)
        at.text_input(key="bulk_url_pattern").set_value(
            "https://www.jjwxc.net/tag.php?tag=baihe&page={page}")
        at.number_input(key="bulk_pattern_start").set_value(2)
        at.number_input(key="bulk_pattern_end").set_value(3)
        [button] = [b for b in at.button if b.label == "Generate URLs"]
        button.click().run(timeout=30)
        assert not at.exception
        assert at.text_area(key="bulk_urls").value == (
            "https://www.jjwxc.net/tag.php?tag=baihe&page=2\n"
            "https://www.jjwxc.net/tag.php?tag=baihe&page=3"
        )
