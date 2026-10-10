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
fixes (a vendor API) or an address only the PC owner can set through a
local_only route (the Ollama address in Settings); the connection is then not pinned and redirects are
not followed unless `allow_redirects=True` is passed. A redirect to another
origin never carries the caller's credential headers.

A guarded fetch ignores environment proxies (`HTTP(S)_PROXY`): a proxy would
re-resolve the name and defeat the pin. See "Guarded fetches ignore proxies"
in docs/technical-notes.md.

Standard library plus `requests`.
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional, Tuple, Union
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
        try:
            return self.body.decode(self.encoding or "utf-8", errors="replace")
        except LookupError:  # a site's Content-Type can name a charset Python lacks
            return self.body.decode("utf-8", errors="replace")


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
               timeout: float = DEFAULT_TIMEOUT, method: str = "GET",
               trust_env: bool = True, **kwargs):
    """Streamed request connecting to the validated `ip` (not a fresh DNS
    lookup), or an ordinary request when `ip` is None. `trust_env=False` keeps
    environment proxies out of it (a LAN or loopback address)."""
    import requests
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    session.trust_env = trust_env
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


def _clamp(timeout, remaining: float):
    if isinstance(timeout, tuple):
        return tuple(min(t, remaining) for t in timeout)
    return min(timeout, remaining)


def _origin(url: str):
    p = urlsplit(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == "https" else 80)


def _is_credential_header(name: str) -> bool:
    n = name.lower()
    return n in ("authorization", "proxy-authorization", "cookie") or any(
        k in n for k in ("api-key", "apikey", "token", "secret"))


def _headers_for_hop(headers: Optional[dict], origin, target: str) -> Optional[dict]:
    # A key meant for one vendor must not follow a redirect elsewhere (requests'
    # rebuild_auth did this before the manual redirect loop replaced it).
    if not headers or _origin(target) == origin:
        return headers
    return {k: v for k, v in headers.items() if not _is_credential_header(k)}


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


def request(method: str, url: str, *, timeout: Union[float, Tuple[float, float]], max_bytes: int,
            deadline: Optional[float] = None, headers: Optional[dict] = None,
            allow_redirects: Optional[bool] = None, max_redirects: int = MAX_REDIRECTS,
            guard: Optional[Callable[[str], Optional[str]]] = check_public,
            truncate: bool = False, max_error_bytes: Optional[int] = None,
            trust_env: bool = True, clock=None, **kwargs) -> Response:
    """`timeout` is seconds, or a (connect, read) pair. `deadline` is the total
    seconds for all hops and the body (default 3 x the longer `timeout`). Extra `kwargs` (params, json, data, files) go to requests
    and are sent on the first hop only; a redirect is followed as a GET.
    `allow_redirects` defaults to True with a guard and False without one.
    A body over `max_bytes` raises ResponseTooLarge, or is cut when
    `truncate` is set; with `max_error_bytes`, a 4xx/5xx body is cut to that
    instead of refused, so a vendor's error text stays small and keeps its status.
    `trust_env=False` bypasses environment proxies, for an address a proxy
    must not be asked to reach (loopback, the user's LAN server)."""
    import requests

    clock = clock or time.monotonic
    deadline = 3 * (max(timeout) if isinstance(timeout, tuple) else timeout) if deadline is None else deadline
    started = clock()
    current = url
    origin = _origin(url)
    if allow_redirects is None:
        allow_redirects = guard is not None
    session_kw = {} if trust_env else {"trust_env": False}
    for hop in range(max_redirects + 1):
        remaining = deadline - (clock() - started)
        if remaining <= 0:
            raise ResponseTooSlow()
        ip = guard(current) if guard else None
        try:
            resp = pinned_get(current, ip, _headers_for_hop(headers, origin, current), _clamp(timeout, remaining),
                              method if hop == 0 else "GET", **session_kw,
                              **(kwargs if hop == 0 else {}))
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
            if max_error_bytes is not None and resp.status_code >= 400:
                body = _read_truncated(resp, max_error_bytes, left, clock)
            else:
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
