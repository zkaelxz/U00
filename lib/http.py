"""
lib/http.py -- the one outbound HTTP front door for fetches the app makes
itself (engine SDK calls are separate, see engine_backends/shared.py).

`get` / `post` return a fully read `Response`. They always apply: a connect/read
`timeout=`, a byte cap and a total monotonic deadline on the body
(`capped_body.read_capped`), manual redirect handling with `guard` run on
EVERY hop and the connection pinned to the address the guard validated, and
fixed-text errors. No error carries a URL, host, header or exception text, so
nothing needs redacting on the way out and a key sent in a header can never
come back in a message.

`guard` is `check_public` (the SSRF rule from `lib.url_guard`) for any URL a
person or a site supplied. Pass `guard=None` only for a URL the code itself
fixes (a vendor API); redirects are then followed unguarded and the
connection is not pinned.

Standard library plus `requests`.
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional
from urllib.parse import urljoin, urlsplit

from lib import capped_body, url_guard
from lib.errors import DependencyUnavailableError, InvalidInputError

FETCH_FAILED = "The page could not be fetched."
TOO_LARGE = "The response is larger than expected."
TOO_SLOW = "The request took too long."
DEFAULT_TIMEOUT = 20
MAX_REDIRECTS = 3
_REDIRECT_CODES = (301, 302, 303, 307, 308)


class FetchError(DependencyUnavailableError):
    """The request failed, or the server's answer could not be used."""

    def __init__(self, message: str = FETCH_FAILED):
        super().__init__(message)


class ResponseTooLarge(FetchError):
    def __init__(self):
        super().__init__(TOO_LARGE)


class ResponseTooSlow(FetchError):
    def __init__(self):
        super().__init__(TOO_SLOW)


@dataclass
class Response:
    status: int
    headers: Mapping
    body: bytes
    url: str
    encoding: str = "utf-8"
    ok: bool = field(init=False)

    def __post_init__(self):
        self.ok = 200 <= self.status < 400

    def text(self) -> str:
        return self.body.decode(self.encoding or "utf-8", errors="replace")


def check_public(url: str) -> str:
    """The default guard: the validated address to pin to, or InvalidInputError
    (not public / malformed) or FetchError (did not resolve)."""
    try:
        return url_guard.resolve_public(url)
    except url_guard.URLResolveError:
        raise FetchError() from None
    except url_guard.UnsafeURLError as e:
        raise InvalidInputError(str(e)) from None


def pinned_get(url: str, ip: Optional[str], headers: Optional[dict],
               timeout: float = DEFAULT_TIMEOUT, method: str = "GET", **kwargs):
    """Streamed request connecting to the validated `ip` (not a fresh DNS
    lookup), or an ordinary request when `ip` is None."""
    import requests
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    if ip:
        parts = urlsplit(url)
        host = parts.hostname

        class _PinnedAdapter(HTTPAdapter):
            def init_poolmanager(self, *args, **kw):
                if parts.scheme == "https":  # SNI + cert check against the real name
                    kw["server_hostname"] = host
                    kw["assert_hostname"] = host
                super().init_poolmanager(*args, **kw)

            def send(self, request, **kw):
                p = urlsplit(request.url)
                ip_host = f"[{ip}]" if ":" in ip else ip
                netloc = ip_host + (f":{p.port}" if p.port else "")
                request.url = p._replace(netloc=netloc).geturl()
                request.headers["Host"] = p.netloc
                return super().send(request, **kw)

        session.trust_env = False  # a proxy would re-resolve the hostname itself
        session.mount(f"{parts.scheme}://", _PinnedAdapter())
    return session.request(method, url, headers=headers, timeout=timeout,
                           allow_redirects=False, stream=True, **kwargs)


def _read_truncated(resp, cap_bytes, deadline_seconds, clock) -> bytes:
    """The first `cap_bytes` of the body: a page longer than the cap is cut,
    not refused (text extraction only needs the start of it)."""
    started = clock()
    body = bytearray()
    try:
        for chunk in resp.iter_content(min(capped_body.DEFAULT_CHUNK, cap_bytes)):
            body.extend(chunk[:cap_bytes - len(body)])
            if len(body) >= cap_bytes:
                break
            if clock() - started > deadline_seconds:
                raise ResponseTooSlow()
    finally:
        resp.close()
    return bytes(body)


def request(method: str, url: str, *, timeout: float, max_bytes: int,
            deadline: Optional[float] = None, headers: Optional[dict] = None,
            allow_redirects: bool = True, max_redirects: int = MAX_REDIRECTS,
            guard: Optional[Callable[[str], Optional[str]]] = check_public,
            truncate: bool = False, clock=None, **kwargs) -> Response:
    """`deadline` is the total seconds for all hops and the body (default
    3 x `timeout`). Extra `kwargs` (params, json, data, files) go to requests
    and are sent on the first hop only; a redirect is followed as a GET.
    A body over `max_bytes` raises ResponseTooLarge, or is cut when
    `truncate` is set."""
    import requests

    clock = clock or time.monotonic
    deadline = 3 * timeout if deadline is None else deadline
    started = clock()
    current = url
    for hop in range(max_redirects + 1):
        remaining = deadline - (clock() - started)
        if remaining <= 0:
            raise ResponseTooSlow()
        ip = guard(current) if guard else None
        try:
            resp = pinned_get(current, ip, headers, min(timeout, remaining),
                              method if hop == 0 else "GET", **(kwargs if hop == 0 else {}))
        except requests.RequestException:
            raise FetchError() from None
        location = resp.headers.get("Location")
        if allow_redirects and resp.status_code in _REDIRECT_CODES:
            resp.close()
            if not location:
                raise FetchError()
            current = urljoin(current, location)
            continue
        try:
            left = max(deadline - (clock() - started), 0)
            body = (_read_truncated(resp, max_bytes, left, clock) if truncate else
                    capped_body.read_capped(resp, max_bytes, left, ResponseTooLarge,
                                            make_deadline_error=ResponseTooSlow, clock=clock))
        except (FetchError, InvalidInputError):
            raise
        except Exception:
            raise FetchError() from None
        return Response(resp.status_code, dict(resp.headers), body, current,
                        getattr(resp, "encoding", None) or "utf-8")
    raise FetchError()  # too many redirects


def get(url: str, *, timeout: float, max_bytes: int, **kw) -> Response:
    return request("GET", url, timeout=timeout, max_bytes=max_bytes, **kw)


def post(url: str, *, timeout: float, max_bytes: int, **kw) -> Response:
    return request("POST", url, timeout=timeout, max_bytes=max_bytes, **kw)
