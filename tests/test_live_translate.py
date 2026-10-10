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

        assert cues == [{"id": 0, "start": 61.0, "end": 62.0, "text": "你好",
                          "translated": "[translated] 你好", "translation": "done"}]

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

    def _install_fake_yt_dlp_with_attempt_log(self, monkeypatch, extract_info_fn, opts_log=None):
        import types
        fake_module = types.ModuleType("yt_dlp")
        attempts = []

        class FakeYDL:
            def __init__(self, opts):
                client = (opts.get("extractor_args", {}).get("youtube", {}).get("player_client") or [None])[0]
                attempts.append((opts["format"], client))
                if opts_log is not None:
                    opts_log.append(opts)

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

    def test_js_runtimes_is_a_dict_not_a_list(self, monkeypatch):
        """Regression test: yt-dlp's real, current js_runtimes option is a
        dict of {runtime: {config}}, not a flat list -- a list raises
        "Invalid js_runtimes format" and breaks Live capture entirely."""
        opts_log = []
        self._install_fake_yt_dlp_with_attempt_log(
            monkeypatch, lambda _last: {"id": "abc", "url": "https://cdn.example/stream.m3u8"},
            opts_log=opts_log)

        lt.resolve_stream_url("https://example.com/live")

        assert opts_log[0]["js_runtimes"] == {"deno": {}, "node": {}, "bun": {}, "quickjs": {}}

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

    def test_page_needs_reloaded_triggers_the_full_retry_loop(self, monkeypatch):
        """Regression test for a real reported failure: yt-dlp's
        DownloadError "The page needs to be reloaded" doesn't contain the
        literal substring "no video formats found", so it used to skip the
        retry loop entirely and fail on the very first attempt instead of
        trying the player-client fallbacks."""
        def always_fail(_last_attempt):
            raise RuntimeError("The page needs to be reloaded")

        attempts = self._install_fake_yt_dlp_with_attempt_log(monkeypatch, always_fail)

        with pytest.raises(lt.LiveCaptureError, match="proof-of-origin token"):
            lt.resolve_stream_url("https://example.com/live")

        assert attempts == (
            [("bestaudio/best", None), ("best", None)]
            + [("best", c) for c in lt._YOUTUBE_CLIENT_FALLBACKS]
        )

    def test_a_non_format_error_fails_fast_without_trying_every_client(self, monkeypatch):
        """A private/deleted video fails identically on every attempt --
        looping through every player client would just waste time."""
        def always_private(_last_attempt):
            raise RuntimeError("Private video")

        attempts = self._install_fake_yt_dlp_with_attempt_log(monkeypatch, always_private)

        with pytest.raises(lt.LiveCaptureError, match="Common causes"):
            lt.resolve_stream_url("https://example.com/live")
        assert len(attempts) == 1

    def test_cookies_browser_reaches_yt_dlp_opts(self, monkeypatch):
        opts_log = []
        self._install_fake_yt_dlp_with_attempt_log(
            monkeypatch, lambda _last: {"id": "abc", "url": "https://cdn.example/stream.m3u8"},
            opts_log=opts_log)

        lt.resolve_stream_url("https://example.com/live", cookies_browser="chrome")

        assert opts_log[0]["cookiesfrombrowser"] == ("chrome",)

    def test_cookies_file_reaches_yt_dlp_opts(self, monkeypatch):
        opts_log = []
        self._install_fake_yt_dlp_with_attempt_log(
            monkeypatch, lambda _last: {"id": "abc", "url": "https://cdn.example/stream.m3u8"},
            opts_log=opts_log)

        lt.resolve_stream_url("https://example.com/live", cookies_file="/tmp/cookies.txt")

        assert opts_log[0]["cookiefile"] == "/tmp/cookies.txt"

    def test_no_cookies_means_no_cookie_keys(self, monkeypatch):
        opts_log = []
        self._install_fake_yt_dlp_with_attempt_log(
            monkeypatch, lambda _last: {"id": "abc", "url": "https://cdn.example/stream.m3u8"},
            opts_log=opts_log)

        lt.resolve_stream_url("https://example.com/live")

        assert "cookiesfrombrowser" not in opts_log[0]
        assert "cookiefile" not in opts_log[0]
        assert "proxy" not in opts_log[0]

    def test_proxy_reaches_yt_dlp_opts(self, monkeypatch):
        opts_log = []
        self._install_fake_yt_dlp_with_attempt_log(
            monkeypatch, lambda _last: {"id": "abc", "url": "https://cdn.example/stream.m3u8"},
            opts_log=opts_log)

        lt.resolve_stream_url("https://example.com/live", proxy="http://127.0.0.1:9")

        assert opts_log[0]["proxy"] == "http://127.0.0.1:9"


