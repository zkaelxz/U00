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

    def test_adult_toggle_shown_only_for_sources_that_support_it(self, isolated_db, monkeypatch):
        from sources import chapter_check, store
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        store.set_setting("demo_source_enabled", True)
        at = _app(isolated_db)
        keys = [t.key for t in at.toggle]
        assert "src_adult_manhuagui" in keys and "src_adult_demo" not in keys
        at.toggle(key="src_adult_manhuagui").set_value(True)
        at.run(timeout=30)
        assert store.adult_enabled("manhuagui") and not store.adult_enabled("demo")

    def test_test_now_buttons_disabled_for_a_tos_prohibited_source(self, isolated_db, monkeypatch):
        """Step 28 gap 1: the caps used to render/enable "Test Now" came
        from load_capabilities() with no apply_terms() applied, so a
        prohibited source's buttons weren't even conditionally disabled."""
        from sources import chapter_check, registry
        from sources.base import SourceAdapter
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)

        class ProhibitedDemo(SourceAdapter):
            name = "tos_prohibited_demo"
            display_name = "ToS Prohibited Demo"
            content_types = ["manhua"]
            url_patterns = [r"tos-prohibited-demo\.invalid/"]

            def capabilities(self):
                caps = super().capabilities()
                caps.terms = {"checked": True, "tos_prohibited": True}
                return caps
        monkeypatch.setitem(registry._ADAPTERS, "tos_prohibited_demo", ProhibitedDemo)
        at = _app(isolated_db)
        at.text_input(key="src_test_url_tos_prohibited_demo").set_value(
            "https://tos-prohibited-demo.invalid/x")
        at.run(timeout=30)
        for tier in ("STATIC_HTTP", "RENDERED_BROWSER", "AUTHENTICATED_BROWSER"):
            matches = [b for b in at.button if b.key == f"src_test_tos_prohibited_demo_{tier}"]
            assert matches, f"button for {tier} not found"
            assert matches[0].disabled

    def _video_preview(self):
        from sources import front_door
        return front_door.Preview(url="https://www.youtube.com/watch?v=abc123def45",
                                  content_type=front_door.VIDEO, platform="youtube")

    def test_importing_video_into_a_drama_with_existing_audio_needs_confirmation(
            self, isolated_db, monkeypatch):
        from sources import chapter_check
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        drama_id = isolated_db.create_drama(title_zh="已有音频", media_type="streamer_vod",
                                            audio_filename="audio.wav")
        at = _app(isolated_db, src_fd_result=self._video_preview())
        picker = at.selectbox(key="src_fd_video_drama")
        assert any("⚠️ has audio" in o for o in picker.options)
        confirm = [c for c in at.checkbox if c.key == f"src_fd_video_confirm_overwrite_{drama_id}"]
        assert confirm and confirm[0].value is False
        assert _button(at, "⬇️ Import video").disabled

    def test_importing_video_into_a_fresh_drama_needs_no_confirmation(self, isolated_db,
                                                                       monkeypatch):
        from sources import chapter_check
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        isolated_db.create_drama(title_zh="空", media_type="streamer_vod")
        at = _app(isolated_db, src_fd_result=self._video_preview())
        picker = at.selectbox(key="src_fd_video_drama")
        assert not any("⚠️ has audio" in o for o in picker.options)
        assert not [c for c in at.checkbox if c.key.startswith("src_fd_video_confirm_overwrite_")]
        assert not _button(at, "⬇️ Import video").disabled

    def test_checking_the_confirmation_allows_importing_over_existing_audio(
            self, isolated_db, monkeypatch):
        from sources import chapter_check, front_door
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda *a, **k: None)
        drama_id = isolated_db.create_drama(title_zh="已有音频", media_type="streamer_vod",
                                            audio_filename="old_audio.wav")
        calls = []
        monkeypatch.setattr(front_door, "import_video",
                            lambda *a, **k: calls.append((a, k)) or "/fake/path.wav")
        at = _app(isolated_db, src_fd_result=self._video_preview())
        at.checkbox(key=f"src_fd_video_confirm_overwrite_{drama_id}").set_value(True).run(timeout=30)
        assert not _button(at, "⬇️ Import video").disabled
        _button(at, "⬇️ Import video").click().run(timeout=30)
        assert calls and calls[0][0][1] == drama_id
