"""
tests/test_media_preview.py -- Step 12c: Review & edit's media player
(row-click seek, typed-timestamp jump, "Play current segment"), the
burned-subtitle preview clip, and the per-line SFX/non-verbal cue marker.

The player is Streamlit's own st.audio/st.video, seeked by re-drawing it
with start_time/end_time -- so these tests drive the real widgets through
AppTest and read back what the player element was actually given.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from streamlit.testing.v1 import AppTest

import core as core_module
import subtitle_formats
from core import Line
from tabs.workspace_tab import _player_state_key


# ------------------------------------------------------------- player (AppTest)

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


# ------------------------------------------------------------- SFX cue marker

SFX_LINES = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A"),
             Line(idx=1, start=1.0, end=2.0, zh="砰", en="door slams", sfx=True, speaker="A")]


class TestSfxExport:
    def test_sfx_cue_text(self):
        assert core_module.sfx_cue_text("door slams") == "<i>[door slams]</i>"
        assert core_module.sfx_cue_text("[door slams]") == "<i>[door slams]</i>"
        assert core_module.sfx_cue_text("(sighs)", italic_tags=False) == "[sighs]"
        assert core_module.sfx_cue_text("  ") == ""

    def test_srt_brackets_and_italicises_sfx_only(self):
        srt = core_module.lines_to_srt(SFX_LINES, "en")
        assert "\nHello\n" in srt
        assert "\n<i>[door slams]</i>\n" in srt

    def test_bilingual_srt_brackets_both_languages(self):
        srt = core_module.lines_to_bilingual_srt(SFX_LINES)
        assert "<i>[door slams]</i>\n<i>[砰]</i>" in srt

    def test_vtt_brackets_and_italicises_sfx(self):
        vtt = subtitle_formats.lines_to_vtt(SFX_LINES, "en")
        assert "\nHello\n" in vtt and "\n<i>[door slams]</i>\n" in vtt

    def test_ass_uses_a_distinct_italic_sfx_style(self):
        ass = subtitle_formats.lines_to_ass(SFX_LINES, subtitle_formats.ASS_PRESETS["Clean"], "en",
                                            speaker_colors={"A": "#FFE066"})
        styles = {l.split(",")[0][7:]: l.split(",") for l in ass.splitlines()
                  if l.startswith("Style: ")}
        assert styles["SFX"][8] == "-1"                      # italic
        assert styles["SFX"][3] == subtitle_formats.ass_color(subtitle_formats.SFX_COLOR)
        assert styles["SFX"][3] != styles["Default"][3]
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert dialogue[0].split(",")[3] == "Speaker 1" and dialogue[0].endswith(",Hello")
        assert dialogue[1].split(",")[3] == "SFX"
        assert dialogue[1].split(",")[4] == ""               # no speaker name on a sound cue
        assert dialogue[1].endswith(",[door slams]")         # brackets, no <i> tags in ASS
        assert "<i>" not in ass

    def test_wrapped_sfx_is_bracketed_once(self):
        long = [Line(idx=0, start=0, end=3, zh="砰", en="a door slams somewhere far away", sfx=True)]
        vtt = subtitle_formats.lines_to_vtt(long, "en", wrap_chars={"en": 12, "zh": 10})
        assert vtt.count("[") == 1 and vtt.count("]") == 1

class TestSfxPersistence:
    def test_restoring_a_snapshot_keeps_the_mark(self):
        """Snapshots and translation versions don't store sfx -- restoring
        one mustn't silently turn a sound cue back into dialogue."""
        current = [Line(idx=0, start=0, end=1, zh="砰", en="bang", sfx=True, id=5)]
        restored = core_module.adopt_ids([Line(idx=0, start=0, end=1, zh="砰", en="door", id=5)],
                                         current)
        assert restored[0].sfx is True

    def test_resegment_split_keeps_the_mark(self):
        import resegment
        ln = Line(idx=0, start=0.0, end=4.0, zh="砰。咚。", en="", sfx=True)
        new_lines, changed = resegment.resegment_lines([ln], "zh", max_chars=1)
        assert len(new_lines) == 2 and changed
        assert all(x.sfx for x in new_lines)


class TestSfxAndNotesPositions:
    """SFX cues, and notes shown on their own line (Step 6i's toggle), can
    sit somewhere other than the dialogue in ASS."""
    NOTES = {0: [{"term": "Qijutang", "note": "lit. 'Hall of Sitting Together'"}]}

    def _ass(self, separate=True, **style_extra):
        style = {**subtitle_formats.ASS_PRESETS["Clean"], **style_extra}
        return subtitle_formats.lines_to_ass(SFX_LINES, style, "en", self.NOTES,
                                             notes_as_separate_line=separate)

    @staticmethod
    def _styles(ass):
        return {l.split(",")[0][7:]: l.split(",") for l in ass.splitlines()
                if l.startswith("Style: ")}

    def test_sfx_and_notes_get_their_own_positions(self):
        styles = self._styles(self._ass(sfx_alignment="top-center", notes_alignment="top-left"))
        assert styles["Default"][18] == "2"   # dialogue stays bottom-center
        assert styles["SFX"][18] == "8"       # top-center
        assert styles["Notes"][18] == "7"     # top-left

    def test_unset_positions_keep_the_dialogues_own(self):
        styles = self._styles(self._ass(alignment="top-right", sfx_alignment=None,
                                        notes_alignment=None))
        assert styles["SFX"][18] == styles["Notes"][18] == styles["Default"][18] == "9"

    def test_notes_position_needs_the_separate_line_toggle(self):
        """Off (Step 6i's default), notes stay inline on the dialogue cue and
        there's no Notes style to position."""
        ass = self._ass(separate=False, notes_alignment="top-left")
        assert "Notes" not in self._styles(ass)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert dialogue[0].endswith("Hello\\N[Qijutang: lit. 'Hall of Sitting Together']")

    def test_sfx_line_still_gets_its_separate_note(self):
        notes = {1: [{"term": "砰", "note": "onomatopoeia"}]}
        ass = subtitle_formats.lines_to_ass(SFX_LINES, subtitle_formats.ASS_PRESETS["Clean"],
                                            "en", notes, notes_as_separate_line=True)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert dialogue[1].split(",")[3] == "SFX" and dialogue[1].endswith(",[door slams]")
        assert dialogue[2].split(",")[3] == "Notes" and dialogue[2].endswith("[砰: onomatopoeia]")

    def test_srt_and_vtt_keep_notes_inline(self):
        assert "Hello\n[Qijutang:" in core_module.lines_to_srt(SFX_LINES, "en", self.NOTES)
        assert "Hello\n[Qijutang:" in subtitle_formats.lines_to_vtt(SFX_LINES, "en", self.NOTES)
