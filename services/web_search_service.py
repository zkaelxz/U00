"""
services/web_search_service.py -- the optional web-search fallback (roadmap
item 114). When a title search finds nothing on the source adapters, the
user can ask their own self-hosted SearXNG instance instead. Off by default.

  - get_config / set_config: the SearXNG base URL and an on/off switch, in
    app_settings. status() says only whether the fallback is usable (no URL).
  - test_connection: one small query, to check the address and that the
    instance has the JSON format turned on. Works while the fallback is off.
  - search: GET <base>/search?q=...&format=json, turned into at most
    MAX_RESULTS {title, snippet, url, domain} rows. The result pages are
    never fetched here: they are links only. Importing one goes through the
    pasted-link flow (sources_url_service), which has its own SSRF guards.

Network: the request goes only to the configured base URL, never to a URL
from the caller. The URL must be http(s) with a host and no user name,
password, query or fragment (settings_service.validate_endpoint_url).
Loopback and LAN addresses are fine (SearXNG usually runs on this PC or the
LAN), but every address the host resolves to is checked at call time:
link-local (cloud metadata), multicast, reserved and unspecified addresses
are refused, and so are Baihe's own ports on loopback. The check is not
pinned to the connection (the address is the PC owner's own, gated setting);
its DNS lookup relies on the OS resolver's timeout. Requests carry timeout=,
follow no redirects, ignore proxy settings and read at most
MAX_RESPONSE_BYTES within READ_DEADLINE. Limits, so a loop cannot get the
owner's SearXNG blocked by the engines it asks, nor one account keep web
search busy for everyone: each caller (signed-in user, else client address)
runs one search at a time and starts at most PER_CALLER_MAX per RATE_WINDOW
seconds; at most MAX_CONCURRENT run at once and RATE_MAX start per window in
the whole app (409 busy / 429 after that). The PC-only Test has its own lock
and bucket, so searches never block it.
Errors are fixed text: never the URL or the server's reply.

No FastAPI import: plain dicts in, plain dicts out.
"""
import ipaddress
import json
import logging
import socket
import threading
from typing import Optional
from urllib.parse import urlsplit

import db
from services import settings_service
from lib import capped_body
from services.auth_service import SlidingWindowRateLimiter
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError)

log = logging.getLogger(__name__)

SETTING = "web_search"
HTTP_TIMEOUT = (3.05, 10)
READ_DEADLINE = 15.0
MAX_RESPONSE_BYTES = 2_000_000
MAX_RESULTS = 20
MAX_QUERY_LEN = 200
MAX_TITLE_LEN = 300
MAX_SNIPPET_LEN = 500
MAX_RESULT_URL_LEN = 2000
RATE_MAX = 10
PER_CALLER_MAX = 4
TEST_RATE_MAX = 5
RATE_WINDOW = 60.0
MAX_CONCURRENT = 3

_UNREACHABLE = "Couldn't reach the SearXNG server. Check the address and that it is running."
_REDIRECTED = ("The SearXNG server answered with a redirect, which is not followed. "
               "Use the address it redirects to.")
_NO_JSON = ("SearXNG refused the JSON format. Add 'json' to search.formats in its "
            "settings.yml, then restart it.")
_LIMITED = ("SearXNG's limiter refused the request. Turn off server.limiter in its "
            "settings.yml, or add this PC's address to its pass list.")
_BAD_REPLY = "The SearXNG server sent a reply this app could not read."
_DISABLED = "Web search is off. Turn it on in Settings first."
_NOT_SET = "Set the SearXNG address in Settings first."
_BUSY = "A web search is already running. Try again in a moment."

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_in_flight_lock = threading.Lock()
_in_flight = set()
_rate = SlidingWindowRateLimiter(RATE_MAX, RATE_WINDOW, max_keys=1)
_caller_rate = SlidingWindowRateLimiter(PER_CALLER_MAX, RATE_WINDOW, max_keys=1000)
_test_lock = threading.Lock()
_test_rate = SlidingWindowRateLimiter(TEST_RATE_MAX, RATE_WINDOW, max_keys=1)


# --- settings ----------------------------------------------------------------

def _stored() -> dict:
    saved = db.get_app_setting(SETTING) or {}
    return saved if isinstance(saved, dict) else {}


def get_config() -> dict:
    s = _stored()
    return {"enabled": bool(s.get("enabled")), "base_url": s.get("base_url") or None}


def status() -> dict:
    """What any signed-in viewer may know: whether the fallback can be used."""
    s = _stored()
    return {"enabled": bool(s.get("enabled") and s.get("base_url"))}


def set_config(enabled: Optional[bool] = None, base_url: Optional[str] = None) -> dict:
    """Fields left as None keep their saved value; "" clears the URL."""
    s = _stored()
    if base_url is not None:
        s["base_url"] = settings_service.validate_endpoint_url(base_url) \
            if base_url.strip() else None
    if enabled is not None:
        s["enabled"] = bool(enabled)
    db.set_app_setting(SETTING, s)
    return get_config()


# --- HTTP ----------------------------------------------------------------------

