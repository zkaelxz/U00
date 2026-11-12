"""
api/routers/diagnostics_ffmpeg_routes.py -- "Install ffmpeg" (thin; see
services/ffmpeg_install_service.py). The status read is `admin.diagnostics`;
the install is `local_only()` + confirm=true, refused while any job runs, and
downloads a pinned, checksum-verified ffmpeg into the app's own folder as a
background job. Nothing but the confirm comes from the request.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.schemas import error_responses
from api.schemas.ffmpeg_install import FfmpegInstallRequest, FfmpegInstallStarted, FfmpegInstallStatus
from services import ffmpeg_install_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/ffmpeg", dependencies=[require_permission("admin.diagnostics")],
            response_model=FfmpegInstallStatus,
            summary="ffmpeg: whether it is found, and the one-click install job")
def get_ffmpeg():
    return svc.get_status()


@router.post("/ffmpeg/install", dependencies=[local_only()],
             response_model=FfmpegInstallStarted,
             summary="PC only: download ffmpeg into the app's own folder as a background job "
                     "(confirm=true)",
             responses=error_responses(409, 422))
def post_ffmpeg_install(body: FfmpegInstallRequest):
    return svc.start_install(confirm=body.confirm)
