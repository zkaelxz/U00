"""api/auth_gates.py -- the route enumeration the permission tests walk and the
ASGI gates built on api.auth's checks: the cross-site, early-auth, loopback-only
and household-host gates and the acting-principal middleware.

Moved out of api/auth.py to keep that module under the size ceiling. Import
these names from `api.auth`, which re-exports them at its end; nothing imports
this module first, because it needs `api.auth` fully defined.
"""

import ipaddress
import re
from urllib.parse import urlsplit

from fastapi import Request

from api.security_headers import ADMIN_SECURITY_HEADERS, HOUSEHOLD_SECURITY_HEADERS, HSTS_VALUE, with_default_headers
from services import ownership_service
from services.service_errors import ForbiddenError, UnauthenticatedError
from api.auth import (MARKER_ATTR, _BODY_METHODS, _GENERIC_403, _authenticate, _cross_site_safe,
                      _is_local_scope)


# --- route enumeration (static test, early gate) ---------------------------

def _declarations_of(dependencies) -> list:
    out = []
    for dep in dependencies or ():
        call = getattr(dep, "dependency", None) or getattr(dep, "call", None)
        mark = getattr(call, MARKER_ATTR, None)
        if mark is not None:
            out.append(mark)
    return out


def iter_route_declarations(app):
    """Yields (route, full_path, methods, [declarations]) for every route the
    app can dispatch to, recursing into included routers on FastAPI versions
    that keep them nested. Declarations inherited from an include_router(...,
    dependencies=...) call count too, so a double declaration is visible."""
    def walk(routes, prefix, inherited):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                ctx = route.include_context
                children = list(original.routes) + list(
                    getattr(original, "_low_priority_routes", ()) or ())
                yield from walk(children, prefix + (ctx.prefix or ""),
                                inherited + _declarations_of(ctx.dependencies))
                continue
            dependant = getattr(route, "dependant", None)
            own = _declarations_of(getattr(dependant, "dependencies", ())) if dependant else []
            # Old FastAPI flattens include-level deps into the route's
            # dependant; don't count them twice.
            decls = own if (inherited and own[:len(inherited)] == inherited) else inherited + own
            yield (route, prefix + getattr(route, "path", ""),
                   frozenset(getattr(route, "methods", None) or ()), decls)
    yield from walk(list(app.router.routes), "", [])


def public_api_paths(app) -> frozenset:
    """Exact /api paths declared public_route() (all are parameter-free)."""
    return frozenset(path for _r, path, _m, decls in iter_route_declarations(app)
                     if path.startswith("/api") and decls == [("public", None)])


def local_only_matchers(app) -> list:
    """(compiled path regex, methods) for every local_only() route, so the
    early gate can refuse a remote request before its body is read."""
    from starlette.routing import compile_path
    return [(compile_path(path)[0], methods)
            for _r, path, methods, decls in iter_route_declarations(app)
            if decls == [("local_only", None)]]


def _json_refusal(status: int, code: str, message: str):
    from fastapi.responses import JSONResponse
    from api.error_handlers import error_body
    return JSONResponse(status_code=status, content=error_body(code, message))


class LocalOnlyCrossSiteGate:
    """Pure-ASGI middleware, installed in both auth modes. Applies the
    local_only() content-type/header rule (_cross_site_safe) to local_only
    routes before the body is read, so a no-cors multipart POST from a page
    on another loopback port is refused before Starlette spools the upload
    to disk. The route dependency still runs the same check afterwards.

    With auth OFF (all_api=True) the rule covers every POST/PUT/PATCH under
    /api, not just local_only routes: off mode grants owner rights with no
    CSRF token, and the loopback Origin check ignores the port, so without
    this a page on another local port (a dev server) could start
    a paid LLM run or cancel a job with a no-preflight simple POST. With
    auth on, every non-GET already needs the session's CSRF header (itself
    a custom header that forces a preflight), so only local_only routes are
    gated there."""

    def __init__(self, app, local_only_fn, all_api=False):
        self.app = app
        self._local_only_fn = local_only_fn
        self._local_only = None
        self._all_api = all_api

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method", "").upper() in _BODY_METHODS:
            if self._local_only is None:
                self._local_only = self._local_only_fn()
            path, method = scope.get("path", ""), scope.get("method", "")
            # Matches the raw scope path. If the app is ever mounted under a
            # root_path, strip it first or this prefix check stops matching.
            gated = (self._all_api and (path == "/api" or path.startswith("/api/"))) \
                or any(rx.match(path) and method in methods for rx, methods in self._local_only)
            if gated and not _cross_site_safe(Request(scope)):
                return await _json_refusal(403, "forbidden", _GENERIC_403)(scope, receive, send)
        return await self.app(scope, receive, send)


