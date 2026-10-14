"""
api/routers/source_suspect_routes.py -- Review's "Check source text" for one
drama: flag lines whose transcription looks off, and list the flagged ones.
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import SourceSuspectCheckResult, SourceSuspectList, error_responses
from services import source_suspect_service

router = APIRouter(prefix="/api/source-suspect", tags=["source-suspect"])


@router.get("/dramas/{drama_id}", dependencies=[require_permission("lines.read")],
            response_model=SourceSuspectList,
            summary="The lines flagged as source text that looks off, with the line each could merge into",
            responses=error_responses(404))
def get_source_suspects(drama_id: int = Path(ge=1)):
    return source_suspect_service.list_suspects(drama_id)


@router.post("/dramas/{drama_id}/check", dependencies=[require_permission("lines.edit")],
             response_model=SourceSuspectCheckResult,
             summary="Flag lines whose source text looks mis-transcribed or badly split",
             responses=error_responses(404))
def post_check_source_text(drama_id: int = Path(ge=1)):
    return source_suspect_service.check_source_text(drama_id)