class TestGenerationHelpers:
    def test_starts_at_zero_and_increments(self):
        job_id = "test_gen_helpers"
        lt._generations.pop(job_id, None)
        assert lt.current_generation(job_id) == 0
        assert lt.bump_generation(job_id) == 1
        assert lt.current_generation(job_id) == 1
        assert lt.bump_generation(job_id) == 2

    def test_independent_per_job_id(self):
        lt._generations.pop("job_a", None)
        lt._generations.pop("job_b", None)
        lt.bump_generation("job_a")
        assert lt.current_generation("job_a") == 1
        assert lt.current_generation("job_b") == 0


class TestStaleChunkGuard:
    """Step 9b.5 exit condition: a chunk result that finishes after a
    generation bump is discarded, and one that finishes before the bump
    is applied."""

    def _setup(self, monkeypatch, job_id, chunks):
        import background_jobs
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://fake-stream")

        class FakeProc:

            error = None

            def poll(self):
                return None
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
        monkeypatch.setattr(lt, "stop_capture", lambda *a, **k: None)

        remaining = list(chunks)

        def fake_list_completed(out_dir, last_completed):
            if remaining:
                return [remaining.pop(0)]
            return []
        monkeypatch.setattr(lt, "list_completed_chunks", fake_list_completed)
        return background_jobs

    def test_a_chunk_that_finishes_before_the_bump_is_applied(self, monkeypatch):
        job_id = "test_stale_before"
        background_jobs = self._setup(monkeypatch, job_id, [(0, "chunk_00000.wav")])

        processed = []

        def fake_process_chunk(path, idx, *a, **kw):
            processed.append(idx)
            return [{"start": 0.0, "end": 1.0, "text": "hi", "translated": "hi-en"}]
        monkeypatch.setattr(lt, "process_chunk", fake_process_chunk)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: bool(processed))

        lt.run_live_job(job_id, "http://example.com/live", "/fake/out", 20, "zh", "medium",
                        engine=None, poll_interval=0.01)

        assert background_jobs.get_status(job_id)["result"] == [
            {"start": 0.0, "end": 1.0, "text": "hi", "translated": "hi-en"}]
        background_jobs.clear_job(job_id)

    def test_a_chunk_that_finishes_after_the_bump_is_discarded(self, monkeypatch):
        job_id = "test_stale_after"
        background_jobs = self._setup(monkeypatch, job_id, [(0, "chunk_00000.wav")])

        def fake_process_chunk(path, idx, *a, **kw):
            # Simulates the Stop button firing WHILE this chunk's own
            # transcribe/translate call was still running.
            lt.bump_generation(job_id)
            return [{"start": 0.0, "end": 1.0, "text": "stale", "translated": "stale-en"}]
        monkeypatch.setattr(lt, "process_chunk", fake_process_chunk)
        # Never cancels via the normal flag -- the generation bump inside
        # process_chunk (above) is the ONLY thing that ends this loop,
        # which is exactly what this test needs to actually exercise.
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: False)

        lt.run_live_job(job_id, "http://example.com/live", "/fake/out", 20, "zh", "medium",
                        engine=None, poll_interval=0.01)

        assert background_jobs.get_status(job_id)["result"] is None
        background_jobs.clear_job(job_id)

    def test_stopping_mid_batch_does_not_process_the_rest_of_the_queued_chunks(self, monkeypatch):
        """Regression coverage for the real gap this step fixes: the inner
        for-loop over list_completed_chunks had no cancel/generation check
        at all, so several already-queued chunks kept getting transcribed
        and applied even after Stop was clicked mid-batch."""
        job_id = "test_stale_batch"
        background_jobs = self._setup(
            monkeypatch, job_id,
            [(0, "chunk_00000.wav"), (1, "chunk_00001.wav"), (2, "chunk_00002.wav")])
        # All three "arrive" in the same list_completed_chunks call, like a
        # burst of chunks finishing while the loop was busy elsewhere. The
        # backlog limit is raised so none is dropped as catch-up: this test is
        # about Stop, not about skipping.
        monkeypatch.setattr(lt, "MAX_BACKLOG_CHUNKS", 3)
        monkeypatch.setattr(lt, "list_completed_chunks",
                            lambda out_dir, last_completed: [
                                (0, "chunk_00000.wav"), (1, "chunk_00001.wav"),
                                (2, "chunk_00002.wav")] if last_completed == -1 else [])

        processed = []

        def fake_process_chunk(path, idx, *a, **kw):
            processed.append(idx)
            if idx == 0:
                lt.bump_generation(job_id)  # Stop clicked right after chunk 0
            return [{"start": 0.0, "end": 1.0, "text": f"t{idx}", "translated": f"t{idx}-en"}]
        monkeypatch.setattr(lt, "process_chunk", fake_process_chunk)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: False)

        lt.run_live_job(job_id, "http://example.com/live", "/fake/out", 20, "zh", "medium",
                        engine=None, poll_interval=0.01)

        # Chunk 0 was already in flight when the bump happened (finishes
        # before, per the exit condition) -- chunks 1/2 must never even
        # start once the batch notices it's stale.
        assert processed == [0]
        assert background_jobs.get_status(job_id)["result"] is None
        background_jobs.clear_job(job_id)


