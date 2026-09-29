"""
api/routers/metadata_routes.py -- Media analysis and metadata auto-fill
(Migration Slice 37). See services/metadata_service.py: autofill returns a
suggestion only; apply writes whitelisted fields.
"""

from fastapi import APIRouter, Path
from api.auth import require_permission
from api.schemas import (AutofillApply, AutofillRequest, AutofillSuggestion, ErrorResponse,
                         MediaAnalysis)
from services import metadata_service

router = APIRouter(prefix="/api/metadata", tags=["metadata"])

_ERRS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/analyze-media", dependencies=[require_permission("library.read")], response_model=MediaAnalysis,
             summary="ffprobe the drama's stored media (numbers/booleans only)",
             responses=_ERRS)
def analyze_media(drama_id: int = Path(ge=1)):
    return metadata_service.analyze_media(drama_id)


@router.post("/dramas/{drama_id}/autofill", dependencies=[require_permission("media.import_url")], response_model=AutofillSuggestion,
             summary="Suggest metadata from a public page or pasted text (writes nothing)",
             responses=_ERRS)
def autofill(payload: AutofillRequest, drama_id: int = Path(ge=1)):
    return metadata_service.autofill_suggestion(
        drama_id, url=payload.url, page_text=payload.page_text, engine_name=payload.engine)


@router.post("/dramas/{drama_id}/autofill/apply", dependencies=[require_permission("lines.edit")],
             summary="Write chosen autofill fields to the drama; returns drama detail",
             responses=_ERRS)
def apply_autofill(payload: AutofillApply, drama_id: int = Path(ge=1)):
    return metadata_service.apply_autofill(drama_id, payload.model_dump(exclude_none=True))
