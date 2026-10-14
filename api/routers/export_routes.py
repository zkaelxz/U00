"""
api/routers/export_routes.py -- Export-stage endpoints for one drama.

The read-only readiness summary, subtitle text generation (SRT/VTT) as a
plain-text download, and the three flagging actions -- each
writes only the flag/flag_note fields (see services/export_service.py's
own docstring for the field-scoped-write discipline) -- live here, as does
EPUB export (novel-narration dramas only) as a binary download.
ASS subtitle text (POST, per-request style, plain-text
download) and the style-options listing are also here, along with the audiobook export
job and the burned-in video job
(POST, returns {job_id}, output downloads via /api/artifacts). The
soft-subtitle and dubbed video jobs (same shape) and
"Mark as exported" (status only; admin.library like the other drama status
writes).
"""

from typing import Optional

from fastapi import APIRouter, Path, Query, Response
from api.auth import require_permission
from api.schemas import (AssExportRequest, AssStyleOptions, AutoQcFlagResult, DubbedVideoRequest,
                         ExportReadiness, FlagActionResult, MarkExportedResult,
                         MediaExportStarted, ReadingSpeedMode, ReadingSpeedModeUpdate,
                         ClearReadingSpeedFlagsResult, SoftsubVideoRequest, error_responses)
from services import export_service, media_export_service

router = APIRouter(prefix="/api/export", tags=["export"])

_MAX_LONG_CUES_REPORTED = 50
_MEDIA_TYPES = {"srt": "application/x-subrip", "vtt": "text/vtt", "lrc": "text/plain; charset=utf-8"}


@router.get("/dramas/{drama_id}/readiness", dependencies=[require_permission("library.read")], response_model=ExportReadiness,
            summary="Read-only export-readiness summary for one drama",
            responses=error_responses(404))
def get_export_readiness(drama_id: int = Path(ge=1)):
    return export_service.get_export_readiness(drama_id)


@router.get("/dramas/{drama_id}/subtitle", dependencies=[require_permission("lines.read")],
            summary="Generate SRT/VTT/LRC subtitle text for one drama (plain-text download)",
            responses=error_responses(404, 409, 422))
def get_subtitle_text(
        drama_id: int = Path(ge=1),
        fmt: str = Query("srt", pattern="^(srt|vtt|lrc)$"),
        field: str = Query("en", pattern="^(en|zh|bilingual)$"),
        include_notes: bool = Query(False),
        wrap_chars_en: int = Query(None, ge=1, le=200),
        wrap_chars_source: int = Query(None, ge=1, le=200),
        tidy_timing: bool = Query(False)):
    result = export_service.generate_subtitle_export(
        drama_id, fmt, field, include_notes=include_notes,
        wrap_chars_en=wrap_chars_en, wrap_chars_source=wrap_chars_source, tidy_timing=tidy_timing)
    extra = {"X-Cue-Fps": f"{result['fps']:.3f}".rstrip("0").rstrip(".")} if result["fps"] else {}
    return Response(
        content=result["text"], media_type=_MEDIA_TYPES[fmt],
        headers={**extra, "Content-Disposition": export_service.subtitle_disposition(drama_id, field, fmt),
                 "X-Cues-Extended": str(result["cues_extended"]),
                 "X-Cues-Trimmed": str(result["cues_trimmed"]),
                 # Cue numbers, capped so a badly timed title can't make a huge header.
                 "X-Cues-Left-Long": ",".join(map(str, result["cues_left_long"][:_MAX_LONG_CUES_REPORTED]))})


@router.post("/dramas/{drama_id}/flag-overlaps", dependencies=[require_permission("lines.edit")], response_model=FlagActionResult,
            summary="Flag every currently-overlapping, not-yet-flagged line for review",
            responses=error_responses(404))
def post_flag_overlaps(drama_id: int = Path(ge=1)):
    return export_service.flag_overlapping_lines(drama_id)


@router.post("/dramas/{drama_id}/flag-dense-lines", dependencies=[require_permission("lines.edit")], response_model=FlagActionResult,
            summary="Flag every line too dense to read in its on-screen time",
            responses=error_responses(404))
def post_flag_dense_lines(drama_id: int = Path(ge=1)):
    return export_service.flag_dense_lines(drama_id)


@router.get("/dramas/{drama_id}/reading-speed", dependencies=[require_permission("lines.read")], response_model=ReadingSpeedMode,
            summary="The title's reading-speed check mode (normal, relaxed or off)",
            responses=error_responses(404))
def get_reading_speed(drama_id: int = Path(ge=1)):
    return export_service.get_reading_speed_mode(drama_id)


