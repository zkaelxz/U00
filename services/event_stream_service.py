"""
services/event_stream_service.py -- the change broker behind the SSE route
(`GET /api/events`, api/routers/events_routes.py): job, notification-bell
and Live session updates pushed to the React client instead of polled.

How it stays safe: a hook never hands a payload to a stream. background_jobs
and notification_service only say *what* changed (a job id, "the list");
each connection then re-reads it through the same service call its GET
route uses, with that connection's own principal (jobs_service.get_job,
notification_service.list_recent, live_service.get_session), so the
visibility rules are exactly the GET routes' and anything a principal
can't see is skipped (fail closed). The router serializes through the GET
routes' response models.

Bounded: at most MAX_STREAMS in total and MAX_STREAMS_PER_PRINCIPAL per
signed-in user (or the local owner); each connection keeps a set of
changed ids, and more than MAX_PENDING of them collapse into one "resync"
(the client re-reads with one GET) instead of growing. Hooks run on job
threads: they only mark sets under a lock and wake the connection's asyncio
loop with call_soon_threadsafe, never block, and never raise.

A job another process runs (the CLI) fires no hook here, so one
process-wide sweep compares job_records every JOB_SWEEP_SECONDS while any
stream is open.

No FastAPI import.
"""

import asyncio
import json
import threading
import weakref

TOPICS = ("jobs", "notifications", "live")
MAX_STREAMS = 32
MAX_STREAMS_PER_PRINCIPAL = 6
MAX_PENDING = 64
MAX_TRACKED = 500           # job ids / live cursors remembered per connection
HEARTBEAT_SECONDS = 15.0
JOB_SWEEP_SECONDS = 5.0
MIN_BATCH_SECONDS = 0.25    # coalesce bursts (progress ticks) per connection
MAX_STREAM_SECONDS = 30 * 60  # then the client reconnects and resyncs
AUTH_RECHECK_SECONDS = 5.0  # session/permission re-check, at most this often

_lock = threading.Lock()
# Weak, so a stream whose generator never ran (the client left before the
# response started) can't hold a slot forever; a running stream's generator
# holds its Subscription and removes it in `finally`.
_subs = weakref.WeakSet()


class TooManyStreams(Exception):
    """The caller already has MAX_STREAMS_PER_PRINCIPAL streams, or the
    process has MAX_STREAMS."""


def principal_key(principal) -> str:
    """Who a stream counts against: a signed-in user's id, else the local
    owner (auth off, or a principal without a user id)."""
    uid = (principal or {}).get("user_id")
    return f"user:{uid}" if uid is not None else "local"


def parse_topics(raw) -> tuple:
    """"jobs,live" -> ("jobs", "live"); empty/None -> every topic. Unknown
    names raise ValueError."""
    if raw is None or not str(raw).strip():
        return TOPICS
    names = [t.strip() for t in str(raw).split(",") if t.strip()]
    bad = [t for t in names if t not in TOPICS]
    if bad or len(names) > len(TOPICS):
        raise ValueError("topics must be a comma-separated list of: " + ", ".join(TOPICS))
    return tuple(t for t in TOPICS if t in names)


class Subscription:
    """One open stream's pending changes. mark() is called from any
    thread; drain() and the per-connection state below only from the
    stream's own coroutine (and the worker thread it hands a batch to)."""

    def __init__(self, key: str, topics: tuple, loop=None):
        self.key = key
        self.topics = tuple(topics)
        self._mu = threading.Lock()
        self._jobs = set()
        self._live = set()
        self._notifications = False
        self._overflow = False
        self._recheck = False
        self._loop = loop
        self._event = asyncio.Event() if loop is not None else None
        self._signalled = False
        # Per-connection state (stream coroutine only).
        self.sent_jobs = {}           # job id -> digest of what was last shown
        self.live_cursor = {}         # live session id -> cues already counted
        self.last_notifications = None

    # --- any thread -------------------------------------------------------
    def mark(self, kind: str, key=None) -> None:
        with self._mu:
            if kind == "job" and "jobs" in self.topics:
                if key is None:
                    self._overflow = True
                elif not self._overflow:
                    self._jobs.add(key)
            elif kind == "live" and "live" in self.topics:
                if not self._overflow:
                    self._live.add(key)
            elif kind == "notifications" and "notifications" in self.topics:
                self._notifications = True
            else:
                return
            if self._overflow or len(self._jobs) + len(self._live) > MAX_PENDING:
                self._overflow = True
                self._jobs.clear()
                self._live.clear()
            wake = not self._signalled
            self._signalled = True
        if wake:
            self._wake()

    def request_recheck(self) -> None:
        """The stream re-checks its session on its next wake-up, even
        within AUTH_RECHECK_SECONDS of the last check (a session was
        revoked), and is woken now."""
        with self._mu:
            self._recheck = True
            wake = not self._signalled
            self._signalled = True
        if wake:
            self._wake()

    def _wake(self) -> None:
        if self._loop is None or self._event is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:
            pass  # loop already closed: the stream is gone

    # --- stream coroutine -------------------------------------------------
    async def wait(self, timeout: float) -> bool:
        """True when something is pending, False on timeout."""
        if self._event is None:
            return self.pending()
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            return self.pending()
        return True

    def pending(self) -> bool:
        with self._mu:
            return self._signalled

    def drain(self) -> dict:
        with self._mu:
            batch = {"overflow": self._overflow, "jobs": sorted(self._jobs),
                     "live": sorted(self._live), "notifications": self._notifications,
                     "recheck": self._recheck}
            self._jobs.clear()
            self._live.clear()
            self._notifications = False
            self._overflow = False
            self._recheck = False
            self._signalled = False
            if self._event is not None:
                self._event.clear()
        return batch


