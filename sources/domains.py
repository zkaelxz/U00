"""
sources/domains.py -- a domain list for a site that moves between domains.

An adapter opts in with a few lines: it declares `base_urls` (https
origins, the first is the default), `verify_site(text)` and, if the
home page isn't the right page to check, `verify_path`; it builds a
`SiteDomains(self, base_url)` in __init__ and fetches its own pages
through it (`get`/`post`), reading `.base` for the origin that answered.

  * Requests try the source's domain list in order, the last domain that
    worked first. The list is the owner's saved one (settings key
    `source_domains.<source>`) or else the adapter's `base_urls`; the last
    good one is `source_domain_last_good.<source>`. Both are sources.db
    settings, so they survive a restart and apply to every caller of the
    adapter (API jobs, chapter checks, the Sources tab).
  * A domain is skipped on the same failures SourceClient.get_with_mirrors
    skips a mirror on (http.mirror_unreachable), and when the answer came
    from a host that isn't on the list (a redirect): that host's content is
    never used until the owner confirms it.
  * A challenge on any domain stops everything and hands off to the
    person, exactly as elsewhere. It never starts discovery.
  * When every domain failed, discovery runs (at most once per
    DISCOVERY_INTERVAL per source, per process): each listed domain's
    `verify_path` is fetched through the same client (paced, timeout, the
    transport's public-address check on every hop) following at most
    MAX_DISCOVERY_HOPS visible redirects, https only. A host that isn't on
    the list, resolves only to public addresses and passes the adapter's
    `verify_site` is stored as a pending proposal (host name only). Then
    the source's health records ALL_DOMAINS_UNREACHABLE.
  * When discovery ran and found nothing, the unreachable hook (set by the
    service layer, services/source_domains_service.py) gets the redacted
    probe lines -- host names and failure types only.

A `base_url` passed to the adapter explicitly (tests, a one-off override)
is the whole list: nothing is read from or saved to settings and no
discovery runs.
"""

import ipaddress
import threading
from urllib.parse import urljoin, urlsplit

from translate_engines import redact_for_storage

from . import health, store
from .http import NotModified, mirror_unreachable
from .models import ChallengeDetected, FailureReason, FetchFailed, SourceError, SourceUnavailable

MAX_DISCOVERY_HOPS = 3
DISCOVERY_INTERVAL = 3600.0
_REDIRECT_CODES = (301, 302, 303, 307, 308)

_lock = threading.Lock()
_last_discovery = {}
_unreachable_hook = None


def reset_discovery_state():
    """Test helper: forget when each source last ran discovery."""
    with _lock:
        _last_discovery.clear()


def set_unreachable_hook(fn):
    """fn(source, display_name, probe_lines) is called when every listed
    domain failed and discovery found no candidate. None clears it."""
    global _unreachable_hook
    _unreachable_hook = fn


def origin(url) -> str:
    """scheme://host[:port] of an http(s) URL, lowercased; "" otherwise."""
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme.lower() not in ("http", "https") or not host:
        return ""
    return f"{parts.scheme.lower()}://{host}" + (f":{port}" if port else "")


def host_of(url) -> str:
    try:
        return (urlsplit(str(url or "").strip()).hostname or "").lower()
    except ValueError:
        return ""


def configured_origins(adapter_cls) -> list:
    """The source's domain list: the owner's saved one, else base_urls."""
    return store.domain_list(adapter_cls.name) or [origin(u) for u in adapter_cls.base_urls]


def _ordered(source: str, origins: list) -> list:
    last = store.last_good_domain(source)
    if last in origins:
        return [last] + [o for o in origins if o != last]
    return list(origins)


def _is_public_host(host: str) -> bool:
    """A DNS name (never an IP literal) whose every address is public
    (services.url_guard, the same check the transport applies to each hop)."""
    from services import url_guard
    try:
        ipaddress.ip_address(host.strip("[]"))
        return False
    except ValueError:
        pass
    try:
        url_guard.resolve_public(f"https://{host}/")
        return True
    except Exception:
        return False


def _probe_line(host: str, exc) -> str:
    """One redacted line per failed domain: host, failure type and HTTP
    status or exception class -- never a URL or the raw error text."""
    attempt = getattr(exc, "attempt", None)
    status = getattr(attempt, "http_status", None)
    detail = f"HTTP {status}" if status else \
        str(getattr(attempt, "detail", "") or "").split(":", 1)[0].strip()
    line = f"{host}: {exc.reason.value}" + (f" ({detail})" if detail else "")
    return redact_for_storage(line)


def _notify_unreachable(source: str, display_name: str, lines: list):
    hook = _unreachable_hook
    if hook is None:
        return
    try:
        hook(source, display_name, [redact_for_storage(x) for x in lines])
    except Exception:
        import applog
        applog.get_logger().warning("The unreachable-domains hook failed", exc_info=True)


