"""
api/routers/dub_routes.py -- read-only Dub-stage endpoints for one drama
(Migration Slice 25): the config summary and the last run's pacing.

The Generate job is Slice 26 and is not exposed here; voice/character
edits, per-line preview and track download are also out of scope. All
logic lives in services/dub_service.py.
"""

from fastapi import APIRouter, Path

from api.schemas import DubConfig, DubPacing, ErrorResponse
from services import dub_service

router = APIRouter(prefix="/api/dub", tags=["dub"])


@router.get("/dramas/{drama_id}/config", response_model=DubConfig,
            summary="Read-only Dub-stage summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_dub_config(drama_id: int = Path(ge=1)):
    return dub_service.get_dub_config(drama_id)


@router.get("/dramas/{drama_id}/pacing", response_model=DubPacing,
            summary="Per-line pacing from the last dub run",
            responses={404: {"model": ErrorResponse}})
def get_dub_pacing(drama_id: int = Path(ge=1)):
    return dub_service.get_dub_pacing(drama_id)
