"""
api/routers/review_records_routes.py -- Review-stage READ-ONLY records for
one drama (Migration Slice 48): line history, translation versions
(list/compare), translation notes (list/Markdown), stored consistency
issues, emotion summary, stored edit tendencies, and translation-memory
suggestions. See services/review_records_service.py.

Out of scope (each its own later slice): every write (restore/activate/
delete/add/dismiss), LLM analysis (consistency check, emotion detection,
"learn my style", notes generation), background-job starters, bulk modes.

Static paths (`/versions/compare`, `/notes/markdown`) are declared before
any `/{id}` path in the same prefix.
"""

from typing import List, Optional

from fastapi import APIRouter, Path, Query, Response

from api.schemas import (ErrorResponse, ReviewRecordsCompare, ReviewRecordsConsistencyIssue,
                         ReviewRecordsEmotionSummary, ReviewRecordsHistoryItem,
                         ReviewRecordsNote, ReviewRecordsSnapshot, ReviewRecordsTendencies,
                         ReviewRecordsTmSuggestion, ReviewRecordsVersionItem)
from services import review_records_service

router = APIRouter(prefix="/api/review", tags=["review"])

_NF = {404: {"model": ErrorResponse}}
_NF_422 = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}


@router.get("/dramas/{drama_id}/history", response_model=List[ReviewRecordsHistoryItem],
            summary="Line-history snapshots (metadata only), newest first",
            responses=_NF)
def get_history(drama_id: int = Path(ge=1)):
    return review_records_service.list_line_history(drama_id)


@router.get("/dramas/{drama_id}/history/{history_id}", response_model=ReviewRecordsSnapshot,
            summary="One history snapshot's lines (404 if it belongs to another drama)",
            responses=_NF_422)
def get_history_snapshot(drama_id: int = Path(ge=1), history_id: int = Path(ge=1)):
    return review_records_service.get_line_history_snapshot(drama_id, history_id)


@router.get("/dramas/{drama_id}/versions", response_model=List[ReviewRecordsVersionItem],
            summary="Translation versions (metadata only), newest first",
            responses=_NF)
def get_versions(drama_id: int = Path(ge=1)):
    return review_records_service.list_translation_versions(drama_id)


@router.get("/dramas/{drama_id}/versions/compare", response_model=ReviewRecordsCompare,
            summary="Differences between two of this drama's translation versions",
            responses=_NF_422)
def get_versions_compare(drama_id: int = Path(ge=1),
                         left_id: int = Query(..., ge=1),
                         right_id: int = Query(..., ge=1)):
    return review_records_service.compare_versions(drama_id, left_id, right_id)


@router.get("/dramas/{drama_id}/notes", response_model=List[ReviewRecordsNote],
            summary="Translation notes, in line order",
            responses=_NF)
def get_notes(drama_id: int = Path(ge=1)):
    return review_records_service.list_translation_notes(drama_id)


@router.get("/dramas/{drama_id}/notes/markdown",
            summary="Translation notes as Markdown text (served inline, not as a download)",
            responses=_NF)
def get_notes_markdown(drama_id: int = Path(ge=1)):
    """Inline text/markdown; no Content-Disposition, so a client decides
    whether to save it (unlike the export routes' file downloads)."""
    text = review_records_service.get_notes_markdown(drama_id)
    return Response(content=text, media_type="text/markdown; charset=utf-8")


@router.get("/dramas/{drama_id}/consistency", response_model=List[ReviewRecordsConsistencyIssue],
            summary="Stored results of the last consistency check (not re-run)",
            responses=_NF)
def get_consistency(drama_id: int = Path(ge=1)):
    return review_records_service.get_consistency_issues(drama_id)


@router.get("/dramas/{drama_id}/emotions", response_model=ReviewRecordsEmotionSummary,
            summary="Stored emotion tags with local counts",
            responses=_NF)
def get_emotions(drama_id: int = Path(ge=1)):
    return review_records_service.get_emotion_summary(drama_id)


@router.get("/dramas/{drama_id}/tendencies", response_model=ReviewRecordsTendencies,
            summary="Recorded edit statistics plus the stored learned style profile (or null)",
            responses=_NF)
def get_tendencies(drama_id: int = Path(ge=1)):
    return review_records_service.get_edit_tendencies(drama_id)


@router.get("/dramas/{drama_id}/tm-suggestions", response_model=List[ReviewRecordsTmSuggestion],
            summary="Translation-memory suggestions (nothing is applied)",
            responses=_NF_422)
def get_tm_suggestions(drama_id: int = Path(ge=1),
                       line_id: Optional[List[int]] = Query(None)):
    return review_records_service.list_tm_suggestions(drama_id, line_ids=line_id)
