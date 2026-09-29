"""
api/routers/sources_import_routes.py -- importing from Sources into an
existing drama (Discover/Sources/Live spec S-4). Thin: see
services/sources_import_service.py.

Same /api/sources prefix as sources_catalog_routes.py and
sources_search_routes.py. `sources.import` (off by default for household
accounts): these fetch from this PC and write pages or text into a drama.
Every start is a background job; poll GET /api/sources/jobs/{job_id}/result
and cancel with POST /api/jobs/{job_id}/cancel.
"""

from fastapi import APIRouter, Path

from api.auth import require_permission
from api.schemas import ErrorResponse, SourcesChapterImportRequest, SourcesJobStarted
from services import sources_import_service as svc

router = APIRouter(prefix="/api/sources", tags=["sources"])

_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/{name}/import", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: import chosen chapters (by id) of one series into a drama",
             responses=_ERRS)
def post_chapter_import(body: SourcesChapterImportRequest,
                        name: str = Path(min_length=1, max_length=60)):
    return svc.start_chapter_import(name, body.series_id, body.chapter_ids, body.drama_id)
