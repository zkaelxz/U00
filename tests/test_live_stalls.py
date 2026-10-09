"""
tests/test_live_stalls.py -- a Live job keeps captioning when a step stalls: Whisper
that never returns for one chunk, a translator that is slow or failing, a backlog
of chunks, a chunk far longer than it should be, and Stop during any of them.

Whisper, the stream and ffmpeg are fakes; chunk files are real WAVs so the audio
window logic runs for real.
"""
import os
import sys
import threading
import time
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
import core
import live_audio
import live_translate as lt
import live_whisper
from services import live_service

RATE = 8000
PROMPT = 3.0


def _wait(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * int(RATE * seconds))


def _seconds(path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


class FakeProc:
    error = None

    def poll(self):
        return None


class Stream:
    """A fake capture: chunk_NNNNN.wav files appear one per `add`, and
    list_completed_chunks is the real one (a chunk is done once its successor exists)."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.count = 0

    def add(self, n=1, seconds=10):
        for _ in range(n):
            _wav(os.path.join(self.out_dir, f"chunk_{self.count:05d}.wav"), seconds)
            self.count += 1


@pytest.fixture
def job(isolated_db, monkeypatch, tmp_path):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
    monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
    monkeypatch.setattr(lt, "stop_capture", lambda *a, **k: None)
    # Small limits so a "stuck" chunk is given up on within a test.
    monkeypatch.setattr(live_whisper, "MIN_CHUNK_TIMEOUT", 0.4)
    monkeypatch.setattr(live_whisper, "CHUNK_TIMEOUT_FACTOR", 0.0)
    monkeypatch.setattr(live_whisper, "ABANDON_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(core, "release_gpu_models", lambda: None)
    notes = []
    out_dir = str(tmp_path / "chunks")

    def start(job_id, engine, **kw):
        kw.setdefault("overlap_seconds", 0)
        return background_jobs.start_job(
            job_id, lt.run_live_job, job_id, "http://example.com/live", out_dir, 10, "zh",
            "small", engine, poll_interval=0.02, report_note=notes.append, **kw)

    yield Stream(out_dir), start, notes
    for jid in list(background_jobs._jobs):
        background_jobs.request_cancel(jid)
    # Wiping the records first would hide the cancel from a job still looping.
    _wait(lambda: not background_jobs.list_running_jobs(), 3)
    background_jobs.clear_all_jobs()


class Engine:
    def __init__(self, delay=0.0, fail=False):
        self.delay, self.fail = delay, fail

    def translate_batch(self, lines, context):
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("api down")
        return [f"en:{t}" for t in lines]


def _texts(job_id):
    return [c["text"] for c in background_jobs.get_status(job_id).get("result") or []]


class TestWhisperThatNeverReturns:
    def test_a_hung_chunk_is_skipped_and_the_next_one_is_captioned(self, job, monkeypatch):
        stream, start, notes = job
        calls = []
        release = threading.Event()

        def whisper(path, **kw):
            calls.append(path)
            if len(calls) == 2:
                release.wait(20)   # the second chunk: no segment is ever produced
                return []
            return [{"start": 0.5, "end": 1.0, "text": f"t{len(calls)}"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        stream.add(2)
        assert start("live_hang", Engine())
        assert _wait(lambda: _texts("live_hang") == ["t1"])
        stream.add(1)   # chunk 1 is complete now: this is the call that hangs
        assert _wait(lambda: any(n.startswith("Skipped chunk 1 (Whisper did not finish in")
                                 for n in notes)), notes
        stream.add(1)   # the run goes on with the next chunk
        assert _wait(lambda: _texts("live_hang") == ["t1", "t3"])
        assert background_jobs.get_status("live_hang")["status"] == "running"
        release.set()

    def test_a_failing_or_slow_translator_never_blocks_the_next_chunks_whisper(
            self, job, monkeypatch):
        stream, start, notes = job
        seen = []

        def whisper(path, **kw):
            seen.append(os.path.basename(path))
            return [{"start": 0.5, "end": 1.0, "text": f"t{len(seen)}"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        stream.add(2)
        assert start("live_badtr", Engine(delay=0.05, fail=True))
        for want in (1, 2, 3):
            assert _wait(lambda: len(seen) >= want)
            stream.add(1)
        states = {c["translation"] for c in background_jobs.get_status("live_badtr")["result"]}
        assert "failed" in states and states <= {"failed", "pending"}


class TestStopIsPrompt:
    def test_stop_returns_within_a_poll_while_whisper_is_stuck(self, job, monkeypatch):
        stream, start, _ = job
        inside, release = threading.Event(), threading.Event()
        monkeypatch.setattr(live_whisper, "MIN_CHUNK_TIMEOUT", 60.0)

        def whisper(path, **kw):
            inside.set()
            release.wait(20)
            return []
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        stream.add(2)
        assert start("live_stop", Engine())
        assert inside.wait(PROMPT)
        started = time.monotonic()
        background_jobs.request_cancel("live_stop")
        assert _wait(lambda: background_jobs.get_status("live_stop")["status"] != "running", PROMPT)
        assert time.monotonic() - started < PROMPT
        release.set()


class TestRunGuarded:
    def test_returns_the_value_and_passes_errors_on(self):
        assert live_whisper.run_guarded(lambda cb: 7, lambda: False, 5) == 7
        with pytest.raises(ValueError):
            live_whisper.run_guarded(lambda cb: (_ for _ in ()).throw(ValueError("x")),
                                     lambda: False, 5)

    def test_the_worker_ends_at_its_next_segment_once_abandoned(self, monkeypatch):
        monkeypatch.setattr(live_whisper, "ABANDON_GRACE_SECONDS", 2.0)
        reached = []

        def decode(progress_cb):
            for i in range(100):
                time.sleep(0.05)
                reached.append(i)
                progress_cb(i / 100)   # core.transcribe_for_timing does this per segment
            return "finished"
        with pytest.raises(live_whisper.ChunkTimeout):
            live_whisper.run_guarded(decode, lambda: False, 0.2)
        n = len(reached)
        time.sleep(0.3)
        assert len(reached) <= n + 1 and len(reached) < 20

    def test_stop_raises_job_cancelled(self):
        flag = threading.Event()
        threading.Timer(0.2, flag.set).start()
        with pytest.raises(background_jobs.JobCancelled):
            live_whisper.run_guarded(lambda cb: time.sleep(5), flag.is_set, 10)

    def test_per_segment_abort_reaches_the_real_decode_loop(self, monkeypatch, tmp_path):
        """core.transcribe_for_timing calls progress_cb as each lazily yielded
        segment arrives, which is the only place a running decode can be stopped."""
        yielded = []

        class Seg:
            def __init__(self, i):
                self.start, self.end, self.text, self.words = i, i + 0.5, f"w{i}", None

        class Model:
            def transcribe(self, path, **kw):
                def gen():
                    for i in range(1000):
                        time.sleep(0.02)
                        yielded.append(i)
                        yield Seg(i)
                return gen(), type("I", (), {"duration": 1000})()
        monkeypatch.setattr(core, "load_whisper_model", lambda *a, **k: Model())
        wav = tmp_path / "a.wav"
        _wav(wav, 1)
        monkeypatch.setattr(live_whisper, "ABANDON_GRACE_SECONDS", 1.0)
        with pytest.raises(live_whisper.ChunkTimeout):
            live_whisper.run_guarded(
                lambda cb: core.transcribe_for_timing(str(wav), progress_cb=cb),
                lambda: False, 0.2)
        n = len(yielded)
        time.sleep(0.2)
        assert len(yielded) <= n + 1


class TestCatchUp:
    def test_a_backlog_is_dropped_to_the_newest_chunks(self, job, monkeypatch):
        stream, start, notes = job
        seen = []
        gate = threading.Event()

        def whisper(path, **kw):
            seen.append(os.path.basename(path))
            gate.wait(5)
            return []
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        stream.add(7)   # chunks 0-5 are complete: a six-chunk backlog
        assert start("live_catchup", Engine())
        assert _wait(lambda: seen)
        gate.set()
        assert _wait(lambda: any("to catch up" in n for n in notes))
        assert seen[0] == "chunk_00004.wav"   # the newest two of 0-5 are 4 and 5
        assert "Skipped 40 s to catch up." in notes
        # Skipped audio is deleted, not kept on disk.
        assert not [f for f in os.listdir(stream.out_dir) if f in ("chunk_00000.wav", "chunk_00001.wav")]

    def test_split_backlog(self):
        pending = [(i, f"p{i}") for i in range(5)]
        assert live_whisper.split_backlog(pending, 2) == (pending[:3], pending[3:])
        assert live_whisper.split_backlog(pending[:2], 2) == ([], pending[:2])


class TestAudioWindow:
    def test_a_chunk_never_reaches_whisper_longer_than_chunk_plus_overlap(self, job, monkeypatch):
        stream, start, notes = job
        lengths = []

        def whisper(path, **kw):
            lengths.append(_seconds(path))
            return [{"start": 0.0, "end": 1.0, "text": "x"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        stream.add(1, seconds=95)   # a stream timestamp jump: one 95 s "chunk"
        stream.add(1, seconds=10)
        stream.add(1, seconds=10)
        assert start("live_window", Engine(), overlap_seconds=2)
        assert _wait(lambda: len(lengths) >= 2)
        assert max(lengths) <= 10 + 2 + 0.01
        assert any("skipped its first 85 s to catch up" in n for n in notes), notes

    def test_trimmed_chunk_keeps_its_timestamps_on_the_stream_timeline(self, tmp_path, monkeypatch):
        src = tmp_path / "chunk_00000.wav"
        _wav(src, 30)
        window = live_audio.window_for_chunk(str(src), 0, str(tmp_path), 10, None, 0)
        assert window.trimmed_seconds == pytest.approx(20)
        assert _seconds(window.path) == pytest.approx(10)
        monkeypatch.setattr(core, "transcribe_for_timing",
                            lambda p, **kw: [{"start": 1.0, "end": 2.0, "text": "x"}])
        cues = lt.process_chunk(window.path, 0, 10, "zh", "small", Engine(),
                                audio_shift=window.trimmed_seconds)
        assert cues[0]["start"] == pytest.approx(21.0)

    def test_a_normal_chunk_is_left_alone(self, tmp_path):
        src = tmp_path / "chunk_00000.wav"
        _wav(src, 10.3)
        window = live_audio.window_for_chunk(str(src), 0, str(tmp_path), 10, None, 2)
        assert window.path == str(src) and window.trimmed_seconds == 0
        assert window.tail["seconds"] == pytest.approx(2)


class TestWarmStart:
    def test_whisper_is_loaded_before_capture_starts(self, job, monkeypatch):
        stream, start, notes = job
        order = []
        monkeypatch.setattr(live_whisper, "warm_up", lambda size, gpu: order.append(("warm", size, gpu)))
        monkeypatch.setattr(lt, "start_segment_capture",
                            lambda *a, **k: order.append("capture") or FakeProc())
        monkeypatch.setattr(core, "transcribe_for_timing", lambda p, **kw: [])
        assert start("live_warm", Engine(), use_gpu=True)
        assert _wait(lambda: "capture" in order)
        assert order[0] == ("warm", "small", True)
        assert any(n.startswith("Whisper small ready in") for n in notes)

    def test_a_failed_warm_up_does_not_stop_the_job(self, job, monkeypatch):
        stream, start, notes = job

        def boom(size, gpu):
            raise RuntimeError("no cuda")
        monkeypatch.setattr(live_whisper, "warm_up", boom)
        monkeypatch.setattr(core, "transcribe_for_timing", lambda p, **kw: [])
        stream.add(2)
        assert start("live_warmfail", Engine())
        assert _wait(lambda: any("could not be warmed up" in n for n in notes))
        assert background_jobs.get_status("live_warmfail")["status"] == "running"

    def test_stop_while_loading_returns_without_starting_capture(self, job, monkeypatch):
        stream, start, _ = job
        release = threading.Event()
        started = []
        monkeypatch.setattr(live_whisper, "warm_up", lambda size, gpu: release.wait(20))
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: started.append(1))
        assert start("live_warmstop", Engine())
        assert _wait(lambda: "Loading Whisper small" in background_jobs.get_status("live_warmstop")["message"])
        background_jobs.request_cancel("live_warmstop")
        assert _wait(lambda: background_jobs.get_status("live_warmstop")["status"] != "running", PROMPT)
        assert started == []
        release.set()

    def test_the_real_warm_up_runs_one_second_of_silence(self, monkeypatch):
        seen = {}

        def fake(path, **kw):
            seen["seconds"] = _seconds_16k(path)
            seen.update(kw)
            return []
        monkeypatch.setattr(core, "transcribe_for_timing", fake)
        live_whisper.warm_up.real("small", True)
        assert seen["seconds"] == pytest.approx(1.0) and seen["model_size"] == "small"
        assert seen["use_gpu"] is True


def _seconds_16k(path):
    return _seconds(path)


class TestTimingsAndNotes:
    def test_the_status_names_the_speed_of_the_last_chunk(self, job, monkeypatch):
        stream, start, notes = job
        monkeypatch.setattr(core, "transcribe_for_timing",
                            lambda p, **kw: [{"start": 0.0, "end": 1.0, "text": "x"}])
        gate = threading.Event()

        class SlowEngine(Engine):
            def translate_batch(self, lines, context):
                gate.wait(5)
                return super().translate_batch(lines, context)
        stream.add(2)
        assert start("live_speed", SlowEngine())
        assert _wait(lambda: "translating with" in background_jobs.get_status("live_speed")["message"])
        message = background_jobs.get_status("live_speed")["message"]
        assert "transcribed 10 s of audio in" in message and "x real time" in message
        gate.set()

    def test_notes_reach_the_session_status_without_paths(self, isolated_db, monkeypatch):
        live_service._sessions["live_n"] = {"dir": None, "engine": "x"}
        monkeypatch.setattr(background_jobs, "get_status", lambda sid: {"status": "running", "message": "m"})
        try:
            for i in range(9):
                live_service.add_note("live_n", f"note {i} at /home/user/secret/dir/x")
            notes = live_service.get_session("live_n")["notes"]
            assert len(notes) == live_service.MAX_NOTES
            assert notes[-1].startswith("note 8") and "/home" not in " ".join(notes)
        finally:
            live_service._sessions.pop("live_n", None)
