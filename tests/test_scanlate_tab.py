"""
tests/test_scanlate_tab.py -- Step 25r item 1 regression coverage:
Scanlate's "Add bubble manually" section.

Two real, confirmed bugs: (1) clicking "Add bubble" appended to a local
list that was only ever saved by a *different* button's own click branch,
so the appended bubble was discarded the moment the script run ended --
nothing persisted. (2) the whole manual-add section only rendered inside
`if bubbles:`, so a page where detection found nothing (or hasn't been
run yet) had no way to add a bubble by hand at all, despite the tab's own
copy suggesting exactly that as the recovery path.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db


def _drama_with_page(isolated_db):
    from PIL import Image as PILImage

    did = isolated_db.create_drama(title_en="Test Manga", media_type="manhua",
                                    content_mode="audio_drama", status="new")
    ddir = isolated_db.drama_dir(did)
    pages_dir = os.path.join(ddir, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    page_path = os.path.join(pages_dir, "page_0000.png")
    PILImage.new("RGB", (600, 800), "white").save(page_path)
    page_id = isolated_db.create_page(did, 0, os.path.join("pages", "page_0000.png"), 600, 800)
    return did, page_id


def _run(did):
    from streamlit.testing.v1 import AppTest

    def _render():
        import tabs.scanlate_tab as st_mod
        st_mod.render_scanlate_tab()

    at = AppTest.from_function(_render)
    at.session_state["scanlate_drama_pick"] = f"#{did} — Test Manga"
    at.run(timeout=30)
    return at


class TestAddBubbleManually:
    def test_expander_is_visible_with_zero_detected_bubbles(self, isolated_db):
        did, page_id = _drama_with_page(isolated_db)
        assert db.load_bubbles(page_id) == []
        at = _run(did)
        assert not at.exception
        labels = [e.label for e in at.expander]
        assert "➕ Add a bubble manually" in labels

    def test_clicking_add_bubble_persists_it(self, isolated_db):
        did, page_id = _drama_with_page(isolated_db)
        at = _run(did)
        assert not at.exception

        at.text_input(key="manual_source_text").set_value("你好")
        at.text_input(key="manual_text").set_value("Hello")
        at.button(key="manual_ocr_button")  # not clicked -- just confirms it exists
        add_button = [b for b in at.button if b.label == "Add bubble"]
        assert add_button, "'Add bubble' button not found"
        add_button[0].click().run(timeout=30)

        assert not at.exception
        saved = db.load_bubbles(page_id)
        assert len(saved) == 1
        assert saved[0]["source_text"] == "你好"
        assert saved[0]["translated_text"] == "Hello"

    def test_adding_a_second_bubble_keeps_the_first(self, isolated_db):
        did, page_id = _drama_with_page(isolated_db)
        db.save_bubbles(page_id, [
            {"x": 1, "y": 2, "w": 3, "h": 4, "source_text": "first", "translated_text": "first-en"},
        ])
        at = _run(did)
        assert not at.exception

        at.text_input(key="manual_source_text").set_value("second")
        at.text_input(key="manual_text").set_value("second-en")
        add_button = [b for b in at.button if b.label == "Add bubble"][0]
        add_button.click().run(timeout=30)

        assert not at.exception
        saved = db.load_bubbles(page_id)
        assert len(saved) == 2
        assert {b["source_text"] for b in saved} == {"first", "second"}


def _button(at, label):
    matches = [b for b in at.button if b.label == label]
    assert matches, f"button {label!r} not found on the page"
    return matches[0]


class TestBulkFindAndReplace:
    """Step 25o: Scanlate's bulk find & replace didn't clear the on-screen
    bubble text-area widget after Apply -- the box kept showing the
    pre-replacement text until an unrelated refresh, and a subsequent
    "Save bubble edits" from that stale widget would revert the
    just-applied replacement. sc_fr_matches also wasn't scoped by drama."""

    def _drama_with_bubble(self, isolated_db, translated_text):
        from PIL import Image as PILImage

        did = isolated_db.create_drama(title_en="Test Manga", media_type="manhua",
                                        content_mode="audio_drama", status="new")
        ddir = isolated_db.drama_dir(did)
        pages_dir = os.path.join(ddir, "pages")
        os.makedirs(pages_dir, exist_ok=True)
        page_path = os.path.join(pages_dir, "page_0000.png")
        PILImage.new("RGB", (600, 800), "white").save(page_path)
        page_id = isolated_db.create_page(did, 0, os.path.join("pages", "page_0000.png"), 600, 800)
        isolated_db.save_bubbles(page_id, [
            {"x": 1, "y": 2, "w": 3, "h": 4, "source_text": "src", "translated_text": translated_text},
        ])
        bubble_id = isolated_db.load_bubbles(page_id)[0]["id"]
        return did, page_id, bubble_id

    def test_apply_clears_the_bubble_text_widget(self, isolated_db):
        did, page_id, bubble_id = self._drama_with_bubble(isolated_db, "Hello Bob")
        at = _run(did)
        assert at.text_area(key=f"btr_{bubble_id}").value == "Hello Bob"

        at.text_input(key="sc_fr_find").set_value("Bob").run()
        at.text_input(key="sc_fr_replace").set_value("Alice").run()
        _button(at, "🔍 Preview matches").click().run()
        _button(at, "✅ Apply 1 change(s)").click().run()

        assert at.text_area(key=f"btr_{bubble_id}").value == "Hello Alice"
        assert db.load_bubbles(page_id)[0]["translated_text"] == "Hello Alice"

    def test_save_bubble_edits_after_apply_does_not_revert_it(self, isolated_db):
        did, page_id, bubble_id = self._drama_with_bubble(isolated_db, "Hello Bob")
        at = _run(did)

        at.text_input(key="sc_fr_find").set_value("Bob").run()
        at.text_input(key="sc_fr_replace").set_value("Alice").run()
        _button(at, "🔍 Preview matches").click().run()
        _button(at, "✅ Apply 1 change(s)").click().run()

        _button(at, "💾 Save bubble edits").click().run()

        assert db.load_bubbles(page_id)[0]["translated_text"] == "Hello Alice"

    def test_matches_are_scoped_by_drama(self, isolated_db):
        did_a, _, _ = self._drama_with_bubble(isolated_db, "Hello Bob")
        did_b, page_b_id, _ = self._drama_with_bubble(isolated_db, "Nothing to see here")

        at = _run(did_a)
        at.text_input(key="sc_fr_find").set_value("Bob").run()
        _button(at, "🔍 Preview matches").click().run()
        assert _button(at, "✅ Apply 1 change(s)")

        # Switching the drama picker to drama B must not carry drama A's
        # preview matches with it -- sc_fr_matches used to be a single
        # session-state key shared across every drama.
        at.session_state["scanlate_drama_pick"] = f"#{did_b} — Test Manga"
        at.run(timeout=30)
        assert not [b for b in at.button if "Apply" in b.label]
        assert db.load_bubbles(page_b_id)[0]["translated_text"] == "Nothing to see here"


