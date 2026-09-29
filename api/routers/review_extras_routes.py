"""
api/routers/review_extras_routes.py -- the Review stage's optional extras
(inventory R46, R37, R35, R03): merge short adjacent lines (read-only preview,
then apply with a stale-id/stale-plan guard and a history snapshot), learn my
style (one synchronous LLM call; paid-engine gate and the shared LLM slot),
SenseVoice audio tags (a job plus the side-by-side rows) and a burned-subtitle
preview clip around one line (a job, then Range playback of the fixed-name
clip). Thin wrapper over services/review_extras_service.py.
"""

from typing import Optional

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import FileResponse

from api.auth import require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.schemas import (BurnPreviewInfo, BurnPreviewStart, BurnPreviewStarted, ErrorResponse,
                         MergeShortApply, MergeShortPreview, MergeShortResult, SenseVoiceStarted,
                         SenseVoiceTags, StyleApplyRequest, StyleLearnRequest, StyleResetRequest,
                         StyleState)
from services import review_extras_service as svc
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/review-extras", tags=["review-extras"])

_R = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
      409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
      503: {"model": ErrorResponse}}


# --- R46: merge short adjacent lines ---------------------------------------

@router.get("/dramas/{drama_id}/merge-short/preview", dependencies=[require_permission("lines.read")],
            response_model=MergeShortPreview,
            summary="Preview merging short adjacent lines (read-only)", responses=_R)
def get_merge_short_preview(drama_id: int = Path(ge=1),
                            min_duration: Optional[float] = Query(None, ge=0.1, le=10),
                            max_gap: Optional[float] = Query(None, ge=0, le=5),
                            max_chars: Optional[int] = Query(None, ge=10, le=500)):
    return svc.preview_merge_short(drama_id, min_duration, max_gap, max_chars)


@router.post("/dramas/{drama_id}/merge-short/apply", dependencies=[require_permission("lines.edit")],
             response_model=MergeShortResult,
             summary="Merge short adjacent lines as previewed (snapshot first)", responses=_R)
def post_merge_short_apply(body: MergeShortApply, drama_id: int = Path(ge=1)):
    return svc.apply_merge_short(drama_id, body.expected_line_ids, body.expected_groups,
                                 body.min_duration, body.max_gap, body.max_chars)


# --- R37: learn my style -----------------------------------------------------

@router.get("/dramas/{drama_id}/style", dependencies=[require_permission("review.use")],
            response_model=StyleState, summary="The learned style profile for this drama's scope",
            responses={404: {"model": ErrorResponse}})
def get_style(drama_id: int = Path(ge=1)):
    return svc.get_style(drama_id)


@router.post("/dramas/{drama_id}/style/learn", dependencies=[require_permission("jobs.start")],
             response_model=StyleState,
             summary="Learn style preferences from recorded edits (one LLM call)",
             responses={**_R, 429: {"model": ErrorResponse}})
def post_style_learn(body: StyleLearnRequest, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    with llm_slot(request):
        return svc.learn_style(drama_id, body.engine, body.model, body.gemini_free_tier)


@router.post("/dramas/{drama_id}/style/apply", dependencies=[require_permission("lines.edit")],
             response_model=StyleState,
             summary="Turn the learned profile on or off for future translations", responses=_R)
def post_style_apply(body: StyleApplyRequest, drama_id: int = Path(ge=1)):
    return svc.set_style_applied(drama_id, body.apply)


@router.post("/dramas/{drama_id}/style/reset", dependencies=[require_permission("lines.edit")],
             response_model=StyleState, summary="Forget the learned profile (confirm=true)",
             responses=_R)
def post_style_reset(body: StyleResetRequest, drama_id: int = Path(ge=1)):
    if body.confirm is not True:
        raise InvalidInputError("Resetting the learned style needs confirm=true.")
    return svc.reset_style(drama_id)


# --- R35: SenseVoice audio tags ---------------------------------------------

@router.post("/dramas/{drama_id}/sensevoice", dependencies=[require_permission("jobs.start")],
             response_model=SenseVoiceStarted,
             summary="Start SenseVoice emotion/sound tagging (poll /api/jobs/{job_id})",
             responses=_R)
def post_sensevoice(drama_id: int = Path(ge=1)):
    return svc.start_sensevoice(drama_id)


@router.get("/dramas/{drama_id}/sensevoice", dependencies=[require_permission("lines.read")],
            response_model=SenseVoiceTags,
            summary="Text-based vs audio tags per line, side by side",
            responses={404: {"model": ErrorResponse}})
def get_sensevoice(drama_id: int = Path(ge=1)):
    return svc.get_sensevoice(drama_id)


# --- R03: burned-subtitle preview clip --------------------------------------

@router.post("/dramas/{drama_id}/burn-preview", dependencies=[require_permission("jobs.start")],
             response_model=BurnPreviewStarted,
             summary="Render a short burned-subtitle clip around one line (a job)",
             responses=_R)
def post_burn_preview(body: BurnPreviewStart, drama_id: int = Path(ge=1)):
    return svc.start_burn_preview(drama_id, body.line_id, body.pad_seconds, body.preset)


@router.get("/dramas/{drama_id}/burn-preview/info", dependencies=[require_permission("lines.read")],
            response_model=BurnPreviewInfo,
            summary="Whether a preview clip exists (and for which line), presets, limits",
            responses={404: {"model": ErrorResponse}})
def get_burn_preview_info(drama_id: int = Path(ge=1)):
    return svc.get_burn_preview_info(drama_id)


@router.get("/dramas/{drama_id}/burn-preview/clip", dependencies=[require_permission("media.stream")],
            summary="Stream the rendered preview clip with HTTP Range support",
            responses={200: {"content": {"video/mp4": {}}}, 206: {"content": {"video/mp4": {}}},
                       404: {"model": ErrorResponse},
                       416: {"description": "Range not satisfiable"}})
def get_burn_preview_clip(drama_id: int = Path(ge=1)):
    path = svc.preview_clip_path(drama_id)
    # FileResponse streams in chunks and implements Range (206/416); generic name.
    return FileResponse(path, media_type="video/mp4", filename=f"drama_{drama_id}_preview.mp4",
                        content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
