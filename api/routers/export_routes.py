"""
api/routers/export_routes.py -- Export-stage endpoints for one drama
(Phase 6's first Workspace stage).

Migration Slice 12 added the read-only readiness summary. Migration
Slice 14 added subtitle text generation (SRT/VTT) as a plain-text
download. Migration Slice 15 adds the three flagging actions -- each
writes only the flag/flag_note fields (see services/export_service.py's
own docstring for the field-scoped-write discipline). Still out of
scope: ASS, EPUB, audiobook, burned-in video export.
"""

from fastapi import APIRouter, Path, Query, Response

from api.schemas import AutoQcFlagResult, ErrorResponse, ExportReadiness, FlagActionResult
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


@router.post("/dramas/{drama_id}/flag-overlaps", response_model=FlagActionResult,
            summary="Flag every currently-overlapping, not-yet-flagged line for review",
            responses={404: {"model": ErrorResponse}})
def post_flag_overlaps(drama_id: int = Path(ge=1)):
    return export_service.flag_overlapping_lines(drama_id)


@router.post("/dramas/{drama_id}/flag-dense-lines", response_model=FlagActionResult,
            summary="Flag every line too dense to read in its on-screen time",
            responses={404: {"model": ErrorResponse}})
def post_flag_dense_lines(drama_id: int = Path(ge=1)):
    return export_service.flag_dense_lines(drama_id)


@router.post("/dramas/{drama_id}/flag-auto-qc", response_model=AutoQcFlagResult,
            summary="Run Auto QC's factual-detail check and update flags in place",
            responses={404: {"model": ErrorResponse}})
def post_flag_auto_qc(drama_id: int = Path(ge=1)):
    return export_service.run_auto_qc_flagging(drama_id)
