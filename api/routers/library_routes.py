"""
api/routers/library_routes.py -- read-only Library endpoints.

Every route here is a thin adapter: parse/validate the HTTP request,
call `services.library_service`, convert the result into the contract
in `api/schemas.py`. No SQL, no filtering logic of its own -- that all
lives in the service, which the Streamlit Library tab calls too, so the
two UIs can't disagree about what a filter means.

Handlers are plain `def` (not `async def`) on purpose: `db.py` is
blocking `sqlite3`, and FastAPI runs a sync handler in its threadpool
instead of on the event loop.
"""

from typing import List, Optional

from fastapi import APIRouter, Path, Query

from api.schemas import DramaDetail, DramaListResponse, DramaSummary, ErrorResponse
from services import library_service

router = APIRouter(prefix="/api/library", tags=["library"])

_SUMMARY_FIELDS = tuple(f for f in DramaSummary.model_fields if f != "custom_tags")
_DETAIL_FIELDS = tuple(f for f in DramaDetail.model_fields
                       if f not in ("custom_tags", "has_audio", "has_novel_reference",
                                    "has_cover_art"))


def _to_summary(drama: dict) -> DramaSummary:
    return DramaSummary(**{f: drama.get(f) for f in _SUMMARY_FIELDS},
                        custom_tags=library_service.split_custom_tags(drama))


def _to_detail(drama: dict) -> DramaDetail:
    return DramaDetail(**{f: drama.get(f) for f in _DETAIL_FIELDS},
                       custom_tags=library_service.split_custom_tags(drama),
                       has_audio=bool(drama.get("audio_filename")),
                       has_novel_reference=bool(drama.get("novel_reference_filename")),
                       has_cover_art=bool(drama.get("cover_art_filename")))


@router.get("/dramas", response_model=DramaListResponse,
            summary="List dramas in the library (the Library tab's 'All dramas' list)",
            responses={422: {"model": ErrorResponse}})
def list_dramas(
        search: str = Query("", max_length=200, description="Substring of title or summary."),
        studio: str = Query("", max_length=200),
        author: str = Query("", max_length=200),
        voice_actor: str = Query("", max_length=200),
        status: str = Query("", max_length=50),
        source_language: str = Query("", max_length=10),
        media_type: str = Query("", max_length=50),
        quick_filter: Optional[str] = Query(
            None, description="One of: Favorite, On Hold, Plan to Translate."),
        tag: List[str] = Query([], description="Repeatable; a drama must carry every tag.")):
    dramas = library_service.list_library_dramas(
        search=search, studio=studio, author=author, voice_actor=voice_actor, status=status,
        source_language=source_language, media_type=media_type,
        quick_filter=quick_filter, custom_tags=tag)
    items = [_to_summary(d) for d in dramas]
    return DramaListResponse(items=items, count=len(items))


@router.get("/dramas/{drama_id}", response_model=DramaDetail, summary="One drama's details",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def get_drama(drama_id: int = Path(ge=1)):
    return _to_detail(library_service.get_library_drama(drama_id))
