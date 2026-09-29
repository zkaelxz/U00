"""
sources/http.py -- the one paced HTTP client every adapter and the
generic importer go through (Step 23 items 2, 3, 3b, 4).

What it guarantees, structurally rather than by convention:
  * Human-paced: a random 1-3s gap (configurable) between requests to
    the same source, one request in flight per source by default, and an
    adapter can declare a stricter per-host minimum (e.g. a robots.txt
    Crawl-delay).
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
"""

import random
import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from . import detect, health, store
from .cache import RawCache
from .models import (AccessTier, AttemptRecord, ChallengeDetected, CHALLENGE_REASONS,
                     FailureReason, FetchFailed, SourceUnavailable)

DEFAULT_TIMEOUT = 20
MAX_SINGLE_BACKOFF = 60.0

# A plain desktop browser identity -- the same posture as a person's own
# browser, never a named bot identity a site has chosen to block.
DEFAULT_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")


class Cancelled(Exception):
    """The person pressed Cancel; raised at the next safe point."""


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


@dataclass
class Response:
    status_code: int
    headers: dict
    content: bytes
    url: str
    from_cache: bool = False
    reasons: list = field(default_factory=list)
    # Cookies the response actually set, read via requests' own cookiejar
    # rather than a plain `headers` lookup: a response can carry several
    # Set-Cookie lines, and plain-dict header merging (below) only keeps
    # the last one -- mangaz.com's own login-ticket exchange (Step 23l)
    # needs a specific cookie by name regardless of Set-Cookie order.
    cookies: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return decode_html(self.content, self.headers)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


def decode_html(content: bytes, headers: dict = None) -> str:
    """Decodes a page the way a browser would: the header's charset, else
    the page's own <meta charset>, else UTF-8. (requests' own fallback for
    text/html without a charset is ISO-8859-1, which garbles every CJK page
    that only declares its encoding in a meta tag.)"""
    ctype = ""
    for k, v in (headers or {}).items():
        if k.lower() == "content-type":
            ctype = v
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    enc = m.group(1) if m else None
    if not enc:
        mm = _META_CHARSET.search(content[:4096] or b"")
        enc = mm.group(1).decode("ascii", "ignore") if mm else "utf-8"
    try:
        return content.decode(enc, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)
REDIRECT_REFUSED = "Refused: the address is not a public web address."
TOO_MANY_REDIRECTS = "Refused: too many redirects."


class UnsafeRedirect(Exception):
    """A request (or one of its redirect hops) targeted a non-public or
    non-http(s) address, or the redirect chain was too long. The message is
    fixed: no URL or IP is echoed."""


def _proxy_in_use(session, url, proxy_url) -> bool:
    """True when this request will go through a proxy (the app's own
    setting, or an environment proxy requests would pick up). The proxy
    connects on our behalf, so the connection can't be pinned; the target
    host is still validated by name."""
    if proxy_url:
        return True
    if not getattr(session, "trust_env", False):
        return False
    try:
        from requests.utils import get_environ_proxies
        return bool(get_environ_proxies(url))
    except Exception:
        return False


