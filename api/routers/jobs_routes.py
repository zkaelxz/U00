"""
api/routers/jobs_routes.py -- read-only job-list endpoints (Migration
Slice 8), reading `db.job_records` (Migration Slice 7's cross-process
mirror) through `services.jobs_service`.

No cancel endpoint here on purpose. Cancelling a job from a different
process than the one running it needs the owning process to actually
notice the request -- more than a records-only mirror provides -- and
is deliberately deferred; see docs/migration-review.md's Slice 8 note.
"""

from fastapi import APIRouter, Path

from api.schemas import JobListResponse, JobRecord, ErrorResponse
from services import jobs_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse,
            summary="Every job this app knows about, cross-process, newest-started first")
def list_jobs():
    items = [JobRecord(**r) for r in jobs_service.list_jobs()]
    return JobListResponse(items=items, count=len(items))


@router.get("/{job_id}", response_model=JobRecord, summary="One job's cross-process record",
            responses={404: {"model": ErrorResponse}})
def get_job(job_id: str = Path(min_length=1)):
    return JobRecord(**jobs_service.get_job(job_id))
