"""
tests/test_live_cancel.py -- a cancel reaches a Live job wherever it is
blocked: an Ollama request that never answers, a capture subprocess that
never produces a chunk, the stream lookup, and a Whisper call (abandoned on a worker
thread, since faster-whisper cannot be interrupted mid-step).

The blocked Ollama server is a real local socket that accepts and never
replies; the capture is a real `sh` that leaves a `sleep` grandchild.
"""
import functools
import os
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
import live_translate as lt
from services import job_stage_service
from engine_backends import local
from engine_backends.shared import TranslationCancelled

PROMPT = 3.0   # "within a few seconds"


def _wait(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class SilentServer:
    """Accepts connections and never answers; records when a client hangs up."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self.hung_up = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            threading.Thread(target=self._watch, args=(conn,), daemon=True).start()

    def _watch(self, conn):
        try:
            while conn.recv(65536):
                pass   # the request body; no reply
        except OSError:
            pass
        self.hung_up += 1
        conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture
def silent_ollama(monkeypatch):
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    server = SilentServer()
    yield server
    server.close()


@pytest.fixture
def jobs(isolated_db):
    background_jobs.clear_all_jobs()
    yield
    for jid in list(background_jobs._jobs):
        background_jobs.request_cancel(jid)
    background_jobs.clear_all_jobs()


class TestOllamaCallIsAbortable:
    def test_cancel_closes_the_blocked_request(self, silent_ollama):
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        token = local.abort_check_var.set(cancel.is_set)
        started = time.monotonic()
        try:
            with pytest.raises(TranslationCancelled):
                local._ollama_chat(f"http://127.0.0.1:{silent_ollama.port}",
                                   {"model": "qwen3:8b", "messages": []})
        finally:
            local.abort_check_var.reset(token)
        assert time.monotonic() - started < PROMPT
        assert silent_ollama.accepted == 1
        # The connection was closed, which is what makes Ollama stop generating.
        assert _wait(lambda: silent_ollama.hung_up == 1)

    def test_without_a_cancel_check_the_request_path_is_unchanged(self, monkeypatch):
        seen = {}

        def fake_post(url, json=None, stream=None, timeout=None):
            seen.update(url=url, timeout=timeout)
            raise local.OllamaUnavailableError("ollama_unreachable", "x")
        monkeypatch.setattr("requests.post", fake_post)
        with pytest.raises(local.OllamaUnavailableError):
            local._ollama_chat("http://localhost:11434", {"model": "m"})
        assert seen["timeout"] == local.OLLAMA_CHAT_TIMEOUT


def _shown(job_id):
    """The message as the Jobs menu and the Live page read it."""
    return job_stage_service.annotate(background_jobs.get_status(job_id))["message"]


def _chunk_dir(tmp_path, count=2):
    out_dir = tmp_path / "chunks"
    out_dir.mkdir()
    for i in range(count):
        (out_dir / f"chunk_{i:05d}.wav").write_bytes(b"")
    return str(out_dir)


class FakeProc:
    error = None

    def poll(self):
        return None


def _start(job_id, out_dir, engine, **kw):
    return background_jobs.start_job(
        job_id, lt.run_live_job, job_id, "http://example.com/live", out_dir, 10, "zh", "small",
        engine, poll_interval=0.05, overlap_seconds=0,
        report_stage=functools.partial(job_stage_service.set_stage, job_id), **kw)


class TestBlockedOllamaTranslation:
    def test_status_names_ollama_then_cancel_returns_promptly(
            self, jobs, silent_ollama, tmp_path, monkeypatch):
        import core
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
        stopped = []
        monkeypatch.setattr(lt, "stop_capture", lambda proc, **kw: stopped.append(kw))
        monkeypatch.setattr(core, "transcribe_for_timing",
                            lambda path, **kw: [{"start": 0.0, "end": 2.0, "text": "你好"}])
        engine = local.OllamaEngine(model="qwen3:8b",
                                    base_url=f"http://127.0.0.1:{silent_ollama.port}")
        assert _start("live_cancel_ollama", _chunk_dir(tmp_path), engine)

        assert _wait(lambda: silent_ollama.accepted == 1)
        message = _shown("live_cancel_ollama")
        assert "translating with qwen3:8b" in message and "Ollama" in message

        started = time.monotonic()
        background_jobs.request_cancel("live_cancel_ollama")
        assert _wait(lambda: background_jobs.get_status("live_cancel_ollama")["status"] != "running",
                     PROMPT)
        assert time.monotonic() - started < PROMPT
        assert _wait(lambda: silent_ollama.hung_up == 1)
        assert stopped == [{"kill": True}]
        # The transcript stays, marked as never translated.
        result = background_jobs.get_status("live_cancel_ollama")["result"]
        assert [(c["text"], c["translated"], c["translation"]) for c in result] == [
            ("你好", "", "cancelled")]

    def test_a_long_wait_on_ollama_is_said_plainly(self, jobs, monkeypatch):
        background_jobs._jobs["j"] = {"status": "running", "message": "", "progress": 0.0,
                                      "cancel_requested": False, "result": None}
        now = [1000.0]
        monkeypatch.setattr(job_stage_service.time, "time", lambda: now[0])
        job_stage_service.set_stage(
            "j", "Chunk 1: translating with qwen3:8b (Ollama)", slow_after=60,
            slow_note="Still waiting on Ollama after {secs} s: it may be loading the model.")
        assert _shown("j") == "Chunk 1: translating with qwen3:8b (Ollama)"
        now[0] += 75
        assert _shown("j").endswith(
            "Still waiting on Ollama after 75 s: it may be loading the model.")
        # Once cancel is asked the cancel text wins over the slow note.
        background_jobs.request_cancel("j")
        assert _shown("j") == background_jobs.CANCELLING_MESSAGE


    def test_the_slow_note_is_pushed_without_a_poll(self, jobs):
        background_jobs._jobs["j2"] = {"status": "running", "message": "", "progress": 0.0,
                                       "cancel_requested": False, "result": None}
        events = []
        background_jobs.add_change_listener(events.append)
        try:
            job_stage_service.set_stage("j2", "Chunk 1: translating", slow_after=0.05,
                                      slow_note="Still waiting on Ollama after {secs} s.")
            seen = len(events)
            assert _wait(lambda: len(events) > seen)
            assert "Still waiting on Ollama" in _shown("j2")
        finally:
            background_jobs.remove_change_listener(events.append)


class TestWhisperIsAbandonedOnCancel:
    def test_a_stuck_whisper_call_does_not_hold_up_a_cancel(self, jobs, tmp_path, monkeypatch):
        import core
        release = threading.Event()
        inside = threading.Event()

        def blocked_whisper(path, **kw):
            inside.set()
            release.wait(10)   # never reaches a segment, so it can only be abandoned
            return []
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
        monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: FakeProc())
        monkeypatch.setattr(lt, "stop_capture", lambda proc, **kw: None)
        monkeypatch.setattr(core, "transcribe_for_timing", blocked_whisper)
        released = []
        monkeypatch.setattr(core, "release_gpu_models", lambda: released.append(1))
        assert _start("live_cancel_whisper", _chunk_dir(tmp_path), object(), use_gpu=True)
        assert inside.wait(PROMPT)
        assert "transcribing 0 s of audio with Whisper small (GPU)" in \
            _shown("live_cancel_whisper")

        started = time.monotonic()
        background_jobs.request_cancel("live_cancel_whisper")
        message = _shown("live_cancel_whisper")
        assert "stopping Whisper on chunk 0" in message and "cannot be interrupted" not in message
        assert _wait(lambda: background_jobs.get_status("live_cancel_whisper")["status"] != "running",
                     PROMPT)
        assert time.monotonic() - started < PROMPT
        # Dropping the cached model frees nothing while the stuck call holds it, and
        # the next run would load a second copy beside it; the call is named instead.
        assert released == []
        release.set()


class TestBlockedLookupAndCapture:
    def test_cancel_during_the_stream_lookup(self, jobs, tmp_path, monkeypatch):
        release = threading.Event()
        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: release.wait(30))
        started_capture = []
        monkeypatch.setattr(lt, "start_segment_capture",
                            lambda *a, **k: started_capture.append(1))
        assert _start("live_cancel_resolve", str(tmp_path), object())
        assert _wait(lambda: background_jobs.get_status("live_cancel_resolve")["message"]
                     .startswith("Resolving"))
        started = time.monotonic()
        background_jobs.request_cancel("live_cancel_resolve")
        assert _wait(lambda: background_jobs.get_status("live_cancel_resolve")["status"] != "running",
                     PROMPT)
        assert time.monotonic() - started < PROMPT
        assert started_capture == []
        release.set()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
    def test_cancel_kills_the_capture_tree(self, jobs, tmp_path, monkeypatch):
        pidfile = tmp_path / "grandchild.pid"
        # Like ffmpeg: its own session, and a child that outlives a plain kill.
        proc = subprocess.Popen(
            ["sh", "-c", f"sleep 300 & echo $! > {pidfile}; wait"],
            stdin=subprocess.PIPE, start_new_session=True)

        class Pump:
            error = None

            def halt(self):
                pass

            def join(self):
                pass

        monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
        monkeypatch.setattr(lt, "start_segment_capture",
                            lambda *a, **k: lt.SegmentCapture(proc, Pump()))
        try:
            assert _start("live_cancel_capture", str(tmp_path / "empty"), object())
            assert _wait(lambda: pidfile.exists() and pidfile.read_text().strip())
            grandchild = int(pidfile.read_text())
            assert _wait(lambda: background_jobs.get_status("live_cancel_capture")["message"]
                         .startswith("Capturing audio"))

            started = time.monotonic()
            background_jobs.request_cancel("live_cancel_capture")
            assert _wait(lambda: background_jobs.get_status("live_cancel_capture")["status"]
                         != "running", PROMPT)
            assert time.monotonic() - started < PROMPT
            assert proc.poll() is not None
            assert _wait(lambda: _gone(grandchild))
        finally:
            if proc.poll() is None:
                background_jobs.kill_tree(proc)
            proc.stdin.close()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
    def test_stop_capture_kill_skips_the_graceful_wait(self):
        # A process that ignores SIGTERM would otherwise cost the 5 s timeout.
        proc = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 300"],
                                start_new_session=True)
        started = time.monotonic()
        lt.stop_capture(proc, kill=True)
        assert proc.poll() is not None
        assert time.monotonic() - started < PROMPT


def _gone(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] == "Z"
    except FileNotFoundError:
        if os.path.isdir("/proc"):
            return True
        # No /proc (macOS): a missing file says nothing, so probe the pid itself.
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return False
        return False
    except OSError:
        return False
