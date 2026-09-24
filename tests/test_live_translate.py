"""
tests/test_live_translate.py -- live_translate.py, the near-live
translation pipeline for an ongoing stream.

resolve_stream_url() is mocked at its yt-dlp boundary (no real network
call, and no real live broadcast exists to test against). Everything
else -- ffmpeg actually segmenting a continuous source into chunks,
detecting which ones are safe to read, and the offset/translate math --
is exercised for real: a local ffmpeg process reading an infinite
synthetic audio source (`-f lavfi -i "sine=..."`, which never ends, the
same way a live stream never ends) is segmented on disk and the chunk-
completion logic is run against the real files that produces. This is
what actually proves the capture -> chunk-detection -> process loop
works, not just that the code compiles.

Requires a real `ffmpeg` binary; skipped entirely if one isn't on PATH.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_translate as lt

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class TestListCompletedChunks:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert lt.list_completed_chunks(str(tmp_path / "nope"), -1) == []

    def test_chunk_with_no_successor_is_not_yet_completed(self, tmp_path):
        (tmp_path / "chunk_00000.wav").write_bytes(b"x")
        assert lt.list_completed_chunks(str(tmp_path), -1) == []

    def test_chunk_with_a_successor_is_completed(self, tmp_path):
        (tmp_path / "chunk_00000.wav").write_bytes(b"x")
        (tmp_path / "chunk_00001.wav").write_bytes(b"x")
        result = lt.list_completed_chunks(str(tmp_path), -1)
        assert result == [(0, str(tmp_path / "chunk_00000.wav"))]

    def test_only_returns_chunks_after_last_completed_index(self, tmp_path):
        for i in range(5):
            (tmp_path / f"chunk_{i:05d}.wav").write_bytes(b"x")
        result = lt.list_completed_chunks(str(tmp_path), 1)
        assert [i for i, _ in result] == [2, 3]  # 4 has no successor yet

    def test_ignores_unrelated_files(self, tmp_path):
        (tmp_path / "chunk_00000.wav").write_bytes(b"x")
        (tmp_path / "chunk_00001.wav").write_bytes(b"x")
        (tmp_path / "notes.txt").write_bytes(b"x")
        result = lt.list_completed_chunks(str(tmp_path), -1)
        assert len(result) == 1


class TestProcessChunk:
    def test_shifts_timestamps_by_chunk_offset(self, monkeypatch, tmp_path):
        import core
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 1.0, "end": 2.0, "text": "你好"}])

        class FakeEngine:
            def translate_batch(self, lines, context):
                return [f"[translated] {t}" for t in lines]

        chunk_path = tmp_path / "chunk_00003.wav"
        chunk_path.write_bytes(b"x")
        cues = lt.process_chunk(str(chunk_path), chunk_index=3, segment_seconds=20,
                                 source_language="zh", whisper_size="medium",
                                 engine=FakeEngine())

        assert cues == [{"start": 61.0, "end": 62.0, "text": "你好",
                          "translated": "[translated] 你好"}]

    def test_blank_transcribed_lines_are_dropped(self, monkeypatch, tmp_path):
        import core
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 0.0, "end": 1.0, "text": "   "}])
        chunk_path = tmp_path / "chunk_00000.wav"
        chunk_path.write_bytes(b"x")

        cues = lt.process_chunk(str(chunk_path), 0, 20, "zh", "medium", engine=None)
        assert cues == []

    def test_a_translation_failure_is_captured_per_line_not_raised(self, monkeypatch, tmp_path):
        import core
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: [{"start": 0.0, "end": 1.0, "text": "你好"}])

        class FailingEngine:
            def translate_batch(self, lines, context):
                raise RuntimeError("api down")

        chunk_path = tmp_path / "chunk_00000.wav"
        chunk_path.write_bytes(b"x")
        cues = lt.process_chunk(str(chunk_path), 0, 20, "zh", "medium",
                                 engine=FailingEngine())
        assert len(cues) == 1
        assert "translation failed" in cues[0]["translated"]

    def test_context_prompt_reaches_whisper_as_initial_prompt(self, monkeypatch, tmp_path):
        """Regression coverage for a real, previously-documented gap: each
        chunk used to be transcribed with no knowledge of what was just
        said in the previous one, so a sentence split across a chunk
        boundary had nothing to anchor its second half to."""
        import core
        captured = {}

        def fake_transcribe(path, **kw):
            captured["initial_prompt"] = kw.get("initial_prompt")
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]
        monkeypatch.setattr(core, "transcribe_for_timing", fake_transcribe)

        chunk_path = tmp_path / "chunk_00001.wav"
        chunk_path.write_bytes(b"x")
        lt.process_chunk(str(chunk_path), 1, 20, "zh", "medium", engine=None,
                          context_prompt="previous chunk's tail")

        assert captured["initial_prompt"] == "previous chunk's tail"

    def test_context_prompt_defaults_to_empty(self, monkeypatch, tmp_path):
        import core
        captured = {}
        monkeypatch.setattr(core, "transcribe_for_timing",
                             lambda path, **kw: captured.update(kw) or [])
        chunk_path = tmp_path / "chunk_00000.wav"
        chunk_path.write_bytes(b"x")
        lt.process_chunk(str(chunk_path), 0, 20, "zh", "medium", engine=None)
        assert captured["initial_prompt"] == ""


class TestStopCapture:
    def test_terminates_a_running_process(self):
        proc = subprocess.Popen(["sleep", "30"])
        lt.stop_capture(proc, timeout=5)
        assert proc.poll() is not None

    def test_a_process_that_already_exited_is_a_no_op(self):
        proc = subprocess.Popen(["true"])
        proc.wait()
        lt.stop_capture(proc)  # must not raise


class TestResolveStreamUrl:
    def test_wraps_a_yt_dlp_failure_in_a_clear_error(self, monkeypatch):
        import types
        fake_module = types.ModuleType("yt_dlp")

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                raise RuntimeError("Video unavailable")

        fake_module.YoutubeDL = FakeYDL
        monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

        with pytest.raises(lt.LiveCaptureError, match="Video unavailable"):
            lt.resolve_stream_url("https://example.com/live")

    def test_missing_url_in_info_raises_clear_error(self, monkeypatch):
        import types
        fake_module = types.ModuleType("yt_dlp")

        class FakeYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                return {"title": "a stream with no url field"}

        fake_module.YoutubeDL = FakeYDL
        monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)

        with pytest.raises(lt.LiveCaptureError, match="direct stream URL"):
            lt.resolve_stream_url("https://example.com/live")

    def _install_fake_yt_dlp_with_attempt_log(self, monkeypatch, extract_info_fn):
        import types
        fake_module = types.ModuleType("yt_dlp")
        attempts = []

        class FakeYDL:
            def __init__(self, opts):
                client = (opts.get("extractor_args", {}).get("youtube", {}).get("player_client") or [None])[0]
                attempts.append((opts["format"], client))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def extract_info(self, url, download=False):
                return extract_info_fn(attempts[-1])

        fake_module.YoutubeDL = FakeYDL
        monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)
        return attempts

    def test_no_video_formats_gives_a_specific_actionable_message_after_exhausting_all_attempts(
            self, monkeypatch):
        """Regression test for a real reported failure: yt-dlp raising
        "No video formats found!" for a URL confirmed to be a real, live
        stream, on a confirmed-current yt-dlp version -- ruling out the
        generic "link is wrong/private/region-locked" and version-skew
        explanations. This is YouTube's proof-of-origin token requirement,
        worked around by trying alternate player clients; the message
        after all of them fail should say so, not repeat the generic one."""
        def always_fail(_last_attempt):
            raise RuntimeError("No video formats found!")

        attempts = self._install_fake_yt_dlp_with_attempt_log(monkeypatch, always_fail)

        with pytest.raises(lt.LiveCaptureError, match="proof-of-origin token"):
            lt.resolve_stream_url("https://example.com/live")

        # bestaudio/best, then best, then best with each fallback client.
        assert attempts == (
            [("bestaudio/best", None), ("best", None)]
            + [("best", c) for c in lt._YOUTUBE_CLIENT_FALLBACKS]
        )

    def test_no_video_formats_recovers_via_the_best_fallback(self, monkeypatch):
        def fail_only_on_bestaudio(last_attempt):
            if last_attempt[0] == "bestaudio/best":
                raise RuntimeError("No video formats found!")
            return {"url": "https://cdn.example.com/live.m3u8"}

        attempts = self._install_fake_yt_dlp_with_attempt_log(monkeypatch, fail_only_on_bestaudio)

        result = lt.resolve_stream_url("https://example.com/live")

        assert result == "https://cdn.example.com/live.m3u8"
        assert attempts == [("bestaudio/best", None), ("best", None)]

    def test_recovers_via_a_player_client_fallback(self, monkeypatch):
        """The actual real-world case this was built for: default and
        plain "best" both fail, but requesting through an alternate
        player client (here, the second one tried) succeeds."""
        def succeed_on_second_client(last_attempt):
            if last_attempt == ("best", lt._YOUTUBE_CLIENT_FALLBACKS[1]):
                return {"url": "https://cdn.example.com/live.m3u8"}
            raise RuntimeError("No video formats found!")

        attempts = self._install_fake_yt_dlp_with_attempt_log(monkeypatch, succeed_on_second_client)

        result = lt.resolve_stream_url("https://example.com/live")

        assert result == "https://cdn.example.com/live.m3u8"
        assert attempts[-1] == ("best", lt._YOUTUBE_CLIENT_FALLBACKS[1])

    def test_a_non_format_error_fails_fast_without_trying_every_client(self, monkeypatch):
        """A private/deleted video fails identically on every attempt --
        looping through every player client would just waste time."""
        def always_private(_last_attempt):
            raise RuntimeError("Private video")

        attempts = self._install_fake_yt_dlp_with_attempt_log(monkeypatch, always_private)

        with pytest.raises(lt.LiveCaptureError, match="Common causes"):
            lt.resolve_stream_url("https://example.com/live")
        assert len(attempts) == 1


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
class TestRealCaptureEndToEnd:
    """Runs actual ffmpeg against an infinite synthetic source (a sine
    wave that never ends, the same shape as a live stream) and proves the
    capture -> chunk-detection -> cleanup loop genuinely works against
    real files on disk, not just mocked calls."""

    def test_segments_a_never_ending_source_into_completed_chunks(self, tmp_path):
        out_dir = str(tmp_path / "chunks")
        cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=1000",
               "-vn", "-ac", "1", "-ar", "16000", "-f", "segment",
               "-segment_time", "1", "-reset_timestamps", "1",
               os.path.join(out_dir, "chunk_%05d.wav")]
        os.makedirs(out_dir, exist_ok=True)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 5
            last_completed = -1
            seen = []
            while time.time() < deadline and len(seen) < 3:
                for idx, path in lt.list_completed_chunks(out_dir, last_completed):
                    assert os.path.exists(path)
                    assert os.path.getsize(path) > 0
                    seen.append(idx)
                    last_completed = idx
                time.sleep(0.2)
            assert seen == sorted(seen)
            assert len(seen) >= 2, "expected ffmpeg to have completed several chunks by now"
        finally:
            lt.stop_capture(proc)


class TestRunLiveJobContextCarrying:
    """run_live_job itself, with resolve_stream_url/capture/chunk-listing
    all mocked at their boundary -- this proves the ORCHESTRATION actually
    threads one chunk's own transcribed tail into the next chunk's call,
    which is what process_chunk's own tests can't show on their own (they
    only prove a single call reacts correctly to a context_prompt handed
    to it)."""

    def test_each_chunks_own_tail_is_carried_into_the_next_chunks_call(self, monkeypatch, job_id="test_live_carry"):
        import background_jobs
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}

        monkeypatch.setattr(lt, "resolve_stream_url", lambda url: "http://fake-stream")

        class FakeProc:
            def poll(self):
                return None  # never looks "ended" on its own -- cancel ends the loop

        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
        monkeypatch.setattr(lt, "stop_capture", lambda *a, **k: None)

        # First call (last_completed=-1) hands back chunk 0; second call
        # (last_completed=0) hands back chunk 1; after that, nothing --
        # matching list_completed_chunks' own real incremental contract.
        def fake_list_completed(out_dir, last_completed):
            if last_completed == -1:
                return [(0, "chunk_00000.wav")]
            if last_completed == 0:
                return [(1, "chunk_00001.wav")]
            return []
        monkeypatch.setattr(lt, "list_completed_chunks", fake_list_completed)

        seen_prompts = []

        def fake_process_chunk(path, idx, segment_seconds, source_language, whisper_size,
                                engine, use_gpu=False, context_prompt=""):
            seen_prompts.append(context_prompt)
            return [{"start": 0.0, "end": 1.0, "text": f"text from chunk {idx}",
                     "translated": f"translated {idx}"}]
        monkeypatch.setattr(lt, "process_chunk", fake_process_chunk)

        # Ends the loop once both chunks have been processed -- there's no
        # real stream here to naturally run dry.
        def fake_is_cancel_requested(jid):
            return len(seen_prompts) >= 2
        monkeypatch.setattr(background_jobs, "is_cancel_requested", fake_is_cancel_requested)

        lt.run_live_job(job_id, "http://example.com/live", "/fake/out", 20,
                         "zh", "medium", engine=None, poll_interval=0.01)

        assert seen_prompts[0] == ""  # no previous chunk yet
        assert seen_prompts[1] == "text from chunk 0"  # chunk 0's own tail
        background_jobs.clear_job(job_id)

