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
from tabs.workspace_tab import _line_audio_clip, _unsaved_line_count


def _fake_slicer(calls):
    def fake(audio_path, start, end, out_path):
        calls.append((audio_path, start, end))
        with open(out_path, "wb") as f:
            f.write(f"RIFF{start}-{end}".encode())
        return out_path
    return fake


class TestLineAudioClip:
    def test_cuts_the_given_range_and_cleans_up(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(core_module, "extract_audio_slice", _fake_slicer(calls))
        audio = _line_audio_clip("/fake/audio.wav", 12.5, 14.25, str(tmp_path))
        assert calls == [("/fake/audio.wav", 12.5, 14.25)]
        assert audio == b"RIFF12.5-14.25"
        assert not os.path.exists(tmp_path / "_play_slice.wav")

    def test_temp_slice_is_removed_even_when_reading_fails(self, monkeypatch, tmp_path):
        def failing(audio_path, start, end, out_path):
            open(out_path, "wb").close()
            raise RuntimeError("ffmpeg failed")
        monkeypatch.setattr(core_module, "extract_audio_slice", failing)
        with pytest.raises(RuntimeError):
            _line_audio_clip("/fake/audio.wav", 0, 1, str(tmp_path))
        assert not os.path.exists(tmp_path / "_play_slice.wav")


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
