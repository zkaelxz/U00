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

from fastapi import APIRouter, Header, Request
from api.auth import is_auth_enabled, is_local_request, local_only, public_route
from api.schemas import API_VERSION, HealthResponse, MetaResponse, ShutdownResponse
from services import shutdown_service
from services.service_errors import ForbiddenError, NotFoundError

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
                        environment=environment, local=local)


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
