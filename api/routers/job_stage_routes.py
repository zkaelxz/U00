"""
api/routers/job_stage_routes.py -- Step 41 item 5: a job's per-stage
timing and estimated spend. Thin: see services/jobs_service.get_job_stages
and services/job_timing_service.

- `GET /api/jobs/{job_id}/stages` (`library.read`): the latest runs of one
  job, each with its stages. 404 for a job the caller can't see, exactly
  like `GET /api/jobs/{job_id}`.
"""

from fastapi import APIRouter, Path, Request

from api.auth import require_permission
from api.job_stage_schemas import JobStageTimings
from api.schemas import ErrorResponse
from services import jobs_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}/stages", dependencies=[require_permission("library.read")],
            response_model=JobStageTimings,
            summary="Per-stage timing and estimated spend of a job's latest runs",
            responses={404: {"model": ErrorResponse}})
def get_job_stages(request: Request, job_id: str = Path(min_length=1)):
    return jobs_service.get_job_stages(job_id, principal=request.state.principal)
