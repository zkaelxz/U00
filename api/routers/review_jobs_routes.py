"""
api/routers/review_jobs_routes.py -- Review-stage AI jobs for one drama
(Migration Slice 44): consistency check, emotion tagging, translation notes,
flag pass and fix-flagged. Each starts a background job that does everything
itself (field-scoped DB writes); progress is read via /api/jobs. Logic lives
in services/review_jobs_service.py.
"""

from fastapi import APIRouter, Path

from api.schemas import (EmotionJobStart, ErrorResponse, FixFlaggedJobStart,
                         ReviewJobStart, ReviewJobStarted)
from services import review_jobs_service

router = APIRouter(prefix="/api/review-jobs", tags=["review-jobs"])

_ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
           409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
           503: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/consistency", response_model=ReviewJobStarted,
             summary="Start the translation consistency check", responses=_ERRORS)
def start_consistency(body: ReviewJobStart, drama_id: int = Path(ge=1)):
    return review_jobs_service.start_consistency_check(
        drama_id, body.engine, body.model, body.gemini_free_tier)


@router.post("/dramas/{drama_id}/emotion", response_model=ReviewJobStarted,
             summary="Start emotional-register tagging", responses=_ERRORS)
def start_emotion(body: EmotionJobStart, drama_id: int = Path(ge=1)):
    return review_jobs_service.start_emotion_tagging(
        drama_id, body.engine, body.model, body.gemini_free_tier, body.use_audio_cues)


@router.post("/dramas/{drama_id}/notes", response_model=ReviewJobStarted,
             summary="Start translation-notes generation", responses=_ERRORS)
def start_notes(body: ReviewJobStart, drama_id: int = Path(ge=1)):
    return review_jobs_service.start_translation_notes(
        drama_id, body.engine, body.model, body.gemini_free_tier)


@router.post("/dramas/{drama_id}/flag", response_model=ReviewJobStarted,
             summary="Start the 'needs a second look' flag pass", responses=_ERRORS)
def start_flag(body: ReviewJobStart, drama_id: int = Path(ge=1)):
    return review_jobs_service.start_flag_review(
        drama_id, body.engine, body.model, body.gemini_free_tier)


@router.post("/dramas/{drama_id}/fix-flagged", response_model=ReviewJobStarted,
             summary="Re-transcribe and re-translate flagged lines", responses=_ERRORS)
def start_fix_flagged(body: FixFlaggedJobStart, drama_id: int = Path(ge=1)):
    return review_jobs_service.start_fix_flagged(
        drama_id, body.engine, body.model, body.gemini_free_tier, body.job_cost_cap_usd)
