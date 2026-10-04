"""
api/routers/dub_routes.py -- Dub-stage endpoints for one drama: the config
summary, the last run's pacing, the Generate job and the finished-track
download. All logic lives in services/dub_service.py.
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from fastapi.responses import FileResponse

from api.schemas import DubConfig, DubPacing, DubRunRequest, DubRunStarted, ErrorResponse
from services import dub_service

router = APIRouter(prefix="/api/dub", tags=["dub"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=DubConfig,
            summary="Read-only Dub-stage summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_dub_config(drama_id: int = Path(ge=1)):
    return dub_service.get_dub_config(drama_id)


@router.get("/dramas/{drama_id}/pacing", dependencies=[require_permission("library.read")], response_model=DubPacing,
            summary="Per-line pacing from the last dub run",
            responses={404: {"model": ErrorResponse}})
def get_dub_pacing(drama_id: int = Path(ge=1)):
    return dub_service.get_dub_pacing(drama_id)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=DubRunStarted,
             summary="Start the dub/narration track job (poll GET /api/jobs/{job_id})",
             responses={404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def start_dub_run(body: DubRunRequest, drama_id: int = Path(ge=1)):
    return dub_service.start_dub_run(
        drama_id, tts_engine=body.tts_engine, max_speedup=body.max_speedup,
        max_slowdown=body.max_slowdown, narration_language=body.narration_language,
        keep_background=body.keep_background)


@router.get("/dramas/{drama_id}/track", dependencies=[require_permission("media.stream")],
            summary="Download the finished dub/narration track (WAV)",
            responses={404: {"model": ErrorResponse}})
def download_dub_track(drama_id: int = Path(ge=1)):
    track = dub_service.get_dub_track(drama_id)
    return FileResponse(
        track["path"], media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="drama_{drama_id}_{track["name"]}"'})
