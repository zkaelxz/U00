"""
api/routers/device_token_routes.py -- browser-extension device tokens.
Thin: the rules live in `services/device_token_service.py`.

    GET  /api/auth/device-tokens                     authenticated()   my extension devices
    POST /api/auth/device-tokens                     extension.send    add one (token shown once)
    POST /api/auth/device-tokens/{device_token_id}/revoke
                                                     authenticated()   revoke one of mine
    GET  /api/extension/devices                      local_only()      everyone's
    POST /api/extension/devices/{device_token_id}/revoke
                                                     local_only()      revoke anyone's

The own routes act only on the caller's tokens: the user id comes from the
session, never the request, and an id that isn't one of the caller's live
tokens is a 404 whether or not it exists. Listing and revoking need no
permission, so someone who lost `extension.send` can still see and revoke
what they made. With sign-in off they answer 404 (the owner at the PC has
no account). Everyone's tokens are for the owner at the PC only
(`local_only()`, like the rest of `/api/extension/*`, which Caddy also
refuses): the household listener never serves them. Responses
never carry a hash; the create response is the only one with the token,
and like every response here it is `Cache-Control: no-store`.
"""

from fastapi import APIRouter, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.auth import (authenticated, client_ip, is_auth_enabled, is_local_request, local_only,
                      require_permission)
from api.schemas import (AdminDeviceTokenList, DeviceTokenCreated, DeviceTokenCreateRequest,
                         DeviceTokenList, DeviceTokenRevoked, ErrorResponse)
from services import device_token_service as svc
from services.service_errors import NotFoundError

router = APIRouter(tags=["auth"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_MAX_ID = 2 ** 62   # past SQLite's integer range the lookup would raise, not 404
_TokenId = Path(..., ge=1, le=_MAX_ID)


def _no_store(body: BaseModel) -> JSONResponse:
    return JSONResponse(body.model_dump(), headers=dict(_NO_STORE))


def _own(request: Request) -> dict:
    if not is_auth_enabled(request.app):
        raise NotFoundError("Not found.")
    return request.state.principal


@router.get("/api/auth/device-tokens", dependencies=[authenticated()],
            response_model=DeviceTokenList, summary="My browser-extension devices")
def list_own(request: Request):
    principal = _own(request)
    return _no_store(DeviceTokenList(tokens=svc.list_own(principal["user_id"]),
                                     max_active=svc.MAX_LIVE_TOKENS))


@router.post("/api/auth/device-tokens", dependencies=[require_permission("extension.send")],
             response_model=DeviceTokenCreated,
             summary="Add a browser-extension device (the token is shown once)",
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        429: {"model": ErrorResponse}})
def create(body: DeviceTokenCreateRequest, request: Request):
    principal = _own(request)
    return _no_store(DeviceTokenCreated(**svc.create_token(
        principal, body.label, body.expires_in_days, ip=client_ip(request),
        at_pc=is_local_request(request))))


@router.post("/api/auth/device-tokens/{device_token_id}/revoke",
             dependencies=[authenticated()], response_model=DeviceTokenRevoked,
             summary="Revoke one of my browser-extension devices")
def revoke_own(request: Request, device_token_id: int = _TokenId):
    principal = _own(request)
    return _no_store(DeviceTokenRevoked(**svc.revoke_own(
        principal["user_id"], device_token_id, ip=client_ip(request))))


@router.get("/api/extension/devices", dependencies=[local_only()],
            response_model=AdminDeviceTokenList, summary="PC only: every browser-extension device")
def admin_list():
    return _no_store(AdminDeviceTokenList(tokens=svc.admin_list()))


@router.post("/api/extension/devices/{device_token_id}/revoke", dependencies=[local_only()],
             response_model=DeviceTokenRevoked, summary="PC only: revoke anyone's extension device")
def admin_revoke(request: Request, device_token_id: int = _TokenId):
    return _no_store(DeviceTokenRevoked(**svc.admin_revoke(
        device_token_id, request.state.principal.get("user_id"),
        at_pc=is_local_request(request))))
