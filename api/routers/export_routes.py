"""
api/routers/export_routes.py -- read-only Export-readiness endpoint
(Migration Slice 12, Phase 6's first Workspace stage).

One route: a drama's export-readiness summary (line/translation counts,
overlap/Auto-QC/dense-line issue counts). Never flags a line, never
generates a subtitle file -- see services/export_service.py's own
docstring for the scope decision.
"""

from fastapi import APIRouter, Path

from api.schemas import ErrorResponse, ExportReadiness
from services import export_service

router = APIRouter(prefix="/api/export", tags=["export"])


@router.get("/dramas/{drama_id}/readiness", response_model=ExportReadiness,
            summary="Read-only export-readiness summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_export_readiness(drama_id: int = Path(ge=1)):
    return export_service.get_export_readiness(drama_id)
