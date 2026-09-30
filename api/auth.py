"""
api/auth.py -- the deny-by-default permission layer (Step 133; see
docs/remote-access-decision.md, which holds the route -> permission table).

Every route in `api/routers/*.py` (and the frontend catch-all in
`api/static_frontend.py`) must declare exactly one of

    dependencies=[require_permission("library.read")]   # a catalogue permission
    dependencies=[public_route()]                        # health-style, no login
    dependencies=[local_only()]                          # PC-only (loopback), see below
    dependencies=[authenticated()]                       # any signed-in user; only for
                                                         # routes on the caller's own
                                                         # session (/api/auth/logout)

`tests/test_api_permissions.py` walks every route (`iter_route_declarations`)
and fails if one lacks exactly one, so a new route can't ship undeclared.

Modes (`BAIHE_API_AUTH`, see `api/api_config.py`):
- `off` (default): every request is the local owner with every permission,
  no session, no CSRF -- today's behaviour. So `LoopbackOnlyGate` refuses
  (403) every request that isn't a direct loopback one (proxy headers,
  non-loopback peer/Host/Origin), and a non-loopback BAIHE_API_HOST is
  refused at startup (`api_config.check_bind_safety`).
- `on`: `require_permission` needs a valid session cookie
  (`__Host-baihe_session`; plain `baihe_session` only in the plain-http
  loopback dev case, see `session_cookie_secure`), the permission, and, for
  POST/PUT/PATCH/DELETE, a matching `X-CSRF-Token` header. The sign-in
  callback (step 134, `api/routers/auth_routes.py`) also sets a readable
  `__Host-baihe_csrf` cookie so the React client can send that header
  (double submit); the server still checks the header against the hash
  stored with the session, never against the cookie. 401 = no/invalid
  session, 403 = lacking the permission (code `forbidden`) or a failed CSRF
  check (code `csrf_failed`, so the client can tell it apart from a
  PC-only refusal). Messages are generic. `local_only()` needs no session but requires a
  direct loopback connection (peer, Host, no proxy headers, loopback
  Origin): the owner at the PC. That is a safeguard, not authentication
  (see the key-write note in docs/archive/migration-handoff.md); the real admin
  isolation is the separate admin listener (D5), not built yet.
  `EarlyAuthGate` repeats the cheap part of that check before the request
  body is read, so an anonymous client can't make the server parse a large
  multipart upload before being refused.

Paid engines: a route that starts LLM work declares `jobs.start` (household)
and its handler calls `require_engines_allowed` with the engines the request
names, so a user without `engines.paid` can only use `FREE_ENGINES`.
"""

import ipaddress
from urllib.parse import urlsplit

from fastapi import Depends, Request

from services import auth_service, ownership_service
from services.service_errors import ForbiddenError, UnauthenticatedError

COOKIE_NAME = "__Host-baihe_session"     # Secure mode (always, except loopback-http dev)
DEV_COOKIE_NAME = "baihe_session"        # plain-http loopback dev (__Host- needs Secure)
CSRF_COOKIE_NAME = "__Host-baihe_csrf"
DEV_CSRF_COOKIE_NAME = "baihe_csrf"
CSRF_HEADER = "X-CSRF-Token"
_UNSAFE_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))
MARKER_ATTR = "_baihe_auth"   # (kind, permission-or-None); read by the static test
_GENERIC_401 = "Authentication required."
_GENERIC_403 = "Not allowed."


class CsrfFailedError(ForbiddenError):
    """403 with its own code: the session is fine but the request lacks the
    session's CSRF token. The React client reloads the token instead of
    treating the 403 as "this is a PC-only action"."""

    code = "csrf_failed"


def _auth_enabled(app) -> bool:
    """Fail closed: an app built without settings is treated as auth on."""
    settings = getattr(app.state, "settings", None)
    return bool(getattr(settings, "auth_enabled", True))


def local_owner_principal() -> dict:
    return {"user_id": None, "email": None, "is_admin": True, "is_local_owner": True,
            "permissions": list(auth_service.PERMISSIONS)}


def _marked(fn, kind, permission=None):
    setattr(fn, MARKER_ATTR, (kind, permission))
    return Depends(fn)


def _authenticate(request: Request) -> dict:
    """Session + CSRF check shared by the dependency. Returns the principal
    or raises 401/403. Nothing is cached: permissions are re-read from the
    DB on every request so a grant/revoke applies to the next one."""
    token = session_token(request)
    principal = auth_service.resolve_session(token)
    if principal is None:
        raise UnauthenticatedError(_GENERIC_401)
    if request.method.upper() not in ("GET", "HEAD", "OPTIONS") and not auth_service.verify_csrf(
            token, request.headers.get(CSRF_HEADER)):
        raise CsrfFailedError(_GENERIC_403)
    return principal


def session_token(request: Request):
    """The raw session cookie under the name for this request's mode. A
    Secure-mode request only ever reads `__Host-baihe_session`, so a plain
    `baihe_session` cookie planted by a sibling subdomain is ignored."""
    return request.cookies.get(session_cookie_name(request))