class TestFontUploadDoesNotLeakAcrossDramas:
    """Step 25p: the custom-font uploader's key was scoped only by font
    category (`font_upload_{cat}`), not by drama -- Streamlit kept
    returning a previously-selected font until cleared, so uploading a
    font on drama A and switching to drama B silently created or
    overwrote B's fonts/<cat>.ttf with A's font on that render, no
    button needed."""

    class _FakeUpload:
        def __init__(self, data):
            self._data = data

        def getbuffer(self):
            return memoryview(self._data)

    def _fake_uploader(self, monkeypatch, cat="handwritten"):
        """Only fakes the given font category's uploader -- with all three
        categories rendered every time, faking every "font_upload_" key
        would let the first-rendered category consume a pending upload
        meant for another one."""
        import streamlit
        real = streamlit.file_uploader
        prefix = f"font_upload_{cat}"
        state = {"pending": None, "by_key": {}}

        def fake(label, *args, key=None, **kwargs):
            if key and key.startswith(prefix):
                if state["pending"] is not None:
                    state["by_key"][key] = state["pending"]
                    state["pending"] = None
                return state["by_key"].get(key)
            return real(label, *args, key=key, **kwargs)

        monkeypatch.setattr(streamlit, "file_uploader", fake)
        return state

    def _drama_with_bubble(self, isolated_db, translated_text="hi"):
        from PIL import Image as PILImage

        did = isolated_db.create_drama(title_en="Test Manga", media_type="manhua",
                                        content_mode="audio_drama", status="new")
        ddir = isolated_db.drama_dir(did)
        pages_dir = os.path.join(ddir, "pages")
        os.makedirs(pages_dir, exist_ok=True)
        page_path = os.path.join(pages_dir, "page_0000.png")
        PILImage.new("RGB", (600, 800), "white").save(page_path)
        page_id = isolated_db.create_page(did, 0, os.path.join("pages", "page_0000.png"), 600, 800)
        isolated_db.save_bubbles(page_id, [
            {"x": 1, "y": 2, "w": 3, "h": 4, "source_text": "src", "translated_text": translated_text},
        ])
        return did, page_id

    def _font_path(self, did, cat="handwritten"):
        return os.path.join(db.drama_dir(did), "fonts", f"{cat}.ttf")

    def test_switching_dramas_never_overwrites_the_new_dramas_font(self, isolated_db, monkeypatch):
        uploads = self._fake_uploader(monkeypatch)
        did_a, _ = self._drama_with_bubble(isolated_db, "Hello Bob")
        did_b, _ = self._drama_with_bubble(isolated_db, "Nothing to see here")
        os.makedirs(os.path.dirname(self._font_path(did_b)), exist_ok=True)
        with open(self._font_path(did_b), "wb") as f:
            f.write(b"drama B's own font")

        at = _run(did_a)
        uploads["pending"] = self._FakeUpload(b"drama A's font")
        at.run(timeout=30)

        at.session_state["scanlate_drama_pick"] = f"#{did_b} — Test Manga"
        at.run(timeout=30)

        with open(self._font_path(did_b), "rb") as f:
            assert f.read() == b"drama B's own font"
        assert not os.path.exists(self._font_path(did_a))

    def test_uploaded_font_is_only_written_after_an_explicit_save(self, isolated_db, monkeypatch):
        uploads = self._fake_uploader(monkeypatch)
        did, _ = self._drama_with_bubble(isolated_db)

        at = _run(did)
        uploads["pending"] = self._FakeUpload(b"drama A's font")
        at.run(timeout=30)
        assert not os.path.exists(self._font_path(did))

        [save] = [b for b in at.button if b.key == f"font_save_handwritten_{did}"]
        save.click().run(timeout=30)
        with open(self._font_path(did), "rb") as f:
            assert f.read() == b"drama A's font"


