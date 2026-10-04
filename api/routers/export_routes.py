"""
api/routers/export_routes.py -- Export-stage endpoints for one drama
(Phase 6's first Workspace stage).

The read-only readiness summary, subtitle text generation (SRT/VTT) as a
plain-text download, and the three flagging actions -- each
writes only the flag/flag_note fields (see services/export_service.py's
own docstring for the field-scoped-write discipline) -- live here, as does
EPUB export (novel-narration dramas only) as a binary download.
ASS subtitle text (POST, per-request style, plain-text
download) and the style-options listing are also here, along with the audiobook export
job and the burned-in video job
(POST, returns {job_id}, output downloads via /api/artifacts). Parity
E17/E19 add the soft-subtitle and dubbed video jobs (same shape), and E22
"Mark as exported" (status only; admin.library like the other drama status
writes).
"""

from typing import Optional

from fastapi import APIRouter, Path, Query, Response
from api.auth import require_permission
from api.schemas import (AssExportRequest, AssStyleOptions, AutoQcFlagResult, DubbedVideoRequest,
                         ErrorResponse, ExportReadiness, FlagActionResult, MarkExportedResult,
                         MediaExportStarted, SoftsubVideoRequest)
from services import export_service, media_export_service

router = APIRouter(prefix="/api/export", tags=["export"])

_MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt"}


@router.get("/dramas/{drama_id}/readiness", dependencies=[require_permission("library.read")], response_model=ExportReadiness,
            summary="Read-only export-readiness summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_export_readiness(drama_id: int = Path(ge=1)):
    return export_service.get_export_readiness(drama_id)


@router.get("/dramas/{drama_id}/subtitle", dependencies=[require_permission("lines.read")],
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


@router.post("/dramas/{drama_id}/flag-overlaps", dependencies=[require_permission("lines.edit")], response_model=FlagActionResult,
            summary="Flag every currently-overlapping, not-yet-flagged line for review",
            responses={404: {"model": ErrorResponse}})
def post_flag_overlaps(drama_id: int = Path(ge=1)):
    return export_service.flag_overlapping_lines(drama_id)


@router.post("/dramas/{drama_id}/flag-dense-lines", dependencies=[require_permission("lines.edit")], response_model=FlagActionResult,
            summary="Flag every line too dense to read in its on-screen time",
            responses={404: {"model": ErrorResponse}})
def post_flag_dense_lines(drama_id: int = Path(ge=1)):
    return export_service.flag_dense_lines(drama_id)


@router.post("/dramas/{drama_id}/flag-auto-qc", dependencies=[require_permission("lines.edit")], response_model=AutoQcFlagResult,
            summary="Run Auto QC's factual-detail check and update flags in place",
            responses={404: {"model": ErrorResponse}})
def post_flag_auto_qc(drama_id: int = Path(ge=1)):
    return export_service.run_auto_qc_flagging(drama_id)


@router.get("/dramas/{drama_id}/epub", dependencies=[require_permission("lines.read")],
            summary="Generate an EPUB for one novel-narration drama (binary download)",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                      422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def get_epub(drama_id: int = Path(ge=1), field: str = Query("en", pattern="^(en|zh)$")):
    data = export_service.generate_epub(drama_id, field=field)
    filename = f"drama_{drama_id}_{field}.epub"
    return Response(
        content=data, media_type="application/epub+zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/dramas/{drama_id}/ass", dependencies=[require_permission("lines.read")],
             summary="Generate ASS subtitle text for one drama (plain-text download)",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_ass_text(req: AssExportRequest, drama_id: int = Path(ge=1)):
    style = req.style.model_dump(exclude_unset=True) if req.style is not None else None
    text = export_service.generate_ass_text(
        drama_id, field=req.field, style=style, preset=req.preset,
        speaker_colors=req.speaker_colors, per_speaker_colors=req.per_speaker_colors,
        include_notes=req.include_notes, notes_as_separate_line=req.notes_as_separate_line,
        wrap_chars_en=req.wrap_chars_en, wrap_chars_source=req.wrap_chars_source)
    filename = f"drama_{drama_id}_{req.field}.ass"
    return Response(
        content=text, media_type="text/x-ssa; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


# Static path; every other route here lives under /dramas/..., so it can't be shadowed.
@router.get("/ass-style-options", dependencies=[require_permission("library.read")], response_model=AssStyleOptions,
            summary="Presets, fonts, alignments and ranges for ASS style controls")
def get_ass_style_options():
    return export_service.get_ass_style_options()


@router.post("/dramas/{drama_id}/audiobook", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the audiobook (.m4b) export job (poll GET /api/jobs/{job_id})",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_audiobook(drama_id: int = Path(ge=1)):
    return media_export_service.start_audiobook_export(drama_id)


@router.post("/dramas/{drama_id}/burned-video", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the burned-in (hardsub ASS) video export job",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_burned_video(drama_id: int = Path(ge=1), req: Optional[AssExportRequest] = None):
    req = req or AssExportRequest()
    style = req.style.model_dump(exclude_unset=True) if req.style is not None else None
    return media_export_service.start_burned_video_export(
        drama_id, field=req.field, style=style, preset=req.preset,
        speaker_colors=req.speaker_colors, per_speaker_colors=req.per_speaker_colors,
        include_notes=req.include_notes, notes_as_separate_line=req.notes_as_separate_line,
        wrap_chars_en=req.wrap_chars_en, wrap_chars_source=req.wrap_chars_source)


@router.post("/dramas/{drama_id}/softsub-video", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the soft-subtitle video export job (subtitle track muxed in)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_softsub_video(drama_id: int = Path(ge=1), req: Optional[SoftsubVideoRequest] = None):
    req = req or SoftsubVideoRequest()
    return media_export_service.start_softsub_video_export(
        drama_id, field=req.field, include_notes=req.include_notes)


@router.post("/dramas/{drama_id}/dubbed-video", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the dubbed video export job (dub audio replaces, or mixes over, the original)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_dubbed_video(drama_id: int = Path(ge=1), req: Optional[DubbedVideoRequest] = None):
    req = req or DubbedVideoRequest()
    return media_export_service.start_dubbed_video_export(drama_id, keep_original=req.keep_original)


@router.post("/dramas/{drama_id}/mark-exported", dependencies=[require_permission("admin.library")], response_model=MarkExportedResult,
             summary="Mark the drama as exported (sets its status only)",
             responses={404: {"model": ErrorResponse}})
def post_mark_exported(drama_id: int = Path(ge=1)):
    return export_service.mark_exported(drama_id)
