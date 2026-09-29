"""
api/routers/sources_extraction_routes.py -- the pasted-URL extraction
extras (Streamlit Sources parity SO09, SO06). Thin: see
services/sources_extraction_service.py and, for the comic import job,
services/sources_import_service.py.

`POST /url/import-comic` (SO06) is `sources.import` like the novel URL
import: it fetches from this PC and adds pages to a drama. The URL is
checked (public address) in the request; a request not from this PC gets
static HTTP only. The AI fallback is opt-in, as on /url/import.

Same /api/sources prefix as the other Sources routers. The paths here have
a fixed first segment ("url/..."), so none can be read as a source
`{name}`.
"""

from fastapi import APIRouter, Request

from api.auth import is_local_request, require_engines_allowed, require_permission
from api.schemas import ErrorResponse, SourcesJobStarted
from api.sources_extraction_schemas import SourcesAiEngines, SourcesComicUrlImportRequest
from services import sources_extraction_service as svc
from services import sources_import_service as import_svc

router = APIRouter(prefix="/api/sources", tags=["sources"])

_ERRS = {400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
         404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}


def ai_engine_for(request: Request, use_ai, engine):
    """The requested fallback engine name (None = off), with `engines.paid`
    required for a paid one."""
    name = svc.resolve_ai_engine_name(use_ai, engine)
    if name is not None:
        require_engines_allowed(request, name)
    return name


@router.get("/url/ai-engines", dependencies=[require_permission("sources.import")],
            response_model=SourcesAiEngines,
            summary="Engines the pasted-URL AI fallback can use, and the saved default")
def get_ai_engines():
    return svc.engines_view()


@router.post("/url/import-comic", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: add a pasted URL's comic pages to a manhua/manga/manhwa drama",
             responses=_ERRS)
def post_comic_url_import(body: SourcesComicUrlImportRequest, request: Request):
    return import_svc.start_comic_url_import(
        body.url, body.drama_id, local=is_local_request(request),
        principal=request.state.principal,
        ai_engine=ai_engine_for(request, body.use_ai, body.engine))
