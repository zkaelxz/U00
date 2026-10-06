"""
tests/test_sfx_export.py -- the per-line SFX/non-verbal cue marker: how it
is exported to subtitle formats, persisted, and positioned among notes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import core as core_module
import subtitle_formats
from core import Line


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
