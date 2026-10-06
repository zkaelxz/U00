"""
tests/test_video_export.py -- Step 6e: vertical/shorts export. ffmpeg is
never actually run (subprocess.run is monkeypatched, the same pattern
test_export_formats.py already uses for burn_ass/burn_subtitles) -- these
pin the constructed command and the estimate math, not real video output.
Whether a rendered clip actually plays and looks right is a manual check
(per the roadmap's own exit condition), not something a unit test can judge.
The input-whitelist tests at the end run a real ffmpeg and skip without one.
"""
import os
import shutil
import socket
import subprocess
import sys
import threading

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


class TestSoftsubContainer:
    @pytest.mark.parametrize("source,expected", [
        ("a.mp4", ".mp4"), ("a.mkv", ".mkv"), ("a.webm", ".mkv"), ("a.mov", ".mkv"),
        ("a.avi", ".mkv"), ("a.flv", ".mkv"), ("a.ts", ".mkv"),
        ("A.MP4", ".mp4"), ("A.MKV", ".mkv"), ("A.WEBM", ".mkv"), ("noext", ".mkv"),
    ])
    def test_extension_choice(self, source, expected):
        assert ve.softsub_output_extension(source) == expected

    @pytest.mark.parametrize("source,codec", [
        ("a.mp4", "mov_text"), ("a.mkv", "srt"), ("a.webm", "srt"), ("a.mov", "srt"),
        ("a.avi", "srt"), ("A.WEBM", "srt"),
    ])
    def test_command_codec_follows_chosen_container(self, source, codec):
        out = "out" + ve.softsub_output_extension(source)
        cmd = ve.mux_soft_subtitles_cmd(source, "s.srt", out)
        assert cmd[cmd.index("-c:s") + 1] == codec
        assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "copy"

    def test_webm_with_vp8_and_opus_muxes_with_real_ffmpeg(self, tmp_path):
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            pytest.skip("ffmpeg not installed")
        src = tmp_path / "t.webm"
        made = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64:r=10",
             "-f", "lavfi", "-i", "sine=d=1", "-c:v", "libvpx", "-c:a", "libopus", str(src)],
            capture_output=True)
        if made.returncode != 0:
            pytest.skip("ffmpeg lacks libvpx/libopus")
        out = str(tmp_path / ("out" + ve.softsub_output_extension(str(src))))
        ve.mux_soft_subtitles(str(src), "1\n00:00:00,000 --> 00:00:00,900\nhi\n", out)
        assert os.path.getsize(out) > 0


# ---- input whitelist: real ffmpeg on real files --------------------------

HAS_FFMPEG = shutil.which("ffmpeg") is not None
_needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")

_TONE = ["-f", "lavfi", "-i", "sine=d=1"]
_PICTURE = ["-f", "lavfi", "-i", "testsrc=d=1:s=64x64:r=10"]
# Each accepted upload/download extension, as ffmpeg writes it, plus
# mislabelled files the whitelist still has to take: MPEG-TS saved as .mp4
# and raw ADTS AAC saved as .m4a.
_SAMPLES = {
    ".mp3": _TONE + ["-c:a", "libmp3lame"],
    ".wav": _TONE + ["-c:a", "pcm_s16le"],
    ".m4a": _TONE + ["-c:a", "aac"],
    ".flac": _TONE + ["-c:a", "flac"],
    ".ogg": _TONE + ["-c:a", "libvorbis"],
    ".mp4": _PICTURE + _TONE + ["-c:v", "mpeg4", "-c:a", "aac"],
    ".mkv": _PICTURE + _TONE + ["-c:v", "mpeg4", "-c:a", "aac"],
    ".mov": _PICTURE + _TONE + ["-c:v", "mpeg4", "-c:a", "aac"],
    ".webm": _PICTURE + _TONE + ["-c:v", "libvpx", "-c:a", "libopus"],
    "ts.mp4": _PICTURE + _TONE + ["-c:v", "mpeg4", "-c:a", "aac", "-f", "mpegts"],
    "adts.m4a": _TONE + ["-c:a", "aac", "-f", "adts"],
}


def _encoders() -> str:
    return subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True,
                          text=True, timeout=30).stdout if HAS_FFMPEG else ""


