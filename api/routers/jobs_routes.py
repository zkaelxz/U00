"""
api/routers/jobs_routes.py -- read-only job-list endpoints (Migration
Slice 8), reading `db.job_records` (Migration Slice 7's cross-process
mirror) through `services.jobs_service`.

Slice 22 adds POST /{job_id}/cancel (cross-process: flags the DB row, the
owning process notices via a throttled check).

POST /clear-finished and POST /{job_id}/delete permanently erase finished
jobs' history: `local_only()` plus `confirm=true`, so a remote or household
user can never erase it.
"""

from fastapi import APIRouter, Path, Request
from api.auth import local_only, require_permission
from api.schemas import (DeleteConfirm, ErrorResponse, JobCancelResult, JobDeleteResult, JobListResponse,
                         JobRecord, JobsClearFinishedResult)
from services import jobs_service

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", dependencies=[require_permission("library.read")], response_model=JobListResponse,
            summary="Every job this app knows about, cross-process, newest-started first")
def list_jobs(request: Request):
    items = [JobRecord(**r) for r in jobs_service.list_jobs(principal=request.state.principal)]
    return JobListResponse(items=items, count=len(items))


@router.get("/{job_id}", dependencies=[require_permission("library.read")], response_model=JobRecord, summary="One job's cross-process record",
            responses={404: {"model": ErrorResponse}})
def get_job(request: Request, job_id: str = Path(min_length=1)):
    return JobRecord(**jobs_service.get_job(job_id, principal=request.state.principal))


@router.post("/{job_id}/cancel", dependencies=[require_permission("jobs.cancel")], response_model=JobCancelResult,
             summary="Request cancellation of a queued/running job (any process)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def cancel_job(request: Request, job_id: str = Path(min_length=1)):
    return JobCancelResult(**jobs_service.cancel_job(job_id, principal=request.state.principal))


@router.post("/clear-finished", dependencies=[local_only()], response_model=JobsClearFinishedResult,
             summary="PC only: permanently delete every finished job record (confirm=true)",
             responses={422: {"model": ErrorResponse}})
def clear_finished_jobs(body: DeleteConfirm):
    return JobsClearFinishedResult(**jobs_service.clear_finished_jobs(confirm=body.confirm))


@router.post("/{job_id}/delete", dependencies=[local_only()], response_model=JobDeleteResult,
             summary="PC only: permanently delete one finished job record (confirm=true)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def delete_job(body: DeleteConfirm, job_id: str = Path(min_length=1)):
    return JobDeleteResult(**jobs_service.delete_job(job_id, confirm=body.confirm))
