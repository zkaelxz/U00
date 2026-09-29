"""
api/routers/diagnostics_installs_routes.py -- Diagnostics actions
that run as background jobs (thin; see
services/diagnostics_installs_service.py):

- Deno, the JavaScript runtime yt-dlp needs (Q02). Status is
  `admin.diagnostics`; the install is `local_only()` + confirm=true, 409
  while any job, restore, cleanup or install runs. The download URL comes
  only from the service's static table (never the request).

Progress is also visible through GET /api/jobs/{job_id}; cancel through
POST /api/jobs/{job_id}/cancel.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.diagnostics_install_schemas import (DiagnosticsDenoInstallRequest,
                                             DiagnosticsDenoStatus, DiagnosticsJobStarted)
from api.schemas import ErrorResponse
from services import diagnostics_installs_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}


@router.get("/deno", dependencies=[require_permission("admin.diagnostics")],
            response_model=DiagnosticsDenoStatus,
            summary="JS runtime for yt-dlp, Deno on PATH or installed, the install job")
def get_deno():
    return svc.get_deno_status()


@router.post("/deno/install", dependencies=[local_only()],
             response_model=DiagnosticsJobStarted,
             summary="PC only: install Deno as a background job (confirm=true)",
             responses=_ERRS)
def post_deno_install(body: DiagnosticsDenoInstallRequest):
    return svc.start_deno_install(confirm=body.confirm)

