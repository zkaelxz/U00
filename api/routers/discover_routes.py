"""
api/routers/discover_routes.py -- Discover known-titles catalog
(Migration Slice 55). Thin: see services/discover_catalog_service.py.
No network, no LLM.
"""

from fastapi import APIRouter, Path, Query
from api.auth import local_only, require_permission
from api.schemas import (DiscoverPlatforms, DiscoverSearchLinks, ErrorResponse, KnownTitle,
                         KnownTitleCreate, KnownTitleDelete, KnownTitleDeleted, KnownTitleList,
                         KnownTitleSeedResult)
from services import discover_catalog_service as svc

router = APIRouter(prefix="/api/discover", tags=["discover"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}


@router.get("/titles", dependencies=[require_permission("library.read")], response_model=KnownTitleList, summary="Search the known-titles catalog",
            responses=_ERRS)
def get_titles(q: str = Query("", max_length=200), language: str = Query("", max_length=10),
               media_type: str = Query("", max_length=40)):
    return svc.list_titles(q, language, media_type)


@router.post("/titles", dependencies=[require_permission("admin.library")], response_model=KnownTitle, status_code=201,
             summary="Add a known title manually", responses=_ERRS)
def post_title(body: KnownTitleCreate):
    return svc.create_title(body.model_dump())


@router.post("/titles/seed", dependencies=[require_permission("admin.library")], response_model=KnownTitleSeedResult,
             summary="Load the starter titles (idempotent)", responses=_ERRS)
def post_seed():
    return svc.seed_titles()


@router.post("/titles/{title_id}/delete", dependencies=[local_only()], response_model=KnownTitleDeleted,
             summary="Delete a known title (confirm=true)", responses=_ERRS)
def post_delete(body: KnownTitleDelete, title_id: int = Path(ge=1, le=svc.MAX_ID)):
    return svc.delete_title(title_id, body.confirm)


@router.post("/titles/{title_id}/import-to-library", dependencies=[require_permission("admin.library")], status_code=201,
             summary="Create a drama from a known title (409 if already imported)",
             responses=_ERRS)
def post_import(title_id: int = Path(ge=1, le=svc.MAX_ID)):
    return svc.import_to_library(title_id)


@router.get("/platforms", dependencies=[require_permission("library.read")], response_model=DiscoverPlatforms,
            summary="Known platforms (static list)", responses=_ERRS)
def get_platforms(language: str = Query("", max_length=10),
                  content_type: str = Query("", max_length=40)):
    return svc.list_platforms(language, content_type)


@router.get("/search-links", dependencies=[require_permission("library.read")], response_model=DiscoverSearchLinks,
            summary="Search links across platforms (built locally, no request made)",
            responses=_ERRS)
def get_search_links(q: str = Query("", max_length=200),
                     format: str = Query("", max_length=40)):
    return svc.search_links(q, format)