def _sample(tmp_path, kind: str) -> str:
    args = _SAMPLES[kind]
    for codec in args[args.index("-c:a") + 1:][:1] + (
            [args[args.index("-c:v") + 1]] if "-c:v" in args else []):
        if f" {codec} " not in _encoders():
            pytest.skip(f"this ffmpeg has no {codec} encoder")
    path = str(tmp_path / ("sample" + kind if kind.startswith(".") else kind))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, "-t", "1", path],
                   check=True, timeout=60)
    return path


def test_every_accepted_extension_has_a_sample():
    from services.media_upload_service import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
    assert set(AUDIO_EXTENSIONS + VIDEO_EXTENSIONS) <= set(_SAMPLES)


@_needs_ffmpeg
@pytest.mark.parametrize("kind", list(_SAMPLES))
def test_audio_extraction_reads_every_supported_input(tmp_path, kind):
    """The upload and URL-import extraction command, as run on each type."""
    from services import url_media_service
    wav = str(tmp_path / "out.wav")
    subprocess.run(url_media_service._extract_cmd(_sample(tmp_path, kind), wav),
                   check=True, capture_output=True, timeout=60)
    assert os.path.getsize(wav) > 1000


@_needs_ffmpeg
@pytest.mark.parametrize("kind", [".mp3", ".flac", ".mp4", ".webm"])
def test_waveform_peaks_decode_supported_inputs(tmp_path, kind):
    from services import media_peaks_service
    pcm = media_peaks_service._decode(_sample(tmp_path, kind), 0.0, 0.5)
    assert len(pcm) > 1000


@_needs_ffmpeg
@pytest.mark.parametrize("kind", [".mp4", ".mkv", ".mov", ".webm"])
def test_video_exports_read_supported_inputs(tmp_path, kind):
    """Soft subtitles, the dub track (replaced and mixed) and the burned-in
    preview, on each accepted video type."""
    video = _sample(tmp_path, kind)
    dub = _sample(tmp_path, ".wav")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,800\nhi\n", encoding="utf-8")
    # These test the inputs: VP8 and Opus can't be stream-copied into .mp4.
    out_ext = ".mkv" if kind in (".mkv", ".webm") else ".mp4"
    runs = {
        "softsub": ve.mux_soft_subtitles_cmd(video, str(srt), str(tmp_path / f"s{out_ext}")),
        "dub": ve.replace_audio_with_dub_cmd(video, dub, str(tmp_path / "d.mkv")),
        "dub_mixed": ve.replace_audio_with_dub_cmd(video, dub, str(tmp_path / "m.mkv"), -20),
    }
    for name, cmd in runs.items():
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        assert result.returncode == 0, (name, result.stderr[-500:])
        assert os.path.getsize(cmd[-1]) > 0
    if " subtitles " in subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                       capture_output=True, text=True, timeout=30).stdout:
        out = str(tmp_path / "p.mp4")
        ve.render_preview_clip(video, "[Script Info]\n", out, 0.0, 0.5)
        assert os.path.getsize(out) > 0


class _Listener:
    """Counts connections to a loopback port."""

    def __init__(self):
        self.connections = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            conn.close()

    def close(self):
        self.sock.close()


@_needs_ffmpeg
def test_a_dash_manifest_saved_as_mp4_opens_no_connection(tmp_path):
    """ffmpeg 6.1's DASH demuxer opens http fragment URLs even under
    `-protocol_whitelist file` (checked when this was written), so the
    format whitelist is what keeps a manifest uploaded as .mp4 unread."""
    listener = _Listener()
    fake = tmp_path / "source.mp4"
    fake.write_text(
        '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" '
        'mediaPresentationDuration="PT2S" minBufferTime="PT1S" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011"><Period>'
        '<AdaptationSet mimeType="audio/mp4"><Representation id="a" bandwidth="1">'
        f"<BaseURL>http://127.0.0.1:{listener.port}/a.mp4</BaseURL></Representation>"
        "</AdaptationSet></Period></MPD>", encoding="utf-8")
    from services import url_media_service
    try:
        result = subprocess.run(url_media_service._extract_cmd(str(fake), str(tmp_path / "o.wav")),
                                capture_output=True, timeout=30)
    finally:
        listener.close()
    assert listener.connections == 0
    assert b"Format not on whitelist" in result.stderr and result.returncode != 0
