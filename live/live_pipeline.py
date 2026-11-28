"""The parts of a Live session that run beside the capture loop: cue translation
on its own thread, the cue list both threads write, the status line both report
to, the Stop-and-finish request, and the lag figures the page shows.

Whisper for chunk N+1 runs while chunk N's lines are translated, so a slow
translator costs translations (shown untranslated and counted once the queue
is full), never Whisper time or audio. docs/user-guide-subtitles.md (Live) describes the
figures for the owner."""
import collections
import contextlib
import contextvars
import itertools
import re
import statistics
import sys
import threading
import time

import background_jobs
from live import live_cue_feed
from live import live_whisper

# Chunks waiting for translation behind the one being translated; the worker sends
# all of them in its next request. Beyond this the oldest waiting chunk is shown
# untranslated: the queue never grows without bound and the lines it does
# translate stay close to the speech.
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
        """Appends cues. A cue never starts before the one shown above it: a line re-heard in the next chunk's
        overlap can carry an earlier start than the line before it, and a
        player showing cues by start time would then show them out of order."""
        with self.lock:
            for cue in cues:
                if self.cues and cue["start"] < self.cues[-1]["start"]:
                    # Moved, not shortened: a line kept on screen for no time is never read.
                    late = self.cues[-1]["start"] - cue["start"]
                    cue["start"] += late
                    cue["end"] += late
                self.cues.append(cue)
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


class StartTimeline:
    """App-log line, once per milestone, with seconds since Start: where the
    time goes before the first caption. Numbers and fixed text only."""

    def __init__(self, clock=time.monotonic):
        self._clock, self._t0 = clock, clock()
        self._seen = set()
        self._lock = threading.Lock()

    def has(self, name: str) -> bool:
        return name in self._seen

    def mark(self, name: str) -> None:
        with self._lock:
            if name in self._seen:
                return
            self._seen.add(name)
            took = self._clock() - self._t0
        live_whisper._log("info", f"Live: start +{took:.1f} s: {name}")


