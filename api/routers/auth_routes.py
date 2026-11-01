"""
api/routers/auth_routes.py -- Google sign-in. Thin: the flow,
token checks and user resolution live in `services/oidc_service.py`.

    GET  /api/auth/login?return_to=/path   public_route()   302 to Google
    GET  /api/auth/callback                public_route()   302 back into the app
    POST /api/auth/logout                  authenticated()  ends this session
    GET  /api/auth/me                      public_route()   who am I (the React gate)
    GET  /api/auth/sessions                authenticated()  my signed-in devices
    POST /api/auth/sessions/{auth_session_id}/revoke
                                           authenticated()  sign out one other device
    POST /api/auth/sessions/revoke-others  authenticated()  sign out every other device
    GET  /api/auth/app/start?challenge=    public_route()   phone app sign-in, confirm page
    POST /api/auth/app/confirm             public_route()   303 into the phone app with a code
    POST /api/auth/app/redeem              public_route()   the app's code -> its own session

The three device routes act only on the caller's own sessions: the user id
comes from the session, never the request, and an id that isn't one of the
caller's sessions is a 404 whether or not it exists (auth_service). They
return a coarse device label ("Chrome on Android") and IP prefix only: no
token, hash, user agent or full address. Writes need the CSRF header like
every other one. A revoked device's next request is a 401, and its open
event streams end at once. An admin may list their devices anywhere but
sign them out only at the PC (403 elsewhere). Signing out every other
device also rotates this device's session (new session and CSRF cookies).

With `BAIHE_API_AUTH=off` login, callback and logout answer 404 and `/me`
reports the local owner (signed in, every permission), so nothing changes
for the owner at the PC.

Cookies (names per `api.auth.session_cookie_secure`: `__Host-` in Secure
mode, plain names only for plain-http loopback dev):
- `__Host-baihe_oidc`: the pending login's random transaction id, HttpOnly,
  SameSite=Lax (Google's redirect back is a cross-site top-level GET), 10
  minutes, cleared by the callback whatever the outcome.
- `__Host-baihe_session` / `__Host-baihe_csrf`: see `api.auth`.

Phone app hand-off (the Android shell can't sign in with Google itself: its
web view isn't allowed to, and Chrome keeps its own cookies). The app keeps a
random verifier and opens Chrome at /app/start with its SHA-256. A signed-in
browser gets a confirm page; one that isn't goes through /login first. The
confirm form (session cookie plus a ticket tied to that session, so another
site can't submit it) mints a 60-second single-use code and redirects to
`baihe-shell://signed-in?code=`. The app posts code and verifier to /redeem
from its web view, which gets a fresh session of its own. A redeem posted by
another site is refused (Sec-Fetch-Site / Origin), so a page can't sign a
browser in to someone else's account.

A failed sign-in redirects to `/?login_error=<code>` with one of
`oidc_service.LOGIN_ERROR_CODES` only; the reason goes to the audit log.

`/me` contract (the React session store depends on it; keep the shape):
    {auth_enabled, signed_in, sign_in_configured, zone,
     user: {id, email, display_name, is_admin, is_local_owner} | null,
     permissions: [...], own_ollama_key}
`zone` is "pc" for a direct loopback request (what `local_only()` accepts)
and "internet" for anything else, until "lan"
is added. It never carries the client id, secret, public URL or any token.
"""

import html
import threading
import time
from typing import List, Optional
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Path, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from api.auth import (is_auth_enabled, authenticated, clear_session_cookie, client_ip,
                      is_local_request, listener_principal,
                      local_owner_principal, public_route,
                      session_cookie_secure, session_token, set_csrf_cookie,
                      set_session_cookie)
from api.no_store import NO_STORE, no_store_json
from api.remote_uploads import away_principal
from services import auth_service, member_key_service, oidc_service
from lib.errors import DependencyUnavailableError, NotFoundError, RateLimitedError

router = APIRouter(prefix="/api/auth", tags=["auth"])

OIDC_COOKIE_NAME = "__Host-baihe_oidc"
DEV_OIDC_COOKIE_NAME = "baihe_oidc"
_NOT_SET_UP = "Sign-in isn't set up on the PC."
_state_lock = threading.Lock()
APP_LINK = "baihe-shell://signed-in"
# The confirm form's redirect goes to the app link, and Chrome applies
# form-action to where a form submission ends up, so the household CSP's
# form-action 'self' would block it.
_APP_PAGE_CSP = ("default-src 'none'; style-src 'unsafe-inline'; "
                 "form-action 'self' baihe-shell:; base-uri 'none'; frame-ancestors 'none'")
