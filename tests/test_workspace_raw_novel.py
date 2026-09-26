"""
tests/test_workspace_raw_novel.py -- Step 25r item 2 regression coverage:
Workspace's "Raw novel context" uploader (used to prime Whisper with the
original-language novel for an audio drama).

The real, confirmed bug: the uploader's widget key kept holding the
previously-selected file across the rerun triggered by "Remove", and the
unconditional `if raw_novel_file is not None: ... write` right above it
recreated the just-deleted raw_novel_context.txt from that still-selected
upload on the very same rerun -- so Remove looked like it worked (a
success/rerun happened) but the file never actually went away. Fixed by
gating the write behind an explicit "Save" button instead of writing on
every render.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _drama(isolated_db):
    did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                    content_mode="audio_drama", status="not started",
                                    source_language="zh")
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    return did, ddir


def _run(did):
    from streamlit.testing.v1 import AppTest

    def _render():
        import tabs.workspace_tab as wt
        wt.render_workspace_tab()

    at = AppTest.from_function(_render)
    at.session_state["active_drama_id"] = did
    at.session_state["lines"] = None
    at.run(timeout=30)
    return at


class TestRawNovelContextRemove:
    def test_remove_actually_deletes_the_file(self, isolated_db):
        did, ddir = _drama(isolated_db)
        raw_path = os.path.join(ddir, "raw_novel_context.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write("original novel text")

        at = _run(did)
        assert not at.exception

        at.checkbox(key=f"confirm_rmraw_{did}").set_value(True).run(timeout=30)
        remove_button = [b for b in at.button if b.label == "🗑️ Remove raw novel context"]
        assert remove_button, "Remove button not found -- expander may not have opened"
        remove_button[0].click().run(timeout=30)

        assert not at.exception
        assert not os.path.exists(raw_path)

    def test_remove_sticks_even_with_a_file_still_selected_in_the_uploader(self, isolated_db):
        did, ddir = _drama(isolated_db)
        raw_path = os.path.join(ddir, "raw_novel_context.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write("original novel text")

        at = _run(did)
        assert not at.exception

        uploader = at.file_uploader(key=f"raw_novel_{did}")
        uploader.upload("novel.txt", b"a different novel upload", "text/plain")
        at.run(timeout=30)
        assert not at.exception

        save_button = [b for b in at.button if b.label == "💾 Save this novel upload"]
        assert save_button, "Save button should appear once a file is selected"
        save_button[0].click().run(timeout=30)
        assert not at.exception
        with open(raw_path, "r", encoding="utf-8") as f:
            assert f.read() == "a different novel upload"

        # The uploader widget still holds the file (never explicitly cleared) --
        # this is exactly the state that used to make Remove a no-op.
        at.checkbox(key=f"confirm_rmraw_{did}").set_value(True).run(timeout=30)
        remove_button = [b for b in at.button if b.label == "🗑️ Remove raw novel context"]
        remove_button[0].click().run(timeout=30)

        assert not at.exception
        assert not os.path.exists(raw_path), (
            "Remove didn't stick -- the still-selected upload recreated the file")
