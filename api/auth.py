"""
api/auth.py -- the deny-by-default permission layer (see
docs/remote-access-decision.md; the route -> permission table is docs/route-permissions.md).

Every route in `api/routers/*.py` (and the frontend catch-all in
`api/static_frontend.py`) must declare exactly one of

    dependencies=[require_permission("library.read")]   # a catalogue permission
    dependencies=[public_route()]                        # health-style, no login
    dependencies=[local_only()]                          # PC-only (loopback), see below
    dependencies=[authenticated()]                       # any signed-in user; only for
                                                         # routes on the caller's own
                                                         # sessions and extension
                                                         # device tokens (logout, the
                                                         # /api/auth/sessions routes,
                                                         # list/revoke device tokens,
                                                         # remove my own Ollama key)
    dependencies=[require_device_token()]                # the extension bridge only:
                                                         # a device token, never a
                                                         # session (/api/ext-bridge)

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
  callback (`api/routers/auth_routes.py`) also sets a readable
  `__Host-baihe_csrf` cookie so the React client can send that header
  (double submit); the server still checks the header against the hash
  stored with the session, never against the cookie. 401 = no/invalid
  session, 403 = lacking the permission (code `forbidden`) or a failed CSRF
  check (code `csrf_failed`, so the client can tell it apart from a
  PC-only refusal). Messages are generic. `local_only()` needs no session but requires a
  direct loopback connection (peer, Host, no proxy headers, loopback
  Origin): the owner at the PC. That is a safeguard, not authentication
  (see the key-write note in docs/archive/migration-handoff.md); the real admin
  isolation is the separate admin listener (below).
- Household listener (`BAIHE_API_HOUSEHOLD_PORT`, see
  `api/api_config.py`): the app other devices reach through the reverse
  proxy. Auth on, and nothing on it is ever "the PC": `is_local_request` is
  False and `local_only()` refuses every request, however direct and
  loopback it looks, so a proxy that strips forwarding headers still can't
  reach a PC-only route or a handler's own PC check. A signed-in admin
  there holds the household permissions, the admin view ones (user list,
  audit log) and member-account changes (`admin.users`), never another
  admin write permission (`listener_principal`).
  The PC's own listener (`BAIHE_API_PORT`, auth off, `LoopbackOnlyGate`) is the admin listener.
  `EarlyAuthGate` repeats the cheap part of that check before the request
  body is read, so an anonymous client can't make the server parse a large
  multipart upload before being refused. `HouseholdGate`, outermost there,
  answers only the `BAIHE_PUBLIC_URL` Host and adds the security headers.

Paid engines: a route that starts LLM work declares `jobs.start` (household)
and its handler calls `require_engines_allowed` with the engines the request
names, so a user without `engines.paid` can only use `FREE_ENGINES`.
"""

import ipaddress
from urllib.parse import urlsplit

from fastapi import Depends, Request

from api import remote_uploads
from services import auth_service, device_token_service, ownership_service
from lib.errors import ForbiddenError, UnauthenticatedError

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


def is_auth_enabled(app) -> bool:
    """Fail closed: an app built without settings is treated as auth on."""
    settings = getattr(app.state, "settings", None)
    return bool(getattr(settings, "auth_enabled", True))


def _never_local(app) -> bool:
    """True on the household listener: no request to it counts as the PC.
    Fail closed: an app built without settings is never local either."""
    settings = getattr(app.state, "settings", None)
    return getattr(settings, "listener", None) != "admin"


_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def listener_principal(app, principal):
    """The principal as this listener lets it act. On the household listener
    an admin account keeps its household permissions, the admin view
    permissions (user list, audit log) and `admin.users` (member accounts;
    a change to an admin account is still PC-only in the service), drops
    every other admin write permission, and loses the admin override: it
    still sees every item and job, but changes only what a member could
    (`ownership_service`). Settings, diagnostics and library admin stay
    PC-only, and a remote admin session has no second factor.
    Default-deny: any `admin.*` permission not listed in
    `ADMIN_HOUSEHOLD_PERMISSIONS` is dropped.
    None stays None."""
    if principal is None or not _never_local(app):
        return principal
    return dict(principal, admin_override=False, permissions=[
        p for p in principal["permissions"]
        if not p.startswith("admin.") or p in auth_service.ADMIN_HOUSEHOLD_PERMISSIONS])


