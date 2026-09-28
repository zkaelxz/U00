"""
api/routers/export_routes.py -- Export-stage endpoints for one drama
(Phase 6's first Workspace stage).

Migration Slice 12 added the read-only readiness summary. Migration
Slice 14 adds subtitle text generation (SRT/VTT) as a plain-text download
-- see services/export_service.py's own docstring for what's still out
of scope (ASS, EPUB, audiobook, burned-in video, and any flagging
action).
"""

from fastapi import APIRouter, Path, Query, Response

from api.schemas import ErrorResponse, ExportReadiness
from services import export_service

router = APIRouter(prefix="/api/export", tags=["export"])

_MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt"}


@router.get("/dramas/{drama_id}/readiness", response_model=ExportReadiness,
            summary="Read-only export-readiness summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_export_readiness(drama_id: int = Path(ge=1)):
    return export_service.get_export_readiness(drama_id)


@router.get("/dramas/{drama_id}/subtitle",
            summary="Generate SRT/VTT subtitle text for one drama (plain-text download)",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def get_subtitle_text(
        drama_id: int = Path(ge=1),
        fmt: str = Query("srt", pattern="^(srt|vtt)$"),
        field: str = Query("en", pattern="^(en|zh|bilingual)$"),
        include_notes: bool = Query(False),
        wrap_chars_en: int = Query(None, ge=1, le=200),
        wrap_chars_source: int = Query(None, ge=1, le=200)):
    text = export_service.generate_subtitle_text(
        drama_id, fmt, field, include_notes=include_notes,
        wrap_chars_en=wrap_chars_en, wrap_chars_source=wrap_chars_source)
    filename = f"drama_{drama_id}_{field}.{fmt}"
    return Response(
        content=text, media_type=_MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
