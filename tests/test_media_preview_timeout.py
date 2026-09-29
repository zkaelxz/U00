"""
tests/test_media_preview_timeout.py -- video_export.render_preview_clip's
ffmpeg timeout: a hung ffmpeg is stopped after `timeout` seconds and turned
into a fixed-text TimeoutError (no paths), so the Review "burned subtitle
preview" job (burnpreview_<id>, which blocks drama delete/restructure) can't
stay running forever. subprocess is mocked -- no ffmpeg needed.
"""
import os
import subprocess

import pytest

import video_export as ve


def test_default_timeout_is_passed_to_ffmpeg(monkeypatch):
    seen = {}
    monkeypatch.setattr(ve.subprocess, "run", lambda cmd, **kw: seen.update(kw))
    ve.render_preview_clip("/v.mp4", "[Script Info]", "/out.mp4", 1.0, 3.0)
    assert seen["timeout"] == ve.PREVIEW_CLIP_TIMEOUT_SECONDS
    assert seen["check"] is True


def test_caller_can_set_the_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(ve.subprocess, "run", lambda cmd, **kw: seen.update(kw))
    ve.render_preview_clip("/v.mp4", "x", "/out.mp4", 0, 1, timeout=7)
    assert seen["timeout"] == 7


def test_hung_ffmpeg_raises_fixed_text_and_cleans_up(monkeypatch):
    ass_paths = []

    def hung(cmd, **kw):
        ass_paths.append(cmd[cmd.index("-vf") + 1].split("'")[1].replace("\\:", ":"))
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(ve.subprocess, "run", hung)
    with pytest.raises(TimeoutError) as info:
        ve.render_preview_clip("/secret/dir/v.mp4", "x", "/secret/out.mp4", 0, 1, timeout=0.5)
    msg = str(info.value)
    assert "too long" in msg and "/secret" not in msg and "ffmpeg -y" not in msg
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert ass_paths and not os.path.exists(ass_paths[0])   # temp .ass removed


def test_other_ffmpeg_failures_are_unchanged(monkeypatch):
    def fail(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(ve.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        ve.render_preview_clip("/v.mp4", "x", "/out.mp4", 0, 1)
