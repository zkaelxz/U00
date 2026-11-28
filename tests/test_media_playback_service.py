"""
tests/test_media_playback_service.py -- Review & edit's playback helpers in
services/media_playback_service.py (moved out of tabs/workspace_tab.py): the
burned-subtitle preview's ASS text and ffmpeg call. No Streamlit or tabs import.
"""
import os

import pytest

import subtitle_ass
import video_export as ve
from core import Line
from services.media_playback_service import burn_preview_ass

LINES = [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
         Line(idx=1, start=12.5, end=15.25, zh="再见", en="Goodbye"),
         Line(idx=2, start=83.0, end=86.0, zh="砰", en="door slams", sfx=True)]

SFX_LINES = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A"),
             Line(idx=1, start=1.0, end=2.0, zh="砰", en="door slams", sfx=True, speaker="A")]


# ------------------------------------------------------- burned preview clip

class TestBurnPreview:
    STYLE = {"style": {**subtitle_ass.ASS_PRESETS["Clean"], "font": "Segoe UI", "size": 41,
                       "primary": "#FF0000"}}

    def test_clip_is_padded_around_the_line_and_retimed(self):
        start, end, ass = burn_preview_ass(LINES, LINES[1], self.STYLE)
        assert (start, end) == (10.5, 17.25)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogue) == 1
        assert dialogue[0].startswith("Dialogue: 0,0:00:02.00,0:00:04.75,")  # clip-relative
        assert dialogue[0].endswith(",Goodbye")

    def test_overlapping_cues_both_stay_fully_visible(self):
        lines = [Line(idx=0, start=10.0, end=14.0, zh="a", en="One"),
                 Line(idx=1, start=12.0, end=16.0, zh="b", en="Two")]
        start, _, ass = burn_preview_ass(lines, lines[1], None)
        assert start == 10.0
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert [d.split(",")[1:3] for d in dialogue] == [["0:00:00.00", "0:00:04.00"],
                                                         ["0:00:02.00", "0:00:06.00"]]

    def test_captions_use_the_current_style_settings(self):
        _, _, ass = burn_preview_ass(LINES, LINES[1], self.STYLE)
        default = next(l for l in ass.splitlines() if l.startswith("Style: Default,"))
        fields = default.split(",")
        assert fields[1] == "Segoe UI" and fields[2] == "41"
        assert fields[3] == subtitle_ass.ass_color("#FF0000")

    def test_falls_back_to_clean_when_export_section_not_rendered(self):
        _, _, ass = burn_preview_ass(LINES, LINES[1], None)
        default = next(l for l in ass.splitlines() if l.startswith("Style: Default,"))
        assert default.split(",")[2] == str(subtitle_ass.ASS_PRESETS["Clean"]["size"])

    def test_the_export_field_is_shown_and_english_falls_back_when_missing(self):
        _, _, ass = burn_preview_ass(LINES, LINES[1], {"field": "zh"})
        assert [l for l in ass.splitlines() if l.startswith("Dialogue:")][0].endswith(",再见")
        lines = [Line(idx=0, start=0.0, end=2.0, zh="你好", en="")]
        _, _, ass = burn_preview_ass(lines, lines[0], {"field": "en"})
        assert [l for l in ass.splitlines() if l.startswith("Dialogue:")][0].endswith(",你好")

    def test_karaoke_is_timed_to_the_clip(self):
        from segment_splitting import encode_line_words
        words = [{"word": "再", "start": 12.5, "end": 13.0}, {"word": "见", "start": 13.5, "end": 15.0}]
        lines = [Line(idx=0, start=12.5, end=15.25, zh="再见", en="Goodbye",
                      word_timings=encode_line_words("再见", words))]
        clean = subtitle_ass.ASS_PRESETS["Clean"]
        rows = subtitle_ass.source_rows(clean, "zh", None, None, karaoke=True)
        _, _, ass = burn_preview_ass(lines, lines[0], {"field": "zh", "source_text": rows})
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")][0]
        assert dialogue.endswith(r",{\2a&H90&}{\kf50}再{\k50}{\kf150}见{\r}")

    def test_clip_start_never_goes_negative(self):
        start, _, _ = burn_preview_ass(LINES, LINES[0], None)
        assert start == 0.0

    def test_render_preview_clip_trims_and_burns_the_ass_file(self, monkeypatch):
        cmds = []
        monkeypatch.setattr(ve.subprocess, "run", lambda cmd, **k: cmds.append(cmd))
        ve.render_preview_clip("/v.mp4", "[Script Info]", "/out.mp4", 10.5, 17.25)
        cmd = cmds[0]
        assert cmd[cmd.index("-ss") + 1] == "10.5"
        assert float(cmd[cmd.index("-t") + 1]) == pytest.approx(6.75)
        vf = cmd[cmd.index("-vf") + 1]
        assert vf.startswith("subtitles='") and "force_style" not in vf and "crop" not in vf

    def test_ass_tempfile_is_cleaned_up_even_on_failure(self, monkeypatch):
        seen = []

        def failing(cmd, **k):
            seen.append(cmd[cmd.index("-vf") + 1].split("'")[1].replace("\\:", ":"))
            raise RuntimeError("ffmpeg failed")
        monkeypatch.setattr(ve.subprocess, "run", failing)
        with pytest.raises(RuntimeError):
            ve.render_preview_clip("/v.mp4", "x", "/out.mp4", 0, 1)
        assert seen and not os.path.exists(seen[0])

class TestSfxBurnPreview:
    def test_burned_preview_carries_the_sfx_style(self):
        _, _, ass = burn_preview_ass(SFX_LINES, SFX_LINES[1], None)
        assert ",SFX,,0,0,0,,[door slams]" in ass


# ------------------------------------------------------- per-line audio clip
