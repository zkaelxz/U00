"""Streamlit widget, AppTest and tab-source tests split out of tests/test_media_preview.py.

Delete this file together with the Streamlit tabs (docs/streamlit-retirement-plan.md
section 9, guardrail 4). The logic tests stay in tests/test_media_preview.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from streamlit.testing.v1 import AppTest
import core as core_module
from core import Line
from tabs.workspace_tab import _player_state_key


LINES = [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
         Line(idx=1, start=12.5, end=15.25, zh="再见", en="Goodbye"),
         Line(idx=2, start=83.0, end=86.0, zh="砰", en="door slams", sfx=True)]


def _player_app(media_path, kind="audio"):
    def _render(media_path, kind):
        import os
        import streamlit as st
        from tabs.workspace_tab import _render_review_player
        _render_review_player(7, os.path.dirname(media_path), (kind, media_path),
                              st.session_state.lines)

    at = AppTest.from_function(_render, args=(media_path, kind))
    at.session_state["lines"] = [Line(**{f: getattr(ln, f) for f in core_module.LINE_FIELDS})
                                 for ln in LINES]
    return at


def _player(at, kind="audio"):
    return at.get(kind)[0].proto


@pytest.fixture
def media(tmp_path):
    p = tmp_path / "audio.wav"
    p.write_bytes(b"RIFF0000WAVEfmt ")
    return str(p)


class TestPlayerTimes:
    def test_rounds_outward_to_whole_seconds(self):
        from tabs.workspace_tab import _player_times
        assert _player_times(12.5, 15.25) == (12, 16)
        assert _player_times(12.0, 15.0) == (12, 15)
        assert _player_times(83.9, None) == (83, None)
        assert _player_times(5.0, 5.0) == (5, 6)  # never a zero-length segment


class TestReviewPlayer:
    def test_starts_at_zero_without_autoplay(self, media):
        at = _player_app(media)
        at.run(timeout=30)
        p = _player(at)
        assert p.start_time == 0 and p.end_time == 0 and not p.autoplay
        assert at.button(key="rv_play_segment_7").disabled  # nothing selected yet

    @pytest.mark.parametrize("typed,expected", [("1:23", 83), ("12.5", 12)])
    def test_typed_timestamp_seeks_the_player(self, media, typed, expected):
        at = _player_app(media)
        at.run(timeout=30)
        at.text_input(key="rv_jump_7").input(typed)
        at.button[0].click()  # the form's ⏩ Jump submit button
        at.run(timeout=30)
        p = _player(at)
        assert p.start_time == expected  # Streamlit's player takes whole seconds
        assert p.end_time == 0  # a jump plays on, it isn't a segment
        assert p.autoplay

    def test_typed_timestamp_selects_the_line_it_lands_in(self, media):
        at = _player_app(media)
        at.run(timeout=30)
        at.text_input(key="rv_jump_7").input("13")
        at.button[0].click()
        at.run(timeout=30)
        assert at.session_state[_player_state_key(7)]["line_idx"] == 1
        assert not at.button(key="rv_play_segment_7").disabled

    def test_bad_timestamp_warns_and_leaves_the_player_alone(self, media):
        at = _player_app(media)
        at.run(timeout=30)
        at.text_input(key="rv_jump_7").input("1:75")
        at.button[0].click()
        at.run(timeout=30)
        assert _player(at).start_time == 0
        assert any("Couldn't read" in w.value for w in at.warning)

    def test_play_current_segment_stops_at_the_line_end(self, media):
        at = _player_app(media)
        at.session_state[_player_state_key(7)] = {"start": 12.5, "end": None, "line_idx": 1,
                                                  "n": 1}
        at.run(timeout=30)
        at.button(key="rv_play_segment_7").click()
        at.run(timeout=30)
        p = _player(at)
        # Whole seconds only, rounded outward: from 12 (just before 12.5) to
        # 16 (just after 15.25) -- the line's end, not the file's, and never
        # clipping the line's last fraction of a second.
        assert p.start_time == 12
        assert p.end_time == 16
        assert p.autoplay
        assert at.session_state[_player_state_key(7)]["end"] == 15.25

    def test_play_current_segment_uses_the_lines_edited_timing(self, media):
        at = _player_app(media)
        at.session_state[_player_state_key(7)] = {"start": 0.0, "end": None, "line_idx": 1,
                                                  "n": 1}
        at.run(timeout=30)
        at.session_state["lines"][1].end = 14.0  # edited in the end box since
        at.button(key="rv_play_segment_7").click()
        at.run(timeout=30)
        assert _player(at).end_time == 14

    def test_repeating_the_same_seek_gets_a_fresh_player(self, media):
        """Streamlit autoplays a given media element only once, and its
        identity includes its form -- so the form keyed on the seek counter
        gives a second click on the same line a new element that plays."""
        at = _player_app(media)
        at.session_state[_player_state_key(7)] = {"start": 12.5, "end": 15.25, "line_idx": 1,
                                                  "n": 1}
        at.run(timeout=30)
        at.button(key="rv_play_segment_7").click()
        at.run(timeout=30)
        id1 = _player(at).id
        at.button(key="rv_play_segment_7").click()
        at.run(timeout=30)
        p = _player(at)
        assert (p.start_time, p.end_time) == (12, 16) and p.autoplay
        assert p.id != id1

    def test_video_uses_st_video_and_offers_burned_preview(self, tmp_path):
        p = tmp_path / "ep.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        at = _player_app(str(p), kind="video")
        at.run(timeout=30)
        assert len(at.get("video")) == 1 and not at.get("audio")
        assert at.button(key="rv_burn_btn_7").disabled  # needs a selected line

    def test_audio_only_has_no_burned_preview(self, media):
        at = _player_app(media)
        at.run(timeout=30)
        assert not any(b.key == "rv_burn_btn_7" for b in at.button)


class TestRowClickSeeksInWorkspace:
    """End to end through the real Workspace tab: clicking a line's # in
    Review & edit selects it and seeks the player to its start."""

    def test_row_click_seeks_player(self, isolated_db):
        did = isolated_db.create_drama(title_en="Seek Drama", media_type="audio_drama",
                                       content_mode="audio_drama", status="translated")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"RIFF0000WAVEfmt ")
        isolated_db.update_drama(did, audio_filename="audio.wav")
        isolated_db.save_lines(did, [Line(**{f: getattr(ln, f) for f in core_module.LINE_FIELDS})
                                     for ln in LINES])

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.run(timeout=60)
        at.run(timeout=60)
        assert at.get("audio")[0].proto.start_time == 0
        at.button(key="rvseek_1").click()
        at.run(timeout=60)
        p = at.get("audio")[0].proto
        assert p.start_time == 12 and p.end_time == 0 and p.autoplay
        assert at.button(key="rvseek_1").proto.type == "primary"  # shown as selected
