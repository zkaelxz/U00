"""
api/routers/auth_routes.py -- Google sign-in (step 134). Thin: the flow,
token checks and user resolution live in `services/oidc_service.py`.

    GET  /api/auth/login?return_to=/path   public_route()   302 to Google
    GET  /api/auth/callback                public_route()   302 back into the app
    POST /api/auth/logout                  authenticated()  ends this session
    GET  /api/auth/me                      public_route()   who am I (the React gate)

With `BAIHE_API_AUTH=off` login, callback and logout answer 404 and `/me`
reports the local owner (signed in, every permission), so nothing changes
for the owner at the PC.

Cookies (names per `api.auth.session_cookie_secure`: `__Host-` in Secure
mode, plain names only for plain-http loopback dev):
- `__Host-baihe_oidc`: the pending login's random transaction id, HttpOnly,
  SameSite=Lax (Google's redirect back is a cross-site top-level GET), 10
  minutes, cleared by the callback whatever the outcome.
- `__Host-baihe_session` / `__Host-baihe_csrf`: see `api.auth`.

A failed sign-in redirects to `/?login_error=<code>` with one of
`oidc_service.LOGIN_ERROR_CODES` only; the reason goes to the audit log.

`/me` contract (the React session store depends on it; keep the shape):
    {auth_enabled, signed_in, sign_in_configured, zone,
     user: {id, email, display_name, is_admin, is_local_owner} | null,
     permissions: [...]}
`zone` is "pc" for a direct loopback request (what `local_only()` accepts)
and "internet" for anything else, until the network-zones slice adds
"lan". It never carries the client id, secret, public URL or any token.
"""

import threading
import time
from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from api.auth import (_auth_enabled, authenticated, clear_session_cookie, client_ip,
                      is_local_request, local_owner_principal, public_route,
                      session_cookie_secure, session_token, set_csrf_cookie,
                      set_session_cookie)
from services import auth_service, oidc_service
from services.service_errors import DependencyUnavailableError, NotFoundError

router = APIRouter(prefix="/api/auth", tags=["auth"])

OIDC_COOKIE_NAME = "__Host-baihe_oidc"
DEV_OIDC_COOKIE_NAME = "baihe_oidc"
_NOT_SET_UP = "Sign-in isn't set up on the PC."
_NO_STORE = {"Cache-Control": "no-store"}
_state_lock = threading.Lock()


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


class LogoutResponse(BaseModel):
    signed_in: bool


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
    if not _auth_enabled(request.app):
        raise NotFoundError("Not found.")


def _config(request: Request) -> dict:
    config = oidc_service.config_from_settings(request.app.state.settings)
    if config is None:
        raise DependencyUnavailableError(_NOT_SET_UP)
    return config


def _oidc_cookie_name(request: Request) -> str:
    return OIDC_COOKIE_NAME if session_cookie_secure(request) else DEV_OIDC_COOKIE_NAME


def _redirect(request: Request, location: str) -> RedirectResponse:
    resp = RedirectResponse(location, status_code=302, headers=dict(_NO_STORE))
    resp.delete_cookie(_oidc_cookie_name(request), path="/", httponly=True, samesite="lax",
                       secure=session_cookie_secure(request))
    return resp


@router.get("/login", dependencies=[public_route()], summary="Start Google sign-in")
def login(request: Request, return_to: str = "/"):
    _require_auth_on(request)
    sign_in = _sign_in(request)
    sign_in.check_rate("login", client_ip(request))
    txn_id, url = sign_in.begin(_config(request), return_to)
    resp = RedirectResponse(url, status_code=302, headers=dict(_NO_STORE))
    resp.set_cookie(_oidc_cookie_name(request), txn_id, httponly=True, samesite="lax",
                    secure=session_cookie_secure(request), path="/",
                    max_age=oidc_service.TRANSACTION_TTL_SECONDS)
    return resp


@router.get("/callback", dependencies=[public_route()], summary="Google sign-in callback")
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    _require_auth_on(request)
    sign_in = _sign_in(request)
    ip = client_ip(request)
    sign_in.check_rate("callback", ip)
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
    resp = JSONResponse(LogoutResponse(signed_in=False).model_dump(), headers=dict(_NO_STORE))
    clear_session_cookie(resp, request)
    return resp


@router.get("/me", dependencies=[public_route()], response_model=MeResponse,
            summary="Who is signed in, and what they may do")
def me(request: Request):
    settings = request.app.state.settings
    body = {"auth_enabled": _auth_enabled(request.app),
            "sign_in_configured": bool(getattr(settings, "sign_in_configured", False)),
            "zone": "pc" if is_local_request(request) else "internet"}
    if not body["auth_enabled"]:
        owner = local_owner_principal()
        body.update(signed_in=True, permissions=sorted(owner["permissions"]),
                    user={"id": None, "email": None, "display_name": "PC owner",
                          "is_admin": True, "is_local_owner": True})
    else:
        principal = auth_service.resolve_session(session_token(request))
        if principal is None:
            body.update(signed_in=False, user=None, permissions=[])
        else:
            display = auth_service.get_user(principal["user_id"])["display_name"]
            body.update(signed_in=True, permissions=sorted(principal["permissions"]),
                        user={"id": principal["user_id"], "email": principal["email"],
                              "display_name": display, "is_admin": principal["is_admin"],
                              "is_local_owner": False})
    return JSONResponse(MeResponse(**body).model_dump(), headers=dict(_NO_STORE))
