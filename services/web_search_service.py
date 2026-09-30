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
MAX_RESPONSE_BYTES within READ_DEADLINE. One search runs at a time.
Errors are fixed text: never the URL or the server's reply.

No Streamlit or FastAPI import: plain dicts in, plain dicts out.
"""
import ipaddress
import json
import logging
import socket
import threading
import time
from typing import Optional
from urllib.parse import urlsplit

import db
from services import settings_service
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
BAIHE_OWN_PORTS = (8501, 8600, 8756)
API_PORT_ENV = "BAIHE_API_PORT"

_UNREACHABLE = "Couldn't reach the SearXNG server. Check the address and that it is running."
_REDIRECTED = ("The SearXNG server answered with a redirect, which is not followed. "
               "Use the address it redirects to.")
_NO_JSON = ("SearXNG refused the JSON format. Add 'json' to search.formats in its "
            "settings.yml, then restart it.")
_BAD_REPLY = "The SearXNG server sent a reply this app could not read."
_DISABLED = "Web search is off. Turn it on in Settings first."
_NOT_SET = "Set the SearXNG address in Settings first."
_BUSY = "A web search is already running. Try again in a moment."

_lock = threading.Lock()


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

def _baihe_ports() -> set:
    ports = set(BAIHE_OWN_PORTS)
    raw = (settings_service.resolve_env_names((API_PORT_ENV,)) or "").strip()
    if raw.isdigit():
        ports.add(int(raw))
    return ports


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
        if ip.is_loopback and port in _baihe_ports():
            raise InvalidInputError("That address is this app's own port, not SearXNG.")


def _read_capped(resp) -> bytes:
    """The body, at most MAX_RESPONSE_BYTES and READ_DEADLINE seconds in
    all (the per-read timeout alone restarts on every trickled chunk)."""
    started, body = time.monotonic(), bytearray()
    for chunk in resp.iter_content(64 * 1024):
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES or time.monotonic() - started > READ_DEADLINE:
            raise DependencyUnavailableError(_BAD_REPLY)
    return bytes(body)


def _query_server(base_url: str, query: str) -> dict:
    import requests
    _check_target(base_url)
    session = requests.Session()
    session.trust_env = False  # a proxy would reach the LAN server on our behalf
    try:
        resp = session.get(base_url + "/search",
                           params={"q": query, "format": "json"},
                           headers={"Accept": "application/json"},
                           timeout=HTTP_TIMEOUT, allow_redirects=False, stream=True)
        try:
            if 300 <= resp.status_code < 400:
                raise DependencyUnavailableError(_REDIRECTED)
            if resp.status_code == 403:
                raise DependencyUnavailableError(_NO_JSON)
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


def _run(base_url: str, query: str) -> list:
    if not _lock.acquire(blocking=False):
        raise ConflictError(_BUSY, details={"reason": "busy"})
    try:
        data = _query_server(base_url, query)
    finally:
        _lock.release()
    results, seen = [], set()
    for item in data.get("results") or []:
        row = _result(item)
        if row and row["url"] not in seen:
            seen.add(row["url"])
            results.append(row)
            if len(results) >= MAX_RESULTS:
                break
    return results


def search(query) -> dict:
    """Web results for `query` from the configured SearXNG instance: links
    only, labelled as web results by the caller. 409 while off."""
    q = _clean_query(query)
    s = _stored()
    if not s.get("enabled"):
        raise ConflictError(_DISABLED, details={"reason": "disabled"})
    if not s.get("base_url"):
        raise InvalidInputError(_NOT_SET)
    return {"query": q, "source": "searxng", "results": _run(s["base_url"], q)}


def test_connection() -> dict:
    """Works while the fallback is off, so it can be checked before turning
    it on."""
    base_url = _stored().get("base_url")
    if not base_url:
        raise InvalidInputError(_NOT_SET)
    return {"ok": True, "result_count": len(_run(base_url, "baihe"))}
