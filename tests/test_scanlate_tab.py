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
