"""
api/auth.py -- the deny-by-default permission dependency (Step 133; see
docs/remote-access-decision.md and the route table there).

Every route in `api/routers/*.py` must declare exactly one of

    dependencies=[require_permission("library.read")]   # a catalogue permission
    dependencies=[public_route()]                        # health-style, no login
    dependencies=[local_only()]                          # PC-only (loopback), see below

`tests/test_api_permissions.py` enumerates `app.routes` and fails if any
route lacks exactly one, so a new route can't ship undeclared.

Modes (`BAIHE_API_AUTH`, see `api/api_config.py`):
- `off` (default): every request is the local owner with every
  permission, no session, no CSRF -- today's behaviour.
- `on`: `require_permission` needs a valid session cookie (`baihe_session`),
  the permission, and, for POST/PUT/PATCH/DELETE, a matching
  `X-CSRF-Token` header. 401 = no/invalid session, 403 = lacking the
  permission or CSRF. Messages are generic and reveal nothing.
  `local_only()` needs no session but requires a loopback connection
  (peer, Host, no proxy headers, loopback Origin): the PC owner at the
  PC. It is a safeguard, not authentication (see the key-write note in
  docs/migration-handoff.md); the real admin isolation is the separate
  admin listener (D5), not built yet.
"""

from fastapi import Depends, Request

from services import auth_service
from services.service_errors import ForbiddenError, UnauthenticatedError

COOKIE_NAME = "baihe_session"
CSRF_HEADER = "X-CSRF-Token"
_UNSAFE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
MARKER_ATTR = "_baihe_auth"   # (kind, permission-or-None); read by the static test


def _auth_enabled(request: Request) -> bool:
    """Fail closed: if the app was built without settings, auth is on."""
    settings = getattr(request.app.state, "settings", None)
    return bool(getattr(settings, "auth_enabled", True))


def local_owner_principal() -> dict:
    return {"user_id": None, "email": None, "is_admin": True, "is_local_owner": True,
            "permissions": list(auth_service.PERMISSIONS)}


def _marked(fn, kind, permission=None):
    setattr(fn, MARKER_ATTR, (kind, permission))
    return Depends(fn)


def require_permission(permission: str):
    if permission not in auth_service.PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r}; add it to "
                         "services/auth_service.py PERMISSIONS first")

    def dependency(request: Request):
        if not _auth_enabled(request):
            request.state.principal = local_owner_principal()
            return request.state.principal
        token = request.cookies.get(COOKIE_NAME)
        principal = auth_service.resolve_session(token)
        if principal is None:
            raise UnauthenticatedError("Authentication required.")
        if request.method in _UNSAFE_METHODS and not auth_service.verify_csrf(
                token, request.headers.get(CSRF_HEADER)):
            raise ForbiddenError("Not allowed.")
        if permission not in principal["permissions"]:
            raise ForbiddenError("Not allowed.")
        request.state.principal = principal
        return principal

    return _marked(dependency, "permission", permission)


def public_route():
    """Explicit marker for endpoints that need no login (liveness/meta)."""
    def dependency(request: Request):
        return None
    return _marked(dependency, "public")


def local_only():
    """PC-only route. Off mode: no-op (today's behaviour). On mode: the
    connection must be loopback, direct (no proxy headers), same-origin-ish."""
    def dependency(request: Request):
        if not _auth_enabled(request):
            request.state.principal = local_owner_principal()
            return request.state.principal
        if not is_local_request(request):
            raise ForbiddenError("Not allowed.")
        request.state.principal = local_owner_principal()
        return request.state.principal

    return _marked(dependency, "local_only")


def is_local_request(request: Request) -> bool:
    """Reuses the loopback helpers from settings_routes (imported lazily:
    that module imports this one)."""
    from urllib.parse import urlsplit
    from api.routers.settings_routes import (_LOOPBACK_HOSTS, _PROXY_HEADERS, _host_name,
                                             _is_loopback_peer)
    peer = request.client.host if request.client else None
    if not _is_loopback_peer(peer):
        return False
    host_header = request.headers.get("host", "")
    if "@" in host_header or _host_name(host_header) not in _LOOPBACK_HOSTS:
        return False
    if any(h in request.headers for h in _PROXY_HEADERS):
        return False
    origin = request.headers.get("origin")
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


def session_cookie_secure(request: Request) -> bool:
    """Secure always, except plain-http loopback when BAIHE_API_COOKIE_SECURE=0."""
    settings = getattr(request.app.state, "settings", None)
    if getattr(settings, "cookie_secure", True):
        return True
    return not (request.url.scheme == "http" and is_local_request(request))


def set_session_cookie(response, request: Request, raw_token: str):
    """For step 134's login route: HttpOnly, SameSite=Lax, Secure per above."""
    response.set_cookie(COOKIE_NAME, raw_token, httponly=True, samesite="lax",
                        secure=session_cookie_secure(request), path="/",
                        max_age=auth_service.ABSOLUTE_TIMEOUT_SECONDS)


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/")
