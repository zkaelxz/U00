"""
tests/test_sources_tab.py -- the Sources tab renders, and its main
paths work end to end against the offline demo source. No network.
"""

import pytest


def _render():
    import tabs.sources_tab as t
    t.render_sources_tab()


def _app(isolated_db, **session):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_function(_render)
    for k, v in session.items():
        at.session_state[k] = v
    at.run(timeout=30)
    assert not at.exception, [e.value for e in at.exception]
    return at


def _button(at, label):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"button {label!r} not found; have {[b.label for b in at.button]}"
    return matches[0]


class TestSourcesTab:
    def test_renders_with_no_dramas_and_no_sources_used(self, isolated_db, monkeypatch):
        from sources import chapter_check
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        at = _app(isolated_db)
        headings = [e.label for e in at.expander]
        assert "🚪 Paste any URL" in headings and "⚙️ Source settings" in headings

    def test_demo_series_browser_lists_sorted_chapters(self, isolated_db, monkeypatch):
        from sources import chapter_check, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        store.set_setting("demo_source_enabled", True)
        isolated_db.create_drama(title_zh="演示", media_type="manhua")
        at = _app(isolated_db, src_series=("demo", "1"))
        assert any("Demo Comic" in m.value for m in at.markdown)
        assert _button(at, "📥 Import 0 selected chapter(s)").disabled

    def test_demo_challenge_shows_the_handoff(self, isolated_db, monkeypatch):
        from sources import chapter_check, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        store.set_setting("demo_source_enabled", True)
        at = _app(isolated_db, src_fd_handoff={"url": "https://demo.invalid/img/2/1/1.png",
                                                "reason": "CLOUDFLARE_CHALLENGE"})
        assert any("Automatic challenge solving is disabled" in w.value for w in at.warning)
        assert _button(at, "🔁 Retry") and _button(at, "✖️ Cancel")

    def test_saving_settings_persists_them(self, isolated_db, monkeypatch):
        from sources import chapter_check, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        at = _app(isolated_db)
        at.number_input(key="src_set_min").set_value(2.0)
        at.number_input(key="src_set_max").set_value(4.0)
        at.selectbox(key="src_set_cache").set_value("keep_originals")
        _button(at, "💾 Save source settings").click()
        at.run(timeout=30)
        assert store.get_setting("pace_min_delay") == 2.0
        assert store.get_setting("pace_max_delay") == 4.0
        assert store.get_setting("cache_mode") == "keep_originals"

    def test_series_browser_fetches_the_chapter_list_once_across_reruns(self, isolated_db,
                                                                       monkeypatch):
        from sources import chapter_check, mock, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        store.set_setting("demo_source_enabled", True)
        calls = []
        real = mock.DemoSource.get_chapters
        monkeypatch.setattr(mock.DemoSource, "get_chapters",
                            lambda self, sid: calls.append(sid) or real(self, sid))
        isolated_db.create_drama(title_zh="演示", media_type="manhua")
        at = _app(isolated_db, src_series=("demo", "1"))
        at.run(timeout=30)
        at.run(timeout=30)
        assert calls == ["1"]
        _button(at, "🔄 Reload chapter list").click()
        at.run(timeout=30)
        assert calls == ["1", "1"]