@router.post("/dramas/{drama_id}/reading-speed", dependencies=[require_permission("lines.edit")], response_model=ReadingSpeedMode,
             summary="Set the title's reading-speed check mode; existing flags are not changed",
             responses=error_responses(404, 422))
def post_reading_speed(payload: ReadingSpeedModeUpdate, drama_id: int = Path(ge=1)):
    return export_service.set_reading_speed_mode(drama_id, payload.mode)


@router.post("/dramas/{drama_id}/clear-reading-speed-flags", dependencies=[require_permission("lines.edit")],
             response_model=ClearReadingSpeedFlagsResult,
             summary="Clear only the reading-speed flags (snapshot first); recheck flags again at the current mode",
             responses=error_responses(404, 409))
def post_clear_reading_speed_flags(drama_id: int = Path(ge=1), recheck: bool = Query(False)):
    return export_service.clear_reading_speed_flags(drama_id, recheck)


@router.post("/dramas/{drama_id}/flag-auto-qc", dependencies=[require_permission("lines.edit")], response_model=AutoQcFlagResult,
            summary="Run Auto QC's factual-detail check and update flags in place",
            responses=error_responses(404))
def post_flag_auto_qc(drama_id: int = Path(ge=1)):
    return export_service.run_auto_qc_flagging(drama_id)


@router.get("/dramas/{drama_id}/epub", dependencies=[require_permission("lines.read")],
            summary="Generate an EPUB for one novel-narration drama (binary download)",
            responses=error_responses(400, 404, 422, 503))
def get_epub(drama_id: int = Path(ge=1), field: str = Query("en", pattern="^(en|zh)$")):
    data = export_service.generate_epub(drama_id, field=field)
    return Response(
        content=data, media_type="application/epub+zip",
        headers={"Content-Disposition": export_service.subtitle_disposition(drama_id, field, "epub", "ebook")})


@router.post("/dramas/{drama_id}/ass", dependencies=[require_permission("lines.read")],
             summary="Generate ASS subtitle text for one drama (plain-text download)",
             responses=error_responses(404, 422))
def post_ass_text(req: AssExportRequest, drama_id: int = Path(ge=1)):
    style = req.style.model_dump(exclude_unset=True) if req.style is not None else None
    text = export_service.generate_ass_text(
        drama_id, field=req.field, style=style, preset=req.preset,
        speaker_colors=req.speaker_colors, per_speaker_colors=req.per_speaker_colors,
        include_notes=req.include_notes, notes_as_separate_line=req.notes_as_separate_line,
        wrap_chars_en=req.wrap_chars_en, wrap_chars_source=req.wrap_chars_source)
    return Response(
        content=text, media_type="text/x-ssa; charset=utf-8",
        headers={"Content-Disposition": export_service.subtitle_disposition(drama_id, req.field, "ass")})


# Static path; every other route here lives under /dramas/..., so it can't be shadowed.
@router.get("/ass-style-options", dependencies=[require_permission("library.read")], response_model=AssStyleOptions,
            summary="Presets, fonts, alignments and ranges for ASS style controls")
def get_ass_style_options():
    return export_service.get_ass_style_options()


@router.post("/dramas/{drama_id}/audiobook", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the audiobook (.m4b) export job (poll GET /api/jobs/{job_id})",
             responses=error_responses(404, 409, 422, 503))
def post_audiobook(drama_id: int = Path(ge=1)):
    return media_export_service.start_audiobook_export(drama_id)


@router.post("/dramas/{drama_id}/burned-video", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the burned-in (hardsub ASS) video export job",
             responses=error_responses(404, 409, 422, 503))
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
             responses=error_responses(404, 409, 422, 503))
def post_softsub_video(drama_id: int = Path(ge=1), req: Optional[SoftsubVideoRequest] = None):
    req = req or SoftsubVideoRequest()
    return media_export_service.start_softsub_video_export(
        drama_id, field=req.field, include_notes=req.include_notes,
        subtitle_format=req.subtitle_format)


@router.post("/dramas/{drama_id}/dubbed-video", dependencies=[require_permission("jobs.start")], response_model=MediaExportStarted,
             summary="Start the dubbed video export job (dub audio replaces, or mixes over, the original)",
             responses=error_responses(404, 409, 422, 503))
def post_dubbed_video(drama_id: int = Path(ge=1), req: Optional[DubbedVideoRequest] = None):
    req = req or DubbedVideoRequest()
    return media_export_service.start_dubbed_video_export(drama_id, keep_original=req.keep_original)


@router.post("/dramas/{drama_id}/mark-exported", dependencies=[require_permission("admin.library")], response_model=MarkExportedResult,
             summary="Mark the drama as exported (sets its status only)",
             responses=error_responses(404))
def post_mark_exported(drama_id: int = Path(ge=1)):
    return export_service.mark_exported(drama_id)
