"""
sources/http_transport.py -- the real network transport under SourceClient:
one request with its redirects followed by hand, and what comes back.

Every hop, the first included, must be http(s) to a public address
(lib.url_guard), with the connection pinned to the validated address. The
body is streamed and decoded here under a size cap and an overall deadline,
with cancel checked between chunks, and every request carries a timeout.
Refusals (UnsafeRedirect, ResponseRefused) carry fixed messages that echo
no URL or address.
"""

import re
import threading
import time
import zlib
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from lib import http as lib_http
from lib import url_guard

from . import charset_sniff, store
from .models import FailureReason, FetchFailed


DEFAULT_TIMEOUT = 20

# Body limits for the real transport (security review MED-1): the body is
# streamed and never read past its cap, and one request (every redirect
# hop plus the body) must finish within its deadline, so a huge or
# slow-drip response can't hold memory or keep a job "running" forever.
MAX_PAGE_BYTES = 10_000_000
MAX_IMAGE_BYTES = 20_000_000
REQUEST_DEADLINE = 180.0
_BODY_CHUNK = 65_536


class Cancelled(Exception):
    """The person pressed Cancel; raised at the next safe point."""


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
    # the last one, so a caller that needs one cookie by name would miss
    # it whenever it isn't the final Set-Cookie line.
    cookies: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return decode_html(self.content, self.headers)

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([\w-]+)""", re.I)


# Declared names that are strict subsets of GB18030: decode with the superset
# so a character outside the declared table still comes out right.
_GB_SUBSETS = {"gb2312", "gb_2312", "gb_2312-80", "gbk", "x-gbk", "cp936", "gb-2312", "euc-cn", "euccn"}


def decode_html(content: bytes, headers: dict | None = None) -> str:
    """Decodes a page the way a browser would: the header's charset, else
    the page's own <meta charset>, else a sniff (UTF-8, then legacy CJK
    encodings). A declared charset is authoritative. (requests' own fallback
    for text/html without a charset is ISO-8859-1, which garbles every CJK
    page that only declares its encoding in a meta tag.)"""
    content = content or b""
    ctype = ""
    for k, v in (headers or {}).items():
        if k.lower() == "content-type":
            ctype = str(v)
    m = re.search(r"charset=[\"']?([\w-]+)", ctype, re.I)
    enc = m.group(1) if m else None
    if not enc:
        mm = _META_CHARSET.search(content[:4096])
        enc = mm.group(1).decode("ascii", "ignore") if mm else None
    if not enc:
        return charset_sniff.sniff_decode(content)
    if enc.lower() in _GB_SUBSETS:
        enc = "gb18030"
    try:
        return content.decode(enc, errors="replace").lstrip("\ufeff")
    except LookupError:
        return charset_sniff.sniff_decode(content)


MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)
REDIRECT_REFUSED = "Refused: the address is not a public web address."
TOO_MANY_REDIRECTS = "Refused: too many redirects."


class UnsafeRedirect(FetchFailed):
    """A request (or one of its redirect hops) targeted a non-public or
    non-http(s) address, or the redirect chain was too long. The message is
    fixed: no URL or IP is echoed. Never retried, and the access ladder
    stops on it rather than trying a browser tier."""

    def __init__(self, message: str = "", reason: FailureReason = FailureReason.ACCESS_DENIED,
                 attempt=None):
        super().__init__(message, reason, attempt)


TOO_LARGE = "Refused: the response was too large."
TOO_SLOW = "Refused: the site took too long to respond."


class ResponseRefused(FetchFailed):
    """The body was over its cap or the request ran past its deadline. The
    message is fixed (no URL). Never retried; the access ladder re-raises
    it rather than trying another tier. `status` is the API status to show:
    422 too large, 503 too slow."""

    status = 422

    def __init__(self, message: str = TOO_LARGE, attempt=None):
        super().__init__(message, FailureReason.HTTP_ERROR, attempt)


class ResponseTooLarge(ResponseRefused):
    status = 422

    def __init__(self, attempt=None):
        super().__init__(TOO_LARGE, attempt)


class ResponseTooSlow(ResponseRefused):
    status = 503

    def __init__(self, attempt=None):
        super().__init__(TOO_SLOW, attempt)


@dataclass
class FetchLimits:
    max_page_bytes: int = MAX_PAGE_BYTES
    max_image_bytes: int = MAX_IMAGE_BYTES
    deadline: float = REQUEST_DEADLINE
    cancel_check: object = None
    clock: object = time.monotonic


def header(headers, name: str) -> str:
    for k, v in (headers or {}).items():
        if k.lower() == name:
            return str(v)
    return ""


def _check_limits(limits: FetchLimits, deadline_at: float):
    if limits.cancel_check and limits.cancel_check():
        raise Cancelled("Cancelled.")
    if limits.clock() > deadline_at:
        raise ResponseTooSlow()


# The encodings the body decoder handles itself; anything else is refused
# rather than handed to urllib3's decoder (its read loop runs until it has
# decoded output, so a stream of empty deflate blocks would never return
# to our deadline/cancel checks -- security review M-1).
ACCEPT_ENCODING = "gzip, deflate"
UNSUPPORTED_ENCODING = "Refused: the response used an unsupported compression."
_DECODE_STEP = 1_048_576


class UnsupportedEncoding(ResponseRefused):
    status = 422

    def __init__(self, attempt=None):
        super().__init__(UNSUPPORTED_ENCODING, attempt)


class BodyDecoder:
    """Decodes a response body fed in raw chunks, never producing more than
    `cap` + 1 decoded bytes in total and at most _DECODE_STEP per call to
    zlib, with `check()` run between steps. gzip (x-gzip) and deflate
    (zlib-wrapped, or raw as some servers send it) only; identity or no
    header passes the bytes through. `feed`/`finish` return the decoded
    bytes; the caller counts them against its cap."""

    def __init__(self, content_encoding: str, cap: int, check=None):
        enc = (content_encoding or "").strip().lower()
        if enc in ("", "identity"):
            self._d = None
        elif enc in ("gzip", "x-gzip"):
            self._d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif enc == "deflate":
            self._d = zlib.decompressobj()
            self._first = b""  # until zlib's header is accepted, kept for a raw retry
        else:
            raise UnsupportedEncoding()
        self._deflate = enc == "deflate"
        self._cap, self._out, self._check = cap, 0, check or (lambda: None)

    def _run(self, data: bytes) -> bytes:
        parts = []
        while True:
            self._check()
            if self._d.eof:
                return b"".join(parts)  # trailing bytes after the stream end are ignored
            step = min(_DECODE_STEP, max(self._cap + 1 - self._out, 1))
            out = self._d.decompress(data, step)
            self._out += len(out)
            parts.append(out)
            if self._out > self._cap:
                raise ResponseTooLarge()
            data = self._d.unconsumed_tail
            if not data:
                return b"".join(parts)

    def feed(self, chunk: bytes) -> bytes:
        if self._d is None:
            self._out += len(chunk)
            return chunk
        if self._deflate and self._first is not None:
            self._first += chunk
            try:
                out = self._run(chunk)
            except zlib.error:
                self._d, self._out = zlib.decompressobj(-zlib.MAX_WBITS), 0
                data, self._first = self._first, None
                try:
                    return self._run(data)
                except zlib.error:
                    raise FetchFailed("The response could not be decoded.",
                                      FailureReason.HTTP_ERROR) from None
            if out or len(self._first) >= 2:
                self._first = None
            return out
        try:
            return self._run(chunk)
        except zlib.error:
            raise FetchFailed("The response could not be decoded.",
                              FailureReason.HTTP_ERROR) from None

    def finish(self) -> bytes:
        if self._d is None:
            return b""
        try:
            out = self._d.flush()
        except zlib.error:
            raise FetchFailed("The response could not be decoded.",
                              FailureReason.HTTP_ERROR) from None
        self._out += len(out)
        if self._out > self._cap:
            raise ResponseTooLarge()
        return out


def read_raw_chunk(raw, n: int) -> bytes:
    """Up to n undecoded bytes: whatever arrived (read1) where the stream
    has it. Never decode_content=True (see BodyDecoder)."""
    read1 = getattr(raw, "read1", None)
    if read1 is not None:
        return read1(n, decode_content=False)
    return raw.read(min(n, 8192), decode_content=False)


def _read_body(r, limits: FetchLimits, deadline_at: float) -> bytes:
    """At most the cap for the response's type (images get their own),
    refused early on a Content-Length over it. Reads raw bytes in small
    pieces (read1 returns whatever arrived) and decodes them itself
    (BodyDecoder), checking cancel and the deadline between every raw
    piece and every decode step. Both the raw and the decoded bytes are
    counted against the cap, so a compressed bomb is capped too."""
    ctype = header(r.headers, "content-type").lower()
    cap = limits.max_image_bytes if ctype.startswith("image/") else limits.max_page_bytes
    length = header(r.headers, "content-length").strip()
    if length.isdigit() and int(length) > cap:
        raise ResponseTooLarge()
    check = lambda: _check_limits(limits, deadline_at)  # noqa: E731
    decoder = BodyDecoder(header(r.headers, "content-encoding"), cap, check)
    chunks, raw_total = [], 0
    while True:
        check()
        chunk = read_raw_chunk(r.raw, _BODY_CHUNK)
        if not chunk:
            break
        raw_total += len(chunk)
        if raw_total > cap:
            raise ResponseTooLarge()
        chunks.append(decoder.feed(chunk))
    chunks.append(decoder.finish())
    return b"".join(chunks)


def ascii_url(url: str) -> str:
    """The URL with its host in the exact ASCII form requests will connect
    to (UTS46 IDNA, lowercased), so the name that is validated, pinned and
    sent is one and the same (getaddrinfo's IDNA2003 maps
    'ß' to 'ss', requests' UTS46 keeps it -- two different hosts)."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise UnsafeRedirect(REDIRECT_REFUSED) from None
    if not host or parts.username or parts.password:
        raise UnsafeRedirect(REDIRECT_REFUSED)
    if host.isascii():
        ascii_host = host.lower()
    else:
        try:
            import idna
            ascii_host = idna.encode(host, uts46=True).decode("ascii").lower()
        except Exception:
            raise UnsafeRedirect(REDIRECT_REFUSED) from None
    if ":" in ascii_host:
        ascii_host = f"[{ascii_host}]"
    netloc = ascii_host + (f":{port}" if port is not None else "")
    return parts._replace(netloc=netloc).geturl()


def _requests_transport(method, url, headers, data, timeout, limits: FetchLimits = None):
    """One request, following redirects by hand: every hop -- the
    first included -- must be http(s) with a host whose every resolved
    address is global (lib.url_guard). Without a proxy the connection
    is pinned to the validated address (Host header, SNI and certificate
    checks keep the real name); with one, the target host is validated by
    name and the proxy does the connecting.

    The body is streamed under `limits` (FetchLimits: size caps, an overall
    deadline for the whole request, cancel between chunks); every response
    is closed."""
    limits = limits or FetchLimits()
    deadline_at = limits.clock() + float(limits.deadline)

    session = _thread_session()
    session.max_bytes = max(limits.max_page_bytes, limits.max_image_bytes)
    # Route through a configured proxy, if one is set. Applied
    # here rather than baked into the session (session.proxies would
    # persist across a settings change within the same thread/process
    # lifetime) so a change takes effect on the very next request.
    proxy_url = store.get_setting("http_proxy_url")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    hops = []
    current, cur_method, cur_data, cur_headers = url, method, data, dict(headers or {})
    # Only encodings BodyDecoder handles (requests would also offer br/zstd
    # when those packages are installed).
    if not any(k.lower() == "accept-encoding" for k in cur_headers):
        cur_headers["Accept-Encoding"] = ACCEPT_ENCODING
    for _ in range(MAX_REDIRECTS + 1):
        _check_limits(limits, deadline_at)
        current = ascii_url(current)
        try:
            r = session.request(cur_method, current, headers=cur_headers, data=cur_data,
                                timeout=timeout, allow_redirects=False, proxies=proxies,
                                stream=True)
        except lib_http.FetchError:  # the adapter refused a host the guard did not validate
            raise UnsafeRedirect(REDIRECT_REFUSED) from None
        hops.append(r)
        location = (r.headers or {}).get("Location") or (r.headers or {}).get("location")
        if r.status_code not in _REDIRECT_CODES or not location:
            try:
                content = _read_body(r, limits, deadline_at)
            except lib_http.ResponseTooLarge:
                # a refusal like _read_body's own, not a source-health fetch error
                raise ResponseTooLarge() from None
            finally:
                r.close()
            break
        r.close()
        nxt = urljoin(current, location)
        # Conditional validators belong to the URL they were saved for (a
        # browser sends them only for its cached URL): never to a redirect
        # target, which could answer 304 for something else.
        cur_headers = {k: v for k, v in cur_headers.items()
                       if k.lower() not in _CONDITIONAL_HEADERS}
        if _should_strip_auth(current, nxt):
            cur_headers = url_guard.cross_origin_headers(cur_headers)
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
    # have ever set a same-named cookie; a site's www and reader hosts
    # sharing this session is enough to trigger it. Iterate each
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
    return Response(status_code=r.status_code, headers=dict(r.headers), content=content,
                    url=r.url, cookies=cookies)


_CONDITIONAL_HEADERS = ("if-none-match", "if-modified-since", "if-match", "if-unmodified-since",
                        "if-range")


def _should_strip_auth(old_url, new_url) -> bool:
    """requests' own rule (host, scheme or non-default port change)."""
    try:
        from requests.sessions import SessionRedirectMixin
        return SessionRedirectMixin.should_strip_auth(None, old_url, new_url)
    except Exception:
        return True


def _resolve_error(exc):
    """DNS failure stays an ordinary (retryable) connection error."""
    try:
        import requests
        return requests.exceptions.ConnectionError(str(exc))
    except ImportError:
        return ConnectionError(str(exc))


# One requests.Session per thread: Session is not documented as thread-safe,
# and fetches run on several threads at once (registry.multi_search's pool,
# background jobs, the chapter-check scheduler).
_tls = threading.local()


def _guard(url: str) -> str:
    try:
        return url_guard.resolve_public(url)
    except url_guard.UnsafeURLError:
        raise UnsafeRedirect(REDIRECT_REFUSED) from None
    except url_guard.URLResolveError as e:
        raise _resolve_error(e) from None


def _thread_session():
    if not hasattr(_tls, "session"):
        _tls.session = lib_http.session(timeout=DEFAULT_TIMEOUT, guard=_guard,
                                        max_bytes=MAX_IMAGE_BYTES, trust_env=True)
    return _tls.session