class TestRunLiveJobCookiesPassthrough:
    """run_live_job forwards its cookies params straight to
    resolve_stream_url, the same as the rest of its arguments."""

    def test_cookies_reach_resolve_stream_url(self, monkeypatch, tmp_path):
        import background_jobs
        seen = {}

        def fake_resolve(url, cookies_browser=None, cookies_file=None, proxy=None):
            seen["cookies_browser"] = cookies_browser
            seen["cookies_file"] = cookies_file
            raise lt.LiveCaptureError("stop here -- only checking what was passed in")

        monkeypatch.setattr(lt, "resolve_stream_url", fake_resolve)
        job_id = "test_cookies_passthrough"
        background_jobs.clear_job(job_id)
        with pytest.raises(lt.LiveCaptureError):
            lt.run_live_job(job_id, "https://example.com/live", str(tmp_path), 20, "zh",
                            "tiny", object(), cookies_browser="firefox", cookies_file="/tmp/c.txt")
        assert seen == {"cookies_browser": "firefox", "cookies_file": "/tmp/c.txt"}


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

        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://fake-stream")

        class FakeProc:

            error = None

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
                                engine, use_gpu=False, context_prompt="", **kw):
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



# ---------------------------------------------------------------------------
# Step 9b item 6: real audio overlap between chunks + exact-match dedup.
# ---------------------------------------------------------------------------