class EarlyAuthGate:
    """Pure-ASGI middleware, installed only with auth on. For any /api path
    that isn't a public route it refuses, before the body is read, any
    request that isn't a direct loopback one and
    - targets a local_only() route (403), or
    - has no valid session (401), or
    - is an unsafe method without the session's CSRF token (403).
    The per-route dependency stays the authority (permission checks); this
    moves the cheap refusals ahead of body parsing (multipart uploads are
    spooled to disk with no size limit) and ahead of FastAPI's own
    422/404/405 replies, so an anonymous client learns nothing beyond
    "log in".

    never_local (the household listener): no request is treated as a
    direct loopback one, so local_only() routes are always refused here."""

    def __init__(self, app, public_paths_fn, local_only_fn=lambda: [], never_local=False):
        self.app = app
        self._public_paths_fn = public_paths_fn
        self._local_only_fn = local_only_fn
        self._never_local = never_local
        self._public = None
        self._local_only = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._needs_check(scope):
            return await self.app(scope, receive, send)
        request = Request(scope)
        client = scope.get("client")
        if not self._never_local and _is_local_scope(client[0] if client else None,
                                                     request.headers):
            return await self.app(scope, receive, send)
        if self._is_local_only(scope):
            return await _json_refusal(403, "forbidden", _GENERIC_403)(scope, receive, send)
        try:
            _authenticate(request)
        except (UnauthenticatedError, ForbiddenError) as exc:
            status = 401 if isinstance(exc, UnauthenticatedError) else 403
            return await _json_refusal(status, exc.code, exc.message)(scope, receive, send)
        return await self.app(scope, receive, send)

    def _is_local_only(self, scope) -> bool:
        if self._local_only is None:
            self._local_only = self._local_only_fn()
        path, method = scope.get("path", ""), scope.get("method", "")
        return any(rx.match(path) and method in methods for rx, methods in self._local_only)

    def _needs_check(self, scope) -> bool:
        path = scope.get("path", "")
        # Anything Starlette could route to an /api handler. The frontend
        # catch-all only serves static files for other paths.
        if not (path == "/api" or path.startswith("/api/")):
            return False
        if self._public is None:
            self._public = self._public_paths_fn()
        return path not in self._public