def _on_job_change(job_id) -> None:
    with _lock:
        subs = list(_subs)
    live = isinstance(job_id, str) and job_id.startswith("live_")
    for sub in subs:
        try:
            sub.mark("job", job_id)
            if live:
                sub.mark("live", job_id)
        except Exception:
            pass


def _on_notification() -> None:
    with _lock:
        subs = list(_subs)
    for sub in subs:
        try:
            sub.mark("notifications")
        except Exception:
            pass


def _ensure_hooks() -> None:
    """Idempotent (both registries ignore a listener already added), so
    it is simply called on every open."""
    import background_jobs
    from services import notification_service
    background_jobs.add_change_listener(_on_job_change)
    notification_service.add_listener(_on_notification)


def open_subscription(principal, topics, loop=None) -> Subscription:
    """Registers a stream; raises TooManyStreams over a cap."""
    key = principal_key(principal)
    with _lock:
        if len(_subs) >= MAX_STREAMS:
            raise TooManyStreams("Too many live update streams are open.")
        if sum(1 for s in _subs if s.key == key) >= MAX_STREAMS_PER_PRINCIPAL:
            raise TooManyStreams("Too many live update streams are open for this account.")
        _ensure_hooks()
        sub = Subscription(key, topics, loop)
        _subs.add(sub)
    return sub


def close_subscription(sub) -> None:
    global _sweep_snapshot
    with _lock:
        _subs.discard(sub)
        if len(_subs) == 0:
            _sweep_snapshot = None   # the next first stream takes a fresh baseline


def request_recheck(user_id=None) -> None:
    """After a sign-out elsewhere, a revoke or a deactivation: that user's
    streams (every stream for None) re-check their session at once, so a
    revoked one ends now instead of at its next heartbeat. Never raises."""
    key = None if user_id is None else principal_key({"user_id": user_id})
    with _lock:
        subs = [s for s in _subs if key is None or s.key == key]
    for sub in subs:
        try:
            sub.request_recheck()
        except Exception:
            pass


def open_count(key: str = None) -> int:
    with _lock:
        return sum(1 for s in _subs if key is None or s.key == key)


# --- building a batch (runs in a worker thread) ------------------------------

_sweep_lock = threading.Lock()
_sweep_at = 0.0
_sweep_snapshot = None


def sweep_jobs(now: float = None) -> None:
    """One process-wide pass (at most every JOB_SWEEP_SECONDS, whichever
    stream calls it): marks, on every stream, the job ids whose job_records
    row changed or vanished since the last pass. Catches jobs another
    process runs, which fire no hook here. The first pass only takes the
    baseline. Reads a few columns, keeps one snapshot for the process."""
    global _sweep_at, _sweep_snapshot
    import time
    import db
    now = time.monotonic() if now is None else now
    if not _sweep_lock.acquire(blocking=False):
        return   # another stream is sweeping right now
    try:
        if _sweep_snapshot is not None and now - _sweep_at < JOB_SWEEP_SECONDS:
            return
        _sweep_at = now
        current = db.list_job_record_fingerprints()
        previous, _sweep_snapshot = _sweep_snapshot, current
    finally:
        _sweep_lock.release()
    if previous is None:
        return
    for job_id in set(previous) | set(current):
        if previous.get(job_id) != current.get(job_id):
            _on_job_change(job_id)


def build_events(sub: Subscription, batch: dict, principal) -> list:
    """[(event_name, raw_dict)] for one drained batch, each read through
    the matching GET route's service call with `principal`. Raw dicts are
    serialized by the router through the GET response models.

    - ("resync", {"topics": [...]}) when the batch overflowed: re-read.
    - ("job", job record) for a job the principal can see.
    - ("job_gone", {"job_id"}) for a job this connection was shown that is
      now gone or no longer visible (never for one it was never shown).
    - ("notifications", {"items": [...]}) when the principal's list changed.
    - ("live", session status without cues) for a visible Live session.
    """
    from services import jobs_service, live_service, notification_service
    from services.service_errors import NotFoundError
    if batch.get("overflow"):
        return [("resync", {"topics": list(sub.topics)})]
    out = []
    for job_id in batch.get("jobs") or ():
        try:
            record = jobs_service.get_job(job_id, principal=principal)
        except NotFoundError:
            if sub.sent_jobs.pop(job_id, None) is not None:
                out.append(("job_gone", {"job_id": job_id}))
            continue
        digest = json.dumps(record, sort_keys=True, default=str)
        if sub.sent_jobs.get(job_id) == digest:
            continue   # the hook and the sweep both saw the same change
        if job_id in sub.sent_jobs or len(sub.sent_jobs) < MAX_TRACKED:
            sub.sent_jobs[job_id] = digest
        out.append(("job", record))
    for session_id in batch.get("live") or ():
        after = sub.live_cursor.get(session_id, 0)
        try:
            status = live_service.get_session(session_id, after, principal=principal)
        except NotFoundError:
            continue
        if session_id in sub.live_cursor or len(sub.live_cursor) < MAX_TRACKED:
            sub.live_cursor[session_id] = status.get("next_index", after)
        # Cue text stays behind GET /api/live/sessions/{id}?after=: the
        # client fetches new cues from its own cursor when next_index moves.
        out.append(("live", dict(status, cues=[])))
    if batch.get("notifications"):
        items = notification_service.list_recent(principal=principal)
        digest = json.dumps(items, sort_keys=True, default=str)
        if digest != sub.last_notifications:
            sub.last_notifications = digest
            out.append(("notifications", {"items": items}))
    return out
