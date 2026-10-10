"""Keeps Live's Whisper calls from stalling the job or outlasting Stop.

faster-whisper cannot be interrupted inside a decode step, and it yields segments
only after decoding a whole 30 s window, so a call that is given up on usually
keeps running for a while. WhisperRunner therefore owns the ONE thread Live may
have inside Whisper at a time: while an abandoned call is still alive, no other
call (and so no second model load or competing decode) is started; the chunk is
skipped instead. The cached model is never dropped to get rid of a stuck call:
the thread still holds it, so dropping frees no memory and only makes the next
call load a second copy.
"""
import os
import sys
import threading
import time
import traceback

import background_jobs
import ollama_unload

# A chunk should take well under its own length; this is generous so a busy GPU
# or a slow CPU is warned about long before a chunk is given up on.
MIN_CHUNK_TIMEOUT = 30.0
CHUNK_TIMEOUT_FACTOR = 6.0
# Covers a first-time model download, which has no other time limit.
WARM_UP_TIMEOUT = 900.0
POLL_SECONDS = 0.1
# How long an abandoned worker gets to reach its next segment before it is
# treated as stuck; kept short so Stop returns within a couple of seconds.
ABANDON_GRACE_SECONDS = 1.0
WARM_UP_SECONDS = 2
WARM_UP_NOISE_LEVEL = 0.01


class ChunkTimeout(Exception):
    """Whisper did not finish a chunk within the limit; `seconds` is the limit."""

    def __init__(self, seconds: float):
        super().__init__(f"Whisper did not finish in {seconds:.0f} s")
        self.seconds = seconds


class WhisperBusy(Exception):
    """An earlier, abandoned Whisper call is still running; `label` names it."""

    def __init__(self, label: str):
        super().__init__(f"Whisper is still busy with {label}")
        self.label = label


class _Abandoned(Exception):
    """Raised inside the worker, from its per-segment callback, to end the decode."""


def chunk_timeout(segment_seconds: float) -> float:
    return max(MIN_CHUNK_TIMEOUT, CHUNK_TIMEOUT_FACTOR * float(segment_seconds))


def _log(level: str, text: str) -> None:
    """App log only: a stack names file paths, which never go to a client."""
    try:
        import applog
        getattr(applog.get_logger(), level)(text)
    except Exception:
        pass


# The one Whisper thread this process may have, across jobs: a per-runner field
# would let Stop then Start put a second decode beside an abandoned, stuck one.
# A call the job gave up on while it was alive also holds a gpu_lock claim until
# the thread exits.
_outstanding_lock = threading.Lock()
_outstanding = None


def outstanding_label():
    """Label of the Whisper call still alive in this process, else None. It
    outlives an abandoned call's job, so admin "nothing running" checks use it:
    the thread may still write the model cache or release its gpu_lock row."""
    with _outstanding_lock:
        entry = _outstanding
        if entry is not None and entry["worker"].is_alive():
            return entry["label"]
    return None


def gpu_claim_held() -> bool:
    """True while an abandoned GPU call of this process holds its gpu_lock claim."""
    with _outstanding_lock:
        entry = _outstanding
        return bool(entry is not None and entry["claimed"] and entry["worker"].is_alive())


CLAIM_PREFIX = "live-whisper:"
# Per process, so a second launch's startup sweep can tell this process's
# claim from a dead one's.
CLAIM_HOLDER = f"{CLAIM_PREFIX}{os.getpid()}"
# Shown on a queued job when only the abandoned call holds the GPU.
WAIT_MESSAGE = "Waiting for the GPU (a Live Whisper call is still finishing)"


def _redacted(exc: BaseException) -> str:
    try:
        from translate_engines import redact_secrets
        return redact_secrets(str(exc))
    except Exception:
        return type(exc).__name__


def wait_for_outstanding(timeout: float) -> bool:
    """Joins the Whisper thread still alive in this process; True once none is.
    Admin actions that replace the database or the model cache call this: an
    abandoned worker can still be writing the model or releasing its gpu_lock
    row after its job has ended."""
    with _outstanding_lock:
        entry = _outstanding
    if entry is not None and entry["worker"].is_alive():
        entry["worker"].join(max(0.0, timeout))
    return outstanding_label() is None