def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class TestDedupOverlap:
    """dedup_overlap() on its own: segment timestamps are relative to the
    PADDED chunk, so everything before `overlap_seconds` is audio the
    previous chunk already covered."""

    def test_cjk_suffix_prefix_match_trims_the_repeat_and_keeps_the_rest(self):
        out = lt.dedup_overlap([_seg(0.5, 4.0, "我们明天去北京吧")], 2.0, "我们明天去")
        assert out == [_seg(2.0, 4.0, "北京吧")]

    def test_a_segment_wholly_inside_the_match_is_dropped_whole(self):
        out = lt.dedup_overlap(
            [_seg(0.0, 1.5, "then I said"), _seg(1.5, 5.0, "we should go home.")],
            2.0, "and then I said")
        assert out == [_seg(1.5, 5.0, "we should go home.")]

    def test_punctuation_and_case_differences_still_match(self):
        out = lt.dedup_overlap([_seg(0.0, 4.0, "hello world! How are you")],
                               2.0, "Hello, World.")
        assert out == [_seg(2.0, 4.0, "How are you")]

    def test_a_garbled_partial_word_at_the_overlap_start_is_skipped(self):
        # The overlap audio started mid-word, which Whisper heard as "嗯".
        out = lt.dedup_overlap([_seg(0.0, 4.0, "嗯明天去北京吧")], 2.0, "我们明天去北京")
        assert out == [_seg(2.0, 4.0, "吧")]

    def test_a_single_token_after_a_skip_is_not_treated_as_a_match(self):
        # "好" appearing two tokens in is coincidence, not the overlap --
        # trimming "我很好" here would drop real new speech.
        out = lt.dedup_overlap([_seg(1.0, 5.0, "我很好的朋友")], 2.0, "今天也好")
        assert out == [_seg(1.0, 5.0, "我很好的朋友")]

    def test_legitimate_repeats_after_the_match_are_kept(self):
        out = lt.dedup_overlap(
            [_seg(0.5, 3.0, "我们明天去北京吧"), _seg(3.5, 5.0, "好的走吧")], 2.0, "我们明天去")
        assert [s["text"] for s in out] == ["北京吧", "好的走吧"]

    def test_empty_tail_text_leaves_segments_untouched(self):
        # The previous chunk emitted nothing for this audio, so nothing
        # here can be a duplicate of it.
        segs = [_seg(0.5, 1.5, "嗯"), _seg(2.5, 4.0, "你好")]
        assert lt.dedup_overlap(segs, 2.0, "") == segs

    def test_no_overlap_leaves_segments_untouched(self):
        segs = [_seg(0.5, 1.5, "你好")]
        assert lt.dedup_overlap(segs, 0.0, "你好") == segs

    def test_no_exact_match_drops_only_segments_wholly_inside_the_overlap(self):
        segs = [_seg(0.0, 1.5, "别的话"), _seg(1.0, 4.0, "跨界的一句"), _seg(4.0, 6.0, "新内容")]
        out = lt.dedup_overlap(segs, 2.0, "完全不同")
        # The straddling line is kept whole rather than risk losing its
        # after-the-boundary half.
        assert out == [_seg(1.0, 4.0, "跨界的一句"), _seg(4.0, 6.0, "新内容")]

    def test_segments_starting_after_the_overlap_are_never_matched(self):
        # "好的" said AFTER the boundary is new speech even though the
        # previous chunk's tail also ended with "好的".
        segs = [_seg(0.2, 1.0, "嗯"), _seg(3.0, 5.0, "好的走吧")]
        out = lt.dedup_overlap(segs, 2.0, "好的")
        assert out == [_seg(3.0, 5.0, "好的走吧")]

    def test_a_trimmed_segment_that_ends_inside_the_overlap_keeps_its_start(self):
        # The previous chunk lost its very last word at the cut ("go");
        # the padded chunk rescues it. Its timing is kept as heard.
        out = lt.dedup_overlap([_seg(0.5, 1.8, "we should go"), _seg(2.5, 4.0, "now")],
                               2.0, "we should")
        assert out == [_seg(0.5, 1.8, "go"), _seg(2.5, 4.0, "now")]


def _write_wav(path, samples, rate=8000):
    import struct
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def _read_samples(path):
    import struct
    import wave
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


