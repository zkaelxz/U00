"""
tests/test_video_export.py -- Step 6e: vertical/shorts export. ffmpeg is
never actually run (subprocess.run is monkeypatched, the same pattern
test_export_formats.py already uses for burn_ass/burn_subtitles) -- these
pin the constructed command and the estimate math, not real video output.
Whether a rendered clip actually plays and looks right is a manual check
(per the roadmap's own exit condition), not something a unit test can judge.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import video_export as ve


class TestEscapeFilterPath:
    def test_plain_path_only_escapes_colon_and_backslash(self):
        assert ve._escape_filter_path("C:\\tmp\\a.ass") == "C\\:\\\\tmp\\\\a.ass"

    def test_single_quote_cannot_break_out_of_the_quoted_filter_arg(self):
        # Wrapped as subtitles='<escaped>': a quote must be closed, escaped
        # (backslash-escaped at both ffmpeg parsing levels) and reopened.
        assert ve._escape_filter_path("/tmp/it's.ass") == "/tmp/it'\\\\\\''s.ass"
        wrapped = f"subtitles='{ve._escape_filter_path(chr(39))}'"
        assert wrapped == "subtitles=''\\\\\\'''"


class TestRenderVerticalClip:
    def _capture(self, monkeypatch):
        cmds = []
        monkeypatch.setattr(ve.subprocess, "run", lambda cmd, **k: cmds.append(cmd))
        return cmds

    def test_crops_to_9_16_centered_by_default(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        ve.render_vertical_clip("in.mp4", "[ASS]", "out.mp4", start=0.0, end=10.0)
        vf = cmds[0][cmds[0].index("-vf") + 1]
        assert vf.startswith("crop=ih*9/16:ih:(iw-ih*9/16)*0.5000:0")

    def test_crop_position_shifts_the_window(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        ve.render_vertical_clip("in.mp4", "[ASS]", "out.mp4", crop_position=0.0)
        vf = cmds[0][cmds[0].index("-vf") + 1]
        assert "(iw-ih*9/16)*0.0000" in vf

    def test_crop_position_is_clamped_to_0_1(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        ve.render_vertical_clip("in.mp4", "[ASS]", "out.mp4", crop_position=5.0)
        vf = cmds[0][cmds[0].index("-vf") + 1]
        assert "*1.0000" in vf

    def test_subtitles_are_burned_via_the_ass_file_not_force_style(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        ve.render_vertical_clip("in.mp4", "[ASS content]", "out.mp4")
        vf = cmds[0][cmds[0].index("-vf") + 1]
        assert "subtitles=" in vf
        assert "force_style" not in vf

    def test_start_and_end_become_ss_and_duration(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        ve.render_vertical_clip("in.mp4", "[ASS]", "out.mp4", start=30.0, end=45.0)
        cmd = cmds[0]
        assert cmd[cmd.index("-ss") + 1] == "30.0"
        assert cmd[cmd.index("-t") + 1] == "15.0"

    def test_no_end_renders_to_the_end_of_the_source(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        ve.render_vertical_clip("in.mp4", "[ASS]", "out.mp4", start=5.0, end=None)
        assert "-t" not in cmds[0]

    def test_ass_tempfile_is_cleaned_up_even_on_failure(self, monkeypatch, tmp_path):
        written_paths = []
        real_mkstemp = ve.tempfile.mkstemp

        def spying_mkstemp(*a, **k):
            fd, path = real_mkstemp(*a, **k, dir=str(tmp_path))
            written_paths.append(path)
            return fd, path

        def failing_run(cmd, **k):
            raise ve.subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(ve.tempfile, "mkstemp", spying_mkstemp)
        monkeypatch.setattr(ve.subprocess, "run", failing_run)
        with pytest.raises(ve.subprocess.CalledProcessError):
            ve.render_vertical_clip("in.mp4", "[ASS]", "out.mp4")
        assert written_paths and not os.path.exists(written_paths[0])


class TestFfmpegTimeouts:
    def test_full_length_exports_pass_a_finite_timeout(self, monkeypatch):
        seen = []
        monkeypatch.setattr(ve.subprocess, "run", lambda cmd, **k: seen.append(k.get("timeout")))
        ve.burn_subtitles("v.mp4", "1\n00:00:00,000 --> 00:00:01,000\nhi\n", "o.mp4")
        ve.burn_ass("v.mp4", "[Script Info]\n", "o.mp4")
        ve.mux_soft_subtitles("v.mp4", "1\n00:00:00,000 --> 00:00:01,000\nhi\n", "o.mp4")
        ve.replace_audio_with_dub("v.mp4", "d.wav", "o.mp4")
        ve.render_vertical_clip("v.mp4", "[Script Info]\n", "o.mp4", 0.0, 1.0)
        assert seen == [ve.EXPORT_TIMEOUT_SECONDS] * 5


class TestOtherFfmpegTimeouts:
    def test_dub_time_stretch_and_m4b_fallback_have_timeouts(self, tmp_path, monkeypatch):
        import subprocess
        import dub
        seen = []

        def fake_run(cmd, **k):
            seen.append(k.get("timeout"))
            if cmd[-1].endswith(".partial.wav"):
                open(cmd[-1], "wb").close()
        monkeypatch.setattr(subprocess, "run", fake_run)
        dub.time_stretch("a.wav", str(tmp_path / "o.wav"), 1.1)
        (tmp_path / "narration_track.wav").write_bytes(b"x")
        dub.export_narration_m4b([], str(tmp_path))
        assert seen == [dub.CLIP_FFMPEG_TIMEOUT_SECONDS, dub.M4B_ENCODE_TIMEOUT_SECONDS]

    def test_core_extract_audio_from_video_has_a_timeout(self, monkeypatch):
        import subprocess
        import core
        seen = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(k.get("timeout")))
        core.extract_audio_from_video("v.mp4", "a.wav")
        assert seen == [core.EXTRACT_AUDIO_TIMEOUT_SECONDS]