def release_stale_claims() -> int:
    """Deletes `live-whisper:<pid>` gpu_lock rows whose process is gone; run at
    server startup. A claim whose process died would otherwise keep GPU jobs
    queued until the row goes stale. A live pid's row is another running
    instance's abandoned decode, still in its VRAM. This process's own pid
    counts as gone: no Whisper thread exists yet, so the row is from an earlier
    run that had the same pid."""
    import contextlib
    import db
    released = 0
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT holder FROM gpu_lock WHERE holder LIKE ?", (CLAIM_PREFIX + "%",)).fetchall()
        for row in rows:
            suffix = row["holder"][len(CLAIM_PREFIX):]
            if suffix.isdigit() and int(suffix) != os.getpid() and (
                    background_jobs.owner_process_alive(int(suffix))):
                continue
            released += conn.execute(
                "DELETE FROM gpu_lock WHERE holder = ?", (row["holder"],)).rowcount
        conn.commit()
    return released


def _claim_gpu(entry: dict) -> None:
    """Holds a gpu_lock slot while an abandoned GPU call may still decode, so a
    queued GPU job (or a CLI run) is not promoted into its VRAM after the Live
    job released its own slot. Never heartbeated: a call stuck forever stops
    counting after db.GPU_LOCK_STALE_SECONDS, like any silent holder."""
    if not entry["gpu"]:
        return
    try:
        import db
        with _outstanding_lock:
            if entry["ended"]:
                return
            # Up to the table's cap, not the user's gpu_max_parallel: the Live
            # job still holds its own slot at this moment.
            entry["claimed"] = db.try_acquire_gpu_lock(
                CLAIM_HOLDER, "an abandoned Live Whisper decode", max_holders=db.GPU_LOCK_MAX_SLOTS)
    except Exception as exc:
        _log("warning", f"Live: could not claim the GPU for the abandoned call: {_redacted(exc)}")


def _worker_ended(entry: dict) -> None:
    """Releases the claim and wakes the queue: a GPU job held back by the claim
    has nothing else to wake it."""
    with _outstanding_lock:
        entry["ended"] = True
        held, entry["claimed"] = entry["claimed"], False
        if held:
            try:
                import db
                db.release_gpu_lock(CLAIM_HOLDER)
            except Exception as exc:
                _log("warning", f"Live: could not release the GPU claim: {_redacted(exc)}")
    if held:
        try:
            background_jobs._promote_next_queued_gpu_job()
        except Exception as exc:
            _log("warning", f"Live: could not promote the next GPU job: {_redacted(exc)}")


class WhisperRunner:
    """Runs Whisper calls one at a time on a worker thread the job polls. Stop
    and a time limit are noticed within a poll; the worker quits at its next
    segment. `clock`, `poll` and `grace` are injectable so tests need no sleeps."""

    def __init__(self, should_stop, note, clock=None, poll: float = None, grace: float = None,
                 unload: "ollama_unload.JobScope" = None, gpu: bool = False):
        self._should_stop = should_stop
        self._note = note
        self.unload = unload or ollama_unload.JobScope(False)
        self._clock = clock or time.monotonic
        self._poll = POLL_SECONDS if poll is None else poll
        self._grace = grace
        self._gpu = bool(gpu)
        self._closed = False

    @property
    def grace(self) -> float:
        return ABANDON_GRACE_SECONDS if self._grace is None else self._grace

    def busy_with(self):
        """Label of the call still running in this process, else None."""
        return outstanding_label()

    def close(self):
        """Marks the job over; returns the label of a call still running, to be
        named in the final status. That thread ends by itself at its next segment."""
        self._closed = True
        return self.busy_with()

    def run(self, call, timeout: float, label: str):
        """call(progress_cb) -> result. progress_cb(fraction) must be called by
        `call` once per segment: it is where an abandoned worker ends. Raises
        WhisperBusy when an earlier call is still alive, JobCancelled once Stop
        is pressed and ChunkTimeout after `timeout` seconds; the worker is
        abandoned in the last two cases."""
        abandoned = threading.Event()
        box = {}
        entry = {"worker": None, "label": label, "gpu": self._gpu, "claimed": False, "ended": False}

        def progress(_fraction):
            if abandoned.is_set():
                raise _Abandoned

        def work():
            try:
                with ollama_unload.job_scope(self.unload):
                    box["value"] = call(progress)
            except _Abandoned:
                pass
            except BaseException as exc:
                box["error"] = exc
            finally:
                if abandoned.is_set():
                    _log("info", f"Live: the abandoned Whisper call ({label}) has ended"
                         + (" after the job did" if self._closed else ""))
                _worker_ended(entry)

        global _outstanding
        worker = threading.Thread(target=work, daemon=True, name="live-whisper")
        entry["worker"] = worker
        # Read before the worker starts: a call that finishes its first step
        # (or a test clock moved by it) must not push the limit further out.
        deadline = self._clock() + timeout
        with _outstanding_lock:
            current = _outstanding
            if current is not None and current["worker"].is_alive():
                raise WhisperBusy(current["label"])
            worker.start()
            # Only a started thread is outstanding: join() on an unstarted one raises.
            _outstanding = entry
        while True:
            worker.join(self._poll)
            if not worker.is_alive():
                break
            if self._should_stop():
                self._abandon(entry, abandoned, "stop")
                raise background_jobs.JobCancelled("live whisper")
            if self._clock() >= deadline:
                self._abandon(entry, abandoned, "time limit")
                raise ChunkTimeout(timeout)
        if "error" in box:
            raise box["error"]
        return box["value"]

    def finish(self) -> str:
        """Closes the runner and returns the job's final status. A call that
        outlives the job is named in the notes, because the final status message
        is replaced by the cancel text."""
        busy = self.close()
        if busy:
            self._note(f"Whisper is still finishing {busy} in the background; it ends by itself.")
        return "Stopped."

    def timing(self, path: str, audio_seconds: float) -> "ChunkTiming":
        return ChunkTiming(path, audio_seconds, self._gpu)

    def flush_ollama_notice(self) -> None:
        text = self.unload.take_notice()
        if text:
            self._note(text)

    def mark_model_ready(self):
        self.unload.model_ready = True

    def _abandon(self, entry: dict, abandoned: threading.Event, why: str) -> None:
        worker = entry["worker"]
        abandoned.set()
        worker.join(self.grace)
        if not worker.is_alive():
            return
        _claim_gpu(entry)
        frame = sys._current_frames().get(worker.ident)
        stack = "".join(traceback.format_stack(frame)) if frame is not None else "(gone)"
        _log("warning", f"Live: Whisper call abandoned ({why}); it is at:\n{stack}")


