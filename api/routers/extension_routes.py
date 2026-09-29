"""
api/routers/extension_routes.py -- PC-only control for the browser-extension
bridge (page_server): status, on/off, and showing its token (API batch 1;
user decision 2026-09-29). Thin: see services/extension_service.py.

All three are `local_only()`: the bridge and its token belong to the owner
at the PC. The status never carries the port or the token. The token is
returned only by `POST /token` with `confirm=true`, with
`Cache-Control: no-store`; nothing here logs it.

Turning the bridge on starts it in this process only when the API's
background services are on (`BAIHE_API_BACKGROUND`, the same switch the
startup hook uses); otherwise it is started at the next launch. Turning it
off persists the setting; a running endpoint keeps serving until the API
restarts (`restart_needed`), because page_server has no stop.
"""

from fastapi import APIRouter, Request, Response

from api.auth import local_only, require_permission
from api.schemas import (ErrorResponse, ExtensionEnabledRequest, ExtensionEnabledResult,
                         ExtensionEngineRequest, ExtensionEngineSettings,
                         ExtensionStatus, ExtensionToken, ExtensionTokenRequest)
from services import extension_service as svc

router = APIRouter(prefix="/api/extension", tags=["extension"])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/status", dependencies=[local_only()], response_model=ExtensionStatus,
            summary="PC only: is the extension bridge enabled / running (no port, no token)")
def get_status():
    return svc.get_status()


@router.post("/enabled", dependencies=[local_only()], response_model=ExtensionEnabledResult,
             summary="PC only: turn the extension bridge on or off (persisted)",
             responses={422: {"model": ErrorResponse}})
def post_enabled(body: ExtensionEnabledRequest, request: Request):
    settings = getattr(request.app.state, "settings", None)
    start_now = bool(getattr(settings, "background_services", False))
    return svc.set_enabled(body.enabled, start_now=start_now)


@router.post("/token", dependencies=[local_only()], response_model=ExtensionToken,
             summary="PC only: show the extension token (confirm=true; never cached)",
             responses={422: {"model": ErrorResponse}})
def post_token(body: ExtensionTokenRequest, response: Response):
    result = svc.reveal_token(confirm=body.confirm)
    response.headers.update(_NO_STORE)
    return result


@router.get("/engine", dependencies=[require_permission("admin.settings")],
            response_model=ExtensionEngineSettings,
            summary="The extension's translation engine and model (no keys)")
def get_engine():
    return svc.get_translation_settings()


@router.post("/engine", dependencies=[local_only()], response_model=ExtensionEngineSettings,
             summary="PC only: set the extension's translation engine and model (persisted)",
             responses={422: {"model": ErrorResponse}})
def post_engine(body: ExtensionEngineRequest):
    return svc.set_translation_settings(body.engine, body.model)