def _check_target(url: str):
    """Refuses a base URL that resolves anywhere a SearXNG server cannot
    sensibly be (see the module docstring)."""
    parts = urlsplit(url)
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError, UnicodeError):
        raise DependencyUnavailableError(_UNREACHABLE) from None
    if not infos:
        raise DependencyUnavailableError(_UNREACHABLE)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if (ip.is_link_local or ip.is_multicast or ip.is_unspecified
                or (ip.is_reserved and not ip.is_private)):
            raise InvalidInputError("That server address is not allowed.")
        if ip.is_loopback and port in settings_service.baihe_own_ports():
            raise InvalidInputError("That address is this app's own port, not SearXNG.")


def _read_capped(resp) -> bytes:
    """The body, at most MAX_RESPONSE_BYTES and READ_DEADLINE seconds in all."""
    return capped_body.read_capped(resp, MAX_RESPONSE_BYTES, READ_DEADLINE,
                                   lambda: DependencyUnavailableError(_BAD_REPLY))


def _query_server(base_url: str, query: str) -> dict:
    import requests
    _check_target(base_url)
    session = requests.Session()
    session.trust_env = False  # a proxy would reach the LAN server on our behalf
    try:
        resp = session.get(base_url + "/search",
                           params={"q": query, "format": "json"},
                           headers={"Accept": "application/json", "Accept-Language": "en",
                                    "User-Agent": "Baihe-Subtitler"},
                           timeout=HTTP_TIMEOUT, allow_redirects=False, stream=True)
        try:
            if 300 <= resp.status_code < 400:
                raise DependencyUnavailableError(_REDIRECTED)
            if resp.status_code == 403:
                raise DependencyUnavailableError(_NO_JSON)
            if resp.status_code == 429:
                raise DependencyUnavailableError(_LIMITED)
            if resp.status_code >= 400:
                log.info("SearXNG answered HTTP %s", resp.status_code)
                raise DependencyUnavailableError(_UNREACHABLE)
            body = _read_capped(resp)
        finally:
            resp.close()
    except requests.RequestException:
        raise DependencyUnavailableError(_UNREACHABLE) from None
    finally:
        session.close()
    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        raise DependencyUnavailableError(_BAD_REPLY) from None
    if not isinstance(data, dict) or not isinstance(data.get("results", []), list):
        raise DependencyUnavailableError(_BAD_REPLY)
    return data


def _clean_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text[:limit]


def _result(item) -> Optional[dict]:
    """One SearXNG result as a link row, or None if its URL is not a plain
    http(s) link."""
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if not isinstance(url, str) or len(url) > MAX_RESULT_URL_LEN \
            or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname \
            or parts.username is not None or parts.password is not None:
        return None
    domain = parts.hostname.lower()
    return {"title": _clean_text(item.get("title"), MAX_TITLE_LEN) or domain,
            "snippet": _clean_text(item.get("content"), MAX_SNIPPET_LEN),
            "url": url, "domain": domain}


def _clean_query(query) -> str:
    text = _clean_text(query, MAX_QUERY_LEN + 1)
    if not text:
        raise InvalidInputError("Enter a title to search for.")
    if len(text) > MAX_QUERY_LEN:
        raise InvalidInputError(f"The search is too long (at most {MAX_QUERY_LEN} characters).")
    return text


def _search_as(caller: str, base_url: str, query: str) -> dict:
    """The shared-search limits (module docstring) around one request."""
    with _in_flight_lock:
        if caller in _in_flight:
            raise ConflictError(_BUSY, details={"reason": "busy"})
        _in_flight.add(caller)
    try:
        _caller_rate.hit(caller)
        if not _slots.acquire(blocking=False):
            raise ConflictError(_BUSY, details={"reason": "busy"})
        try:
            _rate.hit("searxng")
            return _query_server(base_url, query)
        finally:
            _slots.release()
    finally:
        with _in_flight_lock:
            _in_flight.discard(caller)


def _links(data: dict) -> list:
    results, seen = [], set()
    for item in data.get("results") or []:
        row = _result(item)
        if row and row["url"] not in seen:
            seen.add(row["url"])
            results.append(row)
            if len(results) >= MAX_RESULTS:
                break
    return results


def search(query, caller: str = "local") -> dict:
    """Web results for `query` from the configured SearXNG instance: links
    only, labelled as web results by the caller. 409 while off. `caller`
    names the per-caller bucket (the route passes the user or address)."""
    q = _clean_query(query)
    s = _stored()
    if not s.get("enabled"):
        raise ConflictError(_DISABLED, details={"reason": "disabled"})
    if not s.get("base_url"):
        raise InvalidInputError(_NOT_SET)
    caller = str(caller or "unknown")[:200]
    return {"query": q, "source": "searxng",
            "results": _links(_search_as(caller, s["base_url"], q))}


def test_connection() -> dict:
    """Works while the fallback is off, so it can be checked before turning
    it on. PC only; its own lock and bucket, so searches never block it."""
    base_url = _stored().get("base_url")
    if not base_url:
        raise InvalidInputError(_NOT_SET)
    if not _test_lock.acquire(blocking=False):
        raise ConflictError("A test is already running.", details={"reason": "busy"})
    try:
        _test_rate.hit("test")
        data = _query_server(base_url, "baihe")
    finally:
        _test_lock.release()
    return {"ok": True, "result_count": len(_links(data))}