def drop_backlog(pending: list, keep: int, skips: "SkipNotes"):
    """(waiting, last dropped index or None): deletes the files of the chunks
    split_backlog drops and counts them."""
    skipped, waiting = split_backlog(pending, keep)
    for _, stale_path in skipped:
        try:
            os.remove(stale_path)
        except OSError:
            pass
    if skipped:
        skips.catch_up(len(skipped))
    return waiting, (skipped[-1][0] if skipped else None)


def speed_text(took, audio) -> str:
    """Whisper's speed on the last chunk, or "" before there is one."""
    if took is None or audio <= 0:
        return ""
    return f"{audio:.0f} s of audio in {took:.1f} s ({took / audio:.2f}x real time)"


def unload_scope_for(engine, use_gpu: bool, whisper_size: str) -> "ollama_unload.JobScope":
    """Only a local Ollama holds VRAM Whisper could need; a hosted engine, and an
    Ollama cloud model (which runs elsewhere), never get it freed."""
    model = str(getattr(engine, "model", "") or "")
    local = (bool(use_gpu) and getattr(engine, "name", "") == "ollama"
             and not model.endswith(("-cloud", ":cloud")))
    return ollama_unload.JobScope(local, min_free_mb=vram_needed_mb(whisper_size))


def model_cached() -> bool:
    """Whether a Whisper model is already cached, so a chunk's time holds no load."""
    import core
    return bool(core._whisper_model_cache)


def chunk_age(path: str) -> float:
    """Seconds since the chunk file was finished, i.e. how long it waited."""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return 0.0


def split_backlog(pending: list, keep: int):
    """(skipped, kept): chunks beyond the newest `keep` are never transcribed, so
    a slow stretch costs the oldest audio instead of delay that only grows."""
    if keep < 1 or len(pending) <= keep:
        return [], list(pending)
    return list(pending[:-keep]), list(pending[-keep:])


def warm_up(whisper_size: str, use_gpu: bool, progress_cb=None) -> None:
    """Loads the model and decodes a couple of seconds of low-level noise,
    before the first chunk is captured: the load and CUDA/cuBLAS initialisation
    take seconds that would otherwise put chunk 1 behind the live edge. Calls
    the model directly with the VAD filter off: with it on, digital silence
    produces no speech, so the encoder never runs and nothing is initialised."""
    import core
    import numpy
    model = core.load_whisper_model(whisper_size, use_gpu=use_gpu)
    noise = numpy.random.default_rng(0).standard_normal(16000 * WARM_UP_SECONDS)
    audio = (noise * WARM_UP_NOISE_LEVEL).astype("float32")
    segments, _info = model.transcribe(audio, language="en", vad_filter=False, beam_size=1,
                                       condition_on_previous_text=False)
    for _segment in segments:
        if progress_cb:
            progress_cb(0.0)


# Rough VRAM faster-whisper needs per size (float16), for deciding whether a
# failed first load is worth freeing Ollama again.
_VRAM_NEEDED_MB = {"tiny": 1000, "base": 1000, "small": 2000, "medium": 3500}
_VRAM_NEEDED_LARGE_MB = 5500
# A chunk that takes more than this many times its own audio is called out in
# the status, so the owner sees a stall without opening the log.
SLOW_CHUNK_FACTOR = 3.0


