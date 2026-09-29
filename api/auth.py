"""
api/auth.py -- the deny-by-default permission layer (Step 133; see
docs/remote-access-decision.md, which holds the route -> permission table).

Every route in `api/routers/*.py` (and the frontend catch-all in
`api/static_frontend.py`) must declare exactly one of

    dependencies=[require_permission("library.read")]   # a catalogue permission
    dependencies=[public_route()]                        # health-style, no login
    dependencies=[local_only()]                          # PC-only (loopback), see below

`tests/test_api_permissions.py` walks every route (`iter_route_declarations`)
and fails if one lacks exactly one, so a new route can't ship undeclared.

Modes (`BAIHE_API_AUTH`, see `api/api_config.py`):
- `off` (default): every request is the local owner with every permission,
  no session, no CSRF -- today's behaviour. The server refuses to bind a
  non-loopback address in this mode (`api_config.check_bind_safety`).
- `on`: `require_permission` needs a valid session cookie (`baihe_session`),
  the permission, and, for POST/PUT/PATCH/DELETE, a matching `X-CSRF-Token`
  header. 401 = no/invalid session, 403 = lacking the permission or CSRF.
  Messages are generic. `local_only()` needs no session but requires a
  direct loopback connection (peer, Host, no proxy headers, loopback
  Origin): the owner at the PC. That is a safeguard, not authentication
  (see the key-write note in docs/migration-handoff.md); the real admin
  isolation is the separate admin listener (D5), not built yet.
  `EarlyAuthGate` repeats the cheap part of that check before the request
  body is read, so an anonymous client can't make the server parse a large
  multipart upload before being refused.

Paid engines: a route that starts LLM work declares `jobs.start` (household)
and its handler calls `require_engines_allowed` with the engines the request
names, so a user without `engines.paid` can only use `FREE_ENGINES`.
"""

from urllib.parse import urlsplit

from fastapi import Depends, Request

from services import auth_service
from services.service_errors import ForbiddenError, UnauthenticatedError

COOKIE_NAME = "baihe_session"
CSRF_HEADER = "X-CSRF-Token"
_UNSAFE_METHODS = frozenset(("POST", "PUT", "PATCH", "DELETE"))
MARKER_ATTR = "_baihe_auth"   # (kind, permission-or-None); read by the static test
_GENERIC_401 = "Authentication required."
_GENERIC_403 = "Not allowed."


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
    token = request.cookies.get(COOKIE_NAME)
    principal = auth_service.resolve_session(token)
    if principal is None:
        raise UnauthenticatedError(_GENERIC_401)
    if request.method.upper() not in ("GET", "HEAD", "OPTIONS") and not auth_service.verify_csrf(
            token, request.headers.get(CSRF_HEADER)):
        raise ForbiddenError(_GENERIC_403)
    return principal


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
        return principal

    return _marked(dependency, "permission", permission)


def public_route():
    """Explicit marker for endpoints that need no login (liveness/meta and
    the static frontend shell)."""
    def dependency(request: Request):
        return None
    return _marked(dependency, "public")


def local_only():
    """PC-only route. Off mode: no-op (today's behaviour; routes that had
    their own loopback guard keep it). On mode: the connection must be a
    direct loopback one."""
    def dependency(request: Request):
        if _auth_enabled(request.app) and not is_local_request(request):
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
    principal = getattr(request.state, "principal", None) or {}
    if "engines.paid" in principal.get("permissions", ()):
        return
    from translate_engines import FREE_ENGINES
    if any(not name or name not in FREE_ENGINES for name in engine_names):
        raise ForbiddenError(_GENERIC_403)


def session_cookie_secure(request: Request) -> bool:
    """Secure always, except a direct plain-http loopback request when
    BAIHE_API_COOKIE_SECURE=0 (local dev). A request through a proxy is
    never "direct" (proxy headers present), so X-Forwarded-Proto can't
    turn Secure off."""
    settings = getattr(request.app.state, "settings", None)
    if getattr(settings, "cookie_secure", True):
        return True
    return not (request.url.scheme == "http" and is_local_request(request))


def set_session_cookie(response, request: Request, raw_token: str):
    """For step 134's login route: HttpOnly, SameSite=Lax, Secure per above.
    The login route must always issue a fresh token from
    `auth_service.create_session` (never adopt one the client sent) and
    revoke the client's previous session, so a planted cookie can't be
    fixed onto a victim's login."""
    response.set_cookie(COOKIE_NAME, raw_token, httponly=True, samesite="lax",
                        secure=session_cookie_secure(request), path="/",
                        max_age=auth_service.ABSOLUTE_TIMEOUT_SECONDS)


def clear_session_cookie(response, request: Request):
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                           secure=session_cookie_secure(request))


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


class EarlyAuthGate:
    """Pure-ASGI middleware, installed only with auth on. For any /api path
    that isn't a public route it refuses, before the body is read:
    - a request with no valid session that isn't a direct loopback request
      (401), and
    - an unsafe-method request that has a session but no matching CSRF
      token and isn't direct loopback (403).
    The per-route dependency stays the authority (permission checks,
    local_only); this only moves the cheapest refusals ahead of body
    parsing (large multipart uploads) and of FastAPI's own 422/405 replies,
    so an anonymous client learns nothing beyond "log in"."""

    def __init__(self, app, public_paths_fn):
        self.app = app
        self._public_paths_fn = public_paths_fn
        self._public = None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not self._needs_check(scope):
            return await self.app(scope, receive, send)
        request = Request(scope)
        client = scope.get("client")
        if _is_local_scope(client[0] if client else None, request.headers):
            return await self.app(scope, receive, send)
        try:
            _authenticate(request)
        except (UnauthenticatedError, ForbiddenError) as exc:
            from api.error_handlers import error_body
            from fastapi.responses import JSONResponse
            status = 401 if isinstance(exc, UnauthenticatedError) else 403
            response = JSONResponse(status_code=status,
                                    content=error_body(exc.code, exc.message))
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)

    def _needs_check(self, scope) -> bool:
        path = scope.get("path", "")
        # Anything Starlette could route to an /api handler. The frontend
        # catch-all only serves static files for other paths.
        if not (path == "/api" or path.startswith("/api/")):
            return False
        if self._public is None:
            self._public = self._public_paths_fn()
        return path not in self._public
