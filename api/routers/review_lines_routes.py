"""
api/routers/review_lines_routes.py -- the Review stage's READ-ONLY line
views for one drama (Migration Slice 47): paginated/filtered line list,
transcript search, find-and-replace PREVIEW, coverage check, pacing check,
per-line provenance and original-transcript text.

Every route here only reads. The find-and-replace preview is a POST solely
because it takes a request body; it writes nothing. Lines are addressed by
permanent line id, never by position.

Out of scope (later slices): applying a replace or any other edit/flag/
restore, the media player and preview, translation-memory suggestions, LLM
tools, bulk modes, and history/versions/notes reads (Slice 48).
"""

from typing import List

from fastapi import APIRouter, Path, Query
from api.auth import require_permission
from api.schemas import (ErrorResponse, ReviewLinesCoverage, ReviewLinesFindReplaceRequest,
                         ReviewLinesLine, ReviewLinesMatch, ReviewLinesOriginalText,
                         ReviewLinesPacing, ReviewLinesPage, ReviewLinesProvenance)
from services import review_lines_service

router = APIRouter(prefix="/api/review", tags=["review"])

_404 = {404: {"model": ErrorResponse}}
_404_422 = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


@router.get("/dramas/{drama_id}/lines", dependencies=[require_permission("lines.read")], response_model=ReviewLinesPage,
            summary="One page of a drama's lines, optionally only flagged/untranslated",
            responses=_404_422)
def get_review_lines(drama_id: int = Path(ge=1), page: int = Query(1, ge=1),
                     page_size: int = Query(40, ge=1, le=200), only: str = "all"):
    return review_lines_service.list_review_lines(drama_id, page, page_size, only)


@router.get("/dramas/{drama_id}/search", dependencies=[require_permission("lines.read")], response_model=List[ReviewLinesLine],
            summary="Lines whose source or translation contains a term",
            responses=_404_422)
def search_review_lines(drama_id: int = Path(ge=1),
                        term: str = Query(..., min_length=1, max_length=500),
                        limit: int = Query(50, ge=1, le=200)):
    return review_lines_service.search_lines(drama_id, term, limit)


@router.post("/dramas/{drama_id}/find-replace/preview", dependencies=[require_permission("lines.read")], response_model=List[ReviewLinesMatch],
             summary="Preview a find-and-replace over translated text (writes nothing)",
             responses=_404_422)
def post_find_replace_preview(body: ReviewLinesFindReplaceRequest, drama_id: int = Path(ge=1)):
    """POST only because of the request body; nothing is applied or saved."""
    return review_lines_service.preview_find_replace(
        drama_id, body.find, body.replace, case_sensitive=body.case_sensitive,
        use_regex=body.use_regex)


@router.get("/dramas/{drama_id}/coverage", dependencies=[require_permission("lines.read")], response_model=ReviewLinesCoverage,
            summary="Coverage check: long lines, large gaps, blank source/translation",
            responses=_404)
def get_review_coverage(drama_id: int = Path(ge=1)):
    return review_lines_service.get_coverage_report(drama_id)


@router.get("/dramas/{drama_id}/pacing-flags", dependencies=[require_permission("lines.read")], response_model=ReviewLinesPacing,
            summary="Lines whose translation is a poor fit for their time slot",
            responses=_404)
def get_review_pacing_flags(drama_id: int = Path(ge=1)):
    return review_lines_service.get_pacing_flags(drama_id)


@router.get("/dramas/{drama_id}/lines/{line_id}/provenance", dependencies=[require_permission("lines.read")],
            response_model=ReviewLinesProvenance,
            summary="Read-only 'What happened here?' view for one line",
            responses=_404)
def get_review_line_provenance(drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    return review_lines_service.get_line_provenance(drama_id, line_id)


@router.get("/dramas/{drama_id}/lines/{line_id}/original-text", dependencies=[require_permission("lines.read")],
            response_model=ReviewLinesOriginalText,
            summary="What the latest raw transcription originally said for one line",
            responses=_404)
def get_review_line_original_text(drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    return review_lines_service.get_original_text(drama_id, line_id)
