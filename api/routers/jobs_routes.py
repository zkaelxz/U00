"""
api/routers/jobs_routes.py -- read-only job-list endpoints (Migration
Slice 8), reading `db.job_records` (Migration Slice 7's cross-process
mirror) through `services.jobs_service`.

Slice 22 adds POST /{job_id}/cancel (cross-process: flags the DB row, the
owning process notices via a throttled check).
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import JobListResponse, JobRecord, ErrorResponse, JobCancelResult
from services import jobs_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", dependencies=[require_permission("library.read")], response_model=JobListResponse,
            summary="Every job this app knows about, cross-process, newest-started first")
def list_jobs():
    items = [JobRecord(**r) for r in jobs_service.list_jobs()]
    return JobListResponse(items=items, count=len(items))


@router.get("/{job_id}", dependencies=[require_permission("library.read")], response_model=JobRecord, summary="One job's cross-process record",
            responses={404: {"model": ErrorResponse}})
def get_job(job_id: str = Path(min_length=1)):
    return JobRecord(**jobs_service.get_job(job_id))


@router.post("/{job_id}/cancel", dependencies=[require_permission("jobs.cancel")], response_model=JobCancelResult,
             summary="Request cancellation of a queued/running job (any process)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def cancel_job(job_id: str = Path(min_length=1)):
    return JobCancelResult(**jobs_service.cancel_job(job_id))
