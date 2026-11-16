"""
api/routers/narration_routes.py -- novel-narration "Chunk & tag speakers".
A config read plus one job-starting action; see
services/narration_service.py. Poll the job via GET /api/jobs/{job_id}.
"""

from fastapi import APIRouter, Path, Query, Request
from api.auth import require_engines_allowed, require_permission
from api.schemas import NarrationConfig, NarrationEstimate, NarrationRunRequest, NarrationRunResult, error_responses
from services import narration_service

router = APIRouter(prefix="/api/narration", tags=["narration"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=NarrationConfig,
            summary="Read-only chunk-and-tag options for one drama",
            responses=error_responses(404))
def get_narration_config(drama_id: int = Path(ge=1)):
    return narration_service.get_narration_config(drama_id)


@router.get("/dramas/{drama_id}/estimate", dependencies=[require_permission("library.read")], response_model=NarrationEstimate,
            summary="Cost estimate for chunking and tagging the chosen chapters",
            responses=error_responses(404, 409, 422))
def get_narration_estimate(drama_id: int = Path(ge=1), engine: str | None = Query(None, max_length=40),
                           model: str | None = Query(None, max_length=200),
                           chapter_from: int | None = Query(None, ge=1), chapter_to: int | None = Query(None, ge=1)):
    return narration_service.estimate_tag_cost(drama_id, engine, model, chapter_from, chapter_to)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=NarrationRunResult,
             summary="Start the background chunk-and-tag job for one drama",
             responses=error_responses(404, 409, 422, 503))
def post_start_narration(payload: NarrationRunRequest, request: Request,
                         drama_id: int = Path(ge=1),
                         fresh: bool = Query(False, description=(
                             "Drop an interrupted run's saved batches and start over"))):
    if not payload.one_narrator:
        require_engines_allowed(request, payload.engine, model=payload.model)
    return narration_service.start_narration_run(
        drama_id, engine_name=payload.engine, model=payload.model, fresh=fresh,
        append=payload.append, chapter_from=payload.chapter_from, chapter_to=payload.chapter_to,
        one_narrator=payload.one_narrator)