def _requests_transport(method, url, headers, data, timeout):
    """One request, following redirects by hand (B-25): every hop -- the
    first included -- must be http(s) with a host whose every resolved
    address is global (services.url_guard). Without a proxy the connection
    is pinned to the validated address (Host header, SNI and certificate
    checks keep the real name); with one, the target host is validated by
    name and the proxy does the connecting."""
    from services import url_guard

    session = _thread_session()
    # Step 98: route through a configured proxy, if one is set. Applied
    # here rather than baked into the session (session.proxies would
    # persist across a settings change within the same thread/process
    # lifetime) so a change takes effect on the very next request.
    proxy_url = store.get_setting("http_proxy_url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    hops = []
    current, cur_method, cur_data, cur_headers = url, method, data, dict(headers or {})
    for _ in range(MAX_REDIRECTS + 1):
        try:
            ip = url_guard.resolve_public(current)
        except url_guard.UnsafeURLError:
            raise UnsafeRedirect(REDIRECT_REFUSED) from None
        except url_guard.URLResolveError as e:
            raise _resolve_error(e) from None
        host = (urlsplit(current).hostname or "").lower()
        _tls.pin = None if _proxy_in_use(session, current, proxy_url) else (host, ip)
        try:
            r = session.request(cur_method, current, headers=cur_headers, data=cur_data,
                                timeout=timeout, allow_redirects=False, proxies=proxies)
        finally:
            _tls.pin = None
        hops.append(r)
        location = (r.headers or {}).get("Location") or (r.headers or {}).get("location")
        if r.status_code not in _REDIRECT_CODES or not location:
            break
        nxt = urljoin(current, location)
        if (urlsplit(nxt).hostname or "").lower() != host:
            cur_headers = {k: v for k, v in cur_headers.items() if k.lower() != "authorization"}
        if r.status_code == 303 or (r.status_code in (301, 302) and cur_method == "POST"):
            if cur_method != "HEAD":
                cur_method = "GET"
            cur_data = None
        current = nxt
    else:
        raise UnsafeRedirect(TOO_MANY_REDIRECTS)
    # `r.cookies` alone only carries the *final* hop's own Set-Cookie headers
    # (requests' HTTPAdapter.build_response extracts each response's cookies
    # onto that same response object, not onto the ones before it) -- a
    # cookie set on an intermediate redirect hop would otherwise be silently
    # dropped from the Response this function returns. Merge every hop's own
    # cookies, oldest first, so a later hop (including the final response)
    # can still override an earlier same-named cookie. Deliberately never
    # `session.cookies` -- that jar accumulates cookies from every request
    # any adapter makes on this thread for the app's whole runtime, and
    # flattening it with dict()/.update() raises a real
    # requests.cookies.CookieConflictError the moment two different hosts
    # have ever set a same-named cookie (confirmed: mangaz.com's own two
    # hosts, www.mangaz.com/vw.mangaz.com, share this session). Iterate each
    # jar's own Cookie objects directly rather than `dict.update(jar)`: a
    # plain dict.update() against a RequestsCookieJar calls its __getitem__
    # per key, and RequestsCookieJar._find_no_duplicates treats a falsy
    # cookie value (an empty string) as "not found" and raises KeyError
    # instead of returning it -- a real bug in `requests` itself, hit live
    # by kuaikan's real site, which sets exactly such an empty-value cookie
    # (`referer_name=""`) on every visit.
    # With redirects followed by hand, each hop is its own response; a
    # response's own `history` is still honoured in case a transport hands
    # one back.
    cookies = {}
    for resp in hops:
        for hop in list(getattr(resp, "history", None) or []) + [resp]:
            for cookie in hop.cookies:
                cookies[cookie.name] = cookie.value
    return Response(status_code=r.status_code, headers=dict(r.headers), content=r.content,
                    url=r.url, cookies=cookies)


def _resolve_error(exc):
    """DNS failure stays an ordinary (retryable) connection error."""
    try:
        import requests
        return requests.exceptions.ConnectionError(str(exc))
    except ImportError:
        return ConnectionError(str(exc))


def _pinning_adapter():
    """An HTTPAdapter that, while `_tls.pin` names this request's host,
    connects to the validated IP instead of re-resolving the name (the
    request URL is restored before the session extracts cookies, so the
    cookie jar and Response.url still see the real host)."""
    from requests.adapters import HTTPAdapter

    class _PinningAdapter(HTTPAdapter):
        def build_connection_pool_key_attributes(self, request, verify, cert=None):
            host_params, pool_kwargs = super().build_connection_pool_key_attributes(
                request, verify, cert)
            name = getattr(request, "_baihe_pinned_name", None)
            if name and host_params.get("scheme") == "https":
                pool_kwargs["server_hostname"] = name
                pool_kwargs["assert_hostname"] = name
            return host_params, pool_kwargs

        def send(self, request, **kw):
            pin = getattr(_tls, "pin", None)
            parts = urlsplit(request.url)
            if not pin or (parts.hostname or "").lower() != pin[0]:
                return super().send(request, **kw)
            name, ip = pin
            original = request.url
            ip_host = f"[{ip}]" if ":" in ip else ip
            request.url = parts._replace(netloc=ip_host + (f":{parts.port}" if parts.port else "")).geturl()
            request.headers["Host"] = parts.netloc.rsplit("@", 1)[-1]
            request._baihe_pinned_name = name
            try:
                resp = super().send(request, **kw)
            finally:
                request.url = original
            resp.url = original
            return resp

    return _PinningAdapter()


_tls = threading.local()


def _thread_session():
    import requests
    if not hasattr(_tls, "session"):
        _tls.session = requests.Session()
        _tls.session.mount("http://", _pinning_adapter())
        _tls.session.mount("https://", _pinning_adapter())
    return _tls.session


def _is_timeout(exc) -> bool:
    name = type(exc).__name__.lower()
    return "timeout" in name


def _is_connection_error(exc) -> bool:
    try:
        import requests
        return isinstance(exc, requests.exceptions.ConnectionError)
    except ImportError:
        return isinstance(exc, ConnectionError)


# Shared per-source state, so two clients for the same source (a search
# and an import running at once) still share one pace and one concurrency
# limit rather than each getting their own.
_state_lock = threading.Lock()
_source_state = {}


def _state(source: str, max_concurrent: int) -> dict:
    with _state_lock:
        st = _source_state.get(source)
        if st is None or st["limit"] != max_concurrent:
            st = {"sem": threading.BoundedSemaphore(max_concurrent), "limit": max_concurrent,
                  "pace_lock": threading.Lock(), "last": {}, "good_mirror": None}
            _source_state[source] = st
        return st


def reset_pacing_state():
    """Test helper / settings-change hook: forget every source's last
    request time, rebuild the concurrency limits, and forget which mirror
    last worked for each source."""
    with _state_lock:
        _source_state.clear()


class SourceClient:
    """
    transport(method, url, headers, data, timeout) -> Response is
    injectable so tests never touch the network; `sleep`/`clock`/`rng` are
    injectable so pacing is testable without real waiting.
    """

    def __init__(self, source: str, policy: PacingPolicy = None, transport=None,
                 sleep=time.sleep, clock=time.monotonic, rng=None, cache: RawCache = None,
                 default_headers: dict = None, cancel_check=None, status_cb=None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.source = source
        self.policy = policy or PacingPolicy.from_settings()
        self.transport = transport or _requests_transport
        self.sleep = sleep
        self.clock = clock
        self.rng = rng or random.Random()
        self.cache = cache
        self.default_headers = {"User-Agent": DEFAULT_USER_AGENT}
        self.default_headers.update(default_headers or {})
        self.cancel_check = cancel_check
        self.status_cb = status_cb
        self.timeout = timeout
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0,
                      "current_action": "Idle", "current_delay": 0.0,
                      "access_method": "Normal HTTP"}
        self.attempts = []   # AttemptRecord per failed/succeeded request, for diagnostics

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
    def _wait_turn(self, host: str, st: dict):
        """Called holding the source's pace lock."""
        self._maybe_take_a_break(st)
        last = st["last"].get(host)
        gap = self.rng.uniform(self.policy.min_delay, self.policy.max_delay)
        gap = max(gap, float(self.policy.host_min_interval.get(host, 0.0)))
        if last is not None:
            wait = last + gap - self.clock()
            if wait > 0:
                self._status(f"Waiting {wait:.1f}s before next request...", wait)
                self._sleep_cancellable(wait)
        st["last"][host] = self.clock()

    def _pick_break_at(self) -> int:
        lo = self.policy.session_break_min_requests
        hi = self.policy.session_break_max_requests
        return max(1, round(self.rng.uniform(lo, hi)))

    def _maybe_take_a_break(self, st: dict):
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
                                     self.policy.session_break_max_delay)
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

        cacheable = use_cache and method.upper() == "GET" and self.cache is not None
        if cacheable:
            cached = self.cache.get(url)
            if cached is not None:
                self.stats["cache_hits"] += 1
                self._status(f"Cache hit: {url}")
                return Response(200, {}, cached, url, from_cache=True)

        hdrs = dict(self.default_headers)
        hdrs.update(headers or {})
        host = urlsplit(url).netloc
        st = _state(self.source, self.policy.max_concurrent)

        attempt_no = 0
        while True:
            with st["sem"]:
                with st["pace_lock"]:
                    self._wait_turn(host, st)
                self._status(action or f"Fetching {url}", 0.0)
                self.stats["requests"] += 1
                started = self.clock()
                try:
                    resp = self.transport(method.upper(), url, hdrs, data, self.timeout)
                    exc = None
                except Exception as e:     # network-level failure
                    resp, exc = None, e
                latency = self.clock() - started

            if exc is not None:
                reason = FailureReason.TIMEOUT if _is_timeout(exc) else FailureReason.HTTP_ERROR
                attempt = AttemptRecord(tier=AccessTier.STATIC_HTTP.value, ok=False,
                                        reason=reason.value, detail=f"{type(exc).__name__}: {exc}"[:300],
                                        final_url=url, at=time.time())
                retryable = reason == FailureReason.TIMEOUT or _is_connection_error(exc)
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
                    if cacheable:
                        self.cache.put(url, resp.content)
                    self._status("Idle", 0.0)
                    return resp
                reason = reasons[0] if reasons else FailureReason.HTTP_ERROR
                attempt = AttemptRecord(tier=AccessTier.STATIC_HTTP.value, ok=False,
                                        reason=reason.value, at=time.time(), **ev)
                if reason in CHALLENGE_REASONS:
                    self.attempts.append(attempt)
                    if record_health:
                        health.record_failure(self.source, reason.value,
                                              f"Challenge at {url}")
                    self._status("Stopped: browser verification needed", 0.0)
                    raise ChallengeDetected(
                        f"{reason.value} at {url} -- automated requests stopped.",
                        url=url, reason=reason, attempt=attempt)
                retryable = reason == FailureReason.RATE_LIMIT or resp.status_code >= 500

            self.attempts.append(attempt)
            if retryable and attempt_no < self.policy.max_retries:
                backoff = min(self.policy.backoff_base * (2 ** attempt_no), MAX_SINGLE_BACKOFF)
                retry_hdr = (resp.headers if resp is not None else {})
                ra = {k.lower(): v for k, v in retry_hdr.items()}.get("retry-after")
                if ra and str(ra).strip().isdigit():
                    backoff = min(max(backoff, float(ra)), MAX_SINGLE_BACKOFF)
                attempt_no += 1
                self.stats["retries"] += 1
                self._status(f"{attempt.reason} -- retry {attempt_no}/{self.policy.max_retries} "
                             f"in {backoff:.0f}s", backoff)
                self._sleep_cancellable(backoff)
                continue

            if record_health:
                health.record_failure(self.source, attempt.reason or "UNKNOWN",
                                      attempt.describe())
            self._status("Idle", 0.0)
            raise FetchFailed(attempt.describe(), FailureReason(attempt.reason), attempt)

    def paced(self, fn, url: str, access_method: str, action: str = None):
        """Runs a non-HTTP fetch (e.g. a headless-browser render) under the
        same per-source pace and concurrency limit as ordinary requests, so
        a higher ladder tier can't be used to hit a source faster."""
        self._check_cancel()
        st = _state(self.source, self.policy.max_concurrent)
        with st["sem"]:
            with st["pace_lock"]:
                self._wait_turn(urlsplit(url).netloc, st)
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
        st = _state(self.source, self.policy.max_concurrent)
        preferred = st.get("good_mirror")
        order = mirrors if preferred not in mirrors else \
            [preferred] + [m for m in mirrors if m != preferred]
        errors = []
        for base in order:
            url = base.rstrip("/") + path
            try:
                resp = self.get(url, record_health=False, **kw)
            except FetchFailed as e:
                if e.reason in (FailureReason.HTTP_ERROR, FailureReason.TIMEOUT,
                                FailureReason.RATE_LIMIT) and \
                        (e.attempt is None or not e.attempt.http_status
                         or e.attempt.http_status >= 500 or e.attempt.http_status == 429):
                    errors.append(f"{base}: {e}")
                    continue
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
