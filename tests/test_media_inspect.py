"""
tests/test_media_inspect.py -- Step 59: Media/Project Inspector.

Two layers, same split as tests/test_video_export.py's TestProbeDuration:
mocked-ffprobe-output tests for parsing/heuristics (fast, no real binary),
plus one real-ffprobe test against a tiny fixture generated on the fly with
ffmpeg (skipped if ffmpeg/ffprobe aren't on PATH) -- this is the "confirms
analyzing a real (small, test-fixture) media file returns accurate duration/
resolution/audio-track data" exit condition from the roadmap.
"""
import json
import os
import shutil
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import media_inspect as mi


def _fake_run(stdout_obj):
    def run(cmd, **kwargs):
        assert cmd[0] == "ffprobe"
        return type("Result", (), {"stdout": json.dumps(stdout_obj), "stderr": ""})()
    return run


class TestProbeMediaParsing:
    def test_video_with_audio_and_no_subtitles(self, monkeypatch):
        probe_json = {
            "format": {"duration": "125.5"},
            "streams": [
                {"index": 0, "codec_type": "video", "width": 1920, "height": 1080,
                 "r_frame_rate": "30000/1001", "tags": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
                 "tags": {"language": "jpn"}},
            ],
        }
        monkeypatch.setattr(mi.subprocess, "run", _fake_run(probe_json))
        analysis = mi.probe_media("video.mp4", filename="video.mp4")

        assert analysis.duration_seconds == pytest.approx(125.5)
        assert analysis.has_video is True
        assert analysis.width == 1920 and analysis.height == 1080
        assert analysis.fps == pytest.approx(30000 / 1001)
        assert len(analysis.audio_tracks) == 1
        assert analysis.audio_tracks[0].language == "jpn"
        assert analysis.subtitle_tracks == []

    def test_audio_only_file_reports_no_video(self, monkeypatch):
        probe_json = {
            "format": {"duration": "600.0"},
            "streams": [
                {"index": 0, "codec_type": "audio", "codec_name": "mp3", "channels": 2,
                 "tags": {"language": "und"}},
            ],
        }
        monkeypatch.setattr(mi.subprocess, "run", _fake_run(probe_json))
        analysis = mi.probe_media("audio.mp3", filename="audio.mp3")

        assert analysis.has_video is False
        assert analysis.width is None and analysis.height is None and analysis.fps is None
        # "und" (undetermined) is ffprobe's own placeholder, not a real tag --
        # must not be reported as a real detected language.
        assert analysis.audio_tracks[0].language is None

    def test_attached_cover_art_is_not_counted_as_video(self, monkeypatch):
        # A single embedded image (mp3/m4a album art) is its own "video"
        # stream in ffprobe's output -- an audio file with cover art must
        # still be reported as audio-only.
        probe_json = {
            "format": {"duration": "200.0"},
            "streams": [
                {"index": 0, "codec_type": "audio", "codec_name": "mp3", "channels": 2, "tags": {}},
                {"index": 1, "codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500,
                 "r_frame_rate": "0/0", "disposition": {"attached_pic": 1}, "tags": {}},
            ],
        }
        monkeypatch.setattr(mi.subprocess, "run", _fake_run(probe_json))
        analysis = mi.probe_media("audio.mp3", filename="audio.mp3")

        assert analysis.has_video is False
        assert analysis.width is None

    def test_embedded_subtitle_tracks_are_reported(self, monkeypatch):
        probe_json = {
            "format": {"duration": "1000.0"},
            "streams": [
                {"index": 0, "codec_type": "video", "width": 1280, "height": 720,
                 "r_frame_rate": "24/1", "tags": {}},
                {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2, "tags": {}},
                {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text",
                 "tags": {"language": "eng"}},
            ],
        }
        monkeypatch.setattr(mi.subprocess, "run", _fake_run(probe_json))
        analysis = mi.probe_media("video.mp4", filename="video.mp4")

        assert len(analysis.subtitle_tracks) == 1
        assert analysis.subtitle_tracks[0].language == "eng"
        # Existing subtitles change the suggested pipeline's first step.
        assert analysis.suggested_pipeline[0].startswith("Import existing subtitle track (eng)")

    def test_ffprobe_missing_binary_raises_probe_error(self, monkeypatch):
        def run(cmd, **kwargs):
            raise FileNotFoundError("no ffprobe")
        monkeypatch.setattr(mi.subprocess, "run", run)
        with pytest.raises(mi.ProbeError, match="ffprobe isn't installed"):
            mi.probe_media("video.mp4")

    def test_ffprobe_failure_raises_probe_error_with_stderr(self, monkeypatch):
        def run(cmd, **kwargs):
            raise subprocess.CalledProcessError(1, cmd, stderr="Invalid data found")
        monkeypatch.setattr(mi.subprocess, "run", run)
        with pytest.raises(mi.ProbeError, match="Invalid data found"):
            mi.probe_media("not_media.txt")

    def test_ffprobe_decodes_utf8_with_replacement(self, monkeypatch):
        seen = {}

        def run(cmd, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, stdout='{"format": {"tags": {"title": "日本語"}}}')
        monkeypatch.setattr(mi.subprocess, "run", run)
        assert mi.run_ffprobe("video.mp4")["format"]["tags"]["title"] == "日本語"
        assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"
        assert "text" not in seen

    def test_ffprobe_real_decode_of_non_ascii_json(self, monkeypatch):
        # A code-page decode of UTF-8 bytes is what text=True does on Windows; the
        # real subprocess module is exercised through a Python child printing UTF-8.
        payload = json.dumps({"format": {"tags": {"title": "日本語タイトル"}}}, ensure_ascii=False)
        child = [sys.executable, "-c",
                 f"import sys; sys.stdout.buffer.write({payload.encode('utf-8')!r})"]
        real_run = subprocess.run
        monkeypatch.setattr(mi.subprocess, "run", lambda cmd, **kw: real_run(child, **kw))
        assert mi.run_ffprobe("video.mp4")["format"]["tags"]["title"] == "日本語タイトル"

    @pytest.mark.parametrize("stdout", [None, ""])
    def test_ffprobe_no_output_raises_probe_error(self, monkeypatch, stdout):
        monkeypatch.setattr(mi.subprocess, "run",
                            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout=stdout))
        with pytest.raises(mi.ProbeError, match="no output"):
            mi.run_ffprobe("video.mp4")

    def test_ffprobe_invalid_json_raises_probe_error(self, monkeypatch):
        monkeypatch.setattr(mi.subprocess, "run",
                            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="not json"))
        with pytest.raises(mi.ProbeError, match="isn't valid JSON"):
            mi.run_ffprobe("video.mp4")


