"""
api/routers/sources_extraction_routes.py -- the pasted-URL extraction
extras (Streamlit Sources parity SO09, SO06, SO10). Thin: see
services/sources_extraction_service.py and, for the comic import job,
services/sources_import_service.py.

`POST /url/import-comic` (SO06) is `sources.import` like the novel URL
import: it fetches from this PC and adds pages to a drama. The URL is
checked (public address) in the request; a request not from this PC gets
static HTTP only. The AI fallback is opt-in, as on /url/import.

Review extraction (SO10) lives under /dramas/{drama_id}/extraction, so the
path guard hides a drama the caller can't see (404). Reading the review,
re-running it with corrections, its image thumbnails and importing it are
`sources.import`, like the import that opened it (re-running reads only the
page already fetched; importing writes the same append-only text/pages).
Saving the corrections as the site's profile and approving a suggested
profile are `local_only()`: a site profile changes how every later import
from that site is read, a settings-like write, and settings writes are
PC-only (docs/remote-access-decision.md).

Same /api/sources prefix as the other Sources routers. The paths here have
a fixed first segment ("url/..."), so none can be read as a source
`{name}`.
"""

from fastapi import APIRouter, Path, Request
from fastapi.responses import Response

from api.auth import (is_local_request, local_only, require_engines_allowed,
                      require_permission)
from api.schemas import ErrorResponse, SourcesJobStarted
from api.sources_extraction_schemas import (ExtractionComicRerunRequest,
                                            ExtractionNovelRerunRequest,
                                            ExtractionProfileSaved, ExtractionReview,
                                            ExtractionRevisionRequest, SourcesAiEngines,
                                            SourcesComicUrlImportRequest)
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
        ai_engine=ai_engine_for(request, body.use_ai, body.engine), review=body.review)


# ----- SO10: Review extraction ------------------------------------------------

_REVIEW = "/dramas/{drama_id}/extraction"


@router.get(_REVIEW, dependencies=[require_permission("sources.import")],
            response_model=ExtractionReview, responses=_ERRS,
            summary="The extraction waiting for review for this drama (404: none)")
def get_review(request: Request, drama_id: int = Path(ge=1)):
    return svc.review_view(drama_id, principal=request.state.principal,
                           local=is_local_request(request))


@router.post(_REVIEW + "/rerun-novel", dependencies=[require_permission("sources.import")],
             response_model=ExtractionReview, responses=_ERRS,
             summary="Re-run a novel extraction with the chosen parts of the page")
def post_rerun_novel(body: ExtractionNovelRerunRequest, request: Request,
                     drama_id: int = Path(ge=1)):
    return svc.rerun_novel(drama_id, body.revision, body.content_selector,
                           body.exclude_selectors, body.title_block, body.next_link,
                           body.previous_link, body.number_from,
                           principal=request.state.principal, local=is_local_request(request))


@router.post(_REVIEW + "/rerun-comic", dependencies=[require_permission("sources.import")],
             response_model=ExtractionReview, responses=_ERRS,
             summary="Apply image roles and page order (by image id)")
def post_rerun_comic(body: ExtractionComicRerunRequest, request: Request,
                     drama_id: int = Path(ge=1)):
    return svc.rerun_comic(drama_id, body.revision,
                           [i.model_dump() for i in body.images],
                           principal=request.state.principal, local=is_local_request(request))


@router.post(_REVIEW + "/save-profile", dependencies=[local_only()],
             response_model=ExtractionProfileSaved, responses=_ERRS,
             summary="Save the corrections as the site's profile (PC only)")
def post_save_profile(body: ExtractionRevisionRequest, request: Request,
                      drama_id: int = Path(ge=1)):
    return svc.save_profile(drama_id, body.revision, principal=request.state.principal)


@router.post(_REVIEW + "/approve-profile", dependencies=[local_only()],
             response_model=ExtractionProfileSaved, responses=_ERRS,
             summary="Approve the suggested site profile (PC only)")
def post_approve_profile(body: ExtractionRevisionRequest, request: Request,
                         drama_id: int = Path(ge=1)):
    return svc.approve_profile(drama_id, body.revision, principal=request.state.principal)


@router.post(_REVIEW + "/import", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted, responses=_ERRS,
             summary="Job: write the reviewed text or pages into the drama")
def post_review_import(body: ExtractionRevisionRequest, request: Request,
                       drama_id: int = Path(ge=1)):
    return svc.start_review_import(drama_id, body.revision, principal=request.state.principal,
                                   local=is_local_request(request))


@router.get(_REVIEW + "/images/{candidate_id}",
            dependencies=[require_permission("sources.import")], responses=_ERRS,
            response_class=Response,
            summary="A downloaded image in a comic review (thumbnail; raster types only)")
def get_review_image(request: Request, drama_id: int = Path(ge=1),
                     candidate_id: int = Path(ge=0, le=10_000)):
    body, media_type = svc.review_image(drama_id, candidate_id,
                                        principal=request.state.principal,
                                        local=is_local_request(request))
    return Response(body, media_type=media_type, headers={
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox"})