class Stage:
    """The one status line both threads report to. A stream outage wins while
    it lasts (the owner needs to know captions have paused); then Whisper's step
    while it runs (Stop's text names the chunk it interrupts); otherwise the
    chunk being translated; otherwise the capture wait."""

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
        self._outage = None
        # audio/took: seconds of audio Whisper was handed for the current / last chunk.
        self.audio, self._took, self._last_audio = 0.0, None, 0.0
        self._slow_noted = False
        self.timeline = None

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

    def outage(self, text: str | None) -> None:
        """Shows `text` (fixed wording, no URL) until outage(None)."""
        with self._lock:
            self._outage = text
            self._refresh()

    def _refresh(self) -> None:
        if self._outage is not None:
            self._enter(("outage", self._outage), self._report)
        elif self._whisper is not None:
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
    the rolling median of wait + Whisper + translate-queue wait + translate.
    overlap_matched / overlap_fallback: chunk joins by exact text match / by the time rule."""

    def __init__(self, publish):
        self._publish = publish or (lambda numbers: None)
        self._lock = threading.Lock()
        self._totals = collections.deque(maxlen=LAG_WINDOW)
        self._lag = None
        self._measured = 0
        self.skipped = 0
        self.queue = 0
        self.overlap_matched = 0
        self.overlap_fallback = 0
        # Which reconnect try is under way; 0 while the stream is connected.
        self.reconnect_attempt = 0
        # For the session's log line only: every chunk's processing seconds, the worst lag and
        # the translation failures by kind (lib.error_kinds), none of it shown to the viewer.
        self._all_totals: list = []
        self._worst_lag = 0.0
        self.error_kinds: dict = {}

    def log_figures(self) -> dict:
        with self._lock:
            totals = sorted(self._all_totals)
            return {"chunks": self._measured,
                    "median_s": round(totals[len(totals) // 2], 1) if totals else 0.0,
                    "worst_lag_s": round(self._worst_lag, 1), "skipped": int(self.skipped),
                    "errors": dict(self.error_kinds)}

    def failed(self, exc) -> None:
        from lib.error_kinds import error_kind
        kind = error_kind(str(exc))
        with self._lock:
            self.error_kinds[kind] = self.error_kinds.get(kind, 0) + 1

    def figures(self) -> dict:
        with self._lock:
            return {"lag_seconds": round(self._lag, 1) if self._lag is not None else 0.0,
                    "processing_seconds": round(statistics.median(self._totals), 1) if self._totals else 0.0,
                    "translations_skipped": int(self.skipped), "translate_queue": int(self.queue),
                    "chunks_measured": int(self._measured),
                    "overlap_matched": int(self.overlap_matched), "overlap_fallback": int(self.overlap_fallback),
                    "reconnect_attempt": int(self.reconnect_attempt)}

    def chunk_done(self, audio_seconds: float, total: float) -> None:
        with self._lock:
            self._totals.append(float(total))
            self._lag = float(audio_seconds) + float(total)
            self._worst_lag = max(self._worst_lag, self._lag)
            if len(self._all_totals) < 5000:
                self._all_totals.append(float(total))
            self._measured += 1
        self.send()

    def overlap_joined(self, outcome: str) -> None:
        """Counts how a chunk's start was joined to the previous chunk's lines: the exact
        text match, or the rougher time rule when the two hearings of the overlap disagreed."""
        with self._lock:
            if outcome == "fallback":
                self.overlap_fallback += 1
            else:
                self.overlap_matched += 1

    def send(self) -> None:
        self._publish(self.figures())


class _Work:
    def __init__(self, idx, cues, timing, audio, measured=True):
        self.idx, self.cues, self.timing, self.audio = idx, cues, timing, audio
        self.measured = measured
        self.queued_at = time.monotonic()


class TranslationWorker:
    """Translates chunks on one thread, oldest first; chunks that waited while a
    request ran go together in the next one, which costs a hosted model little
    more than one of them (its time is mostly the wait for the first token).
    submit() never blocks: past
    MAX_TRANSLATE_BACKLOG the oldest waiting chunk's lines are marked skipped and
    counted, except with keep_all: the drain after Stop has no stream left to keep
    up with. finish() lets the queue run dry; abandon() drops it and aborts the
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
        # Context variables do not cross threads: a member's own Ollama key
        # comes along explicitly, so their cloud model doesn't use the PC's account,
        # and the copied context keeps the session's paid calls logged for its starter.
        from engine_backends.local import carry_member_cloud_key, member_cloud_key
        context = contextvars.copy_context()
        carried = carry_member_cloud_key(self._run, member_cloud_key())
        self._thread = threading.Thread(target=lambda: context.run(carried),
                                        daemon=True, name="live-translate")
        self._thread.start()

    def _stop_now(self) -> bool:
        return self._abandoned or self._cancelled()

    def submit(self, idx: int, cues: list, timing, audio: float, keep_all: bool = False,
               measured: bool = True) -> None:
        """measured=False: lines with no audio of their own (held back from a
        chunk), kept out of the lag figures."""
        with self._cond:
            if self._abandoned:
                return
            dropped = []
            while not keep_all and len(self._queue) >= MAX_TRANSLATE_BACKLOG:
                dropped.append(self._queue.popleft())
            self._queue.append(_Work(idx, cues, timing, audio, measured))
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
                return []
            works = list(self._queue)
            self._queue.clear()
            self._stats.queue = 0
            return works

    def _run(self) -> None:
        # Context variables do not cross threads: without these an Ollama request
        # here could not be abandoned, and a retry backoff would not see Stop.
        from engine_backends.local import abort_check_var
        from engine_backends.shared import _cancel_check_var
        tokens = (_cancel_check_var.set(self._stop_now), abort_check_var.set(self._stop_now))
        try:
            while True:
                works = self._next()
                if not works:
                    return
                if not self._translate(works):
                    return
        finally:
            _cancel_check_var.reset(tokens[0])
            abort_check_var.reset(tokens[1])

    def _translate(self, works: list) -> bool:
        """False once the session was cancelled."""
        picked = time.monotonic()
        cues = [cue for work in works for cue in work.cues]
        newest = works[-1].idx
        starts = list(itertools.accumulate((len(w.cues) for w in works), initial=0))

        def checkpoint():
            if self._stop_now():
                raise background_jobs.JobCancelled("live translate")

        def sentence_failed(group, exc):
            # Chunks merged into one request fail one by one, as they did when each
            # had its own request: the others' sentences are still asked.
            for pos, work in enumerate(works):
                if any(starts[pos] <= p < starts[pos + 1] for p in group):
                    self._fail(work.cues, work.idx, exc)
        if self._stage.timeline is not None:
            self._stage.timeline.mark("first translation request")
        try:
            live_cue_feed.translate_cues(
                cues, self._translator, checkpoint,
                on_stage=lambda stage: self._stage.chunk(stage, newest),
                on_cues=self._board.publish, lock=self._board.lock,
                on_sentence_error=sentence_failed)
        except background_jobs.JobCancelled:
            return False
        except Exception as exc:
            self._fail(cues, newest, exc)
        finally:
            self._stage.translated()
            self._wake.set()
        if self._stage.timeline is not None and any(
                c["translation"] not in (live_cue_feed.PENDING, live_cue_feed.FAILED) for c in cues):
            self._stage.timeline.mark("first English")
        for pos, work in enumerate(works):
            if work.measured:
                total = work.timing.report(work.idx, self._note, cues=len(work.cues),
                                           queue_wait=picked - work.queued_at,
                                           queue_depth=len(works) - 1 - pos)
                self._stats.chunk_done(work.audio, total)
        return True

    def _fail(self, cues: list, idx: int, exc: Exception) -> None:
        from engine_backends.shared import redact_secrets
        try:
            self._stats.failed(exc)
        except Exception:
            pass   # a stats slip must never stop the failure being shown
        live_whisper.log_failure(f"chunk {idx} translation failed", exc)
        note = f"{live_cue_feed.FAILED_PREFIX}: {redact_secrets(str(exc))}]"
        with self._board.lock:
            for cue in cues:
                if cue["translation"] == live_cue_feed.PENDING:
                    cue["translated"], cue["translation"] = note, live_cue_feed.FAILED
            self._board.publish()


