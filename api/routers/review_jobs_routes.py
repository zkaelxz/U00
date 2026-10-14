"""
api/routers/review_jobs_routes.py -- Review-stage AI jobs for one drama:
consistency check, emotion tagging, translation notes,
flag pass and fix-flagged. Each starts a background job that does everything
itself (field-scoped DB writes); progress is read via /api/jobs. Logic lives
in services/review_jobs_service.py.
"""

from fastapi import APIRouter, Path, Request
from api.auth import is_at_pc, require_engines_allowed, require_permission
from api.schemas import (EmotionJobStart, FixFlaggedJobStart,
                         ReviewJobStart, ReviewJobStarted, error_responses)
from services import model_readiness_service, review_jobs_service

router = APIRouter(prefix="/api/review-jobs", tags=["review-jobs"])

_ERRORS = error_responses(400, 404, 409, 422, 503)


@router.post("/dramas/{drama_id}/consistency", dependencies=[require_permission("jobs.start")], response_model=ReviewJobStarted,
             summary="Start the translation consistency check", responses=_ERRORS)
def start_consistency(body: ReviewJobStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine, model=body.model)
    return review_jobs_service.start_consistency_check(
        drama_id, body.engine, body.model, body.gemini_free_tier, bulk=body.bulk)


@router.post("/dramas/{drama_id}/emotion", dependencies=[require_permission("jobs.start")], response_model=ReviewJobStarted,
             summary="Start emotional-register tagging", responses=_ERRORS)
def start_emotion(body: EmotionJobStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine, model=body.model)
    return review_jobs_service.start_emotion_tagging(
        drama_id, body.engine, body.model, body.gemini_free_tier, body.use_audio_cues,
        bulk=body.bulk)


@router.post("/dramas/{drama_id}/notes", dependencies=[require_permission("jobs.start")], response_model=ReviewJobStarted,
             summary="Start translation-notes generation", responses=_ERRORS)
def start_notes(body: ReviewJobStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine, model=body.model)
    return review_jobs_service.start_translation_notes(
        drama_id, body.engine, body.model, body.gemini_free_tier, bulk=body.bulk)


@router.post("/dramas/{drama_id}/flag", dependencies=[require_permission("jobs.start")], response_model=ReviewJobStarted,
             summary="Start the 'needs a second look' flag pass", responses=_ERRORS)
def start_flag(body: ReviewJobStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine, model=body.model)
    return review_jobs_service.start_flag_review(
        drama_id, body.engine, body.model, body.gemini_free_tier, bulk=body.bulk)


@router.post("/dramas/{drama_id}/fix-flagged", dependencies=[require_permission("jobs.start")], response_model=ReviewJobStarted,
             summary="Re-transcribe and re-translate flagged lines", responses=_ERRORS)
def start_fix_flagged(body: FixFlaggedJobStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine, model=body.model)
    model_readiness_service.require_ready(
        is_at_pc(request), model_readiness_service.for_fix_flagged, drama_id)
    return review_jobs_service.start_fix_flagged(
        drama_id, body.engine, body.model, body.gemini_free_tier, body.job_cost_cap_usd,
        include_genre_notes=body.include_genre_notes,
        default_female_pronouns=body.default_female_pronouns)