_APP_PAGE_STYLE = ("font-family:sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem")


class MeUser(BaseModel):
    id: Optional[int] = None
    email: Optional[str] = None
    display_name: str = ""
    is_admin: bool
    is_local_owner: bool


class MeResponse(BaseModel):
    auth_enabled: bool
    signed_in: bool
    sign_in_configured: bool
    zone: str
    user: Optional[MeUser] = None
    permissions: List[str]
    own_ollama_key: bool = False


class LogoutResponse(BaseModel):
    signed_in: bool


class DeviceSession(BaseModel):
    id: int
    device: str                 # "Chrome on Android"; never the user agent
    created_at: float           # epoch seconds
    last_seen_at: float
    expires_at: float           # signed out by then at the latest
    ip_prefix: str              # IPv4 /24 ("203.0.113") or IPv6 /48; "" if unknown
    current: bool               # the device making this request


class DeviceSessionList(BaseModel):
    sessions: List[DeviceSession]
    idle_timeout_days: int
    absolute_timeout_days: int


class DeviceSessionsRevoked(BaseModel):
    revoked: int


_MAX_ID = 2 ** 62   # past SQLite's integer range the lookup would raise, not 404


def _sign_in(request: Request) -> "oidc_service.SignIn":
    """The app's one SignIn. Tests may set `app.state.oidc_provider` (a fake
    Google), `app.state.auth_clock` (monotonic) and `app.state.auth_wall_clock`
    before the first request."""
    state = request.app.state
    with _state_lock:
        sign_in = getattr(state, "sign_in", None)
        if sign_in is None:
            sign_in = state.sign_in = oidc_service.SignIn(
                provider=getattr(state, "oidc_provider", None),
                clock=getattr(state, "auth_clock", None) or time.monotonic,
                wall=getattr(state, "auth_wall_clock", None) or time.time)
    return sign_in


def _require_auth_on(request: Request):
    if not is_auth_enabled(request.app):
        raise NotFoundError("Not found.")


def _config(request: Request) -> dict:
    config = oidc_service.config_from_settings(request.app.state.settings)
    if config is None:
        raise DependencyUnavailableError(_NOT_SET_UP)
    return config


def _oidc_cookie_name(request: Request) -> str:
    return OIDC_COOKIE_NAME if session_cookie_secure(request) else DEV_OIDC_COOKIE_NAME