class TestPaddedChunkAudio:
    def test_tail_is_the_last_n_seconds_of_real_audio(self, tmp_path):
        path = tmp_path / "chunk_00000.wav"
        _write_wav(path, list(range(8000 * 4)))  # 4s, every sample distinct
        tail = lt.read_wav_tail(str(path), 1.5)
        assert tail["seconds"] == 1.5
        assert tail["chunk_seconds"] == 4.0
        assert tail["params"] == (1, 2, 8000)
        import struct
        assert list(struct.unpack(f"<{len(tail['frames']) // 2}h", tail["frames"])) == \
            list(range(8000 * 4 - 12000, 8000 * 4))

    def test_tail_longer_than_the_chunk_is_the_whole_chunk(self, tmp_path):
        path = tmp_path / "chunk_00000.wav"
        _write_wav(path, [1] * 8000)
        tail = lt.read_wav_tail(str(path), 3.0)
        assert tail["seconds"] == 1.0

    def test_unreadable_chunk_gives_no_tail(self, tmp_path):
        path = tmp_path / "chunk_00000.wav"
        path.write_bytes(b"not a wav")
        assert lt.read_wav_tail(str(path), 2.0) is None
        assert lt.read_wav_tail(str(tmp_path / "missing.wav"), 2.0) is None

    def test_padded_chunk_is_previous_tail_then_this_chunk(self, tmp_path):
        prev, cur, out = tmp_path / "chunk_00000.wav", tmp_path / "chunk_00001.wav", tmp_path / "p.wav"
        _write_wav(prev, [1] * 8000 + [2] * 8000)
        _write_wav(cur, [3] * 16000)
        pad = lt.write_padded_chunk(lt.read_wav_tail(str(prev), 1.0), str(cur), str(out))
        assert pad == 1.0
        assert _read_samples(out) == [2] * 8000 + [3] * 16000

    def test_mismatched_format_is_not_padded(self, tmp_path):
        prev, cur, out = tmp_path / "a.wav", tmp_path / "b.wav", tmp_path / "p.wav"
        _write_wav(prev, [1] * 8000, rate=8000)
        _write_wav(cur, [1] * 16000, rate=16000)
        assert lt.write_padded_chunk(lt.read_wav_tail(str(prev), 1.0), str(cur), str(out)) == 0.0
        assert not out.exists()


# Tiny synthetic "speech": each 0.5s block of real PCM audio holds one
# word, encoded as a constant sample value (code * 100); 0 is silence.
_BLOCK_SECONDS = 0.5
_RATE = 8000
_WORDS = {1: "今天", 2: "天气", 3: "很", 4: "好", 5: "我们", 6: "明天", 7: "去",
          8: "北京", 9: "吧", 10: "好的", 11: "走"}


def _write_word_chunk(path, codes):
    per_block = int(_RATE * _BLOCK_SECONDS)
    _write_wav(path, [c * 100 for c in codes for _ in range(per_block)], rate=_RATE)


