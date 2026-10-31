"""
api/routers/listen_routes.py -- the Reader's Listen for a narrated title: the
config, starting the narration, and the finished track. All logic lives in
services/listen_service.py.
"""

from fastapi import APIRouter, Path, Request
from fastapi.responses import FileResponse

from api.auth import is_at_pc, require_permission
from api.schemas import DubRunStarted, ListenConfig, ListenRunRequest, error_responses
from services import export_service, listen_service, model_readiness_service

router = APIRouter(prefix="/api/listen", tags=["listen"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=ListenConfig,
            summary="What Listen can do for one narrated title",
            responses=error_responses(404))
def get_listen_config(drama_id: int = Path(ge=1)):
    return listen_service.get_listen_config(drama_id)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=DubRunStarted,
             summary="Start the narration job (poll GET /api/jobs/{job_id})",
             responses=error_responses(404, 409, 503))
def start_listen(body: ListenRunRequest, request: Request, drama_id: int = Path(ge=1)):
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_dub, drama_id, tts_engine=body.tts_engine)
    return listen_service.start_listen(drama_id, body.tts_engine, body.narration_language)


@router.get("/dramas/{drama_id}/track", dependencies=[require_permission("media.stream")],
            summary="Download the finished narration track (WAV)",
            responses=error_responses(404))
def download_listen_track(drama_id: int = Path(ge=1)):
    track = listen_service.get_listen_track(drama_id)
    return FileResponse(
        track["path"], media_type="audio/wav",
        headers={"Content-Disposition": export_service.dub_track_disposition(drama_id, track["name"])})
