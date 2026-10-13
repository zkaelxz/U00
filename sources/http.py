"""
sources/http.py -- the one paced HTTP client every adapter and the
generic importer go through.

What it guarantees, structurally rather than by convention:
  * Human-paced: a random 1-3s gap (configurable) between requests to
    the same host, whichever source name or client sends them, one
    request in flight per source by default, and an adapter can declare a
    stricter per-host minimum (e.g. a robots.txt Crawl-delay) that every
    client fetching that host then honours.
  * Session-shaped: every so often (a randomized request count, default
    8-20) the source takes one longer pause (default 30-90s) before
    continuing -- like a person setting the app down and coming back --
    rather than a constant, evenly-spaced request rate for an entire run.
  * Ordinary trouble (HTTP 429, 5xx, timeouts, dropped connections) is
    retried with exponential backoff, up to a capped number of times.
  * An active anti-automation challenge is NEVER retried and never passed
    to anything automated: it raises ChallengeDetected immediately so the
    UI can hand off to the person (Open in Browser / Retry / Cancel).
  * Every request has a timeout.
  * A source in its 🔴 backoff window isn't contacted at all.
  * The raw-content cache is consulted before the network.
  * Live counters (requests, cache hits, current action, current delay)
    for the Source Access status view.
  * Conditional re-polls: inside conditional_poll(), a GET of
    the poll's one known URL carries If-None-Match / If-Modified-Since,
    and a 304 raises NotModified so the caller can skip the parse.
"""

import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from translate_engines import safe_url

from . import detect, health, pacing, store
from .cache import RawCache
from .http_transport import (DEFAULT_TIMEOUT, MAX_IMAGE_BYTES, MAX_PAGE_BYTES, REQUEST_DEADLINE,
                             Cancelled, FetchLimits, Response, ResponseRefused, UnsafeRedirect,
                             _requests_transport)
from .limiter import Limiter
from .models import (AccessTier, AttemptRecord, ChallengeDetected, CHALLENGE_REASONS,
                     FailureReason, FetchFailed, SourceUnavailable)

# Cap on any one retry wait, a server's Retry-After included, so a single
# wait can't stall a job for minutes.
MAX_SINGLE_BACKOFF = pacing.MAX_HOLD


# A plain desktop browser identity -- the same posture as a person's own
# browser, never a named bot identity a site has chosen to block.
DEFAULT_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")




class NotModified(Exception):
    """A conditional re-poll's request came back 304: the resource is
    unchanged since the validators were saved, so there is nothing to
    parse. Only ever raised inside conditional_poll()."""


# A validator longer than this, or holding a control character, is not
# stored or sent (an ETag is a short quoted token; a date is ~29 chars).
MAX_VALIDATOR_LENGTH = 256


def clean_validator(value) -> str:
    """The header value if it is safe to store and send back, else ""."""
    value = str(value or "").strip()
    if not value or len(value) > MAX_VALIDATOR_LENGTH or \
            any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return ""
    return value


@dataclass
class ConditionalPoll:
    """One chapter-list re-poll. `url`/`etag`/`last_modified`
    are the validators saved by the previous poll; while the poll is
    active, every request made on this thread (any SourceClient) is
    recorded, so validators are only kept for a poll that was exactly one
    plain GET -- a multi-request or browser-rendered chapter list can't
    be judged unchanged from one of its responses.

    Single-thread by design: it lives in a threading.local, so an adapter
    that fetched its chapter list on worker threads would go unrecorded
    (none does today; the registry's thread pool is search only)."""
    url: str = ""
    etag: str = ""
    last_modified: str = ""
    not_modified: bool = False
    # A 304 that answered a redirect target, not the validated URL: the
    # saved validators no longer lead to the list, so they are forgotten.
    untrusted_304: bool = False
    started_gets: int = 0                       # every GET sent, failed ones included
    gets: list = field(default_factory=list)   # (url, final_url, status, etag, last_modified)
    other_requests: int = 0                     # POSTs, cache hits, browser renders

    def conditional_headers(self, url: str) -> dict:
        if url != self.url:
            return {}
        hdrs = {}
        if self.etag:
            hdrs["If-None-Match"] = self.etag
        if self.last_modified:
            hdrs["If-Modified-Since"] = self.last_modified
        return hdrs

    def validators(self):
        """(url, etag, last_modified) to save for the next poll, or None."""
        if self.other_requests or self.started_gets != 1 or len(self.gets) != 1:
            return None
        url, final_url, status, etag, last_modified = self.gets[0]
        if status != 200 or final_url != url or not (etag or last_modified):
            return None
        return url, etag, last_modified


