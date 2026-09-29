"""
api/routers/diarization_routes.py -- Diarize-stage endpoints for one drama
(Phase 6's second Workspace stage, Migration Slice 16).

One config read and one job-starting action -- see
services/diarization_service.py's own docstring for the scope decision
(the "run diarization during alignment" checkbox and the turns-to-lines
merge are a future Transcript/Align-stage slice's concern, not this one).
Job status/cancel is not duplicated here: poll the started job through
the existing GET /api/jobs/{job_id} (Migration Slice 8).
"""

from fastapi import APIRouter, Path, Query
from api.auth import require_permission
from api.schemas import DiarizationConfig, DiarizationRunResult, ErrorResponse
from services import diarization_service

router = APIRouter(prefix="/api/diarization", tags=["diarization"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=DiarizationConfig,
            summary="Read-only Diarize-stage summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_diarization_config(drama_id: int = Path(ge=1)):
    return diarization_service.get_diarization_config(drama_id)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=DiarizationRunResult,
            summary="Start a real speaker-detection job for one drama's stored audio",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                      409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_start_diarization(drama_id: int = Path(ge=1),
                           expected_speakers: int = Query(None, ge=0, le=20),
                           overwrite_manual: bool = Query(False),
                           confirm: bool = Query(False),
                           min_speakers: int = Query(None, ge=0, le=20),
                           max_speakers: int = Query(None, ge=0, le=20)):
    return diarization_service.start_diarization_run(
        drama_id, expected_speakers=expected_speakers,
        overwrite_manual=overwrite_manual, confirm=confirm,
        min_speakers=min_speakers, max_speakers=max_speakers)