class TestPerPageContextDoesNotLeakAcrossPages:
    """Step 25d item 12: the "previous page" context used to be a single
    key per DRAMA, holding whichever page was processed most recently --
    not the page actually right before the one being processed.
    Re-detecting an earlier page after a later one has already run used
    to pick up the later page's context instead of its own true
    predecessor's (or "" when that predecessor was never processed)."""

    def _drama_with_pages(self, isolated_db, n=3):
        from PIL import Image as PILImage
        did = isolated_db.create_drama(title_en="Test Manga", media_type="manhua",
                                       content_mode="audio_drama", status="new")
        ddir = isolated_db.drama_dir(did)
        pages_dir = os.path.join(ddir, "pages")
        os.makedirs(pages_dir, exist_ok=True)
        for i in range(n):
            path = os.path.join(pages_dir, f"page_{i:04d}.png")
            PILImage.new("RGB", (600, 800), "white").save(path)
            isolated_db.create_page(did, i, os.path.join("pages", f"page_{i:04d}.png"), 600, 800)
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.scanlate_tab as st_mod
            st_mod.render_scanlate_tab()

        at = AppTest.from_function(_render)
        at.session_state["scanlate_drama_pick"] = f"#{did} — Test Manga"
        at.session_state["settings_claude"] = "sk-ant-fake"
        at.run(timeout=30)
        return at

    def _select_page(self, at, label):
        [sel] = [s for s in at.selectbox if s.label == "Page"]
        return sel.set_value(label).run(timeout=30)

    def _click_detect(self, at):
        [btn] = [b for b in at.button
                if b.label == "🔍 Detect bubbles + auto-clean + auto-translate"]
        return btn.click().run(timeout=30)

    def test_reprocessing_an_earlier_page_uses_its_own_predecessor_not_the_latest_touched(
            self, isolated_db, monkeypatch):
        did = self._drama_with_pages(isolated_db, n=3)

        import scanlate
        monkeypatch.setattr(
            scanlate, "detect_and_ocr_page",
            lambda *a, **k: ([{"x": 0, "y": 0, "w": 10, "h": 10, "source_text": "hi"}], []))

        calls = []

        def fake_translate(bubbles, engine, drama_meta, previous_context="", **kw):
            calls.append(previous_context)
            return f"ctx-after-call-{len(calls)}"
        monkeypatch.setattr(scanlate, "translate_page_bubbles", fake_translate)

        at = self._run(did)
        at = self._select_page(at, "Page 2")
        at = self._click_detect(at)

        at = self._select_page(at, "Page 3")
        at = self._click_detect(at)

        # Back to page 2 -- its own real predecessor (page 1) was never
        # processed, so this must still be "" again, not whatever page 3
        # (the page touched most recently) just produced.
        at = self._select_page(at, "Page 2")
        self._click_detect(at)

        assert calls[0] == ""                    # page 2, first time: no predecessor processed
        assert calls[1] == "ctx-after-call-1"     # page 3: correctly picks up page 2's context
        assert calls[2] == ""                     # page 2 again: NOT page 3's context
