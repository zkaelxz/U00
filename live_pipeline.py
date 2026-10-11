"""The parts of a Live session that run beside the capture loop: cue translation
on its own thread, the cue list both threads write, the status line both report
to, the Stop-and-finish request, and the lag figures the page shows.

Whisper for chunk N+1 runs while chunk N's lines are translated, so a slow
translator costs translations (shown untranslated and counted once the queue
is full), never Whisper time or audio. docs/user-guide.md (Live) describes the
figures for the owner."""
import collections
import contextlib
import statistics
import threading
import time

import background_jobs
import live_cue_feed
import live_whisper

# Chunks waiting for translation behind the one being translated. Beyond this the
# oldest waiting chunk is shown untranslated: the queue never grows without bound
# and the lines it does translate stay close to the speech.
MAX_TRANSLATE_BACKLOG = 2
# Chunks in the rolling median the auto video delay is built from: enough to ride
# out one slow chunk, few enough to follow a change of load within a minute or two.
LAG_WINDOW = 5
# A final chunk shorter than this is a file ffmpeg had only just begun when it
# stopped: there is nothing in it for Whisper to hear.
MIN_FINAL_SECONDS = 0.5
# The capture loop's longest sleep when no chunk is waiting. ffmpeg gives no
# signal when it starts a new file, so this bounds how late a chunk is noticed.
IDLE_WAIT_SECONDS = 0.25

_controls = {}
_controls_lock = threading.Lock()


class Control:
    """What another thread may ask of a running capture loop: finish (Stop
    pressed once: caption what was already captured, then end) and wake (stop
    sleeping now)."""

    def __init__(self):
        self.finish = threading.Event()
        self.wake = threading.Event()

    def idle(self, timeout: float = IDLE_WAIT_SECONDS) -> None:
        if self.wake.wait(timeout):
            self.wake.clear()


def register(job_id: str) -> Control:
    control = Control()
    with _controls_lock:
        _controls[job_id] = control
    return control


def unregister(job_id: str, control: Control) -> None:
    with _controls_lock:
        if _controls.get(job_id) is control:
            del _controls[job_id]


def request_finish(job_id: str) -> bool:
    """True when a capture loop took the request. False when there is none to
    take it (still starting, already ended) or it was asked already: the caller
    then stops the session at once."""
    with _controls_lock:
        control = _controls.get(job_id)
        if control is None or control.finish.is_set():
            return False
        control.finish.set()
    control.wake.set()
    return True


class CueBoard:
    """The session's cues, in id order. One lock covers a change and the publish
    after it, so a snapshot never sees a cue half-updated and an older snapshot
    never overwrites a newer one. Nothing is published once `live()` is false
    (Stop now moved the session on)."""

    def __init__(self, job_id: str, live):
        self.lock = threading.RLock()
        self.cues = []
        self._job_id = job_id
        self._live = live

    def publish(self, _chunk=None) -> None:
        with self.lock:
            if self._live():
                background_jobs.set_result(self._job_id, live_cue_feed.snapshot(self.cues, []))

    def add(self, cues: list) -> None:
        with self.lock:
            self.cues.extend(cues)
            self.publish()

    def mark_pending(self, cues: list, state: str) -> int:
        """Gives every still-pending cue of `cues` `state`; returns how many."""
        with self.lock:
            changed = [c for c in cues if c.get("translation") == live_cue_feed.PENDING]
            for cue in changed:
                cue["translation"] = state
            if changed:
                self.publish()
        return len(changed)


