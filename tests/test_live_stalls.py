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
import ollama_unload
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


class FakeClock:
    """Whisper's time limit, moved by the test instead of by waiting."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TickingClock:
    """Moves a fixed step on every read, for a runner called directly."""

    def __init__(self, step=1.0):
        self.now, self.step = 0.0, step

    def __call__(self):
        self.now += self.step
        return self.now


@pytest.fixture
def job(isolated_db, monkeypatch, tmp_path):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
    monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
    monkeypatch.setattr(lt, "stop_capture", lambda *a, **k: None)
    monkeypatch.setattr(live_whisper, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(live_whisper, "ABANDON_GRACE_SECONDS", 0.05)
    # Dropping the cached model must never be how a stuck call is handled.
    released = []
    monkeypatch.setattr(core, "release_gpu_models", lambda: released.append(1))
    notes = []
    session = {"dir": None, "engine": "x"}
    out_dir = str(tmp_path / "chunks")
    clock = FakeClock()

    def record(text, key=None):
        live_service._sessions["live_notes"] = session
        live_service.add_note("live_notes", text, key=key)
        notes[:] = session.get("notes", [])

    def start(job_id, engine, **kw):
        kw.setdefault("overlap_seconds", 0)
        return background_jobs.start_job(
            job_id, lt.run_live_job, job_id, "http://example.com/live", out_dir, 10, "zh",
            "small", engine, poll_interval=0.02, report_note=record,
            whisper_clock=clock, **kw)

    start.clock = clock
    start.released = released
    yield Stream(out_dir), start, notes
    for jid in list(background_jobs._jobs):
        background_jobs.request_cancel(jid)
    # Wiping the records first would hide the cancel from a job still looping.
    _wait(lambda: not background_jobs.list_running_jobs(), 3)
    background_jobs.clear_all_jobs()
    live_service._sessions.pop("live_notes", None)


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


def _prepare(use_gpu):
    """The GPU loaders' hook as it really is (conftest stubs it for every test)."""
    ollama_unload.prepare_gpu_for_transcription.real(use_gpu)


def _live_whisper_threads():
    return sum(1 for t in threading.enumerate() if t.name == "live-whisper" and t.is_alive())