def local_owner_principal() -> dict:
    return {"user_id": None, "email": None, "is_admin": True, "is_local_owner": True,
            "admin_override": True,
            "permissions": list(auth_service.PERMISSIONS)}


def principal_user_id(request: Request):
    """The caller's user id; None for the local owner (auth off)."""
    return (getattr(request.state, "principal", None) or {}).get("user_id")


def _marked(fn, kind, permission=None):
    setattr(fn, MARKER_ATTR, (kind, permission))
    return Depends(fn)


def _authenticate(request: Request) -> dict:
    """Session + CSRF check shared by the dependency. Returns the principal
    or raises 401/403. Nothing is cached: permissions are re-read from the
    DB on every request so a grant/revoke applies to the next one."""
    token = session_token(request)
    principal = remote_uploads.away_principal(request, listener_principal(
        request.app, auth_service.resolve_session(token)))
    if principal is None:
        raise UnauthenticatedError(_GENERIC_401)
    if request.method.upper() not in _SAFE_METHODS and not auth_service.verify_csrf(
            token, request.headers.get(CSRF_HEADER)):
        raise CsrfFailedError(_GENERIC_403)
    return principal


def session_token(request: Request):
    """The raw session cookie under the name for this request's mode. A
    Secure-mode request only ever reads `__Host-baihe_session`, so a plain
    `baihe_session` cookie planted by a sibling subdomain is ignored."""
    return request.cookies.get(session_cookie_name(request))


def require_permission(*permissions: str):
    """The caller needs every permission listed. Several make one
    declaration, labelled "a + b" in docs/route-permissions.md, so a route
    that shows line text can need lines.read on top of its own permission."""
    if not permissions:
        raise ValueError("require_permission needs at least one permission")
    for permission in permissions:
        if permission not in auth_service.PERMISSIONS:
            raise ValueError(f"unknown permission {permission!r}; add it to "
                             "services/auth_service.py PERMISSIONS first")
    # The household listener, the Caddy template and the guard tests sort
    # routes by a lone admin.* label; a compound label would slip past them.
    if len(permissions) > 1 and any(p.startswith("admin.") for p in permissions):
        raise ValueError("an admin.* permission is always declared on its own")
    # Stopping a job is the one change an admin may make to other people's
    # items from away (owner decision 2026-10-12), so a cancel route's path
    # item only has to be visible; its service applies may_cancel_job.
    stopping = permissions == ("jobs.cancel",)

    def dependency(request: Request):
        if not is_auth_enabled(request.app):
            request.state.principal = local_owner_principal()
            return request.state.principal
        principal = _authenticate(request)
        if not set(permissions) <= set(principal["permissions"]):
            raise ForbiddenError(_GENERIC_403)
        request.state.principal = principal
        ownership_service.note_acting_principal(principal)
        require_path_visible(request, principal, stopping=stopping)
        return principal

    return _marked(dependency, "permission", " + ".join(permissions))


# Path parameters that name an owned item. Every route whose path
# has one is ownership-checked here, so a new route is covered by default;
# tests/test_api_ownership.py fails if a route names a drama or series some
# other way without being listed there.
OWNED_PATH_PARAMS = {"drama_id": "drama", "series_id": "series"}


def require_path_visible(request: Request, principal, stopping: bool = False) -> None:
    """404 (never 403, so a private item's existence isn't revealed) when a
    `{drama_id}`/`{series_id}` path parameter names an item the principal
    can't see. Runs after the permission check, so a caller without the
    permission still gets a plain 403. Any other method also needs the item
    to be editable (ownership_service.require_editable): the same for
    everyone except an admin on the household listener, who sees every
    item but may change only what a member could (403). `stopping`: a
    `jobs.cancel` route, where seeing the item is enough here."""
    check = (ownership_service.require_visible
             if stopping or request.method.upper() in _SAFE_METHODS
             else ownership_service.require_editable)
    for name, kind in OWNED_PATH_PARAMS.items():
        if name in request.path_params:
            check(principal, kind, request.path_params[name])


