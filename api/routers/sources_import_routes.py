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

The novel URL import takes an opt-in AI fallback (`use_ai`, `engine`;
parity SO09): the engine name is checked and `engines.paid` required for a
paid one before the job starts.
`follow_pages` (default 1) also follows next-chapter links on the same
site into a review of the pages read; nothing is written until the person
imports the pages they keep (POST .../extraction/import with `pages`).

POST /{name}/import/{chapter_id}/ai-recover: after an import stopped on a
chapter whose page layout no longer matches the adapter ("needs_ai"), the
person confirms one AI call (engine required; `engines.paid` for a paid
one). The job ends in a Review extraction; nothing is written until the
review is imported.

POST /{name}/save writes chosen chapters of a comic series to
`<data dir>/saved_comics/` as CBZ files, one save at a time (job
`sources_save`); no drama is involved and no path is returned. The save
folder and reading what was saved live in saved_comics_routes.py.

GET /{name}/import-state reads sources.db only: which chapters
of a series are already in a drama, and which the last imports left
failed or not attempted, so the picker can mark them and offer a retry.

A pasted URL is checked (public address) in the request, before any job
starts. From another device (not `is_local_request`) the fetch uses static
HTTP only: no signed-in profile and no browser.
"""

from fastapi import APIRouter, Path, Query, Request

from api.auth import is_local_request, require_engines_allowed, require_permission
from api.schemas import (ErrorResponse, SourcesChapterImportRequest, SourcesChapterSaveRequest,
                         SourcesJobStarted, SourcesUrlPreviewRequest)
from api.sources_extraction_schemas import (SourcesAiRecoverRequest,
                                            SourcesUrlImportFollowRequest)
from api.sources_import_schemas import SourcesImportState
from services import sources_extraction_service as extraction
from services import sources_import_service as svc
from services import sources_save_service as save_svc
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
def post_url_import(body: SourcesUrlImportFollowRequest, request: Request):
    engine = extraction.resolve_ai_engine_name(body.use_ai, body.engine)
    if engine is not None:
        require_engines_allowed(request, engine)
    return svc.start_url_import(body.url, body.drama_id, local=is_local_request(request),
                                principal=request.state.principal, ai_engine=engine,
                                review=body.review, follow_pages=body.follow_pages)


@router.post("/{name}/import", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: import chosen chapters (by id) of one series into a drama",
             responses=_ERRS)
def post_chapter_import(body: SourcesChapterImportRequest, request: Request,
                        name: str = Path(min_length=1, max_length=60)):
    return svc.start_chapter_import(name, body.series_id, body.chapter_ids, body.drama_id,
                                    principal=request.state.principal)


@router.post("/{name}/save", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: save chosen chapters (by id) of one comic series as CBZ files on this PC",
             responses=_ERRS)
def post_chapter_save(body: SourcesChapterSaveRequest,
                      name: str = Path(min_length=1, max_length=60)):
    return save_svc.start_chapter_save(name, body.series_id, body.chapter_ids)


@router.post("/{name}/import/{chapter_id}/ai-recover",
             dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: read one chapter whose page layout changed with one AI call, "
                     "into a review",
             responses=_ERRS)
def post_ai_recover(body: SourcesAiRecoverRequest, request: Request,
                    name: str = Path(min_length=1, max_length=60),
                    chapter_id: str = Path(min_length=1, max_length=200)):
    engine = extraction.resolve_ai_engine_name(True, body.engine)
    require_engines_allowed(request, engine)
    return svc.start_ai_recover(name, chapter_id, body.series_id, body.drama_id, engine,
                                body.confirm, principal=request.state.principal)


@router.get("/{name}/import-state", dependencies=[require_permission("sources.import")],
            response_model=SourcesImportState,
            summary="Chapters of a series already imported into a drama, and ones to retry",
            responses=_ERRS)
def get_import_state(request: Request, name: str = Path(min_length=1, max_length=60),
                     series_id: str = Query(min_length=1, max_length=200),
                     drama_id: int = Query(ge=1)):
    return svc.get_import_state(name, series_id, drama_id, principal=request.state.principal)