@contextlib.contextmanager
def translation_worker(translator, board, stage, stats, note, cancelled, wake):
    """A worker that is always ended (abandoned unless finish() ran) on the way
    out, however the capture loop leaves."""
    worker = TranslationWorker(translator, board, stage, stats, note, cancelled, wake)
    try:
        yield worker
    finally:
        worker.abandon()


# A long session's risks (a stream that keeps breaking, captions drifting
# behind, memory creeping up, spend nearing its cap) are written to the app log
# on these intervals instead of being found by a soak test, and the ones a user
# can act on also get a note.
LAG_LOG_SECONDS = 300
STATS_LOG_SECONDS = 600
LAG_NOTE_SECONDS = 30.0
BURST_RECONNECTS = 3
BURST_WINDOW_SECONDS = 600
SPEND_NOTE_SHARE = 0.8
STALLING_NOTE = "The stream keeps stalling; captions may skip."
LAG_NOTE = "Captions are falling behind."
# Lines this many before a break are compared with the first chunk after it.
REPEAT_LOOKBACK_CUES = 10


def _rss_mb():
    """This process's resident memory in MB, or None where it can't be read
    without psutil (not a dependency)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in (
                        "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                        "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            query = ctypes.windll.psapi.GetProcessMemoryInfo
            query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            if not query(wintypes.HANDLE(-1), ctypes.byref(counters), counters.cb):
                return None
            return counters.WorkingSetSize / 1_048_576
        with open("/proc/self/status", encoding="ascii", errors="replace") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return None


def _gpu_mb():
    """Torch's CUDA memory in MB when torch is already loaded: importing it here
    would cost seconds and a GPU context for a number."""
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_available():
            return torch.cuda.memory_allocated() / 1_048_576
    except Exception:
        pass
    return None


def _same_text(text: str) -> str:
    return re.sub(r"\W+", "", text or "")


class SessionLog:
    """Health lines of one Live session for the app log, and the notes for the
    risks a user can see. Only numbers and fixed text are logged: a stream URL
    can carry a signed token, so none is ever passed in. `spend()` gives
    (spent_usd, limit_usd) or None; `processes()` the session's ffmpeg Popens."""

    def __init__(self, note, stats: LagStats, spend=None, processes=None, clock=time.monotonic,
                 resolved_at=None):
        self._note, self._stats, self._clock = note, stats, clock
        self._spend = spend or (lambda: None)
        self._processes = processes or (lambda: [])
        now = clock()
        self._next_lag = now + LAG_LOG_SECONDS
        self._next_stats = now + STATS_LOG_SECONDS
        self._breaks = collections.deque()
        self._stalling_noted = self._spend_noted = self._lagging = False
        self._spent_at_last_log = 0.0
        self._last_chunk = now
        self._resolved = now if resolved_at is None else resolved_at
        self._mark = None
        self.repeats_dropped = 0

    def _log(self, text: str, level: str = "info") -> None:
        live_whisper._log(level, "Live: " + live_whisper._URL_RE.sub("[URL]", text))

    def tick(self) -> None:
        """Called on each pass of the capture loop."""
        now = self._clock()
        lag = self._stats.figures()["lag_seconds"]
        lagging = lag > LAG_NOTE_SECONDS
        if lagging and not self._lagging:
            self._log(f"captions are {lag:.1f} s behind the stream", "warning")
            self._note(LAG_NOTE, "lag")
        self._lagging = lagging
        if now >= self._next_lag:
            self._next_lag = now + LAG_LOG_SECONDS
            self._log(f"lag {lag:.1f} s")
        spend = self._spend()
        if spend and not self._spend_noted and spend[1] > 0 and spend[0] >= SPEND_NOTE_SHARE * spend[1]:
            self._spend_noted = True
            self._note(f"This session has used {spend[0] / spend[1]:.0%} of its spend limit "
                       f"(${spend[0]:.2f} of ${spend[1]:.2f}).", "spend")
        if now >= self._next_stats:
            self._next_stats = now + STATS_LOG_SECONDS
            self.resources("10 min")
            self.session_stats("10 min")
            if spend:
                self._log(f"spent ${spend[0] - self._spent_at_last_log:.4f} in the last 10 min, "
                          f"${spend[0]:.4f} of the ${spend[1]:.2f} session cap")
                self._spent_at_last_log = spend[0]
            else:
                self._log("spend: no session cap (a free engine, or the monthly cap applies)")

    def session_stats(self, when: str) -> None:
        f = self._stats.log_figures()
        errors = ", ".join(f"{k} {n}" for k, n in sorted(f["errors"].items())) or "none"
        self._log(f"stats ({when}): {f['chunks']} chunks, median {f['median_s']} s each, worst lag "
                  f"{f['worst_lag_s']} s, {f['skipped']} translations skipped, errors: {errors}")

    def resources(self, when: str) -> None:
        rss, gpu = _rss_mb(), _gpu_mb()
        try:
            alive = str(sum(1 for p in self._processes() if p is not None and p.poll() is None))
        except Exception:
            alive = "?"
        self._log(f"resources ({when}): RSS {'?' if rss is None else f'{rss:.0f} MB'}"
                  f"{'' if gpu is None else f', GPU {gpu:.0f} MB'}, ffmpeg processes {alive}")

    def chunk_seen(self) -> None:
        self._last_chunk = self._clock()

    def broke(self, reason: str, breaks: int, chunk: int, stream_seconds: float,
              board_size: int) -> None:
        """A stream break, once the broken capture's chunks are captioned."""
        now = self._clock()
        self._log(f"stream broke off ({reason}): {now - self._last_chunk:.0f} s since the last "
                  f"chunk, reconnect {breaks}; before: chunk {chunk}, stream time "
                  f"{stream_seconds:.1f} s", "warning")
        self.resources("reconnect")
        self._breaks.append(now)
        while self._breaks and now - self._breaks[0] > BURST_WINDOW_SECONDS:
            self._breaks.popleft()
        if len(self._breaks) > BURST_RECONNECTS and not self._stalling_noted:
            self._stalling_noted = True
            self._note(STALLING_NOTE, "stalling")
        self._mark = {"at": now, "stream": stream_seconds, "board": board_size}
        self.repeats_dropped = 0

    def dropped(self, lines: int) -> None:
        self.repeats_dropped += lines

    def re_resolved(self) -> None:
        now = self._clock()
        self._log(f"re-resolved stream address after {(now - self._resolved) / 60:.0f} min")
        self._resolved = now

    def after_break(self, chunk: int, stream_seconds: float, segment_seconds: float,
                    cues: list) -> None:
        """The first chunk captioned after a break: how far the timeline moved
        and what of the speech before the break it repeated."""
        mark, self._mark = self._mark, None
        if mark is None:
            return
        gap = self._clock() - mark["at"]
        moved = stream_seconds - mark["stream"]
        before = {_same_text(c.get("text")) for c in
                  cues[max(0, mark["board"] - REPEAT_LOOKBACK_CUES):mark["board"]]}
        repeated = sum(1 for c in cues[mark["board"]:] if _same_text(c.get("text")) in before - {""})
        self._log(f"after reconnect: chunk {chunk}, stream time {stream_seconds:.1f} s; "
                  f"{self.repeats_dropped} lines dropped as repeats, {repeated} shown that repeat "
                  "a line from before the break")
        # A chunk's slot plus the break is as far as the timeline should move;
        # far more is a timestamp jump in the new capture's audio.
        if moved > gap + 2 * segment_seconds:
            self._log(f"timeline moved {moved:.0f} s across a {gap:.0f} s break", "warning")

    def ended(self, why: str) -> None:
        self.session_stats("end")
        self._log(f"session ended: {why}")