def vram_needed_mb(whisper_size: str) -> float:
    return _VRAM_NEEDED_MB.get(whisper_size, _VRAM_NEEDED_LARGE_MB)


class SkipNotes:
    """Counts skipped chunks per reason into one note each. The status keeps only
    a few notes, so one line per skipped chunk would push the earlier ones out
    exactly while skipping is happening. `note(text, key)` replaces the note
    with the same key and moves it to the newest position."""

    def __init__(self, note, segment_seconds: float):
        self._note = note
        self._seconds = segment_seconds
        self._counts = {"catch_up": 0, "timeout": 0, "busy": 0}

    def _count(self, reason: str, chunks: int) -> int:
        self._counts[reason] += chunks
        return self._counts[reason]

    def _chunks_text(self, total: int) -> str:
        return f"{total} chunks (~{total * self._seconds:.0f} s)"

    def catch_up(self, chunks: int) -> None:
        total = self._count("catch_up", chunks)
        self._note(f"Skipped {self._chunks_text(total)} to catch up.", "skip-catch-up")

    def timed_out(self, idx: int, limit: float) -> None:
        total = self._count("timeout", 1)
        if total == 1:
            text = f"Skipped chunk {idx} (Whisper did not finish in {limit:.0f} s)."
        else:
            text = (f"Skipped {total} chunks (Whisper did not finish in {limit:.0f} s; "
                    f"latest: chunk {idx}).")
        self._note(text, "skip-timeout")

    def busy(self, idx: int, busy_label: str) -> None:
        total = self._count("busy", 1)
        what = f"chunk {idx}" if total == 1 else self._chunks_text(total)
        self._note(f"Skipped {what}: Whisper is still busy with {busy_label}.", "skip-busy")


class ChunkTiming:
    """Where one chunk's time went: waiting for its turn, Whisper (which holds
    the model load on a cold start), then translation."""

    def __init__(self, path: str, audio_seconds: float, use_gpu: bool = False):
        self.audio = audio_seconds
        self.use_gpu = use_gpu
        self.cold = not model_cached()
        self.queued = chunk_age(path)
        self.whisper = 0.0
        self._whisper_done = None

    def whisper_done(self, took: float) -> None:
        self.whisper, self._whisper_done = took, time.monotonic()

    def report(self, idx: int, note) -> None:
        translate = time.monotonic() - (self._whisper_done or time.monotonic())
        log_chunk(idx, self.audio, self.queued, self.whisper, translate, self.cold, note,
                  self.use_gpu)


def warm_start(runner: "WhisperRunner", whisper_size: str, use_gpu: bool) -> float:
    """Seconds the model took to be ready; the load runs under the runner, so
    Stop and the time limit apply and no other Whisper call starts beside it."""
    started = time.monotonic()
    runner.run(lambda progress_cb: warm_up(whisper_size, use_gpu, progress_cb),
               WARM_UP_TIMEOUT, "the model load")
    return time.monotonic() - started


def log_chunk(idx: int, audio: float, queued: float, whisper: float, translate: float,
              cold: bool, note, use_gpu: bool = False) -> None:
    """One app-log line per chunk with where the time went, plus nvidia-smi's
    free/total VRAM when it can be read (numbers only: nothing to redact beyond
    what redact_secrets would pass anyway). A chunk far slower than its audio
    also gets a one-line status note."""
    total = queued + whisper + translate
    slow = audio > 0 and total > SLOW_CHUNK_FACTOR * audio
    gpu = ""
    # nvidia-smi is a subprocess on the job thread: only worth it for a GPU run
    # whose chunk was slow, when the VRAM reading explains why.
    if use_gpu and slow:
        try:
            import diagnostics
            load = diagnostics.external_gpu_load()
            if load:
                gpu = f", GPU free {load['memory_free_mb']:.0f} of {load['memory_total_mb']:.0f} MB"
        except Exception:
            pass
    line = (f"chunk {idx}: {audio:.1f} s of audio; waited {queued:.1f} s, Whisper {whisper:.1f} s"
            f"{' (model was not loaded yet)' if cold else ''}, translate {translate:.1f} s{gpu}")
    try:
        from translate_engines import redact_secrets
        _log("info", "Live " + redact_secrets(line))
    except Exception:
        pass
    if slow:
        note(f"Chunk {idx} took {total:.0f} s for {audio:.0f} s of audio: waited {queued:.1f} s, "
             f"Whisper {whisper:.1f} s, translate {translate:.1f} s{gpu}.", "slow-chunk")
