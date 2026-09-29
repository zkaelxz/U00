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


class TestProbeDuration:
    def test_parses_ffprobe_output(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            assert cmd[0] == "ffprobe"
            assert "video.mp4" in cmd
            return type("Result", (), {"stdout": "123.456000\n"})()
        monkeypatch.setattr(ve.subprocess, "run", fake_run)
        assert ve.probe_duration_seconds("video.mp4") == pytest.approx(123.456)


class TestEscapeFilterPath:
    def test_plain_path_only_escapes_colon_and_backslash(self):
        assert ve._escape_filter_path("C:\\tmp\\a.ass") == "C\\:\\\\tmp\\\\a.ass"

    def test_single_quote_cannot_break_out_of_the_quoted_filter_arg(self):
        # Wrapped as subtitles='<escaped>': a quote must be closed, escaped
        # (backslash-escaped at both ffmpeg parsing levels) and reopened.
        assert ve._escape_filter_path("/tmp/it's.ass") == "/tmp/it'\\\\\\''s.ass"
        wrapped = f"subtitles='{ve._escape_filter_path(chr(39))}'"
        assert wrapped == "subtitles=''\\\\\\'''"


class TestEstimate:
    def test_estimate_scales_with_duration(self):
        short = ve.estimate_vertical_export(30)
        long = ve.estimate_vertical_export(300)
        assert short["time_note"] != long["time_note"]
        assert short["size_note"] != long["size_note"]

    def test_zero_duration_does_not_crash(self):
        est = ve.estimate_vertical_export(0)
        assert est["time_note"] and est["size_note"]
        assert est["is_long"] is False

    def test_long_selection_is_flagged(self):
        assert ve.estimate_vertical_export(19 * 60)["is_long"] is False
        assert ve.estimate_vertical_export(21 * 60)["is_long"] is True
        assert ve.estimate_vertical_export(ve.LONG_CLIP_THRESHOLD_SECONDS)["is_long"] is False

    def test_size_estimate_is_a_low_high_range_in_mb(self):
        est = ve.estimate_vertical_export(600)  # 10 minutes
        assert "MB" in est["size_note"]
        # 10 min at ~2.13-8.13 Mbps (incl. audio) -> roughly 160-610 MB
        assert "160" in est["size_note"] or "159" in est["size_note"] or "161" in est["size_note"]


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
