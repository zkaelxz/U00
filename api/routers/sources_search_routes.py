"""
api/routers/sources_search_routes.py -- Sources search and series/chapter
listing (spec S-3; API batch 1). Thin: see services/sources_search_service.py.

Same /api/sources prefix as sources_catalog_routes.py; the paths here have
a different shape from its /{name}, /{name}/<action> routes, so neither
shadows the other. Both starts are paced background jobs; poll
GET /jobs/{job_id}/result, cancel with POST /api/jobs/{job_id}/cancel.

All three are `library.read` (the remote-access decision's "read and
search allowed"): nothing here writes the library, files or settings.
Importing chapters (spec S-4) is `sources.import`, in
sources_import_routes.py. The result route also serves the check-now job
and, only to a request from this PC, the sign-in and tier-test jobs
(sources_local_routes.py).
"""

from fastapi import APIRouter, Path, Request

from api.auth import is_local_request, require_permission
from api.schemas import (ErrorResponse, SourcesJobResult, SourcesJobStarted,
                         SourcesSearchRequest, SourcesSeriesRequest)
from services import sources_search_service as svc

router = APIRouter(prefix="/api/sources", tags=["sources"])

_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/search", dependencies=[require_permission("library.read")],
             response_model=SourcesJobStarted,
             summary="Job: search the enabled sources (409 if one is running)", responses=_ERRS)
def post_search(body: SourcesSearchRequest):
    return svc.start_search(body.query, body.sources)


@router.post("/{name}/series", dependencies=[require_permission("library.read")],
             response_model=SourcesJobStarted,
             summary="Job: one series' info and chapter list from a source", responses=_ERRS)
def post_series(request: Request, body: SourcesSeriesRequest,
                name: str = Path(min_length=1, max_length=60)):
    return svc.start_series(name, body.series_id, local=is_local_request(request))


@router.get("/jobs/{job_id}/result", dependencies=[require_permission("library.read")],
            response_model=SourcesJobResult,
            summary="A Sources job's status and result (this process only; idle when it has not run)",
            responses=_ERRS)
def get_job_result(request: Request, job_id: str = Path(min_length=1, max_length=100)):
    # Sign-in and tier-test jobs (PC-only) answer 404 to other devices.
    return svc.get_job_result(job_id, local=is_local_request(request),
                              principal=request.state.principal)