def _rate_limited_page() -> HTMLResponse:
    """These two routes are browser navigations: the JSON error body would
    strand a phone on a page with no way back. Stays a 429 so the proxy and
    retry logic still see the limit."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        "<title>Sign-in paused</title><body style='font-family:sans-serif;max-width:32rem;"
        "margin:3rem auto;padding:0 1rem'><h1>Sign-in paused</h1>"
        "<p>Too many sign-in attempts. Wait about 10 minutes and try again.</p>"
        "<p><a href='/'>Back to Baihe</a></p></body>",
        status_code=429, headers={**NO_STORE, "Retry-After": "600"})


def _redirect(request: Request, location: str) -> RedirectResponse:
    resp = RedirectResponse(location, status_code=302, headers=dict(NO_STORE))
    resp.delete_cookie(_oidc_cookie_name(request), path="/", httponly=True, samesite="lax",
                       secure=session_cookie_secure(request))
    return resp


@router.get("/login", dependencies=[public_route()], summary="Start Google sign-in")
def login(request: Request, return_to: str = "/"):
    _require_auth_on(request)
    sign_in = _sign_in(request)
    ip = client_ip(request)
    try:
        sign_in.check_rate("login", ip)
    except RateLimitedError:
        return _rate_limited_page()
    txn_id, url = sign_in.begin(_config(request), return_to, client_ip=ip)
    resp = RedirectResponse(url, status_code=302, headers=dict(NO_STORE))
    resp.set_cookie(_oidc_cookie_name(request), txn_id, httponly=True, samesite="lax",
                    secure=session_cookie_secure(request), path="/",
                    max_age=oidc_service.TRANSACTION_TTL_SECONDS)
    return resp


@router.get("/callback", dependencies=[public_route()], summary="Google sign-in callback")
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    _require_auth_on(request)
    sign_in = _sign_in(request)
    ip = client_ip(request)
    try:
        sign_in.check_rate("callback", ip)
    except RateLimitedError:
        return _rate_limited_page()
    config = _config(request)
    try:
        result = sign_in.complete(
            config, request.cookies.get(_oidc_cookie_name(request)), state=state, code=code,
            error=error, presented_session=session_token(request),
            user_agent=request.headers.get("user-agent", ""), client_ip=ip)
    except oidc_service.LoginError as exc:
        return _redirect(request, f"/?login_error={exc.code}")
    resp = _redirect(request, result["return_to"])
    set_session_cookie(resp, request, result["session_token"])
    set_csrf_cookie(resp, request, result["csrf_token"])
    return resp


@router.post("/logout", dependencies=[authenticated()], response_model=LogoutResponse,
             summary="Sign out of this device")
def logout(request: Request):
    _require_auth_on(request)
    oidc_service.sign_out(request.state.principal)
    resp = JSONResponse(LogoutResponse(signed_in=False).model_dump(), headers=dict(NO_STORE))
    clear_session_cookie(resp, request)
    return resp


@router.get("/me", dependencies=[public_route()], response_model=MeResponse,
            summary="Who is signed in, and what they may do")
def me(request: Request):
    settings = request.app.state.settings
    body = {"auth_enabled": is_auth_enabled(request.app),
            "sign_in_configured": bool(getattr(settings, "sign_in_configured", False)),
            "zone": "pc" if is_local_request(request) else "internet"}
    if not body["auth_enabled"]:
        owner = local_owner_principal()
        body.update(signed_in=True, permissions=sorted(owner["permissions"]),
                    user={"id": None, "email": None, "display_name": "PC owner",
                          "is_admin": True, "is_local_owner": True})
    else:
        principal = away_principal(request, listener_principal(
            request.app, auth_service.resolve_session(session_token(request))))
        if principal is None:
            body.update(signed_in=False, user=None, permissions=[])
        else:
            display = auth_service.get_user(principal["user_id"])["display_name"]
            body.update(signed_in=True, permissions=sorted(principal["permissions"]),
                        own_ollama_key=member_key_service.has_usable_key(principal["user_id"]),
                        user={"id": principal["user_id"], "email": principal["email"],
                              "display_name": display, "is_admin": principal["is_admin"],
                              "is_local_owner": False})
    return JSONResponse(MeResponse(**body).model_dump(), headers=dict(NO_STORE))


def _own(request: Request) -> dict:
    """The caller's session principal; only ever from their own cookie."""
    _require_auth_on(request)
    return request.state.principal


@router.get("/sessions", dependencies=[authenticated()], response_model=DeviceSessionList,
            summary="My signed-in devices")
