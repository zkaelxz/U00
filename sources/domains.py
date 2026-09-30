"""
sources/domains.py -- a domain list for a site that moves between domains.

An adapter opts in with a few lines: it declares `base_urls` (https
origins, the first is the default) and `verify_site(text)`, builds a
`SiteDomains(self, base_url)` in __init__ and fetches its own pages through
it (`get`/`post`), reading `.base` for the origin that answered.

A domain is a DNS name with an optional non-default port ("host" or
"host:port"; 443 stays implicit, a trailing dot is dropped). The list is
the owner's saved one (settings key `source_domains.<source>`, https
origins) or else the adapter's `base_urls`; the last good one is
`source_domain_last_good.<source>`. Both are sources.db settings, so they
survive a restart and apply to every caller of the adapter.

  * Requests go to the listed domains in order, the last good one first,
    through the ordinary SourceClient (its transport follows redirects and
    checks every hop's address is public). A domain is skipped on the same
    failures get_with_mirrors skips a mirror on (http.mirror_unreachable).
  * A result that ended on a host that isn't on the list (or moved from
    https to http) is never used as content, and the client doesn't cache it
    under the listed URL: that domain counts as failed and the next one is
    tried. If that final URL is https and its page passes the adapter's
    `verify_site`, its host[:port] becomes a pending proposal (at most
    store.MAX_PENDING_PROPOSALS per source; a dismissed host never comes
    back). Only the owner's confirmation (services/source_domains_service.py,
    PC only) adds it to the list. No extra request is made to find it.
  * A POST (xbanxia's search) that ended on another host counts as failed
    too. Its form may already have been re-sent by a 307/308 redirect: the
    transport follows those as any HTTP client does.
  * A challenge on any domain stops everything and hands off to the person.
  * When every domain failed, the source's health records
    ALL_DOMAINS_UNREACHABLE; if no proposal is pending, the unreachable hook
    (set by the service layer) gets redacted probe lines -- host names and
    failure types only.

A `base_url` passed to the adapter explicitly (tests, a one-off override)
is the whole list: nothing is read from or saved to settings and nothing
is proposed.
"""

import re
from urllib.parse import urljoin, urlsplit

from translate_engines import redact_for_storage

from . import health, store
from .http import NotModified, mirror_unreachable
from .models import ChallengeDetected, FailureReason, FetchFailed, SourceUnavailable

_DEFAULT_PORTS = {"https": 443, "http": 80}
# A DNS name with an alphabetic TLD. IP literals of every form fail it.
_HOST_RE = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")

_unreachable_hook = None


def set_unreachable_hook(fn):
    """fn(source, display_name, probe_lines) is called when every listed
    domain failed and no proposal is pending. None clears it."""
    global _unreachable_hook
    _unreachable_hook = fn


def normalize_host(value, default_port: int = 443) -> str:
    """"host" or "host:port", lowercased, trailing dot dropped, the default
    port left implicit; "" for anything else (a scheme, path, IP literal,
    whitespace or control character, or a port that is empty, non-numeric,
    0 or above 65535)."""
    text = str(value or "").strip(" ").lower()
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text):
        return ""
    host, sep, port = text.partition(":")
    if sep:
        if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535:
            return ""
        port = int(port)
    host = host.rstrip(".")
    if len(host) > 253 or not _HOST_RE.fullmatch(host):
        return ""
    return host if not sep or port == default_port else f"{host}:{port}"


def hostport(url) -> str:
    """normalize_host of an http(s) URL's host and port; "" if invalid."""
    try:
        parts = urlsplit(str(url or "").strip())
        scheme, port, host = parts.scheme.lower(), parts.port, parts.hostname or ""
    except ValueError:
        return ""
    if scheme not in _DEFAULT_PORTS or not host:
        return ""
    return normalize_host(f"{host}:{port}" if port is not None else host, _DEFAULT_PORTS[scheme])


def _scheme(url) -> str:
    return urlsplit(str(url or "")).scheme.lower()


def origin(url) -> str:
    """scheme://host[:port] of an http(s) URL; "" otherwise."""
    hp = hostport(url)
    return f"{_scheme(url)}://{hp}" if hp else ""


def configured_origins(adapter_cls) -> list:
    """The source's domain list: the owner's saved one, else base_urls."""
    return store.domain_list(adapter_cls.name) or [origin(u) for u in adapter_cls.base_urls]


def _ordered(source: str, origins: list) -> list:
    last = store.last_good_domain(source)
    if last in origins:
        return [last] + [o for o in origins if o != last]
    return list(origins)


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
        try:
            if not self.fixed and store.last_good_domain(self.source) != base:
                store.set_last_good_domain(self.source, base)
        except store.SourcesDatabaseBusy:
            store.log_dropped("a last-good domain record")

    def _proposals_pending(self) -> bool:
        try:
            return bool(store.domain_proposals(self.source))
        except store.SourcesDatabaseBusy:
            store.log_dropped("a domain proposal lookup")
            return True   # unknown: do not notify on a guess

    def _maybe_propose(self, final_url: str, resp) -> bool:
        """Proposes the host an off-list result came from, if it is https
        and its page is this site. True when a proposal is pending for it."""
        hp = hostport(final_url)
        if self.fixed or not hp or _scheme(final_url) != "https":
            return False
        try:
            if not self.adapter.verify_site(resp.text):
                return False
        except Exception:
            return False
        try:
            return store.propose_domain(self.source, hp) or \
                hp in {p["host"] for p in store.domain_proposals(self.source)}
        except store.SourcesDatabaseBusy:
            store.log_dropped("a domain proposal")
            return False

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
        errors, found = [], False
        for base in listed:
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
            final_url = resp.url or url
            final = by_host.get(hostport(final_url))
            if final is None or _scheme(final_url) != _scheme(url) or \
                    (method == "POST" and final != base):
                if final is None:
                    found = self._maybe_propose(final_url, resp) or found
                errors.append(f"{hostport(base)}: redirected away from this domain")
                continue
            health.record_success(self.source, 0.0)
            self._worked(final)
            return resp
        msg = "Every domain on this source's list failed:\n" + "\n".join(errors)
        if found:
            msg += ("\nA possible new address was found. Confirm it on this PC under "
                    "Sources to add it to the list.")
        elif not self.fixed and not self._proposals_pending():
            _notify_unreachable(self.source, self.adapter.display_name or self.source, errors)
        health.record_failure(self.source, FailureReason.ALL_DOMAINS_UNREACHABLE.value, msg)
        raise SourceUnavailable(msg, FailureReason.ALL_DOMAINS_UNREACHABLE)
