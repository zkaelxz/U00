"""
tests/test_media_playback_service.py -- Review & edit's playback helpers in
services/media_playback_service.py (moved out of tabs/workspace_tab.py): the
jump box's timestamp parser, the burned-subtitle preview's ASS text and ffmpeg
call, and the per-line audio clip. No Streamlit or tabs import.
"""
import os

import pytest

import core as core_module
import subtitle_formats
import video_export as ve
from core import Line
from services.media_playback_service import burn_preview_ass, line_audio_clip, parse_timestamp

LINES = [Line(idx=0, start=0.0, end=2.0, zh="你好", en="Hello"),
         Line(idx=1, start=12.5, end=15.25, zh="再见", en="Goodbye"),
         Line(idx=2, start=83.0, end=86.0, zh="砰", en="door slams", sfx=True)]

SFX_LINES = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A"),
             Line(idx=1, start=1.0, end=2.0, zh="砰", en="door slams", sfx=True, speaker="A")]


# ------------------------------------------------------------ jump box parsing

class TestParseTimestamp:
    @pytest.mark.parametrize("text,seconds", [
        ("83", 83.0), ("83.5", 83.5), ("0", 0.0), (" 12 ", 12.0),
        ("1:23", 83.0), ("01:23", 83.0), ("1:23.5", 83.5), ("0:05", 5.0),
        ("1:02:03", 3723.0), ("90:00", 5400.0),
    ])
    def test_accepts_mm_ss_and_raw_seconds(self, text, seconds):
        assert parse_timestamp(text) == seconds

    @pytest.mark.parametrize("text", ["", "   ", "abc", "1:75", "1:60", "-5", "1:-5",
                                      "1:2:3:4", ":30", "1:", "1:02:75"])
    def test_rejects_anything_else(self, text):
        assert parse_timestamp(text) is None


# ------------------------------------------------------- burned preview clip

class TestBurnPreview:
    STYLE = {"style": {**subtitle_formats.ASS_PRESETS["Clean"], "font": "Segoe UI", "size": 41,
                       "primary": "#FF0000"}}

    def test_clip_is_padded_around_the_line_and_retimed(self):
        start, end, ass = burn_preview_ass(LINES, LINES[1], self.STYLE)
        assert (start, end) == (10.5, 17.25)
        dialogue = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
        assert len(dialogue) == 1
        assert dialogue[0].startswith("Dialogue: 0,0:00:02.00,0:00:04.75,")  # clip-relative
        assert dialogue[0].endswith(",Goodbye")

    def test_captions_use_the_current_style_settings(self):
        _, _, ass = burn_preview_ass(LINES, LINES[1], self.STYLE)
        default = next(l for l in ass.splitlines() if l.startswith("Style: Default,"))
        fields = default.split(",")
        assert fields[1] == "Segoe UI" and fields[2] == "41"
        assert fields[3] == subtitle_formats.ass_color("#FF0000")

    def test_falls_back_to_clean_when_export_section_not_rendered(self):
        _, _, ass = burn_preview_ass(LINES, LINES[1], None)
        default = next(l for l in ass.splitlines() if l.startswith("Style: Default,"))
        assert default.split(",")[2] == str(subtitle_formats.ASS_PRESETS["Clean"]["size"])

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
        audio = line_audio_clip("/fake/audio.wav", 12.5, 14.25, str(tmp_path))
        assert calls == [("/fake/audio.wav", 12.5, 14.25)]
        assert audio == b"RIFF12.5-14.25"
        assert not os.path.exists(tmp_path / "_play_slice.wav")

    def test_temp_slice_is_removed_even_when_reading_fails(self, monkeypatch, tmp_path):
        def failing(audio_path, start, end, out_path):
            open(out_path, "wb").close()
            raise RuntimeError("ffmpeg failed")
        monkeypatch.setattr(core_module, "extract_audio_slice", failing)
        with pytest.raises(RuntimeError):
            line_audio_clip("/fake/audio.wav", 0, 1, str(tmp_path))
        assert not os.path.exists(tmp_path / "_play_slice.wav")