_poll_local = threading.local()


def _active_poll():
    return getattr(_poll_local, "poll", None)


@contextmanager
def conditional_poll(url: str = "", etag: str = "", last_modified: str = ""):
    """Runs a chapter-list fetch as a conditional re-poll on this thread;
    yields the ConditionalPoll recording it."""
    poll = ConditionalPoll(url=url or "", etag=clean_validator(etag),
                           last_modified=clean_validator(last_modified))
    previous = _active_poll()
    _poll_local.poll = poll
    try:
        yield poll
    finally:
        _poll_local.poll = previous


@dataclass
class PacingPolicy:
    min_delay: float = 1.0
    max_delay: float = 3.0
    max_concurrent: int = 1
    max_retries: int = 3
    backoff_base: float = 2.0
    host_min_interval: dict = field(default_factory=dict)   # host -> seconds
    # A longer, occasional pause on top of the ordinary per-request gap --
    # picked once per `session_break_min/max_requests` requests, mimicking a
    # person setting the app down and coming back. 0 (either bound) disables
    # it entirely.
    session_break_min_requests: int = 8
    session_break_max_requests: int = 20
    session_break_min_delay: float = 30.0
    session_break_max_delay: float = 90.0

    @classmethod
    def from_settings(cls, host_min_interval: dict = None) -> "PacingPolicy":
        s = store.all_settings()
        lo = max(0.0, float(s["pace_min_delay"]))
        hi = max(lo, float(s["pace_max_delay"]))
        break_lo = max(0, int(s["session_break_min_requests"]))
        break_hi = max(break_lo, int(s["session_break_max_requests"]))
        break_delay_lo = max(0.0, float(s["session_break_min_delay"]))
        break_delay_hi = max(break_delay_lo, float(s["session_break_max_delay"]))
        return cls(min_delay=lo, max_delay=hi,
                   max_concurrent=max(1, int(s["max_concurrent"])),
                   max_retries=max(0, int(s["max_retries"])),
                   backoff_base=max(0.0, float(s["backoff_base"])),
                   host_min_interval=dict(host_min_interval or {}),
                   session_break_min_requests=break_lo, session_break_max_requests=break_hi,
                   session_break_min_delay=break_delay_lo, session_break_max_delay=break_delay_hi)




def _is_timeout(exc) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name


def mirror_unreachable(exc) -> bool:
    """True when a FetchFailed means "this mirror/domain can't be reached
    right now" (network error, timeout, 5xx or 429 after retries), so the
    next one may be tried. A 404, a refusal or a challenge is never this."""
    return exc.reason in (FailureReason.HTTP_ERROR, FailureReason.SERVER_ERROR,
                          FailureReason.TIMEOUT, FailureReason.RATE_LIMIT) and \
        (exc.attempt is None or not exc.attempt.http_status
         or exc.attempt.http_status >= 500 or exc.attempt.http_status == 429)


def _is_connection_error(exc) -> bool:
    try:
        import requests
        return isinstance(exc, requests.exceptions.ConnectionError)
    except ImportError:
        return isinstance(exc, ConnectionError)


# Shared state, so two clients (a search and an import running at once, or
# an adapter and the generic client fetching a pasted URL) can't together
# hit a site faster than either would alone. The pace clock is per host,
# across every source name; the concurrency limit, session-break counter
# and preferred mirror stay per source name.
_state_lock = threading.Lock()
_source_state = {}
_host_state = {}


def _state(source: str) -> dict:
    with _state_lock:
        st = _source_state.get(source)
        if st is None:
            st = {"sem": Limiter(),
                  "break_lock": threading.Lock(), "good_mirror": None}
            _source_state[source] = st
        return st


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_key(url: str) -> str:
    """The pace key for a URL: the lowercased host name, plus the port
    only when it isn't the scheme's default, so `WWW.Example.com`,
    `www.example.com:443` and `www.example.com` share one clock."""
    parts = urlsplit(url)
    try:
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:            # a malformed port; the request itself will fail
        return parts.netloc.lower()
    if not host:
        return parts.netloc.lower()
    if port is not None and port != _DEFAULT_PORTS.get(parts.scheme.lower()):
        host += f":{port}"
    return host


