"""
sources/domains.py -- a domain list for a site that moves between domains.

An adapter opts in with a few lines: it declares `base_urls` (https
origins, the first is the default), `verify_site(text)` and, if the
home page isn't the right page to check, `verify_path`; it builds a
`SiteDomains(self, base_url)` in __init__ and fetches its own pages
through it (`get`/`post`), reading `.base` for the origin that answered.

A domain is a host name with an optional non-default port ("host" or
"host:port"; 443 stays implicit, a trailing dot is dropped). The list is the
owner's saved one (settings key `source_domains.<source>`, https origins)
or else the adapter's `base_urls`; the last good one is
`source_domain_last_good.<source>`. Both are sources.db settings, so they
survive a restart and apply to every caller of the adapter.

Which hosts ordinary fetches contact:
  * The listed domains, in order, the last good one first. A domain is
    skipped on the same failures SourceClient.get_with_mirrors skips a
    mirror on (http.mirror_unreachable).
  * Plus any redirect target the transport follows from them, as for every
    source request: each hop is public-address checked, and Cookie and
    Authorization are dropped on a host change. A page that ends on a host
    that isn't on the list is discarded (the next domain is tried) and is
    not cached under the listed URL. A 307/308 on a POST resends its form
    body to the redirect target, as any HTTP client does.
  * A challenge on any domain stops everything and hands off to the person,
    exactly as elsewhere. It never starts discovery.

Which hosts discovery contacts, when every listed domain failed (at most
once per DISCOVERY_INTERVAL per source; the claim is an atomic write in
sources.db, so threads, processes and restarts can't bypass it):
  * Each listed domain's `verify_path`, then at most MAX_DISCOVERY_HOPS
    redirects that discovery follows itself -- the transport follows none
    here. Before every request the URL must be https, on a valid port, with
    a plain DNS host name (HOST_RE; never an IP literal, IPv6 or
    IPv4-mapped) whose every address is public (services.url_guard), and
    not already visited. The last page is fetched to fingerprint it with
    the adapter's `verify_site`.
  * A host that passes, and isn't on the list, is stored as a pending
    proposal (host[:port] only; at most store.MAX_PENDING_PROPOSALS per
    source, enforced in the insert itself).

Confirmation (services/source_domains_service.py, PC only) is what adds a
host to the list and makes it the last good domain. It does not control
the transport-level redirects above.

Then the source's health records ALL_DOMAINS_UNREACHABLE; when discovery
ran and found nothing, the unreachable hook (set by the service layer) gets
redacted probe lines -- host names and failure types only.

A `base_url` passed to the adapter explicitly (tests, a one-off override)
is the whole list: nothing is read from or saved to settings and no
discovery runs.
"""

import re
from urllib.parse import urljoin, urlsplit

from translate_engines import redact_for_storage

from . import health, store
from .http import NotModified, mirror_unreachable, redirects_not_followed
from .models import ChallengeDetected, FailureReason, FetchFailed, SourceError, SourceUnavailable

MAX_DISCOVERY_HOPS = 3
DISCOVERY_INTERVAL = 3600.0
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_DEFAULT_PORTS = {"https": 443, "http": 80}

# A plain DNS name with a dot and an alphabetic TLD: no scheme, port, path,
# userinfo or IP literal (IPv4, IPv6 and IPv4-mapped forms all fail it).
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                     r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")

_unreachable_hook = None


def set_unreachable_hook(fn):
    """fn(source, display_name, probe_lines) is called when every listed
    domain failed and discovery found no candidate. None clears it."""
    global _unreachable_hook
    _unreachable_hook = fn


def normalize_host(value, default_port: int = 443) -> str:
    """"host" or "host:port", lowercased, trailing dot dropped, the default
    port left implicit; "" for anything else (a scheme, path, IP literal,
    or a port that is empty, non-numeric, 0 or above 65535)."""
    text = str(value or "").strip().lower()
    host, sep, port = text.partition(":")
    if sep:
        if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
            return ""
        port = int(port)
    host = host.rstrip(".")
    if not HOST_RE.match(host):
        return ""
    return host if not sep or port == default_port else f"{host}:{port}"