class SiteDomains:
    def __init__(self, adapter, base_url: str = None):
        self.adapter = adapter
        self.source = adapter.name
        self.fixed = origin(base_url) if base_url else ""
        self.base = self.origins()[0]

    @property
    def client(self):
        return self.adapter.client

    def origins(self) -> list:
        if self.fixed:
            return [self.fixed]
        return _ordered(self.source, configured_origins(type(self.adapter)))

    def _worked(self, base: str):
        self.base = base
        if not self.fixed and store.last_good_domain(self.source) != base:
            store.set_last_good_domain(self.source, base)

    def get(self, path_or_url: str, **kw):
        return self.request("GET", path_or_url, **kw)

    def post(self, path: str, data=None, **kw):
        kw.setdefault("use_cache", False)
        return self.request("POST", path, data=data, **kw)

    def request(self, method: str, path_or_url: str, data=None, **kw):
        """`path_or_url` is a path, or an absolute URL: on a listed host it
        fails over like a path; on any other host it is fetched as given,
        exactly as before this list existed."""
        method = method.upper()
        listed = self.origins()
        by_host = {host_of(o): o for o in listed}
        path = path_or_url
        if "://" in path_or_url or path_or_url.startswith("//"):
            if host_of(path_or_url) not in by_host:
                return self.client.request(method, path_or_url, data=data, **kw)
            parts = urlsplit(path_or_url)
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        queue, tried, errors = list(listed), set(), []
        while queue:
            base = queue.pop(0)
            if base in tried:
                continue
            tried.add(base)
            url = urljoin(base + "/", path)
            try:
                resp = self.client.request(method, url, data=data, record_health=False, **kw)
            except NotModified:
                health.record_success(self.source, 0.0)
                self._worked(base)
                raise
            except FetchFailed as e:
                if mirror_unreachable(e):
                    errors.append(_probe_line(host_of(base), e))
                    continue
                if e.reason != FailureReason.NOT_FOUND:
                    health.record_failure(self.source, e.reason.value, str(e))
                raise
            except ChallengeDetected as e:
                health.record_failure(self.source, e.reason.value, str(e))
                raise
            final = by_host.get(host_of(resp.url or url))
            if final is None:
                errors.append(f"{host_of(base)}: redirected to a host that is not on the list")
                continue
            if method == "POST" and final != base:
                # A redirected POST arrives as a GET without its form: send
                # it again, straight to the listed host the redirect named.
                queue.insert(0, final)
                continue
            health.record_success(self.source, 0.0)
            self._worked(final)
            return resp
        return self._all_failed(listed, errors)

    def _all_failed(self, listed: list, errors: list):
        msg = "Every domain on this source's list failed:\n" + "\n".join(errors)
        if not self.fixed:
            ran, found = self.discover(listed)
            if found:
                msg += ("\nA possible new address was found. Confirm it on this PC under "
                        "Sources before it is used.")
            elif ran and not store.domain_proposals(self.source):
                _notify_unreachable(self.source, self.adapter.display_name or self.source, errors)
        health.record_failure(self.source, FailureReason.ALL_DOMAINS_UNREACHABLE.value, msg)
        raise SourceUnavailable(msg, FailureReason.ALL_DOMAINS_UNREACHABLE)

    # -- discovery -------------------------------------------------------------
    def discover(self, listed: list):
        """(ran, host): whether discovery ran, and the pending host it found
        (new or already pending), else None."""
        now = self.client.clock()
        with _lock:
            last = _last_discovery.get(self.source)
            if last is not None and now - last < DISCOVERY_INTERVAL:
                return False, None
            _last_discovery[self.source] = now
        listed_hosts = {host_of(o) for o in listed}
        pending = {p["host"] for p in store.domain_proposals(self.source)}
        for base in listed:
            try:
                found = self._follow(urljoin(base + "/", self.adapter.verify_path))
            except ChallengeDetected:
                # Never handed to the person from here: a challenge while
                # looking for a new address just ends the search.
                return False, None
            except SourceError:
                continue
            if found is None:
                continue
            host, text = found
            if not host or host in listed_hosts or not _is_public_host(host):
                continue
            try:
                verified = bool(self.adapter.verify_site(text))
            except Exception:
                verified = False
            if not verified:
                continue
            if host in pending or store.propose_domain(self.source, host):
                return True, host
        return True, None

    def _follow(self, url: str):
        """(final host, page text) of an https page, following at most
        MAX_DISCOVERY_HOPS redirects the client hands back; None otherwise."""
        for _ in range(MAX_DISCOVERY_HOPS + 1):
            if urlsplit(url).scheme.lower() != "https":
                return None
            resp = self.client.get(url, use_cache=False, record_health=False,
                                   action=f"Looking for {self.adapter.display_name or self.source}'s "
                                          "new address")
            location = {k.lower(): v for k, v in (resp.headers or {}).items()}.get("location")
            if resp.status_code in _REDIRECT_CODES and location:
                url = urljoin(url, location)
                continue
            final = resp.url or url
            if not 200 <= resp.status_code < 300 or urlsplit(final).scheme.lower() != "https":
                return None
            return host_of(final), resp.text
        return None