def _fake_transcribe_by_decoding_audio(path, **kw):
    """Stands in for Whisper, but genuinely "listens" to whatever audio
    file it's given -- padded or not -- by decoding the block values back
    into words, grouping runs of non-silent blocks into segments."""
    samples = _read_samples(path)
    per_block = int(_RATE * _BLOCK_SECONDS)
    segments, current = [], None
    for i in range(0, len(samples), per_block):
        code = samples[i] // 100
        t = (i // per_block) * _BLOCK_SECONDS
        if code:
            if current is None:
                current = {"start": t, "end": t + _BLOCK_SECONDS, "text": ""}
                segments.append(current)
            current["text"] += _WORDS[code]
            current["end"] = t + _BLOCK_SECONDS
        else:
            current = None
    return segments


class TestOverlapDedupAgainstRealAudio:
    """Step 9b item 6 exit condition: two adjacent chunks of real WAV
    audio, with a known phrase ("我们明天去") inside the overlap window,
    run through run_live_job's own pad -> transcribe -> dedup path. The
    final transcript must contain every word exactly once."""

    def _run(self, monkeypatch, tmp_path, job_id, chunks, overlap_seconds=2):
        import background_jobs
        import core
        out_dir = tmp_path / "chunks"
        out_dir.mkdir()
        for i, codes in enumerate(chunks):
            _write_word_chunk(out_dir / f"chunk_{i:05d}.wav", codes)
        # A successor file is what marks the last real chunk "complete".
        (out_dir / f"chunk_{len(chunks):05d}.wav").write_bytes(b"")

        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://fake-stream")

        class FakeProc:

            error = None

            def poll(self):
                return None
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
        monkeypatch.setattr(lt, "stop_capture", lambda *a, **k: None)

        heard = []

        def transcribe(path, **kw):
            heard.append(os.path.basename(path))
            return _fake_transcribe_by_decoding_audio(path, **kw)
        monkeypatch.setattr(core, "transcribe_for_timing", transcribe)
        monkeypatch.setattr(background_jobs, "is_cancel_requested",
                            lambda jid: len(heard) >= len(chunks))

        class EchoEngine:
            def translate_batch(self, lines, context):
                return [f"EN:{t}" for t in lines]

        segment_seconds = len(chunks[0]) * _BLOCK_SECONDS
        lt.run_live_job(job_id, "http://example.com/live", str(out_dir), segment_seconds,
                        "zh", "tiny", EchoEngine(), poll_interval=0.01,
                        overlap_seconds=overlap_seconds)
        cues = background_jobs.get_status(job_id)["result"]
        background_jobs.clear_job(job_id)
        return cues, heard, out_dir

    # chunk 0 ends "... 我们 明天 去" (cut mid-sentence); chunk 1 carries on
    # "北京 吧 ... 好的 走 吧". The 2s overlap is chunk 0's last 4 blocks.
    CHUNKS = [[1, 2, 3, 4, 0, 5, 6, 7], [8, 9, 0, 10, 11, 9, 0, 0]]

    def test_no_duplicated_text_once_dedup_runs(self, monkeypatch, tmp_path):
        cues, heard, out_dir = self._run(monkeypatch, tmp_path, "test_overlap_dedup", self.CHUNKS)

        assert heard == ["chunk_00000.wav", "padded_00001.wav"]  # chunk 1 really was padded
        assert [c["text"] for c in cues] == ["今天天气很好", "我们明天去", "北京吧", "好的走吧"]
        assert "".join(c["text"] for c in cues) == "今天天气很好我们明天去北京吧好的走吧"
        assert [c["translated"] for c in cues] == [f"EN:{c['text']}" for c in cues]
        # Timestamps are back on the stream's own timeline: "北京吧" is
        # the first thing after the 4s boundary.
        assert [(c["start"], c["end"]) for c in cues] == [
            (0.0, 2.0), (2.5, 4.0), (4.0, 5.0), (5.5, 7.0)]
        assert not list(out_dir.glob("padded_*.wav"))  # cleaned up

    def test_without_dedup_the_same_audio_would_repeat_the_phrase(self, monkeypatch, tmp_path):
        # Proves the test above can actually see a duplicate: the padded
        # transcription genuinely re-hears "我们明天去".
        monkeypatch.setattr(lt, "dedup_overlap", lambda segs, overlap, tail: list(segs))
        cues, _, _ = self._run(monkeypatch, tmp_path, "test_overlap_nodedup", self.CHUNKS)
        assert "".join(c["text"] for c in cues).count("我们明天去") == 2

    def test_overlap_zero_is_the_old_unpadded_behaviour(self, monkeypatch, tmp_path):
        cues, heard, _ = self._run(monkeypatch, tmp_path, "test_overlap_off", self.CHUNKS,
                                   overlap_seconds=0)
        assert heard == ["chunk_00000.wav", "chunk_00001.wav"]
        assert [c["text"] for c in cues] == ["今天天气很好", "我们明天去", "北京吧", "好的走吧"]

    def test_a_word_repeated_right_after_the_boundary_is_kept(self, monkeypatch, tmp_path):
        # chunk 0 ends "... 去"; chunk 1 opens with "去" said AGAIN. Only
        # the overlap's own "我们明天去" is a repeat -- the second "去" is
        # real new speech and must survive, not be swallowed with it.
        chunks = [[1, 2, 0, 0, 0, 5, 6, 7], [7, 8, 9, 0, 0, 0, 0, 0]]
        cues, _, _ = self._run(monkeypatch, tmp_path, "test_overlap_repeat_word", chunks)
        assert [c["text"] for c in cues] == ["今天天气", "我们明天去", "去北京吧"]


class TestOverlapTailTextWindow:
    """What run_live_job hands the NEXT chunk as overlap_tail_text: only
    what the previous chunk emitted for its own last overlap_seconds --
    an older line elsewhere in that chunk must never be matched against
    (it would trim real new speech that happens to repeat it)."""

    def _run(self, monkeypatch, tmp_path, chunk0_cues, overlap_seconds=2):
        import background_jobs
        job_id = "test_overlap_tail_window"
        out_dir = tmp_path / "chunks"
        out_dir.mkdir()
        for i in range(2):
            _write_word_chunk(out_dir / f"chunk_{i:05d}.wav", [1] * 8)  # 4s each
        (out_dir / "chunk_00002.wav").write_bytes(b"")

        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://fake-stream")

        class FakeProc:

            error = None

            def poll(self):
                return None
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
        monkeypatch.setattr(lt, "stop_capture", lambda *a, **k: None)

        calls = []

        def fake_process_chunk(path, idx, *a, overlap_seconds=0.0, overlap_tail_text="", **kw):
            calls.append({"idx": idx, "overlap_seconds": overlap_seconds,
                          "overlap_tail_text": overlap_tail_text})
            return chunk0_cues if idx == 0 else []
        monkeypatch.setattr(lt, "process_chunk", fake_process_chunk)
        monkeypatch.setattr(background_jobs, "is_cancel_requested", lambda jid: len(calls) >= 2)

        lt.run_live_job(job_id, "http://example.com/live", str(out_dir), 4, "zh", "tiny",
                        engine=None, poll_interval=0.01, overlap_seconds=overlap_seconds)
        background_jobs.clear_job(job_id)
        return calls

    def test_only_cues_reaching_into_the_tail_window_are_passed_on(self, monkeypatch, tmp_path):
        calls = self._run(monkeypatch, tmp_path, [
            {"start": 0.0, "end": 1.0, "text": "好的", "translated": ""},
            {"start": 1.5, "end": 3.0, "text": "走吧", "translated": ""}])
        assert calls[0]["overlap_seconds"] == 0.0  # first chunk: nothing to pad with
        assert calls[1]["overlap_seconds"] == 2.0
        assert calls[1]["overlap_tail_text"] == "走吧"

    def test_a_chunk_silent_at_its_end_passes_on_no_tail_text(self, monkeypatch, tmp_path):
        calls = self._run(monkeypatch, tmp_path, [
            {"start": 0.0, "end": 1.0, "text": "好的", "translated": ""}])
        assert calls[1]["overlap_seconds"] == 2.0  # still padded with real audio...
        assert calls[1]["overlap_tail_text"] == ""  # ...but nothing to dedup against

    def test_overlap_is_capped_at_half_a_chunk(self, monkeypatch, tmp_path):
        calls = self._run(monkeypatch, tmp_path, [], overlap_seconds=10)  # 4s chunks
        assert calls[1]["overlap_seconds"] == 2.0


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
class TestPaddingRealFfmpegChunks:
    """The WAV chunks ffmpeg's own segment muxer writes are readable by
    read_wav_tail/write_padded_chunk -- not just WAVs this test suite
    wrote itself."""

    def test_pads_a_real_segmented_chunk(self, tmp_path):
        out_dir = str(tmp_path / "chunks")
        os.makedirs(out_dir)
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                        "-vn", "-ac", "1", "-ar", "16000", "-f", "segment",
                        "-segment_time", "1", "-reset_timestamps", "1",
                        os.path.join(out_dir, "chunk_%05d.wav")],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        c0, c1 = (os.path.join(out_dir, f"chunk_{i:05d}.wav") for i in (0, 1))
        tail = lt.read_wav_tail(c0, 0.5)
        assert tail is not None and tail["params"] == (1, 2, 16000)
        assert tail["seconds"] == pytest.approx(0.5, abs=1e-3)

        padded = os.path.join(out_dir, "padded_00001.wav")
        pad = lt.write_padded_chunk(tail, c1, padded)
        assert pad == tail["seconds"]
        chunk1_frames = len(_read_samples(c1))
        padded_samples = _read_samples(padded)
        assert len(padded_samples) == chunk1_frames + 8000
        assert padded_samples[:8000] == _read_samples(c0)[-8000:]


