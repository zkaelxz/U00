"""
api/routers/timing_check_routes.py -- Review's "Check timing" for one drama:
start the check, read its status and per-line suggestions, snap lines to
speech. The job itself is polled and cancelled through /api/jobs/{job_id}.
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import (TimingCheckStarted, TimingCheckStatus, TimingSnapRequest,
                         TimingSnapResult, error_responses)
from services import timing_check_service

router = APIRouter(prefix="/api/timing-check", tags=["timing-check"])


@router.get("/dramas/{drama_id}", dependencies=[require_permission("lines.read")],
            response_model=TimingCheckStatus,
            summary="The timing check's job status, last result and snap suggestions",
            responses=error_responses(404))
def get_timing_check(drama_id: int = Path(ge=1)):
    return timing_check_service.get_timing_check(drama_id)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")],
             response_model=TimingCheckStarted,
             summary="Start a check of the lines' timing against the speech in the stored audio",
             responses=error_responses(400, 404, 409))
def post_start_timing_check(drama_id: int = Path(ge=1)):
    return timing_check_service.start_timing_check(drama_id)


@router.post("/dramas/{drama_id}/snap", dependencies=[require_permission("lines.edit")],
             response_model=TimingSnapResult,
             summary="Snap flagged lines to the detected speech (history snapshot first)",
             responses=error_responses(404, 422))
def post_snap_to_speech(payload: TimingSnapRequest, drama_id: int = Path(ge=1)):
    return timing_check_service.snap_to_speech(drama_id, payload.line_ids)