class LoopbackOnlyGate:
    """Pure-ASGI middleware, installed only with auth OFF. Off mode hands
    every request full owner rights, so it must only ever serve the owner at
    the PC: any request that isn't a direct loopback one (non-loopback peer,
    non-loopback Host, any proxy/forwarding header, non-loopback Origin) is
    refused with a generic 403 -- every path, /api/health and the frontend
    included. This is what stops a reverse proxy on the same PC (Caddy
    connects from 127.0.0.1 but adds X-Forwarded-For and keeps the public
    Host) from exposing the unauthenticated API, and it also blocks DNS
    rebinding (the Host header is the attacker's name)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            # A frame load is a GET with no Origin, which the check below
            # accepts, so only these headers stop another website framing
            # the owner's app (clickjacking: off mode has no CSRF token).
            send = with_default_headers(send, ADMIN_SECURITY_HEADERS)
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not _is_local_scope(client[0] if client else None, Request(scope).headers):
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                return await _json_refusal(403, "forbidden", _GENERIC_403)(scope, receive, send)
        return await self.app(scope, receive, send)


_DEFAULT_PORTS = {"https": 443, "http": 80}
_HOST_PORT_RE = re.compile(r":([0-9]{1,5})")
_HOST_NAME_RE = re.compile(r"[a-z0-9._-]+")

def _normal_host(name: str, port):
    """(name, port) with the name lowercased, one trailing dot dropped, an
    IPv6 literal bracketed in its compressed form; None if the name is
    unusable. A non-ASCII name is unusable: browsers send the punycode (xn--)
    form, and startup asks for BAIHE_PUBLIC_URL in that form."""
    name = (name or "").strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if ":" in name:
        try:
            return f"[{ipaddress.IPv6Address(name).compressed}]", port
        except ValueError:
            return None
    if name.endswith("."):
        name = name[:-1]
    if not name or not _HOST_NAME_RE.fullmatch(name):
        return None
    return name, port


def parse_host_header(value: str):
    """(name, port or None) from a Host header, normalised like
    `_normal_host`; None when it is malformed (userinfo, path, spaces, a bad
    port...)."""
    value = (value or "").strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        name, rest = value[:end + 1], value[end + 1:]
    else:
        name, sep, port_text = value.partition(":")
        rest = sep + port_text
    port = None
    if rest:
        match = _HOST_PORT_RE.fullmatch(rest)
        if not match or not 1 <= int(match.group(1)) <= 65535:
            return None
        port = int(match.group(1))
    if name.startswith("[") and ":" not in name:
        return None
    return _normal_host(name, port)


def public_host(public_url: str):
    """((name, port), port_is_default) for the Host the household listener
    answers to, from BAIHE_PUBLIC_URL (port: the URL's own, else its
    scheme's default), or None when there is no usable public URL."""
    try:
        parts = urlsplit(public_url or "")
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    except ValueError:
        return None
    if not parts.hostname or port is None:
        return None
    return _normal_host(parts.hostname, port), port == _DEFAULT_PORTS[parts.scheme]


def _host_allowed(allowed, headers: list) -> bool:
    hosts = [v for k, v in headers if k.lower() == b"host"]
    if allowed is None or allowed[0] is None or len(hosts) != 1:
        return False
    (name, port), port_is_default = allowed
    got = parse_host_header(hosts[0].decode("latin-1"))
    if got is None or got[0] != name:
        return False
    # A browser leaves the scheme's default port out of the Host.
    return got[1] == port or (got[1] is None and port_is_default)


def _arrived_over_https(scope) -> bool:
    """The reverse proxy on this PC says the browser used https: the scope's
    scheme is https and there is exactly one `X-Forwarded-Proto: https`.
    The peer can't be checked here: uvicorn's proxy headers handling has
    already replaced it with the browser's address from X-Forwarded-For. It
    sets the scheme from X-Forwarded-Proto only for a trusted (loopback)
    peer, and the household listener binds loopback only."""
    if scope.get("scheme") != "https":
        return False
    protos = [v for k, v in scope.get("headers", ()) if k.lower() == b"x-forwarded-proto"]
    return len(protos) == 1 and protos[0].decode("latin-1").strip().lower() == "https"


class HouseholdGate:
    """Pure-ASGI middleware, outermost, on the household listener only.
    - Host allowlist: the one Host that app answers to is BAIHE_PUBLIC_URL's
      (case, a trailing dot and the default port don't matter). Anything
      else, a missing or repeated Host included, is a 400 before routing:
      a DNS-rebinding page or a stray name pointed at the proxy reaches
      nothing. X-Forwarded-Host is ignored; the proxy must pass the Host on.
    - Security headers on every reply (a header a route set itself, such as
      a stricter CSP, is kept), and HSTS only when the proxy on this PC says
      the browser used https (`_arrived_over_https`).
    Unexpected-error (500) replies are written outside every middleware and
    don't get the headers; they carry no page content."""

    def __init__(self, app, public_url: str):
        self.app = app
        self._allowed = public_host(public_url)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        ok = _host_allowed(self._allowed, scope.get("headers", ()))
        if scope["type"] == "websocket":
            if not ok:
                return await send({"type": "websocket.close", "code": 1008})
            return await self.app(scope, receive, send)
        extra = HOUSEHOLD_SECURITY_HEADERS
        if _arrived_over_https(scope):
            extra = extra + (("Strict-Transport-Security", HSTS_VALUE),)

        send_with_headers = with_default_headers(send, extra)
        if not ok:
            return await _json_refusal(400, "invalid_host", "Unknown host.")(
                scope, receive, send_with_headers)
        return await self.app(scope, receive, send_with_headers)


class ActingPrincipalMiddleware:
    """Pure-ASGI middleware, installed only with auth on. Binds a
    per-request holder (ownership_service.bind_request) that
    require_permission fills with the principal, so background_jobs can
    record which user started a job without every service passing it
    through. Pure ASGI (not BaseHTTPMiddleware) so the context var reaches
    the endpoint."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        token = ownership_service.bind_request()
        try:
            return await self.app(scope, receive, send)
        finally:
            ownership_service.unbind_request(token)
