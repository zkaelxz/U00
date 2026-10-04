"""
api/routers/source_routes.py -- Source-stage config endpoints for one
drama (Phase 6).

Config only (language/script/content mode/transcript mode); upload is
media_routes.py, transcription transcribe_routes.py. POST for the write, matching every other mutation endpoint in
this migration (translate/export/diarization routes are all POST; there
is no PATCH precedent here yet).
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import ErrorResponse, SourceConfig, SourceConfigUpdate
from services import source_service

router = APIRouter(prefix="/api/source", tags=["source"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=SourceConfig,
            summary="Read-only Source-stage config for one drama",
            responses={404: {"model": ErrorResponse}})
def get_source_config(drama_id: int = Path(ge=1)):
    return source_service.get_source_config(drama_id)


@router.post("/dramas/{drama_id}/config", dependencies=[require_permission("lines.edit")], response_model=SourceConfig,
            summary="Update Source-stage config for one drama (partial update)",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                      422: {"model": ErrorResponse}})
def post_source_config(payload: SourceConfigUpdate, drama_id: int = Path(ge=1)):
    return source_service.update_source_config(
        drama_id, source_language=payload.source_language,
        chinese_script=payload.chinese_script, content_mode=payload.content_mode,
        transcript_mode=payload.transcript_mode)