class TestContentTypeGuess:
    def test_filename_with_stream_keyword_guesses_streamer_vod(self):
        guess, _ = mi._guess_content_type("hololive_live_2026.mp4", True, 3600, [])
        assert guess == "streamer_vod"

    def test_asmr_filename_with_no_video_guesses_asmr(self):
        guess, _ = mi._guess_content_type("cozy asmr roleplay.mp3", False, 1800, [])
        assert guess == "asmr"

    def test_long_single_track_audio_guesses_audio_drama(self):
        track = mi.AudioTrack(index=0, codec="mp3", channels=2, language=None)
        guess, _ = mi._guess_content_type("episode_12.mp3", False, 25 * 60, [track])
        assert guess == "audio_drama"

    def test_video_file_guesses_video_drama(self):
        guess, _ = mi._guess_content_type("episode_01.mkv", True, 1200, [])
        assert guess == "video_drama"


class TestSuggestedPipeline:
    def test_no_subtitles_includes_transcribe_and_diarize(self):
        steps = mi._suggest_pipeline("video_drama", [])
        assert steps[0] == "Transcribe (Whisper)"
        assert "Diarize speakers" in steps

    def test_asmr_skips_diarization(self):
        steps = mi._suggest_pipeline("asmr", [])
        assert "Diarize speakers" not in steps

    def test_existing_subtitles_skip_transcription(self):
        sub = mi.SubtitleTrack(index=0, codec="mov_text", language="en")
        steps = mi._suggest_pipeline("video_drama", [sub])
        assert steps[0] == "Import existing subtitle track (en) instead of transcribing"
        assert "Transcribe (Whisper)" not in steps


HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


class TestRealFfprobe:
    @pytest.mark.skipif(not HAS_FFMPEG, reason="needs real ffmpeg/ffprobe")
    def test_real_video_fixture_reports_accurate_duration_resolution_fps(self, tmp_path):
        out = str(tmp_path / "fixture.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
             "-f", "lavfi", "-i", "sine=frequency=1000:duration=2",
             "-t", "2", "-pix_fmt", "yuv420p", "-shortest", out],
            check=True, capture_output=True,
        )
        analysis = mi.probe_media(out, filename="fixture.mp4")

        assert analysis.duration_seconds == pytest.approx(2.0, abs=0.2)
        assert analysis.has_video is True
        assert analysis.width == 320 and analysis.height == 240
        assert analysis.fps == pytest.approx(25.0, abs=0.1)
        assert len(analysis.audio_tracks) == 1

    @pytest.mark.skipif(not HAS_FFMPEG, reason="needs real ffmpeg/ffprobe")
    def test_real_audio_only_fixture_reports_no_video(self, tmp_path):
        wav_path = tmp_path / "fixture.wav"
        with wave.open(str(wav_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * 8000 * 3)

        analysis = mi.probe_media(str(wav_path), filename="fixture.wav")

        assert analysis.duration_seconds == pytest.approx(3.0, abs=0.1)
        assert analysis.has_video is False
        assert len(analysis.audio_tracks) == 1
        assert analysis.content_type_guess == "audio_drama"