def _fake_model_cache(monkeypatch):
    """core's model cache with a load counter: a load only happens on a miss."""
    cache, loads = {}, []

    def load(size, use_gpu=False, **kw):
        if size not in cache:
            loads.append(size)
            cache[size] = object()
        return cache[size]
    monkeypatch.setattr(core, "load_whisper_model", load)
    return load, loads


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
        assert _wait(lambda: len(calls) == 2)
        start.clock.advance(1000)
        assert _wait(lambda: "Skipped chunk 1 (Whisper did not finish in 60 s)." in notes), notes
        stream.add(1)   # the hung call is still alive: this chunk must not start a second one
        assert _wait(lambda: any("Whisper is still busy with chunk 1" in n for n in notes)), notes
        assert len(calls) == 2
        release.set()
        assert _wait(lambda: _live_whisper_threads() == 0)
        stream.add(1)
        assert _wait(lambda: _texts("live_hang") == ["t1", "t3"])
        assert background_jobs.get_status("live_hang")["status"] == "running"
        assert start.released == []

    def test_a_stuck_call_followed_by_more_chunks_has_one_thread_and_one_model_load(
            self, job, monkeypatch):
        stream, start, notes = job
        _, loads = _fake_model_cache(monkeypatch)
        release = threading.Event()
        live_counts, calls = [], []

        def whisper(path, **kw):
            core.load_whisper_model("small")
            calls.append(path)
            live_counts.append(_live_whisper_threads())
            if len(calls) == 1:
                release.wait(20)   # a CUDA call that does not come back
                return []
            return [{"start": 0.5, "end": 1.0, "text": f"t{len(calls)}"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        monkeypatch.setattr(live_whisper, "warm_up",
                            lambda size, gpu, cb=None: core.load_whisper_model(size))
        stream.add(2)
        assert start("live_one", Engine())
        assert _wait(lambda: len(calls) == 1)
        start.clock.advance(1000)
        assert _wait(lambda: any(n.startswith("Skipped chunk 1 (Whisper did not finish") or
                                 n.startswith("Skipped chunk 0 (Whisper did not finish")
                                 for n in notes)), notes
        stream.add(2)   # two more chunks while the first call is still stuck
        assert _wait(lambda: any("still busy with chunk" in n for n in notes)), notes
        assert len(calls) == 1 and _live_whisper_threads() == 1 and loads == ["small"]
        release.set()
        assert _wait(lambda: _live_whisper_threads() == 0)
        stream.add(1)
        assert _wait(lambda: len(calls) >= 2)
        assert max(live_counts) == 1 and loads == ["small"]
        assert start.released == []

    def test_the_original_shape_a_slow_chunk_after_a_local_translation_is_skipped(
            self, job, monkeypatch):
        """Ollama's translation model is resident, the next Whisper call crawls,
        and the loader hook runs on every call. Ollama is freed once, the slow
        chunk is given up on, and the run goes on."""
        stream, start, notes = job
        freed = []
        monkeypatch.setattr(ollama_unload, "_free_ollama_gpu_memory",
                            lambda: freed.append(1))
        slow, release = threading.Event(), threading.Event()
        calls = []

        class LocalOllama(Engine):
            name, model = "ollama", "qwen3:8b"

        def whisper(path, **kw):
            _prepare(True)   # what the loader does
            calls.append(path)
            if len(calls) == 2:
                slow.set()
                release.wait(20)
                return []
            return [{"start": 0.5, "end": 1.0, "text": f"t{len(calls)}"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        monkeypatch.setattr(live_whisper, "warm_up",
                            lambda size, gpu, cb=None: _prepare(True))
        stream.add(2)
        assert start("live_shape", LocalOllama(), use_gpu=True)
        assert _wait(lambda: _texts("live_shape") == ["t1"])
        stream.add(1)
        assert slow.wait(PROMPT)
        start.clock.advance(1000)
        assert _wait(lambda: any("did not finish" in n for n in notes)), notes
        release.set()
        assert _wait(lambda: _live_whisper_threads() == 0)
        stream.add(1)
        assert _wait(lambda: _texts("live_shape") == ["t1", "t3"])
        assert len(freed) == 1

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
        stream, start, notes = job
        inside, release = threading.Event(), threading.Event()

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
        # The final status names the call still running, and nothing was dropped.
        assert _wait(lambda: any("still finishing chunk 0" in n for n in notes))
        assert start.released == []
        release.set()


@pytest.fixture(autouse=True)
def _no_leftover_whisper_worker():
    yield
    live_whisper._outstanding = None


class TestWhisperRunner:
    def _runner(self, should_stop=lambda: False, clock=None):
        return live_whisper.WhisperRunner(should_stop, lambda t, key=None: None, clock=clock or FakeClock(),
                                          poll=0.005, grace=0.05)

    def test_returns_the_value_and_passes_errors_on(self):
        assert self._runner().run(lambda cb: 7, 5, "chunk 0") == 7
        with pytest.raises(ValueError):
            self._runner().run(lambda cb: (_ for _ in ()).throw(ValueError("x")), 5, "chunk 0")

    def test_the_worker_ends_at_its_next_segment_once_abandoned(self):
        reached = []

        def decode(progress_cb):
            for i in range(100):
                time.sleep(0.05)
                reached.append(i)
                progress_cb(i / 100)   # core.transcribe_for_timing does this per segment
            return "finished"
        runner = self._runner(clock=TickingClock())
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(decode, 3, "chunk 0")
        n = len(reached)
        assert _wait(lambda: runner.busy_with() is None)
        assert len(reached) <= n + 2 and len(reached) < 20

    def test_stop_raises_job_cancelled(self):
        flag = threading.Event()
        release = threading.Event()
        threading.Timer(0.05, flag.set).start()
        runner = self._runner(should_stop=flag.is_set)
        with pytest.raises(background_jobs.JobCancelled):
            runner.run(lambda cb: release.wait(20), 10, "chunk 0")
        release.set()

    def test_no_second_call_starts_while_an_abandoned_one_is_alive(self):
        release = threading.Event()
        runner = self._runner(clock=TickingClock())
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(lambda cb: release.wait(20), 3, "chunk 4")
        started = []
        with pytest.raises(live_whisper.WhisperBusy) as busy:
            runner.run(lambda cb: started.append(1), 3, "chunk 5")
        assert busy.value.label == "chunk 4" and started == []
        assert runner.close() == "chunk 4"
        release.set()
        assert _wait(lambda: runner.busy_with() is None)
        assert runner.run(lambda cb: "ok", 3, "chunk 6") == "ok"
        assert runner.close() is None

    def test_a_second_runner_is_refused_while_the_first_ones_abandoned_call_lives(self):
        release = threading.Event()
        with pytest.raises(live_whisper.ChunkTimeout):
            self._runner(clock=TickingClock()).run(lambda cb: release.wait(20), 3, "chunk 4")
        started = []
        other = self._runner()
        with pytest.raises(live_whisper.WhisperBusy):
            other.run(lambda cb: started.append(1), 3, "the model load")
        assert started == []
        release.set()
        assert _wait(lambda: other.busy_with() is None)

    def test_a_new_session_is_refused_while_the_old_sessions_whisper_call_lives(self):
        release = threading.Event()
        with pytest.raises(live_whisper.ChunkTimeout):
            self._runner(clock=TickingClock()).run(lambda cb: release.wait(20), 3, "chunk 4")
        with pytest.raises(live_service.ConflictError, match="still finishing"):
            live_service.start_session(url="https://example.com/live", engine="google")
        release.set()
        assert _wait(lambda: live_whisper.outstanding_label() is None)

    def test_an_abandoned_gpu_call_holds_a_gpu_lock_until_it_ends(self, isolated_db, monkeypatch):
        import db
        release = threading.Event()
        promoted = []
        monkeypatch.setattr(background_jobs, "_promote_next_queued_gpu_job",
                            lambda: promoted.append(db.gpu_lock_status()[0]))
        runner = live_whisper.WhisperRunner(lambda: False, lambda t, key=None: None,
                                            clock=TickingClock(), poll=0.005, grace=0.05, gpu=True)
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(lambda cb: release.wait(20), 3, "chunk 4")
        assert db.gpu_lock_status()[0] == live_whisper.CLAIM_HOLDER
        # The Live job's own slot is released after this; a queued job still waits.
        assert not db.try_acquire_gpu_lock("ui:queued", max_holders=1)
        release.set()
        assert _wait(lambda: promoted)
        assert promoted == [None] and db.gpu_lock_status()[0] is None

    def test_a_call_that_ends_within_the_grace_leaves_no_claim(self, isolated_db):
        import db
        runner = live_whisper.WhisperRunner(lambda: False, lambda t, key=None: None,
                                            clock=TickingClock(), poll=0.005, grace=0.5, gpu=True)

        def decode(cb):
            time.sleep(0.1)
            cb(1.0)
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(decode, 3, "chunk 0")
        assert _wait(lambda: runner.busy_with() is None)
        assert db.gpu_lock_status()[0] is None

    def test_a_cpu_call_never_claims_the_gpu(self):
        release = threading.Event()
        with pytest.raises(live_whisper.ChunkTimeout):
            self._runner(clock=TickingClock()).run(lambda cb: release.wait(20), 3, "chunk 4")
        import db
        assert db.gpu_lock_status()[0] is None
        release.set()

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
        runner = self._runner(clock=TickingClock())
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(lambda cb: core.transcribe_for_timing(str(wav), progress_cb=cb), 3, "chunk 0")
        n = len(yielded)
        assert _wait(lambda: runner.busy_with() is None)
        assert len(yielded) <= n + 2


class TestAbandonedWorkerBlocksAdminActions:
    def _abandon(self, release, gpu=True):
        runner = live_whisper.WhisperRunner(lambda: False, lambda t, key=None: None,
                                            clock=TickingClock(), poll=0.005, grace=0.05, gpu=gpu)
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(lambda cb: release.wait(20), 3, "chunk 4")

    def test_reset_and_model_delete_are_refused_while_it_lives(self, isolated_db):
        from services import diagnostics_gaps_service as dgs
        from services import library_admin_service as las
        from services.service_errors import ServiceError
        release = threading.Event()
        self._abandon(release)
        try:
            assert not background_jobs.acquire_exclusive("x")
            with pytest.raises(ServiceError):
                dgs.reset_library(confirm=True, confirm_text="RESET")
            with pytest.raises(ServiceError):
                dgs._exclusive_delete(lambda: True, "failed")
        finally:
            release.set()
        assert _wait(lambda: background_jobs.acquire_exclusive("x"))
        background_jobs.release_exclusive()

    def test_wait_for_job_threads_waits_for_it(self, isolated_db):
        release = threading.Event()
        self._abandon(release)
        assert not background_jobs.wait_for_job_threads(0.05)
        release.set()
        assert background_jobs.wait_for_job_threads(5.0)

    def test_startup_clears_only_live_whisper_claims(self, isolated_db):
        import db
        assert db.try_acquire_gpu_lock(live_whisper.CLAIM_HOLDER, "x", max_holders=db.GPU_LOCK_MAX_SLOTS)
        assert db.try_acquire_gpu_lock("cli:123", "x", max_holders=db.GPU_LOCK_MAX_SLOTS)
        assert live_whisper.release_stale_claims() == 1
        import contextlib
        with contextlib.closing(db.get_conn()) as conn:
            holders = [r["holder"] for r in conn.execute("SELECT holder FROM gpu_lock")]
        assert holders == ["cli:123"]

    def test_startup_keeps_a_live_process_claim_and_drops_a_dead_one(self, isolated_db, monkeypatch):
        import contextlib
        import db
        alive, dead = f"{live_whisper.CLAIM_PREFIX}4242", f"{live_whisper.CLAIM_PREFIX}4343"
        for holder in (alive, dead):
            assert db.try_acquire_gpu_lock(holder, "x", max_holders=db.GPU_LOCK_MAX_SLOTS)
        monkeypatch.setattr(background_jobs, "owner_process_alive", lambda pid: pid == 4242)
        assert live_whisper.release_stale_claims() == 1
        with contextlib.closing(db.get_conn()) as conn:
            holders = [r["holder"] for r in conn.execute("SELECT holder FROM gpu_lock")]
        assert holders == [alive]

    def test_a_failed_thread_start_leaves_nothing_outstanding(self, isolated_db, monkeypatch):
        def refuse(self):
            raise RuntimeError("can't start new thread")
        monkeypatch.setattr(threading.Thread, "start", refuse)
        runner = live_whisper.WhisperRunner(lambda: False, lambda t, key=None: None)
        with pytest.raises(RuntimeError):
            runner.run(lambda cb: None, 3, "chunk 1")
        assert live_whisper.outstanding_label() is None
        assert live_whisper.wait_for_outstanding(0.05)

    def test_cpu_call_does_not_show_the_gpu_wait_message(self, isolated_db):
        release = threading.Event()
        runner = live_whisper.WhisperRunner(lambda: False, lambda t, key=None: None,
                                            clock=TickingClock(), poll=0.005, grace=0.05, gpu=False)
        with pytest.raises(live_whisper.ChunkTimeout):
            runner.run(lambda cb: release.wait(20), 3, "chunk 4")
        try:
            assert not live_whisper.gpu_claim_held()
            with background_jobs._lock:
                background_jobs._jobs["q2"] = {"status": "queued"}
                background_jobs._note_gpu_wait_reason_locked("q2")
                assert background_jobs._jobs["q2"]["gpu_wait_external"] != live_whisper.WAIT_MESSAGE
                del background_jobs._jobs["q2"]
        finally:
            release.set()

    def test_queued_job_wait_text_names_the_live_call(self, isolated_db):
        release = threading.Event()
        self._abandon(release)
        try:
            job_id = "q1"
            with background_jobs._lock:
                background_jobs._jobs[job_id] = {"status": "queued"}
                background_jobs._note_gpu_wait_reason_locked(job_id)
                assert background_jobs._jobs[job_id]["gpu_wait_external"] == \
                    live_whisper.WAIT_MESSAGE
                del background_jobs._jobs[job_id]
        finally:
            release.set()


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
        assert "Skipped 4 chunks (~40 s) to catch up." in notes
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
        monkeypatch.setattr(live_whisper, "warm_up",
                            lambda size, gpu, cb=None: order.append(("warm", size, gpu)))
        monkeypatch.setattr(lt, "start_segment_capture",
                            lambda *a, **k: order.append("capture") or FakeProc())
        monkeypatch.setattr(core, "transcribe_for_timing", lambda p, **kw: [])
        assert start("live_warm", Engine(), use_gpu=True)
        assert _wait(lambda: "capture" in order)
        assert order[0] == ("warm", "small", True)
        assert any(n.startswith("Whisper small ready in") for n in notes)

    def test_a_failed_warm_up_does_not_stop_the_job(self, job, monkeypatch):
        stream, start, notes = job

        def boom(size, gpu, cb=None):
            raise RuntimeError("no cuda")
        monkeypatch.setattr(live_whisper, "warm_up", boom)
        monkeypatch.setattr(core, "transcribe_for_timing", lambda p, **kw: [])
        stream.add(2)
        assert start("live_warmfail", Engine())
        assert _wait(lambda: any("could not be warmed up" in n for n in notes))
        assert background_jobs.get_status("live_warmfail")["status"] == "running"

    def test_a_warm_up_that_never_ends_is_not_loaded_a_second_time_by_chunk_0(
            self, job, monkeypatch):
        stream, start, notes = job
        _, loads = _fake_model_cache(monkeypatch)
        release = threading.Event()
        entered = []

        def stuck_load(size, gpu, cb=None):
            core.load_whisper_model(size)
            entered.append(1)
            release.wait(20)
        monkeypatch.setattr(live_whisper, "warm_up", stuck_load)
        calls = []
        monkeypatch.setattr(core, "transcribe_for_timing", lambda p, **kw: calls.append(p) or [])
        stream.add(3)
        assert start("live_warmhang", Engine())
        assert _wait(lambda: entered)
        start.clock.advance(live_whisper.WARM_UP_TIMEOUT + 1)
        assert _wait(lambda: any("could not be warmed up" in n for n in notes)), notes
        assert _wait(lambda: any("still busy with the model load" in n for n in notes)), notes
        assert calls == [] and loads == ["small"] and _live_whisper_threads() == 1
        release.set()

    def test_stop_while_loading_returns_without_starting_capture(self, job, monkeypatch):
        stream, start, notes = job
        release = threading.Event()
        started = []
        monkeypatch.setattr(live_whisper, "warm_up", lambda size, gpu, cb=None: release.wait(20))
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: started.append(1))
        assert start("live_warmstop", Engine())
        assert _wait(lambda: "Loading Whisper small" in background_jobs.get_status("live_warmstop")["message"])
        background_jobs.request_cancel("live_warmstop")
        assert _wait(lambda: background_jobs.get_status("live_warmstop")["status"] != "running", PROMPT)
        assert started == []
        assert any("still finishing the model load" in n for n in notes)
        release.set()

    def test_the_real_warm_up_decodes_noise_with_the_vad_filter_off(self, monkeypatch):
        numpy = pytest.importorskip("numpy")
        seen = {"consumed": False}

        class Model:
            def transcribe(self, audio, **kw):
                seen.update(kw, audio=audio)

                def gen():
                    seen["consumed"] = True
                    yield "segment"
                return gen(), None
        monkeypatch.setattr(core, "load_whisper_model", lambda size, use_gpu=False, **k: Model())
        # transcribe_for_timing always enables the VAD filter, so it must not be the path.
        monkeypatch.setattr(core, "transcribe_for_timing",
                            lambda *a, **k: pytest.fail("the warm-up must call the model directly"))
        ticks = []
        live_whisper.warm_up.real("small", True, ticks.append)
        audio = seen["audio"]
        assert seen["vad_filter"] is False and seen["consumed"] and ticks == [0.0]
        assert audio.dtype == numpy.float32 and len(audio) >= 16000
        assert 0 < float(numpy.abs(audio).max()) < 0.2   # not silence, and not loud


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

    def test_a_keyed_note_replaces_its_earlier_one_and_becomes_the_newest(self, isolated_db):
        live_service._sessions["live_k"] = {"dir": None, "engine": "x"}
        try:
            live_service.add_note("live_k", "skipped 1", key="skip")
            for i in range(3):
                live_service.add_note("live_k", f"other {i}")
            live_service.add_note("live_k", "skipped 2", key="skip")
            notes = live_service._sessions["live_k"]["notes"]
            assert notes == ["other 0", "other 1", "other 2", "skipped 2"]
        finally:
            live_service._sessions.pop("live_k", None)

    def test_skips_are_counted_into_one_note_per_reason_and_survive_other_notes(self, isolated_db):
        live_service._sessions["live_s"] = {"dir": None, "engine": "x"}
        try:
            record = lambda text, key=None: live_service.add_note("live_s", text, key=key)  # noqa: E731
            skips = live_whisper.SkipNotes(record, 5)
            for _ in range(5):
                skips.catch_up(1)
                record("something else happened")
            skips.timed_out(7, 60)
            skips.timed_out(9, 60)
            skips.busy(10, "chunk 9")
            skips.busy(11, "chunk 9")
            notes = live_service._sessions["live_s"]["notes"]
            assert "Skipped 5 chunks (~25 s) to catch up." in notes
            assert "Skipped 2 chunks (Whisper did not finish in 60 s; latest: chunk 9)." in notes
            assert "Skipped 2 chunks (~10 s): Whisper is still busy with chunk 9." in notes
            assert len([n for n in notes if "catch up" in n]) == 1
        finally:
            live_service._sessions.pop("live_s", None)

    def test_slower_than_real_time_is_noted_once(self, job, monkeypatch):
        stream, start, notes = job

        def whisper(path, **kw):
            time.sleep(0.03)   # more than the 5 ms of audio below
            return [{"start": 0.0, "end": 0.001, "text": "x"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        stream.add(3, seconds=0.005)
        assert start("live_slowonce", Engine())
        assert _wait(lambda: len(_texts("live_slowonce")) >= 2)
        assert len([n for n in notes if "slower than the stream" in n]) == 1


class TestChunkDiagnostics:
    def test_a_chunk_over_three_times_its_audio_gets_a_status_line_and_a_log_line(
            self, monkeypatch):
        import diagnostics
        monkeypatch.setattr(diagnostics, "external_gpu_load", lambda: {
            "memory_free_mb": 1200.0, "memory_total_mb": 8192.0})
        logged, notes = [], []
        monkeypatch.setattr(live_whisper, "_log", lambda level, text: logged.append(text))
        live_whisper.log_chunk(1, 10.0, 2.0, 200.0, 5.0, False, lambda t, k=None: notes.append(t),
                               use_gpu=True)
        assert logged and "GPU free 1200 of 8192 MB" in logged[0] and "Whisper 200.0 s" in logged[0]
        assert len(notes) == 1 and "took 207 s for 10 s of audio" in notes[0]
        live_whisper.log_chunk(2, 10.0, 0.5, 4.0, 1.0, False, lambda t, k=None: notes.append(t))
        assert len(notes) == 1   # 5.5 s for 10 s of audio is healthy

    def test_nvidia_smi_runs_only_for_a_slow_gpu_chunk(self, monkeypatch):
        import diagnostics
        calls = []
        monkeypatch.setattr(diagnostics, "external_gpu_load", lambda: calls.append(1))
        monkeypatch.setattr(live_whisper, "_log", lambda level, text: None)
        note = lambda t, k=None: None
        live_whisper.log_chunk(0, 10.0, 0.1, 2.0, 1.0, False, note, use_gpu=True)
        live_whisper.log_chunk(1, 10.0, 0.5, 200.0, 1.0, False, note, use_gpu=False)
        assert calls == []
        live_whisper.log_chunk(2, 10.0, 0.5, 200.0, 1.0, False, note, use_gpu=True)
        assert calls == [1]

    def test_a_missing_nvidia_smi_is_not_an_error(self, monkeypatch):
        import diagnostics
        monkeypatch.setattr(diagnostics, "external_gpu_load", lambda: None)
        logged = []
        monkeypatch.setattr(live_whisper, "_log", lambda level, text: logged.append(text))
        live_whisper.log_chunk(0, 10.0, 0.1, 2.0, 1.0, True, lambda t, k=None: None)
        assert "GPU" not in logged[0] and "not loaded yet" in logged[0]


class TestOllamaCadence:
    """Freeing a local Ollama is decided per Live job, not per chunk's thread."""

    freed = None

    def _loader_calls(self, scope, n, monkeypatch):
        if self.freed is None:
            self.freed = []
        freed = self.freed
        monkeypatch.setattr(ollama_unload, "_free_ollama_gpu_memory",
                            lambda: freed.append(1) or "Ollama still has a model loaded.")
        for _ in range(n):
            # A new thread per call, as each chunk's Whisper call gets.
            t = threading.Thread(target=lambda: _in_scope(scope))
            t.start()
            t.join()
        return freed

    def test_a_local_ollama_is_freed_once_per_job_across_threads(self, monkeypatch):
        scope = ollama_unload.JobScope(True, min_free_mb=2000)
        assert len(self._loader_calls(scope, 5, monkeypatch)) == 1

    def test_a_cloud_engine_never_touches_ollama(self, monkeypatch):
        scope = ollama_unload.JobScope(False)
        assert self._loader_calls(scope, 5, monkeypatch) == []
        assert scope.take_notice() is None

    def test_the_notice_comes_back_to_the_job_thread_once(self, monkeypatch):
        scope = ollama_unload.JobScope(True)
        self._loader_calls(scope, 2, monkeypatch)
        assert scope.take_notice() == "Ollama still has a model loaded."
        assert scope.take_notice() is None

    def test_a_cpu_run_does_nothing(self, monkeypatch):
        freed = []
        monkeypatch.setattr(ollama_unload, "_free_ollama_gpu_memory", lambda: freed.append(1))
        with ollama_unload.job_scope(ollama_unload.JobScope(True)):
            _prepare(False)
        assert freed == []

    def test_it_runs_again_only_while_no_whisper_call_has_worked_and_vram_is_short(
            self, monkeypatch):
        now = [0.0]
        scope = ollama_unload.JobScope(True, min_free_mb=2000, clock=lambda: now[0])
        free = [500.0]
        monkeypatch.setattr(ollama_unload, "_free_vram_mb", lambda: free[0])
        assert len(self._loader_calls(scope, 1, monkeypatch)) == 1
        now[0] = 10.0   # inside the recheck window
        assert len(self._loader_calls(scope, 1, monkeypatch)) == 1
        now[0] = 100.0
        free[0] = 9000.0   # plenty of room now
        assert len(self._loader_calls(scope, 1, monkeypatch)) == 1
        free[0] = 500.0
        assert len(self._loader_calls(scope, 1, monkeypatch)) == 2
        scope.model_ready = True   # a Whisper call has worked: nothing left to free for
        now[0] = 500.0
        assert len(self._loader_calls(scope, 1, monkeypatch)) == 2

    def _run_job(self, job, monkeypatch, engine, job_id, use_gpu):
        stream, start, notes = job
        freed = []
        monkeypatch.setattr(ollama_unload, "_free_ollama_gpu_memory",
                            lambda: freed.append(1) or "Ollama still has a model loaded (qwen3:8b).")

        def whisper(path, **kw):
            _prepare(True)   # the loader's hook, per call
            return [{"start": 0.5, "end": 1.0, "text": "x"}]
        monkeypatch.setattr(core, "transcribe_for_timing", whisper)
        monkeypatch.setattr(live_whisper, "warm_up",
                            lambda size, gpu, cb=None: _prepare(gpu))
        stream.add(3)
        assert start(job_id, engine, use_gpu=use_gpu)
        assert _wait(lambda: len(_texts(job_id)) >= 2)
        return freed, notes

    def test_a_job_with_a_local_ollama_frees_it_once_and_shows_the_notice(self, job, monkeypatch):
        class LocalOllama(Engine):
            name, model = "ollama", "qwen3:8b"
        freed, notes = self._run_job(job, monkeypatch, LocalOllama(), "live_ol", True)
        assert len(freed) == 1
        assert "Ollama still has a model loaded (qwen3:8b)." in notes

    def test_a_job_with_a_cloud_translator_never_touches_ollama(self, job, monkeypatch):
        class Hosted(Engine):
            name, model = "deepseek", "deepseek-chat"
        freed, notes = self._run_job(job, monkeypatch, Hosted(), "live_cloud", True)
        assert freed == [] and not any("Ollama" in n for n in notes)

    def test_ollama_cloud_models_are_hosted_too(self, job, monkeypatch):
        class OllamaCloud(Engine):
            name, model = "ollama", "gpt-oss:120b-cloud"
        freed, _ = self._run_job(job, monkeypatch, OllamaCloud(), "live_oc", True)
        assert freed == []


def _in_scope(scope):
    with ollama_unload.job_scope(scope):
        _prepare(True)