def require_permission(permission: str):
    if permission not in auth_service.PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}; add it to "
                         "services/auth_service.py PERMISSIONS first")

    def dependency(request: Request):
        if not _auth_enabled(request.app):
            request.state.principal = local_owner_principal()
            return request.state.principal
        principal = _authenticate(request)
        if permission not in principal["permissions"]:
            raise ForbiddenError(_GENERIC_403)
        request.state.principal = principal
        ownership_service.note_acting_principal(principal)
        require_path_visible(request, principal)
        return principal

    return _marked(dependency, "permission", permission)


# Path parameters that name an owned item (auth B2). Every route whose path
# has one is ownership-checked here, so a new route is covered by default;
# tests/test_api_ownership.py fails if a route names a drama or series some
# other way without being listed there.
OWNED_PATH_PARAMS = {"drama_id": "drama", "series_id": "series"}


def require_path_visible(request: Request, principal) -> None:
    """404 (never 403, so a private item's existence isn't revealed) when a
    `{drama_id}`/`{series_id}` path parameter names an item the principal
    can't see. Runs after the permission check, so a caller without the
    permission still gets a plain 403. Editing is visibility-based
    (ownership_service.can_edit_drama), so reads and writes share it."""
    for name, kind in OWNED_PATH_PARAMS.items():
        if name in request.path_params:
            ownership_service.require_visible(principal, kind, request.path_params[name])


def authenticated():
    """Any signed-in user, no permission needed; unsafe methods still need
    the CSRF token. Only for routes that act on the caller's own session
    (`POST /api/auth/logout`); the static test keeps it under /api/auth/.
    With auth off, the caller is the local owner as usual."""
    def dependency(request: Request):
        if not _auth_enabled(request.app):
            request.state.principal = local_owner_principal()
            return request.state.principal
        request.state.principal = _authenticate(request)
        return request.state.principal

    return _marked(dependency, "authenticated")


def public_route():
    """Explicit marker for endpoints that need no login (liveness/meta and
    the static frontend shell)."""
    def dependency(request: Request):
        return None
    return _marked(dependency, "public")


LOCAL_HEADER = "X-Baihe-Local"
_PREFLIGHTED_TYPES = frozenset(("application/json",))
_BODY_METHODS = frozenset(("POST", "PUT", "PATCH"))


def _cross_site_safe(request: Request) -> bool:
    """A page on another loopback port passes the Origin check (it ignores
    the port) and can send a "simple" POST with no CORS preflight
    (text/plain or form-urlencoded, e.g. a no-cors fetch). So a POST/PUT/
    PATCH to a local_only route must be JSON (forces a preflight, which
    CORS refuses) or carry `X-Baihe-Local: 1` (a custom header also forces
    a preflight; the React upload helper sends it with every multipart
    POST, since multipart/form-data is itself a simple type). DELETE is
    never a simple method."""
    if request.method.upper() not in _BODY_METHODS:
        return True
    if request.headers.get(LOCAL_HEADER) == "1":
        return True
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media in _PREFLIGHTED_TYPES


def local_only():
    """PC-only route. Both modes: a POST/PUT/PATCH must be JSON or carry
    X-Baihe-Local: 1 (see _cross_site_safe). Off mode: otherwise a
    no-op (today's behaviour; routes that had their own loopback guard
    keep it). On mode: the connection must be a direct loopback one."""
    def dependency(request: Request):
        if _auth_enabled(request.app) and not is_local_request(request):
            raise ForbiddenError(_GENERIC_403)
        if not _cross_site_safe(request):
            raise ForbiddenError(_GENERIC_403)
        request.state.principal = local_owner_principal()
        return request.state.principal

    return _marked(dependency, "local_only")


def _is_local_scope(client_host, headers) -> bool:
    """Reuses the loopback helpers from settings_routes (imported lazily:
    that module imports this one). `headers` is any case-insensitive
    mapping (Starlette Headers)."""
    from api.routers.settings_routes import (_LOOPBACK_HOSTS, _PROXY_HEADERS, _host_name,
                                             _is_loopback_peer)
    if not _is_loopback_peer(client_host):
        return False
    host_header = headers.get("host", "")
    if "@" in host_header or _host_name(host_header) not in _LOOPBACK_HOSTS:
        return False
    if any(h in headers for h in _PROXY_HEADERS):
        return False
    origin = headers.get("origin")
    if origin is not None:
        try:
            hostname = urlsplit(origin).hostname
        except ValueError:
            return False
        if "@" in origin or not hostname:
            return False
        if (f"[{hostname}]" if ":" in hostname else hostname) not in _LOOPBACK_HOSTS:
            return False
    return True


def is_local_request(request: Request) -> bool:
    return _is_local_scope(request.client.host if request.client else None, request.headers)


def require_engines_allowed(request: Request, *engine_names):
    """Raises 403 unless the caller holds `engines.paid` or every named
    engine is in `translate_engines.FREE_ENGINES`. A missing name (None:
    "use the configured default") counts as possibly paid."""
    if _holds(request, "engines.paid"):
        return
    from translate_engines import FREE_ENGINES
    if any(not name or name not in FREE_ENGINES for name in engine_names):
        raise ForbiddenError(_GENERIC_403)


