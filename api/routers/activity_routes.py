"""
api/routers/activity_routes.py -- the activity log and the daily GPU usage chart.

- `GET /api/activity` (`library.read`): finished jobs, newest first, only those the caller may see.
- `GET /api/activity/gpu-daily` (`library.read`): GPU seconds per day for the caller's visible jobs.
"""

from fastapi import APIRouter, Query, Request

from api.auth import require_permission
from api.schemas import ActivityList, GpuDaily
from services import activity_service

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("", dependencies=[require_permission("library.read")], response_model=ActivityList,
            summary="Finished jobs, newest first")
def get_activity(request: Request, limit: int = Query(50, ge=1, le=100), before_id: int | None = Query(None, ge=1)):
    return activity_service.list_activity(principal=request.state.principal, limit=limit, before_id=before_id)


@router.get("/gpu-daily", dependencies=[require_permission("library.read")], response_model=GpuDaily,
            summary="GPU time per day for the last days")
def get_gpu_daily(request: Request, days: int = Query(30, ge=1, le=365)):
    return activity_service.gpu_daily(principal=request.state.principal, days=days)