def _host(host: str, min_interval: float) -> dict:
    """The host's shared pace state. Its minimum interval is the largest
    any client has declared for it, so a client that declares none (the
    generic one) still waits as long as the adapter asks for."""
    with _state_lock:
        hs = _host_state.get(host)
        if hs is None:
            hs = {"lock": threading.Lock(), "last": None, "min_interval": 0.0}
            _host_state[host] = hs
        hs["min_interval"] = max(hs["min_interval"], min_interval)
        return hs


def reset_pacing_state(keep_slowdown: bool = False):
    """Test helper / settings-change hook: forget every host's last
    request time and declared minimum interval, rebuild the concurrency
    limits, and forget which mirror last worked for each source. A settings
    change keeps the slowdown a source earned; it is not a way to clear it."""
    with _state_lock:
        _source_state.clear()
        _host_state.clear()
    if not keep_slowdown:
        pacing.reset_slowdown()


class SourceClient:
    """
    transport(method, url, headers, data, timeout) -> Response is
    injectable so tests never touch the network; `sleep`/`clock`/`rng` are
    injectable so pacing is testable without real waiting.
    """

    def __init__(self, source: str, policy: PacingPolicy = None, transport=None,
                 sleep=time.sleep, clock=time.monotonic, rng=None, cache: RawCache = None,
                 default_headers: dict = None, cancel_check=None, status_cb=None,
                 timeout: float = DEFAULT_TIMEOUT, max_page_bytes: int = MAX_PAGE_BYTES,
                 max_image_bytes: int = MAX_IMAGE_BYTES,
                 request_deadline: float = REQUEST_DEADLINE):
        self.source = source
        self.policy = policy or PacingPolicy.from_settings()
        self.transport = transport or self._limited_transport
        self.sleep = sleep
        self.clock = clock
        self.rng = rng or random.Random()
        self.cache = cache
        self.default_headers = {"User-Agent": DEFAULT_USER_AGENT}
        self.default_headers.update(default_headers or {})
        self.cancel_check = cancel_check
        self.status_cb = status_cb
        self.timeout = timeout
        self.max_page_bytes = max_page_bytes
        self.max_image_bytes = max_image_bytes
        self.request_deadline = request_deadline
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0,
                      "current_action": "Idle", "current_delay": 0.0,
                      "access_method": "Normal HTTP"}
        self.attempts = []   # AttemptRecord per failed/succeeded request, for diagnostics
        self.last_page = None   # (final url, text) of the last successful HTML GET

    def _limited_transport(self, method, url, headers, data, timeout):
        """The real transport under this client's body limits; cancel is
        read at call time, so a cancel_check set after construction works."""
        return _requests_transport(method, url, headers, data, timeout, limits=FetchLimits(
            self.max_page_bytes, self.max_image_bytes, self.request_deadline,
            self._cancel_requested))

    def _cancel_requested(self) -> bool:
        return bool(self.cancel_check and self.cancel_check())

    # -- status view plumbing --------------------------------------------
    def _status(self, action: str = None, delay: float = None):
        if action is not None:
            self.stats["current_action"] = action
        if delay is not None:
            self.stats["current_delay"] = delay
        if self.status_cb:
            try:
                self.status_cb(dict(self.stats))
            except Exception:
                pass

    def snapshot(self) -> dict:
        return dict(self.stats)

    def _check_cancel(self):
        if self.cancel_check and self.cancel_check():
            raise Cancelled("Cancelled.")

    def _sleep_cancellable(self, seconds: float):
        end = self.clock() + seconds
        while True:
            self._check_cancel()
            left = end - self.clock()
            if left <= 0:
                return
            self.sleep(min(left, 0.5))

    # -- pacing ------------------------------------------------------------
    def _acquire_cancellable(self, lock):
        # Real-time polling (not self.sleep) so a cancel still interrupts a
        # thread queued behind another one's wait on the same lock.
        while not lock.acquire(timeout=0.2):
            self._check_cancel()
        try:
            self._check_cancel()
        except Cancelled:
            lock.release()
            raise

    def _wait_turn(self, host: str, st: dict):
        """Takes the source's session break, then waits for the host's
        turn. The two locks are never held together, and the host lock
        only blocks other requests to that host."""
        self._acquire_cancellable(st["break_lock"])
        try:
            self._maybe_take_a_break(st, pacing.slow_key(self.source, host))
        finally:
            st["break_lock"].release()
        declared = {str(k).lower(): v for k, v in self.policy.host_min_interval.items()}
        hs = _host(host, float(declared.get(host, 0.0)))
        key = pacing.slow_key(self.source, host)
        self._acquire_cancellable(hs["lock"])
        try:
            gap = self.rng.uniform(self.policy.min_delay, self.policy.max_delay)
            gap = max(gap, hs["min_interval"]) * pacing.multiplier(key)
            wait = pacing.wait_seconds(key, hs["last"], gap, self.clock())
            if wait > 0:
                self._status(f"Waiting {wait:.1f}s before next request...", wait)
                self._sleep_cancellable(wait)
            hs["last"] = self.clock()
        finally:
            hs["lock"].release()

    def _pick_break_at(self) -> int:
        lo = self.policy.session_break_min_requests
        hi = self.policy.session_break_max_requests
        return max(1, round(self.rng.uniform(lo, hi)))

    def _maybe_take_a_break(self, st: dict, key: str):
        """Every `break_at` requests to this source (across every host),
        pauses for longer than the ordinary per-request gap -- a person
        would set the app down and come back rather than keep an evenly
        spaced request rate going for a whole session."""
        if self.policy.session_break_min_requests <= 0:
            return
        if "break_at" not in st:
            st["break_at"] = self._pick_break_at()
            st["since_break"] = 0
        if st["since_break"] >= st["break_at"]:
            pause = self.rng.uniform(self.policy.session_break_min_delay,
                                     self.policy.session_break_max_delay) \
                * pacing.multiplier(key)
            self._status(f"Taking a break ({pause:.0f}s)...", pause)
            self._sleep_cancellable(pause)
            st["since_break"] = 0
            st["break_at"] = self._pick_break_at()
        st["since_break"] += 1

    # -- the request -------------------------------------------------------
    def request(self, method: str, url: str, headers: dict = None, data=None,
                use_cache: bool = True, classify_body: bool = True,
                record_health: bool = True, action: str = None) -> Response:
        self._check_cancel()
        wait = health.retry_after(self.source)
        if wait is not None:
            raise SourceUnavailable(
                f"{self.source} is marked unavailable after repeated failures; "
                f"next try allowed in {wait:.0f}s.", retry_after=wait)

        poll = _active_poll()
        conditional = {}
        if poll is not None:
            if method.upper() == "GET":
                conditional = poll.conditional_headers(url)
                poll.started_gets += 1   # a cache hit also counts as "other" below
            else:
                poll.other_requests += 1
        cacheable = use_cache and method.upper() == "GET" and self.cache is not None \
            and not conditional
        if cacheable:
            cached = self.cache.get(url)
            if cached is not None:
                if poll is not None:
                    poll.other_requests += 1
                self.stats["cache_hits"] += 1
                self._status(f"Cache hit: {safe_url(url) or 'page'}")
                return Response(200, {}, cached, url, from_cache=True)

        hdrs = dict(self.default_headers)
        hdrs.update(headers or {})
        given = {k.lower() for k in hdrs}
        conditional = {k: v for k, v in conditional.items() if k.lower() not in given}
        hdrs.update(conditional)
        host = _host_key(url)
        st = _state(self.source)

        attempt_no = 0
        slowed = False
        while True:
            with st["sem"].at(self.policy.max_concurrent, self._check_cancel):
                self._wait_turn(host, st)
                self._status(action or f"Fetching {safe_url(url) or 'page'}", 0.0)
                self.stats["requests"] += 1
                started = self.clock()
                try:
                    resp = self.transport(method.upper(), url, hdrs, data, self.timeout)
                    exc = None
                except Exception as e:     # network-level failure
                    resp, exc = None, e
                latency = self.clock() - started

            if conditional and resp is not None and resp.status_code == 304 \
                    and resp.url != url:
                poll.untrusted_304 = True   # classified below like any response
            if isinstance(exc, Cancelled):
                self._status("Idle", 0.0)
                raise exc
            if isinstance(exc, (UnsafeRedirect, ResponseRefused)):
                self.attempts.append(AttemptRecord(
                    tier=AccessTier.STATIC_HTTP.value, ok=False,
                    reason=exc.reason.value, detail=str(exc),
                    final_url=url, at=time.time()))
                self._status("Idle", 0.0)
                raise exc
            if exc is not None:
                reason = FailureReason.TIMEOUT if _is_timeout(exc) else FailureReason.HTTP_ERROR
                attempt = AttemptRecord(tier=AccessTier.STATIC_HTTP.value, ok=False,
                                        reason=reason.value, detail=f"{type(exc).__name__}: {exc}"[:300],
                                        final_url=url, at=time.time())
                retryable = reason == FailureReason.TIMEOUT or _is_connection_error(exc)
            elif conditional and resp.status_code == 304 and resp.url == url:
                # Unchanged since the last poll: nothing to classify or parse.
                self.attempts.append(AttemptRecord(tier=AccessTier.STATIC_HTTP.value, ok=True,
                                                   http_status=304, final_url=url,
                                                   at=time.time()))
                if record_health:
                    health.record_success(self.source, latency)
                poll.not_modified = True
                self._status("Idle", 0.0)
                raise NotModified(url)
            else:
                ctype = str({k.lower(): v for k, v in resp.headers.items()}.get("content-type", ""))
                is_page = classify_body and ("html" in ctype or not ctype)
                body = resp.text if is_page else ""
                reasons = detect.classify(resp.status_code, resp.headers, body, url, resp.url) \
                    if (is_page or resp.status_code >= 400) else []
                resp.reasons = reasons
                ev = detect.evidence(resp.status_code, resp.headers, body, url, resp.url)
                if resp.status_code < 400 and not (set(reasons) & CHALLENGE_REASONS):
                    self.attempts.append(AttemptRecord(tier=AccessTier.STATIC_HTTP.value, ok=True,
                                                       at=time.time(), **ev))
                    if record_health:
                        health.record_success(self.source, latency)
                    if pacing.note_success(pacing.slow_key(self.source, host)):
                        self._status("Pace relaxed one step")
                    # Content a redirect fetched from another host, or over a
                    # downgraded scheme, is never stored under the URL that
                    # was asked for.
                    if cacheable and _host_key(resp.url or url) == _host_key(url) \
                            and urlsplit(resp.url or url).scheme == urlsplit(url).scheme:
                        self.cache.put(url, resp.content)
                    if is_page and method.upper() == "GET":
                        self.last_page = (resp.url or url, body)
                    if poll is not None and method.upper() == "GET":
                        low = {k.lower(): v for k, v in resp.headers.items()}
                        poll.gets.append((url, resp.url, resp.status_code,
                                          clean_validator(low.get("etag")),
                                          clean_validator(low.get("last-modified"))))
                    self._status("Idle", 0.0)
                    return resp
                reason = reasons[0] if reasons else FailureReason.HTTP_ERROR
                attempt = AttemptRecord(tier=AccessTier.STATIC_HTTP.value, ok=False,
                                        reason=reason.value, at=time.time(), **ev)
                # A challenge is never retried or routed around: it stops
                # every automated request and goes to the person.
                if reason in CHALLENGE_REASONS:
                    self.attempts.append(attempt)
                    self._note_pushback("challenge", resp, False, host)
                    if record_health:
                        health.record_failure(self.source, reason.value,
                                              f"Challenge at {url}")
                    self._status("Stopped: browser verification needed", 0.0)
                    raise ChallengeDetected(
                        f"{reason.value} at {url} -- automated requests stopped.",
                        url=url, reason=reason, attempt=attempt)
                retryable = reason == FailureReason.RATE_LIMIT or resp.status_code >= 500

            self.attempts.append(attempt)
            slowed = self._note_pushback(pacing.pushback_kind(attempt.reason), resp, slowed, host)
            if retryable and attempt_no < self.policy.max_retries:
                backoff = min(self.policy.backoff_base * (2 ** attempt_no), MAX_SINGLE_BACKOFF)
                retry_hdr = (resp.headers if resp is not None else {})
                ra = {k.lower(): v for k, v in retry_hdr.items()}.get("retry-after")
                # Retry-After can only lengthen the wait, never past the cap;
                # only the delta-seconds form is read (an HTTP-date is ignored).
                ra_seconds = pacing.retry_after_seconds(ra)
                if ra_seconds is not None:
                    backoff = min(max(backoff, ra_seconds), MAX_SINGLE_BACKOFF)
                attempt_no += 1
                self.stats["retries"] += 1
                self._status(f"{attempt.reason} -- retry {attempt_no}/{self.policy.max_retries} "
                             f"in {backoff:.0f}s", backoff)
                self._sleep_cancellable(backoff)
                continue

            if record_health and attempt.reason != FailureReason.NOT_FOUND.value:
                health.record_failure(self.source, attempt.reason or "UNKNOWN",
                                      attempt.describe())
            self._status("Idle", 0.0)
            raise FetchFailed(attempt.describe(), FailureReason(attempt.reason), attempt)

    def _note_pushback(self, kind, resp, slowed: bool, host: str = "") -> bool:
        """Feeds a pushback into the source's automatic slowdown; True once
        this request has slowed it, so retries of it do not slow it again."""
        notice = pacing.note_pushback(pacing.slow_key(self.source, host), kind,
                                      resp.headers if resp is not None else {},
                                      self.clock(), slowed)
        if notice:
            self.stats["notice"] = notice
            self._status(notice)
        return slowed or bool(notice)

    def paced(self, fn, url: str, access_method: str, action: str = None):
        """Runs a non-HTTP fetch (e.g. a headless-browser render) under the
        same per-host pace and per-source concurrency limit as ordinary
        requests, so a higher ladder tier can't be used to hit a source
        faster."""
        self._check_cancel()
        poll = _active_poll()
        if poll is not None:
            poll.other_requests += 1
        st = _state(self.source)
        with st["sem"].at(self.policy.max_concurrent, self._check_cancel):
            self._wait_turn(_host_key(url), st)
            self.stats["access_method"] = access_method
            self.stats["requests"] += 1
            self._status(action or f"{access_method}: {url}", 0.0)
            try:
                return fn(url)
            finally:
                self.stats["access_method"] = "Normal HTTP"
                self._status("Idle", 0.0)

    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, data=None, **kw) -> Response:
        return self.request("POST", url, data=data, use_cache=False, **kw)

    def get_with_mirrors(self, path: str, mirrors, **kw) -> Response:
        """Tries `path` against each base URL in `mirrors`, moving on when
        one is unreachable (network error, timeout, 5xx after its retries).
        A challenge on any mirror still stops everything -- a different
        mirror isn't a way around a verification page. Health is recorded
        once for the whole operation, not per mirror, so a dead primary
        can't push the source into 🔴 before the backups are tried.

        Whichever mirror last actually worked for this source is tried
        first (in-memory only, for this process's lifetime -- it does not
        survive a restart), then the rest of `mirrors` in their given
        order. This only changes *which mirror is tried first*; a mirror
        that was never configured for this call is never tried, and the
        challenge/never-bypass rule above is unaffected."""
        wait = health.retry_after(self.source)
        if wait is not None:
            raise SourceUnavailable(
                f"{self.source} is marked unavailable after repeated failures; "
                f"next try allowed in {wait:.0f}s.", retry_after=wait)
        st = _state(self.source)
        preferred = st.get("good_mirror")
        order = mirrors if preferred not in mirrors else \
            [preferred] + [m for m in mirrors if m != preferred]
        errors = []
        for base in order:
            url = base.rstrip("/") + path
            try:
                resp = self.get(url, record_health=False, **kw)
            except NotModified:
                # A conditional re-poll's 304 is this mirror working.
                health.record_success(self.source, 0.0)
                st["good_mirror"] = base
                raise
            except FetchFailed as e:
                if mirror_unreachable(e):
                    errors.append(f"{base}: {e}")
                    continue
                if e.reason != FailureReason.NOT_FOUND:
                    health.record_failure(self.source, e.reason.value, str(e))
                raise
            except ChallengeDetected as e:
                health.record_failure(self.source, e.reason.value, str(e))
                raise
            health.record_success(self.source, 0.0)
            resp.mirror = base
            st["good_mirror"] = base
            return resp
        msg = "Every configured mirror failed:\n" + "\n".join(errors)
        health.record_failure(self.source, FailureReason.HTTP_ERROR.value, msg)
        raise SourceUnavailable(msg, FailureReason.HTTP_ERROR)
