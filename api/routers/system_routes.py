"""
api/routers/system_routes.py -- liveness and API metadata.

`/api/health` never touches the database or any optional package, so it
answers "is the server up" and nothing else; a client should use a real
endpoint's own error to learn anything more specific.
"""

from fastapi import APIRouter, Request
from api.auth import _auth_enabled, is_local_request, public_route
from api.schemas import API_VERSION, HealthResponse, MetaResponse

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", dependencies=[public_route()], response_model=HealthResponse, summary="Liveness check")
def health():
    return HealthResponse(status="ok")


@router.get("/meta", dependencies=[public_route()], response_model=MetaResponse, summary="API name, contract version, mode")
def meta(request: Request):
    # `local` mirrors local_only(): true exactly when a PC-only route would
    # let this request through. It only drives which controls the UI shows;
    # the PC-only routes still enforce it themselves.
    local = not _auth_enabled(request.app) or is_local_request(request)
    return MetaResponse(app="Baihe Studio", api_version=API_VERSION,
                        environment=request.app.state.settings.environment, local=local)
