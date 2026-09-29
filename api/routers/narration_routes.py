"""
api/routers/narration_routes.py -- novel-narration "Chunk & tag speakers"
(Migration Slice 33). A config read plus one job-starting action; see
services/narration_service.py. Poll the job via GET /api/jobs/{job_id}.
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import ErrorResponse, NarrationConfig, NarrationRunRequest, NarrationRunResult
from services import narration_service

router = APIRouter(prefix="/api/narration", tags=["narration"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=NarrationConfig,
            summary="Read-only chunk-and-tag options for one drama",
            responses={404: {"model": ErrorResponse}})
def get_narration_config(drama_id: int = Path(ge=1)):
    return narration_service.get_narration_config(drama_id)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=NarrationRunResult,
             summary="Start the background chunk-and-tag job for one drama",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_start_narration(payload: NarrationRunRequest, drama_id: int = Path(ge=1)):
    return narration_service.start_narration_run(
        drama_id, engine_name=payload.engine, model=payload.model)
