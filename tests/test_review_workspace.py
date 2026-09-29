"""
tests/test_review_workspace.py -- Step 21: Review & edit's per-line audio
playback and save-status indicator.

Playback: a line's clip is cut only when its "▶️ Play this line" button is
clicked, never for every row on page load, and it covers exactly that
line's time range. Save status: compared against the database, not
st.session_state.lines (which the page's splice-back updates on every
rerun whether or not Save was clicked).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import core as core_module
from core import Line
from tabs.workspace_tab import _unsaved_line_count


def _fake_slicer(calls):
    def fake(audio_path, start, end, out_path):
        calls.append((audio_path, start, end))
        with open(out_path, "wb") as f:
            f.write(f"RIFF{start}-{end}".encode())
        return out_path
    return fake


class TestUnsavedLineCount:
    def _saved(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=1.2345, end=2.0, zh="你好", en="Hello"),
                                     Line(idx=1, start=3.0, end=4.0, zh="再见", en="Bye")])
        return did, isolated_db.load_line_objects(did)

    def test_nothing_unsaved_right_after_loading(self, isolated_db):
        did, lines = self._saved(isolated_db)
        assert _unsaved_line_count(did, lines) == 0

    def test_timing_is_compared_at_the_boxes_two_decimals(self, isolated_db):
        did, lines = self._saved(isolated_db)
        lines[0].start = 1.23  # what the start box shows for a stored 1.2345
        assert _unsaved_line_count(did, lines) == 0

    @pytest.mark.parametrize("field,value", [("start", 1.5), ("end", 2.5), ("zh", "您好"),
                                             ("en", "Hi"), ("speaker", "SPEAKER_00")])
    def test_each_editable_field_counts_as_unsaved(self, isolated_db, field, value):
        did, lines = self._saved(isolated_db)
        setattr(lines[1], field, value)
        assert _unsaved_line_count(did, lines) == 1

    def test_saved_immediately_after_save_lines(self, isolated_db):
        did, lines = self._saved(isolated_db)
        lines[0].en = "Hi there"
        lines[1].zh = "拜拜"
        assert _unsaved_line_count(did, lines) == 2
        isolated_db.save_lines(did, lines)
        assert _unsaved_line_count(did, lines) == 0

    def test_new_or_removed_lines_count_as_unsaved(self, isolated_db):
        did, lines = self._saved(isolated_db)
        assert _unsaved_line_count(did, lines[:1]) == 1
        assert _unsaved_line_count(did, lines + [Line(idx=2, start=5, end=6, zh="新")]) == 1


class TestReviewAndEditUI:
    def _drama(self, isolated_db, tmp_path=None, with_audio=True):
        did = isolated_db.create_drama(title_en="Test Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=1.0, end=2.5, zh="你好", en="Hello"),
                                     Line(idx=1, start=3.0, end=4.75, zh="再见", en="Bye")])
        if with_audio:
            ddir = isolated_db.drama_dir(did)
            os.makedirs(ddir, exist_ok=True)
            open(os.path.join(ddir, "audio.wav"), "wb").close()
            isolated_db.update_drama(did, audio_filename="audio.wav")
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _button(self, at, label, key=None):
        matches = [b for b in at.button if b.label == label and (key is None or b.key == key)]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def _status(self, at):
        return ([c.value for c in at.caption if c.value in ("⏳ Saving...", "✅ Saved")]
                + [w.value for w in at.warning if w.value.startswith("Unsaved changes")]
                + [e.value for e in at.error if e.value.startswith("Save failed")])

    def test_no_audio_is_cut_until_play_is_clicked(self, isolated_db, monkeypatch):
        calls = []
        monkeypatch.setattr(core_module, "extract_audio_slice", _fake_slicer(calls))
        did = self._drama(isolated_db)
        at = self._run(did)
        assert calls == []
        assert f"rv_clip_{did}" not in at.session_state

        self._button(at, "▶️ Play this line", key="rvplay_1").click()
        at.run(timeout=30)
        assert len(calls) == 1
        assert calls[0][1:] == (3.0, 4.75)
        assert calls[0][0].endswith("audio.wav")
        clip = at.session_state[f"rv_clip_{did}"]
        assert clip["idx"] == 1 and clip["audio"] == b"RIFF3.0-4.75"

    def test_no_play_button_without_audio(self, isolated_db):
        did = self._drama(isolated_db, with_audio=False)
        at = self._run(did)
        assert not [b for b in at.button if b.label == "▶️ Play this line"]

    def test_status_shows_saved_then_unsaved_then_saved(self, isolated_db):
        did = self._drama(isolated_db, with_audio=False)
        at = self._run(did)
        assert self._status(at) == ["✅ Saved"]

        at.text_area(key="en_0").set_value("Hi there")
        at.run(timeout=30)
        assert self._status(at) == ["Unsaved changes (1 line(s))"]

        self._button(at, "💾 Save edits (this page)").click()
        at.run(timeout=30)
        assert self._status(at) == ["✅ Saved"]
        assert isolated_db.load_lines(did)[0]["en"] == "Hi there"

    def test_unsaved_survives_the_page_widgets_going_away(self, isolated_db):
        # Moving to another page drops line 0's widgets; session state's
        # lines still hold the edit (the splice-back runs every rerun), but
        # the database doesn't -- so it must still read as unsaved.
        did = isolated_db.create_drama(title_en="Long Drama", media_type="audio_drama",
                                        content_mode="audio_drama", status="translated")
        isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 0.5, zh=f"行{i}", en=f"Line {i}")
                                     for i in range(12)])
        at = self._run(did)
        at.number_input(key="review_page_size").set_value(10)
        at.run(timeout=30)
        at.text_area(key="en_0").set_value("Edited")
        at.run(timeout=30)
        at.number_input(key="review_page").set_value(2)
        at.run(timeout=30)
        assert not [t for t in at.text_area if t.key == "en_0"]
        assert self._status(at) == ["Unsaved changes (1 line(s))"]
        at.number_input(key="review_page").set_value(1)
        at.run(timeout=30)
        assert at.text_area(key="en_0").value == "Edited"
        assert self._status(at) == ["Unsaved changes (1 line(s))"]

    def test_save_failure_is_shown(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, with_audio=False)
        at = self._run(did)

        def boom(*a, **k):
            raise RuntimeError("disk full")
        monkeypatch.setattr(isolated_db, "save_lines", boom)
        at.text_area(key="en_0").set_value("Hi there")
        self._button(at, "💾 Save edits (this page)").click()
        at.run(timeout=30)
        assert self._status(at) == ["Save failed: disk full"]


class TestLineFindAndReplace:
    """Step 23c item 2: regex-capable find & replace for a novel/
    workspace drama's translated lines, reusing scanlate.py's Scanlate-
    bubble preview/apply logic (Step 11 item 9) via its text_field param."""

    def _drama(self, isolated_db):
        did = isolated_db.create_drama(title_en="Novel Drama", media_type="novel",
                                        content_mode="novel_narration", status="translated")
        isolated_db.save_lines(did, [
            Line(idx=0, start=0, end=1, zh="", en="Hello Bob"),
            Line(idx=1, start=1, end=2, zh="", en="Bob said hi"),
            Line(idx=2, start=2, end=3, zh="", en="Nothing to see here"),
        ])
        return did

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=30)
        return at

    def _button(self, at, label):
        matches = [b for b in at.button if b.label == label]
        assert matches, f"button {label!r} not found on the page"
        return matches[0]

    def test_preview_shows_every_match_without_saving_anything(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value("Bob").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("Alice").run()
        self._button(at, "🔍 Preview matches").click().run()

        assert any("2 match(es)" in m.value for m in at.markdown)
        lines = isolated_db.load_line_objects(did)
        assert lines[0].en == "Hello Bob"  # preview only, database untouched

    def test_apply_changes_only_matched_lines_en_field(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value("Bob").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("Alice").run()
        self._button(at, "🔍 Preview matches").click().run()
        self._button(at, "✅ Apply 2 change(s)").click().run()

        lines = isolated_db.load_line_objects(did)
        assert lines[0].en == "Hello Alice"
        assert lines[1].en == "Alice said hi"
        assert lines[2].en == "Nothing to see here"  # untouched

    def test_regex_mode(self, isolated_db):
        did = isolated_db.create_drama(title_en="Regex Drama", media_type="novel",
                                        content_mode="novel_narration", status="translated")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="", en="line1 line2")])
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value(r"line\d").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("X").run()
        at.checkbox(key=f"lines_fr_regex_{did}").set_value(True).run()
        self._button(at, "🔍 Preview matches").click().run()
        self._button(at, "✅ Apply 1 change(s)").click().run()

        lines = isolated_db.load_line_objects(did)
        assert lines[0].en == "X X"

    def test_no_find_text_warns_instead_of_previewing(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        self._button(at, "🔍 Preview matches").click().run()
        assert any("Enter something to find" in w.value for w in at.warning)

    def test_invalid_regex_shows_an_error(self, isolated_db):
        did = self._drama(isolated_db)
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value("(unclosed").run()
        at.checkbox(key=f"lines_fr_regex_{did}").set_value(True).run()
        self._button(at, "🔍 Preview matches").click().run()
        assert any("Invalid find pattern" in e.value for e in at.error)

    def test_apply_refuses_a_line_hand_edited_since_preview(self, isolated_db):
        # Step 25o: Apply used to re-map preview matches onto whatever line
        # currently sits at the previewed idx, with no check that its text
        # still matches what was actually previewed -- a hand-edit made in
        # between would be silently overwritten by the stale replacement.
        did = self._drama(isolated_db)
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value("Bob").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("Alice").run()
        self._button(at, "🔍 Preview matches").click().run()

        # Hand-edit line 1's translation in the Review & edit box below,
        # simulating an edit made in between Preview and Apply.
        at.text_area(key="en_1").set_value("Bob said something else").run()

        self._button(at, "✅ Apply 2 change(s)").click().run()

        assert at.text_area(key="en_0").value == "Hello Alice"  # untouched line still applies
        assert at.text_area(key="en_1").value == "Bob said something else"  # stale match refused

    def test_apply_survives_a_merge_shifting_idx_between_preview_and_apply(self, isolated_db):
        # A merge/re-segmentation between Preview and Apply renumbers every
        # idx after the merge point -- matching by idx alone would land the
        # replacement on whatever unrelated line now sits at that position.
        did = self._drama(isolated_db)
        at = self._run(did)
        at.text_input(key=f"lines_fr_find_{did}").set_value("Bob").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("Alice").run()
        self._button(at, "🔍 Preview matches").click().run()

        original = at.session_state["lines"]
        id1, id2 = original[1].id, original[2].id
        # Simulate a merge: line 0 disappears, everything after it shifts
        # down by one idx, but each surviving line keeps its own permanent id.
        at.session_state["lines"] = [
            Line(idx=0, start=0, end=2, zh="", en=original[1].en, id=id1),
            Line(idx=1, start=2, end=3, zh="", en=original[2].en, id=id2),
        ]

        self._button(at, "✅ Apply 2 change(s)").click().run()

        by_id = {ln.id: ln.en for ln in at.session_state["lines"]}
        assert by_id[id1] == "Alice said hi"  # followed its id, not stale idx 1
        assert by_id[id2] == "Nothing to see here"  # untouched
        db_by_id = {ln.id: ln.en for ln in isolated_db.load_line_objects(did)}
        assert db_by_id[id1] == "Alice said hi"

    def test_apply_clears_the_line_widget_state(self, isolated_db):
        # Step 25o: Apply never cleared en_<idx>, so the box kept showing
        # the pre-replacement text until an unrelated refresh -- and a
        # subsequent "Save edits" from that stale display would undo the
        # replacement.
        did = self._drama(isolated_db)
        at = self._run(did)
        assert at.text_area(key="en_0").value == "Hello Bob"

        at.text_input(key=f"lines_fr_find_{did}").set_value("Bob").run()
        at.text_input(key=f"lines_fr_replace_{did}").set_value("Alice").run()
        self._button(at, "🔍 Preview matches").click().run()
        self._button(at, "✅ Apply 2 change(s)").click().run()

        assert at.text_area(key="en_0").value == "Hello Alice"