def hostport(url) -> str:
    """normalize_host of an http(s) URL's host and port; "" if it has none
    or they aren't valid."""
    try:
        parts = urlsplit(str(url or "").strip())
        scheme = parts.scheme.lower()
        port = parts.port
        host = parts.hostname or ""
    except ValueError:
        return ""
    if scheme not in _DEFAULT_PORTS or not host:
        return ""
    return normalize_host(f"{host}:{port}" if port is not None else host, _DEFAULT_PORTS[scheme])


def origin(url) -> str:
    """scheme://host[:port] of an http(s) URL; "" otherwise."""
    hp = hostport(url)
    return f"{urlsplit(str(url).strip()).scheme.lower()}://{hp}" if hp else ""


def configured_origins(adapter_cls) -> list:
    """The source's domain list: the owner's saved one, else base_urls."""
    return store.domain_list(adapter_cls.name) or [origin(u) for u in adapter_cls.base_urls]


def _ordered(source: str, origins: list) -> list:
    last = store.last_good_domain(source)
    if last in origins:
        return [last] + [o for o in origins if o != last]
    return list(origins)


def _is_public_host(hp: str) -> bool:
    """A valid host[:port] whose every address is public (services.url_guard,
    the same check the transport applies to each hop)."""
    from services import url_guard
    if not hp or normalize_host(hp) != hp:
        return False
    try:
        url_guard.resolve_public(f"https://{hp}/")
        return True
    except Exception:
        return False


def _safe_hop(url: str) -> str:
    """The host[:port] of a URL discovery may request, else ""."""
    try:
        https = urlsplit(url).scheme.lower() == "https"
    except ValueError:
        return ""
    hp = hostport(url) if https else ""
    return hp if hp and _is_public_host(hp) else ""


def _location(resp) -> str:
    return {k.lower(): v for k, v in (resp.headers or {}).items()}.get("location") or ""


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
        by_host = {hostport(o): o for o in listed}
        path = path_or_url
        if "://" in path_or_url or path_or_url.startswith("//"):
            if hostport(path_or_url) not in by_host:
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
                    errors.append(_probe_line(hostport(base), e))
                    continue
                if e.reason != FailureReason.NOT_FOUND:
                    health.record_failure(self.source, e.reason.value, str(e))
                raise
            except ChallengeDetected as e:
                health.record_failure(self.source, e.reason.value, str(e))
                raise
            final = by_host.get(hostport(resp.url or url))
            if final is None:
                errors.append(f"{hostport(base)}: redirected to a host that is not on the list")
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
                        "Sources to add it to the list.")
            elif ran and not store.domain_proposals(self.source):
                _notify_unreachable(self.source, self.adapter.display_name or self.source, errors)
        health.record_failure(self.source, FailureReason.ALL_DOMAINS_UNREACHABLE.value, msg)
        raise SourceUnavailable(msg, FailureReason.ALL_DOMAINS_UNREACHABLE)

    # -- discovery -------------------------------------------------------------
    def discover(self, listed: list):
        """(ran, host): whether discovery ran, and the pending host[:port] it
        found (new or already pending), else None."""
        if not store.claim_discovery(self.source, DISCOVERY_INTERVAL):
            return False, None
        listed_hosts = {hostport(o) for o in listed}
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
            hp, text = found
            if hp in listed_hosts:
                continue
            try:
                verified = bool(self.adapter.verify_site(text))
            except Exception:
                verified = False
            if not verified:
                continue
            if hp in pending or store.propose_domain(self.source, hp):
                return True, hp
        return True, None

    def _follow(self, url: str):
        """(host[:port], page text) of the page discovery reaches from `url`,
        following at most MAX_DISCOVERY_HOPS redirects itself (the transport
        follows none here). None as soon as a hop fails _safe_hop, repeats
        an earlier URL, or the chain is longer."""
        seen = set()
        for _ in range(MAX_DISCOVERY_HOPS + 1):
            hp = _safe_hop(url)
            parts = urlsplit(url)
            key = (hp, parts.path or "/", parts.query)
            if not hp or key in seen:
                return None
            seen.add(key)
            with redirects_not_followed():
                resp = self.client.get(url, use_cache=False, record_health=False,
                                       action=f"Looking for {self.adapter.display_name or self.source}'s "
                                              "new address")
            if resp.status_code in _REDIRECT_CODES:
                location = _location(resp)
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            if not 200 <= resp.status_code < 300:
                return None
            final = resp.url or url
            if final != url and not _safe_hop(final):
                return None
            return hostport(final), resp.text
        return None