class Stage:
    """The one status line both threads report to. Whisper's step wins while it
    runs (Stop's text names the chunk it interrupts); otherwise the chunk being
    translated; otherwise the capture wait."""

    def __init__(self, report, note, whisper_size: str, device: str, limit: float, engine,
                 segment_seconds: int):
        self._report, self._note = report, note
        self._whisper_size, self._device, self._limit = whisper_size, device, limit
        self._segment_seconds = segment_seconds
        # The engine name and model are not secrets (no key, no URL), so they
        # can be named in the status the owner reads.
        self._engine_label = getattr(engine, "model", None) or getattr(engine, "name", "") or "the engine"
        self._engine_name = str(getattr(engine, "name", "") or "the translator").capitalize()
        self._lock = threading.Lock()
        self._key = None
        self._whisper = None
        self._translating = None
        self._next = 0
        # audio/took: seconds of audio Whisper was handed for the current / last chunk.
        self.audio, self._took, self._last_audio = 0.0, None, 0.0
        self._slow_noted = False

    def _speed(self) -> str:
        return live_whisper.speed_text(self._took, self._last_audio)

    def chunk(self, stage: str, idx: int) -> None:
        """process_chunk's on_stage."""
        with self._lock:
            if stage == "transcribing":
                self._whisper = idx
            else:
                self._translating = idx
            self._refresh()

    def whisper_over(self) -> None:
        with self._lock:
            self._whisper = None

    def transcribed(self, idx: int, took: float) -> None:
        audio = self.audio
        with self._lock:
            self._took, self._last_audio = took, audio
            noted, self._slow_noted = self._slow_noted, self._slow_noted or took > audio > 0
        if took > audio > 0 and not noted:
            self._note(f"Chunk {idx}: Whisper took {took:.1f} s for {audio:.0f} s of audio, "
                       "slower than the stream, so the oldest audio will be skipped.")

    def translated(self) -> None:
        with self._lock:
            self._translating = None
            self._refresh()

    def waiting(self, next_idx: int) -> None:
        with self._lock:
            self._next = next_idx
            self._refresh()

    def _refresh(self) -> None:
        if self._whisper is not None:
            self._enter(("transcribing", self._whisper), self._transcribing)
        elif self._translating is not None:
            self._enter(("translating", self._translating), self._translating_line)
        else:
            self._enter(("capturing", self._next), self._capturing)

    def _enter(self, key, show) -> None:
        if self._key != key:
            self._key = key
            show(key[1])

    def _transcribing(self, idx: int) -> None:
        self._report(
            f"Chunk {idx}: transcribing {self.audio:.0f} s of audio with Whisper "
            f"{self._whisper_size} ({self._device})",
            cancel_message=(f"Cancelling... stopping Whisper on chunk {idx} (running for "
                            "{secs} s); this takes a second or two."),
            slow_after=min(90.0, self._limit / 2),
            slow_note="Still transcribing after {secs} s: Whisper is slow on this hardware "
                      f"or the GPU is busy. The chunk is skipped at {self._limit:.0f} s.")

    def _translating_line(self, idx: int) -> None:
        speed = self._speed()
        self._report(
            f"Chunk {idx}: translating with {self._engine_label} ({self._engine_name})"
            + (f" · transcribed {speed}" if speed else ""),
            slow_after=60,
            slow_note="Still waiting on " + self._engine_name + " after {secs} s: it may be "
                      "loading the model or busy with another job. Stop still works.")

    def _capturing(self, idx: int) -> None:
        speed = self._speed()
        self._report(
            f"Capturing audio: waiting for chunk {idx} "
            f"({self._segment_seconds} s of stream each)" + (f" · last chunk: {speed}" if speed else ""),
            slow_after=self._segment_seconds * 2 + 30,
            slow_note="No new audio for {secs} s: the stream may have stalled.")


class LagStats:
    """Numbers only (no path, URL or text), for the session status. lag_seconds:
    how long after the first word of the newest captioned chunk was spoken its
    lines were translated (its audio plus its processing). processing_seconds:
    the rolling median of wait + Whisper + translate-queue wait + translate."""

    def __init__(self, publish):
        self._publish = publish or (lambda numbers: None)
        self._lock = threading.Lock()
        self._totals = collections.deque(maxlen=LAG_WINDOW)
        self._lag = None
        self._measured = 0
        self.skipped = 0
        self.queue = 0

    def figures(self) -> dict:
        with self._lock:
            return {"lag_seconds": round(self._lag, 1) if self._lag is not None else 0.0,
                    "processing_seconds": round(statistics.median(self._totals), 1) if self._totals else 0.0,
                    "translations_skipped": int(self.skipped), "translate_queue": int(self.queue),
                    "chunks_measured": int(self._measured)}

    def chunk_done(self, audio_seconds: float, total: float) -> None:
        with self._lock:
            self._totals.append(float(total))
            self._lag = float(audio_seconds) + float(total)
            self._measured += 1
        self.send()

    def send(self) -> None:
        self._publish(self.figures())


class _Work:
    def __init__(self, idx, cues, timing, audio):
        self.idx, self.cues, self.timing, self.audio = idx, cues, timing, audio
        self.queued_at = time.monotonic()