class TestStreamUrlCheck:
    """The API's hook (services/live_service.py): the resolved stream URL
    is checked before anything fetches it. Off by default."""

    def test_refused_stream_url_never_reaches_ffmpeg(self, monkeypatch):
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "file:///etc/passwd")
        monkeypatch.setattr(lt, "start_segment_capture",
                            lambda *a, **k: pytest.fail("ffmpeg must not start"))

        def refuse(url):
            raise ValueError("not public")
        with pytest.raises(ValueError):
            lt.run_live_job("test_stream_check", "https://example.com/live", "/fake/out", 20,
                            "zh", "tiny", engine=None, stream_url_check=refuse)


class _Capture:
    """A capture whose fetcher error and ffmpeg exit code the test sets."""

    def __init__(self, error=None, code=None):
        self.error = error
        self.code = code

    def poll(self):
        return self.code


class TestCaptureEndings:
    """How run_live_job ends on what the capture reports: a fetch failure
    or an ffmpeg error is a job error with the fetcher's fixed message; a
    stream that ends is a normal finish."""

    def _run(self, monkeypatch, capture, job_id):
        import background_jobs
        background_jobs.clear_job(job_id)
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://fake-stream")
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: capture)
        stopped = []
        monkeypatch.setattr(lt, "stop_capture", lambda c: stopped.append(c))
        try:
            lt.run_live_job(job_id, "https://example.com/live", "/fake/out", 20, "zh", "tiny",
                            engine=None, poll_interval=0.01)
        finally:
            assert stopped == [capture]
            background_jobs.clear_job(job_id)

    def test_a_fetch_failure_is_a_job_error_with_its_fixed_message(self, monkeypatch):
        import live_fetch
        with pytest.raises(lt.LiveCaptureError) as exc:
            self._run(monkeypatch, _Capture(error=live_fetch.STALLED, code=0), "test_live_fail")
        assert str(exc.value) == live_fetch.STALLED

    def test_ffmpeg_exiting_with_an_error_is_a_job_error(self, monkeypatch):
        with pytest.raises(lt.LiveCaptureError) as exc:
            self._run(monkeypatch, _Capture(code=1), "test_live_ffmpeg_fail")
        assert "ffmpeg could not read the stream" in str(exc.value)

    def test_a_stream_that_ends_finishes_the_job(self, monkeypatch):
        self._run(monkeypatch, _Capture(code=0), "test_live_ends")


