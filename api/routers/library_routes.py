"""
api/routers/library_routes.py -- Library endpoints (reads, plus preset/voice-bank rename).

Every route here is a thin adapter: parse/validate the HTTP request,
call `services.library_service`, convert the result into the contract
in `api/schemas/`. No SQL, no filtering logic of its own -- that all
lives in the service, so every caller agrees on what a filter means.

Handlers are plain `def` (not `async def`) on purpose: `db.py` is
blocking `sqlite3`, and FastAPI runs a sync handler in its threadpool
instead of on the event loop.
"""

from typing import List, Optional

from fastapi import APIRouter, Path, Query, Request
from api.auth import require_permission
from api.schemas import (
    DramaDetail, DramaListResponse, DramaSummary, ErrorResponse, LibraryCostResponse,
    LibraryContinueResponse, LibraryDashboard, LibraryFilterOptions, LibraryHistoryResponse, LibraryPreset, LibraryPresetsResponse,
    LibraryRecentResponse, LibraryRename, LibrarySearchResponse, LibrarySeriesResponse,
    LibraryVoice, LibraryVoiceBankResponse)
from services import library_service

router = APIRouter(prefix="/api/library", tags=["library"])

_SUMMARY_FIELDS = tuple(f for f in DramaSummary.model_fields
                        if f not in ("custom_tags", "is_private", "owned_by_me"))
_DETAIL_FIELDS = tuple(f for f in DramaDetail.model_fields
                       if f not in ("custom_tags", "is_private", "owned_by_me", "has_audio", "has_novel_reference",
                                    "has_cover_art", "source_url"))


def _to_summary(drama: dict) -> DramaSummary:
    return DramaSummary(**{f: drama.get(f) for f in _SUMMARY_FIELDS},
                        is_private=drama.get("sharing_private"),
                        owned_by_me=drama.get("sharing_owned"),
                        custom_tags=library_service.split_custom_tags(drama))


def to_detail(drama: dict) -> DramaDetail:
    return DramaDetail(**{f: drama.get(f) for f in _DETAIL_FIELDS},
                       custom_tags=library_service.split_custom_tags(drama),
                       # Never the query: a pasted download link can carry a token.
                       source_url=library_service.display_source_url(drama.get("source_url")) or None,
                       has_audio=bool(drama.get("audio_filename")),
                       has_novel_reference=bool(drama.get("novel_reference_filename")),
                       has_cover_art=bool(drama.get("cover_art_filename")))


@router.get("/dramas", dependencies=[require_permission("library.read")], response_model=DramaListResponse,
            summary="List dramas in the library (the Library page's 'All dramas' list)",
            responses={422: {"model": ErrorResponse}})
def list_dramas(
        request: Request,
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
        quick_filter=quick_filter, custom_tags=tag, principal=request.state.principal)
    items = [_to_summary(d) for d in dramas]
    return DramaListResponse(items=items, count=len(items))


@router.get("/dramas/{drama_id}", dependencies=[require_permission("library.read")], response_model=DramaDetail, summary="One drama's details",
            responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def get_drama(drama_id: int = Path(ge=1)):
    return to_detail(library_service.get_library_drama(drama_id))


_ERR = {422: {"model": ErrorResponse}}
_ERR_WRITE = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
              422: {"model": ErrorResponse}}


@router.get("/stats", dependencies=[require_permission("library.read")], response_model=LibraryDashboard, summary="Dashboard counts and spend")
def get_stats(request: Request):
    return library_service.get_library_dashboard(principal=request.state.principal)


@router.get("/recent", dependencies=[require_permission("library.read")], response_model=LibraryRecentResponse, responses=_ERR,
            summary="Recently active dramas")
def get_recent(request: Request, limit: int = Query(8, ge=1, le=50)):
    return {"items": library_service.list_recently_active(
        limit, principal=request.state.principal)}


@router.get("/costs", dependencies=[require_permission("library.read")], response_model=LibraryCostResponse, summary="Cost breakdown by drama")
def get_costs(request: Request):
    return {"items": library_service.list_cost_by_drama(principal=request.state.principal)}


@router.get("/series", dependencies=[require_permission("library.read")], response_model=LibrarySeriesResponse,
            summary="Series that contain two or more dramas")
def get_series(request: Request):
    return {"items": library_service.list_series_with_dramas(principal=request.state.principal)}


@router.get("/search", dependencies=[require_permission("library.read")], response_model=LibrarySearchResponse, responses=_ERR,
            summary="Search line text across every drama")
def search(request: Request, q: str = Query(min_length=1, max_length=200),
           limit: int = Query(50, ge=1, le=100)):
    return library_service.search_lines(q, limit, principal=request.state.principal)


@router.get("/history", dependencies=[require_permission("library.read")], response_model=LibraryHistoryResponse, responses=_ERR,
            summary="Reading history (default profile)")
def get_history(request: Request, limit: int = Query(25, ge=1, le=100)):
    return {"items": library_service.list_history(limit, principal=request.state.principal)}


@router.get("/continue", dependencies=[require_permission("library.read")], response_model=LibraryContinueResponse,
            responses=_ERR, summary="Continue reading: partly-read dramas, most recent first")
def get_continue(request: Request, limit: int = Query(8, ge=1, le=50)):
    return {"items": library_service.list_continue_reading(limit, principal=request.state.principal)}


@router.get("/filter-options", dependencies=[require_permission("library.read")], response_model=LibraryFilterOptions,
            summary="Studios, authors, voice actors and custom tags in use (the list filters' choices)")
def get_filter_options(request: Request):
    return library_service.get_filter_options(principal=request.state.principal)


@router.get("/presets", dependencies=[require_permission("library.read")], response_model=LibraryPresetsResponse, summary="Saved presets")
def get_presets():
    return {"items": library_service.list_presets()}


@router.post("/presets/{preset_id}/rename", dependencies=[require_permission("admin.library")], response_model=LibraryPreset,
             responses=_ERR_WRITE, summary="Rename a preset")
def rename_preset(body: LibraryRename, preset_id: int = Path(ge=1, le=2**31 - 1)):
    return library_service.rename_preset(preset_id, body.name)


@router.get("/voice-bank", dependencies=[require_permission("library.read")], response_model=LibraryVoiceBankResponse,
            summary="Voice bank entries (no file paths)")
def get_voice_bank():
    return {"items": library_service.list_voice_bank()}


@router.post("/voice-bank/{entry_id}/rename", dependencies=[require_permission("admin.library")], response_model=LibraryVoice,
             responses=_ERR_WRITE, summary="Rename a voice bank entry")
def rename_voice(body: LibraryRename, entry_id: int = Path(ge=1, le=2**31 - 1)):
    return library_service.rename_voice_bank_entry(entry_id, body.name)