def list_sessions(request: Request):
    principal = _own(request)
    rows = auth_service.list_own_sessions(principal["user_id"], principal["session_id"])
    return no_store_json(DeviceSessionList(
        sessions=rows, idle_timeout_days=auth_service.IDLE_TIMEOUT_SECONDS // 86400,
        absolute_timeout_days=auth_service.ABSOLUTE_TIMEOUT_SECONDS // 86400))


@router.post("/sessions/revoke-others", dependencies=[authenticated()],
             response_model=DeviceSessionsRevoked, summary="Sign out every other device")
def revoke_other_sessions(request: Request):
    principal = _own(request)
    result = auth_service.revoke_other_sessions(
        principal["user_id"], principal["session_id"], ip=client_ip(request),
        is_admin=principal["is_admin"], at_pc=is_local_request(request))
    resp = no_store_json(DeviceSessionsRevoked(revoked=result["revoked"]))
    # This device's session was rotated: a copy of its old cookie is dead too.
    set_session_cookie(resp, request, result["session_token"])
    set_csrf_cookie(resp, request, result["csrf_token"])
    return resp


@router.post("/sessions/{auth_session_id}/revoke", dependencies=[authenticated()],
             response_model=DeviceSessionsRevoked, summary="Sign out one of my other devices")
def revoke_session(request: Request, auth_session_id: int = Path(..., ge=1, le=_MAX_ID)):
    principal = _own(request)
    return no_store_json(DeviceSessionsRevoked(**auth_service.revoke_own_session(
        principal["user_id"], auth_session_id, principal["session_id"],
        ip=client_ip(request), is_admin=principal["is_admin"],
        at_pc=is_local_request(request))))


def _app_page(body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
        f"<title>Baihe Studio</title><body style='{_APP_PAGE_STYLE}'>{body}</body>",
        status_code=status_code,
        headers={**NO_STORE, "Content-Security-Policy": _APP_PAGE_CSP, "X-Frame-Options": "DENY",
                 "Referrer-Policy": "no-referrer"})


def _app_link_expired() -> HTMLResponse:
    return _app_page("<p>This sign-in link has expired. Start again from the app.</p>"
                     "<p><a href='/'>Back to Baihe</a></p>", status_code=400)


def _browser_principal(request: Request):
    return away_principal(request, listener_principal(
        request.app, auth_service.resolve_session(session_token(request))))


async def _form_fields(request: Request, *names: str) -> dict:
    form = await request.form(max_files=0, max_fields=len(names) + 2, max_part_size=4096)
    return {n: v if isinstance(v := form.get(n), str) else "" for n in names}


def _posted_by_this_site(request: Request) -> bool:
    """False for a POST another site's page made. The app's own web view
    post carries Origin "null" and Sec-Fetch-Site none. A browser without
    Fetch Metadata also sends Origin "null" from a sandboxed frame, so "null"
    is accepted only alongside Sec-Fetch-Site none."""
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("none", "same-origin"):
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return True
    if origin == "null":
        return fetch_site == "none"
    try:
        parts = urlsplit(origin)
    except ValueError:
        return False
    return bool(parts.netloc) and parts.netloc.lower() == request.headers.get("host", "").lower()


@router.get("/app/start", dependencies=[public_route()], summary="Phone app sign-in: confirm",
            response_class=HTMLResponse)
def app_start(request: Request, challenge: str = ""):
    _require_auth_on(request)
    if not oidc_service.valid_app_challenge(challenge):
        return _app_link_expired()
    principal = _browser_principal(request)
    if principal is None:
        back = quote(f"/api/auth/app/start?challenge={challenge}", safe="")
        return RedirectResponse(f"/api/auth/login?return_to={back}", status_code=302,
                                headers=dict(NO_STORE))
    ticket = _sign_in(request).app_confirm_ticket(principal["session_id"], challenge)
    return _app_page(
        "<h1>Sign in the Baihe Studio app?</h1>"
        "<p>This signs in the Baihe Studio app on this phone as "
        f"{html.escape(principal['email'] or '')}.</p>"
        "<p>Only continue if you just tapped Sign in in the Baihe Studio app. "
        "Otherwise close this page.</p>"
        "<form method=post action='/api/auth/app/confirm'>"
        f"<input type=hidden name=challenge value='{html.escape(challenge, quote=True)}'>"
        f"<input type=hidden name=ticket value='{ticket}'>"
        "<p><button type=submit>Sign in the app</button> <a href='/'>Cancel</a></p></form>")


@router.post("/app/confirm", dependencies=[public_route()], summary="Phone app sign-in: hand off",
             response_class=HTMLResponse)
async def app_confirm(request: Request):
    _require_auth_on(request)
    sign_in = _sign_in(request)
    try:
        sign_in.check_rate("app", client_ip(request))
    except RateLimitedError:
        return _rate_limited_page()
    fields = await _form_fields(request, "challenge", "ticket")
    principal = _browser_principal(request)
    if (principal is None or not oidc_service.valid_app_challenge(fields["challenge"])
            or not sign_in.app_ticket_ok(principal["session_id"], fields["challenge"],
                                         fields["ticket"])):
        return _app_link_expired()
    code = sign_in.mint_app_code(principal["user_id"], fields["challenge"])
    return RedirectResponse(f"{APP_LINK}?code={code}", status_code=303,
                            headers={**NO_STORE, "Content-Security-Policy": _APP_PAGE_CSP})


@router.post("/app/redeem", dependencies=[public_route()], summary="Phone app sign-in: finish")
async def app_redeem(request: Request):
    _require_auth_on(request)
    sign_in = _sign_in(request)
    ip = client_ip(request)
    try:
        sign_in.check_rate("app", ip)
    except RateLimitedError:
        return _rate_limited_page()
    if not _posted_by_this_site(request):
        return _redirect(request, "/?login_error=expired")
    fields = await _form_fields(request, "code", "verifier")
    try:
        result = sign_in.redeem_app_code(
            fields["code"], fields["verifier"], presented_session=session_token(request),
            user_agent=request.headers.get("user-agent", ""), client_ip=ip)
    except oidc_service.LoginError as exc:
        return _redirect(request, f"/?login_error={exc.code}")
    resp = _redirect(request, "/")
    set_session_cookie(resp, request, result["session_token"])
    set_csrf_cookie(resp, request, result["csrf_token"])
    return resp