def require_paid_engines(request: Request):
    """Raises 403 unless the caller holds `engines.paid` (for a cloud
    service that isn't a translate engine, e.g. Groq transcription)."""
    if not _holds(request, "engines.paid"):
        raise ForbiddenError(_GENERIC_403)


def holds_paid_engines(request: Request) -> bool:
    """Whether the caller holds `engines.paid` (always, with auth off), for
    a route that skips a paid extra step rather than refusing the request."""
    return _holds(request, "engines.paid")


def _holds(request: Request, permission: str) -> bool:
    principal = getattr(request.state, "principal", None) or {}
    return permission in principal.get("permissions", ())


def session_cookie_secure(request: Request) -> bool:
    """Secure always, except a direct plain-http loopback request when
    BAIHE_API_COOKIE_SECURE=0 (local dev). A request through a proxy is
    never "direct" (proxy headers present), so X-Forwarded-Proto can't
    turn Secure off."""
    settings = getattr(request.app.state, "settings", None)
    if getattr(settings, "cookie_secure", True):
        return True
    return not (request.url.scheme == "http" and is_local_request(request))


def session_cookie_name(request: Request) -> str:
    """`__Host-` prefix (Secure, Path=/, no Domain: a sibling subdomain can't
    plant or overwrite it) whenever the cookie is Secure; the plain name only
    in the loopback-http dev case, since a browser drops a `__Host-` cookie
    that isn't Secure."""
    return COOKIE_NAME if session_cookie_secure(request) else DEV_COOKIE_NAME


def csrf_cookie_name(request: Request) -> str:
    return CSRF_COOKIE_NAME if session_cookie_secure(request) else DEV_CSRF_COOKIE_NAME


def set_session_cookie(response, request: Request, raw_token: str):
    """For the sign-in callback: HttpOnly, SameSite=Lax (Google's redirect
    back is a cross-site top-level GET), Secure per above, Path=/, no Domain.
    The callback always issues a fresh token from
    `auth_service.create_session` (never adopts one the client sent) and
    revokes the client's previous session, so a planted cookie can't be
    fixed onto a victim's login."""
    response.set_cookie(session_cookie_name(request), raw_token, httponly=True, samesite="lax",
                        secure=session_cookie_secure(request), path="/",
                        max_age=auth_service.ABSOLUTE_TIMEOUT_SECONDS)


def set_csrf_cookie(response, request: Request, raw_csrf: str):
    """Double-submit delivery of the session's CSRF token: readable by the
    same-origin React client (not HttpOnly), SameSite=Strict. The server
    verifies the `X-CSRF-Token` header against the session's stored hash
    (`auth_service.verify_csrf`), not against this cookie."""
    response.set_cookie(csrf_cookie_name(request), raw_csrf, httponly=False, samesite="strict",
                        secure=session_cookie_secure(request), path="/",
                        max_age=auth_service.ABSOLUTE_TIMEOUT_SECONDS)


def clear_session_cookie(response, request: Request):
    secure = session_cookie_secure(request)
    response.delete_cookie(session_cookie_name(request), path="/", httponly=True,
                           samesite="lax", secure=secure)
    response.delete_cookie(csrf_cookie_name(request), path="/", httponly=False,
                           samesite="strict", secure=secure)


def client_ip(request: Request) -> str:
    """The address to rate-limit and audit by. A direct connection is its
    peer. Behind Caddy on the same PC the peer is always loopback, so the
    rightmost `X-Forwarded-For` entry (the one Caddy itself appends; a client
    can only add entries to the left of it) is used instead -- but only when
    the peer is loopback, so a remote client can't pick its own bucket."""
    from api.routers.settings_routes import _is_loopback_peer
    peer = request.client.host if request.client else ""
    if not _is_loopback_peer(peer):
        return peer or "unknown"
    entries = [e.strip() for h in request.headers.getlist("x-forwarded-for")
               for e in h.split(",") if e.strip()]
    if not entries:
        return peer
    try:
        return str(ipaddress.ip_address(entries[-1]))
    except ValueError:
        return peer


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
    this a page on another local port (Streamlit, a dev server) could start
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
    "log in"."""

    def __init__(self, app, public_paths_fn, local_only_fn=lambda: []):
        self.app = app
        self._public_paths_fn = public_paths_fn
        self._local_only_fn = local_only_fn
        self._public = None
        self._local_only = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._needs_check(scope):
            return await self.app(scope, receive, send)
        request = Request(scope)
        client = scope.get("client")
        if _is_local_scope(client[0] if client else None, request.headers):
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
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            if not _is_local_scope(client[0] if client else None, Request(scope).headers):
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                return await _json_refusal(403, "forbidden", _GENERIC_403)(scope, receive, send)
        return await self.app(scope, receive, send)


class ActingPrincipalMiddleware:
    """Pure-ASGI middleware, installed only with auth on (auth B2). Binds a
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