class _Pump:
    def __init__(self, log):
        self.log = log

    def halt(self):
        self.log.append("halt")

    def join(self):
        self.log.append("join")


@pytest.mark.skipif(not HAS_FFMPEG, reason="needs a real ffmpeg binary")
def test_stop_capture_halts_the_fetcher_then_ends_ffmpeg_then_joins(tmp_path):
    log = []
    proc = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "s16le",
                             "-i", "pipe:0", "-f", "null", "-"], stdin=subprocess.PIPE)
    real_terminate = proc.terminate
    proc.terminate = lambda: log.append("terminate") or real_terminate()
    lt.stop_capture(lt.SegmentCapture(proc, _Pump(log)), timeout=5)
    assert log == ["halt", "terminate", "join"]
    assert proc.poll() is not None
    proc.stdin.close()


def test_stop_capture_kills_an_ffmpeg_that_ignores_sigterm():
    log = []
    proc = subprocess.Popen([sys.executable, "-c",
                             "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                             "print('ready', flush=True); time.sleep(30)"],
                            stdout=subprocess.PIPE)
    assert proc.stdout.readline().strip() == b"ready"
    started = time.monotonic()
    lt.stop_capture(lt.SegmentCapture(proc, _Pump(log)), timeout=0.5)
    assert proc.poll() is not None and time.monotonic() - started < 5
    assert log == ["halt", "join"]
    proc.stdout.close()
