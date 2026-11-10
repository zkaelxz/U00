"""
api/routers/system_routes.py -- liveness and API metadata.

`/api/health` never touches the database or any optional package, so it
answers "is the server up" and nothing else; a client should use a real
endpoint's own error to learn anything more specific.

`POST /api/system/shutdown` is the installed launcher's clean
stop: PC-only, and it exists only when the launcher started the server
with a one-time token (services/shutdown_service.py); otherwise it is a 404.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request
from api.auth import is_auth_enabled, is_local_request, local_only, public_route
from api.schemas import (API_VERSION, HealthResponse, MetaResponse, PcProfilePinRequest,
                         PcProfileSavedStatus, PcProfileSetPinRequest, PcProfileStatus, ShutdownResponse)
from lib import features
from services import pc_profile_service, shutdown_service
from lib.errors import ForbiddenError, NotFoundError

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", dependencies=[public_route()], response_model=HealthResponse, summary="Liveness check")
def health():
    return HealthResponse(status="ok")


@router.get("/meta", dependencies=[public_route()], response_model=MetaResponse, summary="API name, contract version, mode")
def meta(request: Request):
    # `local` mirrors local_only(): true exactly when a PC-only route would
    # let this request through. It only drives which controls the UI shows;
    # the PC-only routes still enforce it themselves.
    local = not is_auth_enabled(request.app) or is_local_request(request)
    settings = request.app.state.settings
    # Public, so the household listener doesn't tell other devices its mode.
    environment = "" if getattr(settings, "is_household", True) else settings.environment
    return MetaResponse(app="Baihe Studio", api_version=API_VERSION,
                        environment=environment, local=local,
                        features=features.status(settings.features_off))


@router.post("/system/shutdown", dependencies=[local_only()], response_model=ShutdownResponse,
             status_code=202, summary="Stop the installed app's server cleanly")
def shutdown(x_baihe_shutdown_token: Optional[str] = Header(default=None)):
    # Only the installed launcher knows the token (it started this server
    # with it); a server started any other way has no shutdown route.
    if not shutdown_service.enabled():
        raise NotFoundError("Not found.")
    if not shutdown_service.token_matches(x_baihe_shutdown_token):
        raise ForbiddenError("Not allowed.")
    return ShutdownResponse(**shutdown_service.request_shutdown())


def _profiles_available(request: Request):
    # With single-port sign-in the PC owner is a signed-in account and Guest
    # would hide nothing, so the feature is offered only to the .exe's
    # sign-in-off PC window (the installer sets BAIHE_API_AUTH=off).
    if is_auth_enabled(request.app):
        raise NotFoundError("Not found.")


@router.get("/pc-profile", dependencies=[local_only(), Depends(_profiles_available)], response_model=PcProfileStatus,
            summary="Which profile the PC window is on")
def pc_profile_status():
    return PcProfileStatus(**pc_profile_service.status())


@router.post("/pc-profile/unlock", dependencies=[local_only(), Depends(_profiles_available)], response_model=PcProfileStatus,
             summary="Switch the PC window to Kae with the PIN")
def pc_profile_unlock(body: PcProfilePinRequest):
    return PcProfileStatus(**pc_profile_service.unlock(body.pin))


@router.post("/pc-profile/lock", dependencies=[local_only(), Depends(_profiles_available)], response_model=PcProfileStatus,
             summary="Switch the PC window back to Guest")
def pc_profile_lock():
    return PcProfileStatus(**pc_profile_service.lock())


@router.post("/pc-profile/pin", dependencies=[local_only(), Depends(_profiles_available)], response_model=PcProfileSavedStatus,
            summary="Set or change the profile PIN")
def pc_profile_set_pin(body: PcProfileSetPinRequest):
    return PcProfileSavedStatus(**pc_profile_service.set_pin(
        body.new_pin, body.current_pin, body.hide_existing))


@router.post("/pc-profile/clear", dependencies=[local_only(), Depends(_profiles_available)], response_model=PcProfileStatus,
             summary="Turn profiles off (needs the PIN)")
def pc_profile_clear(body: PcProfilePinRequest):
    return PcProfileStatus(**pc_profile_service.clear_pin(body.pin))
