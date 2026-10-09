"""Keeps one Live chunk's Whisper call from stalling the job or outlasting Stop.

faster-whisper cannot be interrupted inside a decode step, but it yields segments
lazily and core.transcribe_for_timing reports progress once per segment. So the
call runs on a worker thread that the job polls: Stop and a per-chunk time limit
are noticed within a poll, and the worker quits at its next segment (or, if it is
stuck inside a step, is abandoned and the cached model is dropped so the next
chunk loads a fresh one instead of queueing behind it).
"""
import os
import sys
import tempfile
import threading
import time
import traceback
import wave

import background_jobs

# A chunk should take well under its own length; this is generous so a busy GPU
# or a slow CPU is warned about long before a chunk is given up on.
MIN_CHUNK_TIMEOUT = 30.0
CHUNK_TIMEOUT_FACTOR = 6.0
POLL_SECONDS = 0.1
# How long an abandoned worker gets to reach its next segment before it is
# treated as stuck; kept short so Stop returns within a couple of seconds.
ABANDON_GRACE_SECONDS = 1.0


class ChunkTimeout(Exception):
    """Whisper did not finish a chunk within the limit; `seconds` is the limit."""

    def __init__(self, seconds: float):
        super().__init__(f"Whisper did not finish in {seconds:.0f} s")
        self.seconds = seconds


class _Abandoned(Exception):
    """Raised inside the worker, from its per-segment callback, to end the decode."""


def chunk_timeout(segment_seconds: float) -> float:
    return max(MIN_CHUNK_TIMEOUT, CHUNK_TIMEOUT_FACTOR * float(segment_seconds))


def _log_stuck(worker: threading.Thread, why: str) -> None:
    """Where the abandoned worker is stuck, in the app log only (a stack names
    file paths, which never go to a client)."""
    try:
        import applog
        frame = sys._current_frames().get(worker.ident)
        stack = "".join(traceback.format_stack(frame)) if frame is not None else "(gone)"
        applog.get_logger().warning(f"Live: Whisper worker abandoned ({why}); it is at:\n{stack}")
    except Exception:
        pass


def _abandon(worker: threading.Thread, abandoned: threading.Event, why: str) -> None:
    abandoned.set()
    worker.join(ABANDON_GRACE_SECONDS)
    if not worker.is_alive():
        return
    _log_stuck(worker, why)
    import core
    # The stuck call still holds the cached model; a new chunk must not wait on it.
    core.release_gpu_models()


def run_guarded(call, should_stop, timeout: float, poll: float = POLL_SECONDS):
    """call(progress_cb) -> result, run on a worker thread. Raises
    background_jobs.JobCancelled as soon as should_stop() is true and
    ChunkTimeout once `timeout` seconds pass; the worker is abandoned either
    way. progress_cb(fraction) must be called by `call` once per segment: it is
    where an abandoned worker ends."""
    abandoned = threading.Event()
    box = {}

    def progress(_fraction):
        if abandoned.is_set():
            raise _Abandoned

    def work():
        try:
            box["value"] = call(progress)
        except _Abandoned:
            pass
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="live-whisper")
    deadline = time.monotonic() + timeout
    worker.start()
    while True:
        worker.join(poll)
        if not worker.is_alive():
            break
        if should_stop():
            _abandon(worker, abandoned, "stop")
            raise background_jobs.JobCancelled("live whisper")
        if time.monotonic() >= deadline:
            _abandon(worker, abandoned, "time limit")
            raise ChunkTimeout(timeout)
    if "error" in box:
        raise box["error"]
    return box["value"]


def split_backlog(pending: list, keep: int):
    """(skipped, kept): chunks beyond the newest `keep` are never transcribed, so
    a slow stretch costs the oldest audio instead of delay that only grows."""
    if keep < 1 or len(pending) <= keep:
        return [], list(pending)
    return list(pending[:-keep]), list(pending[-keep:])


def warm_up(whisper_size: str, use_gpu: bool) -> None:
    """Loads the model and runs one second of silence through it, before the
    first chunk is captured: the load and the first CUDA call take seconds that
    would otherwise put chunk 1 behind the live edge."""
    import core
    with tempfile.TemporaryDirectory(prefix="baihe_warm_") as tmp:
        path = os.path.join(tmp, "silence.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        core.transcribe_for_timing(path, model_size=whisper_size, language="en", use_gpu=use_gpu)
