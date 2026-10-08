"""
api/routers/diarization_routes.py -- Diarize-stage endpoints for one drama.

One config read, the job-starting action, and re-labelling lines from the
speaker turns already saved. Job status/cancel is not duplicated here: poll the started job through
the existing GET /api/jobs/{job_id}.
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


@router.post("/dramas/{drama_id}/reassign", dependencies=[require_permission("lines.edit")],
             summary="Re-label lines from the speaker turns already saved (no detection run)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def post_reassign_speakers(drama_id: int = Path(ge=1)):
    return diarization_service.reassign_speakers_from_saved_turns(drama_id)