def authenticated():
    """Any signed-in user, no permission needed; unsafe methods still need
    the CSRF token. Only for routes that act on the caller's own sessions
    (`POST /api/auth/logout`, `GET /api/auth/sessions`,
    `POST /api/auth/sessions/revoke-others` and
    `POST /api/auth/sessions/{auth_session_id}/revoke`) and extension
    device tokens (`GET /api/auth/device-tokens` and
    `POST /api/auth/device-tokens/{device_token_id}/revoke`), and removing
    the caller's own Ollama key (`DELETE /api/auth/ollama-key`, so a member
    who lost `jobs.start` can still take their key back); the static test
    pins it to those.
    With auth off, the caller is the local owner as usual."""
    def dependency(request: Request):
        if not is_auth_enabled(request.app):
            request.state.principal = local_owner_principal()
            return request.state.principal
        request.state.principal = _authenticate(request)
        return request.state.principal

    return _marked(dependency, "authenticated")


def require_device_token():
    """For the extension bridge routes only: the caller is the user of the
    device token in `Authorization: Bearer <token>`, who must hold
    `extension.send` (device_token_service.authenticate: 401 for any bad
    token, 403 without the permission, 429 after repeated failures). Only
    that header is read, never a cookie or query string, so a session is
    never accepted here, and no other declaration accepts a device token.
    The principal has member rights only; this listener's limits and the
    path guard apply as in require_permission. The same in both auth
    modes: a device token always names a user, never the local owner."""
    def dependency(request: Request):
        principal = listener_principal(request.app, device_token_service.authenticate(
            request.headers.getlist("authorization"), ip=client_ip(request)))
        request.state.principal = principal
        ownership_service.note_acting_principal(principal)
        require_path_visible(request, principal)
        return principal

    return _marked(dependency, "device_token", device_token_service.PERMISSION)


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
    keep it). On mode: the connection must be a direct loopback one.
    Household listener: always refused."""
    def dependency(request: Request):
        if _never_local(request.app):
            raise ForbiddenError(_GENERIC_403)
        if is_auth_enabled(request.app) and not is_local_request(request):
            raise ForbiddenError(_GENERIC_403)
        if not _cross_site_safe(request):
            raise ForbiddenError(_GENERIC_403)
        request.state.principal = local_owner_principal()
        return request.state.principal

    return _marked(dependency, "local_only")


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")
PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded",
                  "x-real-ip", "tailscale-user-login", "cf-connecting-ip", "cf-ray", "via")
# Proxies add headers outside the exact list (x-forwarded-port, cf-ipcountry,
# ...); any of them means the request did not come straight from this PC.
PROXY_HEADER_PREFIXES = ("x-forwarded-", "forwarded", "x-real-ip", "tailscale-", "cf-", "via")


def host_name(netloc: str) -> str:
    """Host part of a Host header / URL netloc, port removed, lower-cased."""
    netloc = (netloc or "").strip().lower()
    if netloc.startswith("["):
        end = netloc.find("]")
        return netloc[:end + 1] if end != -1 else netloc
    return netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc


def is_loopback_peer(host) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def _is_local_scope(client_host, headers) -> bool:
    """`headers` is any case-insensitive mapping (Starlette Headers)."""
    if not is_loopback_peer(client_host):
        return False
    host_header = headers.get("host", "")
    if "@" in host_header or host_name(host_header) not in LOOPBACK_HOSTS:
        return False
    if (any(h in headers for h in PROXY_HEADERS)
            or any(name.lower().startswith(PROXY_HEADER_PREFIXES) for name in headers.keys())):
        return False
    origin = headers.get("origin")
    if origin is not None:
        try:
            hostname = urlsplit(origin).hostname
        except ValueError:
            return False
        if "@" in origin or not hostname:
            return False
        if (f"[{hostname}]" if ":" in hostname else hostname) not in LOOPBACK_HOSTS:
            return False
    return True


def is_local_request(request: Request) -> bool:
    """Whether this request comes from the owner at the PC. Always False on
    the household listener."""
    if _never_local(request.app):
        return False
    return _is_local_scope(request.client.host if request.client else None, request.headers)


def is_at_pc(request: Request) -> bool:
    """The owner at the PC, for a PC-only choice inside a shared route: with
    auth off every request counts, since off mode serves loopback only."""
    return not is_auth_enabled(request.app) or is_local_request(request)


def require_engines_allowed(request: Request, *engine_names, model=None):
    """Raises 403 unless the caller holds `engines.paid` or every named
    engine is in `engine_backends.engine_registry.FREE_ENGINES`. A missing name (None:
    "use the configured default") counts as possibly paid. Pass the request's
    `model` whenever it has one: an Ollama cloud tag is held to
    `engines.paid` too (`require_cloud_model_allowed`); an omitted model
    resolves to the local default, which is never a cloud tag."""
    if not holds(request, "engines.paid"):
        from engine_backends.engine_registry import FREE_ENGINES
        if any(not name or name not in FREE_ENGINES for name in engine_names):
            raise ForbiddenError(_GENERIC_403)
    require_cloud_model_allowed(request, *[(name, model) for name in engine_names])


def require_cloud_model_allowed(request: Request, *engine_models):
    """Raises 403 unless the caller holds `engines.paid`, none of the
    (engine, model) pairs names an Ollama cloud tag, or the caller saved
    their own Ollama key. A hosted tag sends the text off the PC and spends
    the owner's Ollama quota, so it is held to the same permission as a paid
    engine even though Ollama is free; with their own key the quota is the
    member's, so that alone is enough (owner decision 2026-10-14). The key
    lifts this check only: a paid engine still needs `engines.paid`. A key
    saved but no longer readable is refused even with `engines.paid`: that
    member's cloud runs would otherwise land on the owner's account."""
    from engine_backends.local import is_ollama_cloud_model
    if not any(engine in (None, "ollama") and is_ollama_cloud_model(model)
               for engine, model in engine_models):
        return
    from services import member_key_service
    principal = getattr(getattr(request, "state", None), "principal", None) or {}
    user_id = principal.get("user_id")
    if member_key_service.has_unreadable_key(user_id):
        raise ForbiddenError("Your own Ollama key is saved but can't be read any more, so "
                             "cloud models are off for you until you paste it again in "
                             "Settings > Your Ollama key, or remove it there.")
    if holds(request, "engines.paid") or member_key_service.has_usable_key(user_id):
        return
    raise ForbiddenError("Ollama cloud models send your text off this PC and use the "
                         "owner's Ollama quota, so they need paid-engine permission or your "
                         "own Ollama key (Settings > Your Ollama key). "
                         "Pick a local model instead.")


def require_paid_engines(request: Request):
    """Raises 403 unless the caller holds `engines.paid` (for a cloud
    service that isn't a translate engine, e.g. Groq transcription)."""
    if not holds(request, "engines.paid"):
        raise ForbiddenError(_GENERIC_403)


def holds_paid_engines(request: Request) -> bool:
    """Whether the caller holds `engines.paid` (always, with auth off), for
    a route that skips a paid extra step rather than refusing the request."""
    return holds(request, "engines.paid")


def holds(request: Request, permission: str) -> bool:
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
    the peer is loopback, so a remote client can't pick its own bucket. A
    proxy that passes the header through without appending its own entry
    (a plain tunnel) would let the client choose, so only an appending
    proxy may front a listener with sign-in on (docs/remote-access-listeners.md)."""
    peer = request.client.host if request.client else ""
    if not is_loopback_peer(peer):
        return peer or "unknown"
    entries = [e.strip() for h in request.headers.getlist("x-forwarded-for")
               for e in h.split(",") if e.strip()]
    if not entries:
        return peer
    try:
        return str(ipaddress.ip_address(entries[-1]))
    except ValueError:
        return peer


# Defined in api/auth_gates.py; re-exported so `from api.auth import X` keeps working.
from api.auth_gates import (  # noqa: E402,F401
    _declarations_of, iter_route_declarations, public_api_paths, local_only_matchers,
    _json_refusal, LocalOnlyCrossSiteGate, EarlyAuthGate, LoopbackOnlyGate, _DEFAULT_PORTS,
    _HOST_PORT_RE, _HOST_NAME_RE, _normal_host, parse_host_header, public_host, _host_allowed,
    _arrived_over_https, HouseholdGate, ActingPrincipalMiddleware)
