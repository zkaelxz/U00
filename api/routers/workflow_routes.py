"""
api/routers/workflow_routes.py -- the drama's pipeline progress for the
React stage bar (API batch 1). Thin: see services/workflow_service.py.
Read-only; no network.
"""

from fastapi import APIRouter, Path

from api.auth import require_permission
from api.schemas import ErrorResponse, WorkflowProgress
from services import workflow_service

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


@router.get("/dramas/{drama_id}/progress", dependencies=[require_permission("library.read")],
            response_model=WorkflowProgress,
            summary="Stage index, per-stage state and whole-drama counts",
            responses={404: {"model": ErrorResponse}})
def get_progress(drama_id: int = Path(ge=1, le=2**31 - 1)):
    return workflow_service.get_drama_progress(drama_id)
