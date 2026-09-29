"""
api/routers/sources_import_routes.py -- importing from Sources into an
existing drama (Discover/Sources/Live specs S-4 and S-5). Thin: see
services/sources_import_service.py and services/sources_url_service.py.

Same /api/sources prefix as sources_catalog_routes.py and
sources_search_routes.py. `sources.import` (off by default for household
accounts): these fetch from this PC, and the imports write pages or text
into a drama. Every start is a background job; poll
GET /api/sources/jobs/{job_id}/result and cancel with
POST /api/jobs/{job_id}/cancel.

/url/preview and /url/import are declared BEFORE /{name}/import, so
"/url/import" is never read as a source named "url".

A pasted URL is checked (public address) in the request, before any job
starts. From another device (not `is_local_request`) the fetch uses static
HTTP only: no signed-in profile and no browser.
"""

from fastapi import APIRouter, Path, Request

from api.auth import is_local_request, require_permission
from api.schemas import (ErrorResponse, SourcesChapterImportRequest, SourcesJobStarted,
                         SourcesUrlImportRequest, SourcesUrlPreviewRequest)
from services import sources_import_service as svc
from services import sources_url_service as url_svc

router = APIRouter(prefix="/api/sources", tags=["sources"])

_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/url/preview", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: what a pasted URL is (type, platform, title, chapter, counts)",
             responses=_ERRS)
def post_url_preview(body: SourcesUrlPreviewRequest, request: Request):
    return url_svc.start_preview(body.url, local=is_local_request(request))


@router.post("/url/import", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: append a pasted URL's novel text to a novel drama",
             responses=_ERRS)
def post_url_import(body: SourcesUrlImportRequest, request: Request):
    return svc.start_url_import(body.url, body.drama_id, local=is_local_request(request))


@router.post("/{name}/import", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: import chosen chapters (by id) of one series into a drama",
             responses=_ERRS)
def post_chapter_import(body: SourcesChapterImportRequest,
                        name: str = Path(min_length=1, max_length=60)):
    return svc.start_chapter_import(name, body.series_id, body.chapter_ids, body.drama_id)
