"""
tests/test_video_export.py -- ffmpeg is
never actually run (subprocess.run is monkeypatched, the same pattern
test_export_formats.py already uses) -- these
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
import time

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


class TestOtherFfmpegTimeouts:
    def test_m4b_fallback_has_a_timeout(self, tmp_path, monkeypatch):
        import subprocess
        import dub
        import dub_narration
        seen = []

        def fake_run(cmd, **k):
            seen.append(k.get("timeout"))
        monkeypatch.setattr(subprocess, "run", fake_run)
        (tmp_path / "narration_track.wav").write_bytes(b"x")
        dub_narration.export_narration_m4b([], str(tmp_path))
        assert seen == [dub.M4B_ENCODE_TIMEOUT_SECONDS]

    def test_core_extract_audio_from_video_has_a_timeout(self, monkeypatch):
        import subprocess
        import audio_extract
        seen = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.append(k.get("timeout")))
        audio_extract.extract_audio_from_video("v.mp4", "a.wav")
        assert seen == [audio_extract.EXTRACT_AUDIO_TIMEOUT_SECONDS]


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

    def test_ass_track_is_copied_as_ass_into_mkv(self):
        cmd = ve.mux_soft_subtitles_cmd("v.mp4", "s.ass", "out.mkv", sub_format="ass")
        assert cmd[cmd.index("-c:s") + 1] == "ass" and cmd[cmd.index("-i", cmd.index("-i") + 1) + 1] == "s.ass"
        assert cmd[cmd.index("-c:v") + 1] == "copy" and cmd[cmd.index("-c:a") + 1] == "copy"
        i = cmd.index("-i", cmd.index("-i") + 1)
        assert cmd[i - 4:i] == ve.local_input("ass")

    def test_ass_track_refuses_mp4(self):
        with pytest.raises(ValueError):
            ve.mux_soft_subtitles_cmd("v.mp4", "s.ass", "out.mp4", sub_format="ass")

    def test_srt_stays_the_default(self):
        cmd = ve.mux_soft_subtitles_cmd("v.mp4", "s.srt", "out.mp4")
        assert cmd[cmd.index("-c:s") + 1] == "mov_text"

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
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nhi\n", encoding="utf-8")
        subprocess.run(ve.mux_soft_subtitles_cmd(str(src), str(srt), out),
                       check=True, capture_output=True, timeout=60)
        assert os.path.getsize(out) > 0


def test_a_video_with_no_audio_track_muxes(tmp_path):
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    src = tmp_path / "silent.mkv"
    made = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                           "testsrc=d=1:s=64x64:r=10", str(src)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("ffmpeg could not make the clip")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nhi\n", encoding="utf-8")
    out = str(tmp_path / "out.mkv")
    subprocess.run(ve.mux_soft_subtitles_cmd(str(src), str(srt), out),
                   check=True, capture_output=True, timeout=60)
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
    # Types a library from before the upload extension check can hold.
    ".avi": _PICTURE + _TONE + ["-c:v", "mpeg4", "-c:a", "aac"],
    ".wmv": _PICTURE + _TONE + ["-c:v", "wmv2", "-c:a", "wmav2"],
    ".mpg": _PICTURE + _TONE + ["-c:v", "mpeg2video", "-c:a", "mp2"],
    ".aiff": _TONE + ["-c:a", "pcm_s16be"],
    ".w64": _TONE + ["-c:a", "pcm_s16le"],
    ".caf": _TONE + ["-c:a", "pcm_s16le"],
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
@pytest.mark.parametrize("kind", [".mp4", ".mkv", ".mov", ".webm", ".avi"])
def test_video_exports_read_supported_inputs(tmp_path, kind):
    """Soft subtitles and the dub track (replaced and mixed), on each accepted
    video type and a legacy .avi source."""
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


@_needs_ffmpeg
@pytest.mark.parametrize("kind", [".mp4", ".mkv", ".mov", ".webm", ".avi"])
def test_burned_in_preview_reads_supported_inputs(tmp_path, kind):
    video = _sample(tmp_path, kind)
    if " subtitles " not in subprocess.run(["ffmpeg", "-hide_banner", "-filters"],
                                           capture_output=True, text=True, timeout=30).stdout:
        pytest.skip("this ffmpeg has no subtitles filter (the preview was not tried)")
    if " libx264 " not in _encoders():
        pytest.skip("this ffmpeg has no libx264 encoder (the preview was not tried)")
    out = str(tmp_path / "p.mp4")
    ve.render_preview_clip(video, "[Script Info]\n", out, 0.0, 0.5)
    assert os.path.getsize(out) > 0


def test_the_local_format_whitelist_names_no_playlist_or_network_format():
    names = set(ve.LOCAL_MEDIA_FORMATS.split(","))
    risky = {"hls", "dash", "concat", "ffconcat", "imf", "image2", "lavfi", "sdp",
             "rtp", "rtsp", "webvtt", "vobsub"}
    assert not names & risky


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
    cmd = url_media_service._extract_cmd(str(fake), str(tmp_path / "o.wav"))
    at = cmd.index("-format_whitelist")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        assert listener.connections == 0
        assert b"Format not on whitelist" in result.stderr and result.returncode != 0
        # The control: the same command without the format whitelist connects.
        subprocess.run(cmd[:at] + cmd[at + 2:], capture_output=True, timeout=30, check=False)
        deadline = time.monotonic() + 5
        while listener.connections == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert listener.connections > 0
    finally:
        listener.close()


def test_dub_mix_does_not_normalize_and_limits_without_makeup_gain(tmp_path):
    cmd = ve.replace_audio_with_dub_cmd("in.mp4", "dub.wav", str(tmp_path / "m.mkv"), -20)
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "amix=inputs=2:duration=first:normalize=0" in graph
    assert "alimiter=limit=0.977:level=0" in graph
    assert graph.index("amix") < graph.index("alimiter")


class TestBurnInNeverNeedsNvenc:
    """A card without NVENC (a Tesla V100) must export on the CPU, so no
    burn-in command may name a hardware encoder or decoder."""

    def test_no_burn_in_command_names_a_hardware_codec(self, monkeypatch):
        cmds = []
        monkeypatch.setattr(ve.subprocess, "run", lambda cmd, **k: cmds.append(cmd))
        ve.render_preview_clip("in.mp4", "[ASS]", "out.mp4", 0.0, 5.0)
        assert len(cmds) == 1
        flat = " ".join(" ".join(c) for c in cmds)
        assert "nvenc" not in flat and "cuda" not in flat and "-hwaccel" not in flat
        assert "libx264" in cmds[0]


class TestStoredMediaIsReadOnlyAsLocalMedia:
    """A stored file named .mp4 can be an HLS/DASH manifest naming network
    URLs: every ffmpeg that reads the title's media reads it through
    local_input(), right before its -i."""

    @staticmethod
    def _cmds(monkeypatch):
        cmds = []

        def run(cmd, **kw):
            cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")
        monkeypatch.setattr(subprocess, "run", run)
        return cmds

    def test_preview_clip(self, monkeypatch):
        cmds = self._cmds(monkeypatch)
        ve.render_preview_clip("in.mp4", "[ASS]", "out.mp4", 2.0, 4.0)
        assert len(cmds) == 1
        assert cmds[0][2:6] == ve.local_input(), cmds[0]

    def test_core_audio_extraction_and_slices(self, monkeypatch):
        import audio_extract
        cmds = self._cmds(monkeypatch)
        audio_extract.extract_audio_from_video("in.mp4", "out.wav")
        audio_extract.extract_audio_slice("in.m4a", 1.0, 2.0, "out.wav")
        self._assert_local(cmds, 2)

    def test_hardsub_frames(self, monkeypatch, tmp_path):
        pytest.importorskip("cv2")
        import hardsub_ocr
        cmds = self._cmds(monkeypatch)
        hardsub_ocr.extract_frames("in.mp4", str(tmp_path / "frames"))
        self._assert_local(cmds, 1)

    def test_speech_coverage_decode(self, monkeypatch):
        pytest.importorskip("numpy")
        from services import speech_coverage_service
        cmds = self._cmds(monkeypatch)
        speech_coverage_service._decode_chunk("in.mp3", 0.0, 30.0)
        self._assert_local(cmds, 1)

    @staticmethod
    def _assert_local(cmds, n):
        assert len(cmds) == n
        for cmd in cmds:
            i = cmd.index("-i")
            assert cmd[i - 4:i] == ve.local_input(), cmd