class TranslationWorker:
    """Translates chunks on one thread, oldest first. submit() never blocks: past
    MAX_TRANSLATE_BACKLOG the oldest waiting chunk's lines are marked skipped and
    counted. finish() lets the queue run dry; abandon() drops it and aborts the
    request in flight (an Ollama request at its next poll, any other at its own
    timeout). Both return only once the thread has ended, so it never outlives
    the session."""

    def __init__(self, translator, board: CueBoard, stage: Stage, stats: LagStats, note,
                 cancelled, wake: threading.Event):
        self._translator, self._board, self._stage, self._stats = translator, board, stage, stats
        self._note, self._cancelled, self._wake = note, cancelled, wake
        self._cond = threading.Condition()
        self._queue = collections.deque()
        self._closing = self._abandoned = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="live-translate")
        self._thread.start()

    def _stop_now(self) -> bool:
        return self._abandoned or self._cancelled()

    def submit(self, idx: int, cues: list, timing, audio: float) -> None:
        with self._cond:
            if self._abandoned:
                return
            dropped = []
            while len(self._queue) >= MAX_TRANSLATE_BACKLOG:
                dropped.append(self._queue.popleft())
            self._queue.append(_Work(idx, cues, timing, audio))
            self._stats.queue = len(self._queue)
            self._cond.notify_all()
        skipped = sum(self._board.mark_pending(w.cues, live_cue_feed.SKIPPED) for w in dropped)
        if skipped:
            self._stats.skipped += skipped
            self._note(f"Skipped translating {self._stats.skipped} lines to keep up with the "
                       "stream; their transcript is shown.", "skip-translate")
        self._stats.send()

    def finish(self) -> None:
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        while self._thread.is_alive():
            self._thread.join(IDLE_WAIT_SECONDS)
            if self._cancelled():
                self.abandon()

    def abandon(self) -> None:
        with self._cond:
            self._abandoned = True
            self._queue.clear()
            self._cond.notify_all()
        self._thread.join()

    def _next(self):
        with self._cond:
            while not self._queue and not self._closing and not self._abandoned:
                self._cond.wait()
            if self._abandoned or not self._queue:
                return None, 0
            work = self._queue.popleft()
            self._stats.queue = len(self._queue)
            return work, len(self._queue)

    def _run(self) -> None:
        # Context variables do not cross threads: without these an Ollama request
        # here could not be abandoned, and a retry backoff would not see Stop.
        from engine_backends.local import abort_check_var
        from translate_engines import _cancel_check_var
        tokens = (_cancel_check_var.set(self._stop_now), abort_check_var.set(self._stop_now))
        try:
            while True:
                work, depth = self._next()
                if work is None:
                    return
                if not self._translate(work, depth):
                    return
        finally:
            _cancel_check_var.reset(tokens[0])
            abort_check_var.reset(tokens[1])

    def _translate(self, work: _Work, depth: int) -> bool:
        """False once the session was cancelled."""
        queue_wait = time.monotonic() - work.queued_at

        def checkpoint():
            if self._stop_now():
                raise background_jobs.JobCancelled("live translate")
        try:
            live_cue_feed.translate_cues(
                work.cues, self._translator, checkpoint,
                on_stage=lambda stage: self._stage.chunk(stage, work.idx),
                on_cues=self._board.publish, lock=self._board.lock)
        except background_jobs.JobCancelled:
            return False
        except Exception as exc:
            from translate_engines import redact_secrets
            note = f"{live_cue_feed.FAILED_PREFIX}: {redact_secrets(str(exc))}]"
            with self._board.lock:
                for cue in work.cues:
                    if cue["translation"] == live_cue_feed.PENDING:
                        cue["translated"], cue["translation"] = note, live_cue_feed.FAILED
                self._board.publish()
        finally:
            self._stage.translated()
            self._wake.set()
        total = work.timing.report(work.idx, self._note, cues=len(work.cues),
                                   queue_wait=queue_wait, queue_depth=depth)
        self._stats.chunk_done(work.audio, total)
        return True


@contextlib.contextmanager
def translation_worker(translator, board, stage, stats, note, cancelled, wake):
    """A worker that is always ended (abandoned unless finish() ran) on the way
    out, however the capture loop leaves."""
    worker = TranslationWorker(translator, board, stage, stats, note, cancelled, wake)
    try:
        yield worker
    finally:
        worker.abandon()
