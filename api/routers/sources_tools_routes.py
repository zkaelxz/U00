"""
api/routers/sources_tools_routes.py -- the remaining Sources tools
(feature inventory SO02, SO03, SO08, SO16) and the Discover bulk import's
pasted-text fallback (DI07). Thin: see services/sources_tools_service.py
and services/discover_lookup_service.py.

  POST /api/sources/url/preflight       job: "will this site work?" (one fetch)
  POST /api/sources/url/preview-pasted  what a pasted page source is (parse only)
  POST /api/sources/url/import-pasted   job: its novel text into a novel drama
  POST /api/sources/url/identify-media  job: media resources on an unknown page
  GET  /api/sources/url/identify-media/resource  PC only: one pick's full URL
  GET  /api/sources/url/extractions     recent pasted-URL attempts (diagnostics)
  POST /api/discover/bulk-extract/pasted  job: catalogue entries from pasted text

The resource lookup is `local_only()`: it hands out a full address (a
signed CDN link can carry a token), and the video download it feeds is
PC-only too. The fetch/import routes are `sources.import` like the URL preview and
import (off by default for household accounts); a pasted URL is checked
(public address) in the request, and from another device a fetch is static
HTTP only. The diagnostics list is `admin.settings`, like the attempt log
and profiles. The pasted listing fetches nothing but runs an AI engine, so
it is `jobs.start` plus the paid-engine check. Every path has two segments
under /api/sources, none of them named like a source action, so no
/{name}/<action> route shadows them or is shadowed.

Pasted bodies: a declared Content-Length over the cap is a 413 before any
of the body is read, and the body stream itself is counted, so a chunked
body past the cap is cut off with 413 too; then the model's own limits
(422). The pasted text is parsed only, never returned.
"""

import json
from typing import List

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.auth import (is_local_request, local_only, require_engines_allowed,
                      require_permission)
from api.routers.bug_report_routes import _BodyTooLarge, _capped
from api.schemas import DiscoverJobStarted, ErrorResponse, SourcesJobStarted
from api.sources_tools_schemas import (DiscoverBulkPastedRequest, SourceExtraction,
                                       SourcesIdentifyMediaRequest, SourcesMediaResource,
                                       SourcesPastedImportRequest,
                                       SourcesPastedPreview, SourcesPastedPreviewRequest,
                                       SourcesPreflightRequest)
from services import discover_lookup_service as discover_svc
from services import sources_tools_service as svc
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api", tags=["sources"])

_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}
# JSON escaping (quotes, newlines, backslashes) and the other fields on top
# of the pasted text's own cap.
_HTML_BODY_LIMIT = int(svc.MAX_PASTED_HTML_BYTES * 1.25) + 65_536
_LISTING_BODY_LIMIT = discover_svc.MAX_PASTED_LISTING_CHARS * 4 + 65_536
_TOO_LARGE = "The pasted text is too large."


def _body_schema(model) -> dict:
    return {"requestBody": {"required": True, "content": {"application/json": {
        "schema": model.model_json_schema()}}}}


async def _capped_json(request: Request, model, limit: int):
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > limit:
                raise StarletteHTTPException(413, _TOO_LARGE)
        except ValueError:
            raise InvalidInputError("The request has a bad Content-Length.") from None
    try:
        raw = await _capped(request, limit).body()
    except _BodyTooLarge:
        raise StarletteHTTPException(413, _TOO_LARGE) from None
    try:
        data = json.loads(raw or b"null")
    except ValueError:
        raise InvalidInputError("Send the request as JSON.") from None
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise RequestValidationError(e.errors(include_url=False, include_input=False)) from None


@router.post("/sources/url/preflight", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted,
             summary="Job: will this site work? (one fetch; imports nothing)", responses=_ERRS)
def post_preflight(body: SourcesPreflightRequest, request: Request):
    return svc.start_preflight(body.url, local=is_local_request(request))


@router.post("/sources/url/preview-pasted", dependencies=[require_permission("sources.import")],
             response_model=SourcesPastedPreview, responses=_ERRS,
             openapi_extra=_body_schema(SourcesPastedPreviewRequest),
             summary="What a pasted page source is (after a verification page; parse only)")
async def post_preview_pasted(request: Request):
    body = await _capped_json(request, SourcesPastedPreviewRequest, _HTML_BODY_LIMIT)
    return await run_in_threadpool(svc.preview_pasted, body.url, body.html)


@router.post("/sources/url/import-pasted", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted, responses=_ERRS,
             openapi_extra=_body_schema(SourcesPastedImportRequest),
             summary="Job: append a pasted page source's novel text to a novel drama")
async def post_import_pasted(request: Request):
    body = await _capped_json(request, SourcesPastedImportRequest, _HTML_BODY_LIMIT)
    return await run_in_threadpool(svc.start_pasted_import, body.url, body.html,
                                   body.drama_id, local=is_local_request(request),
                                   principal=request.state.principal)


@router.post("/sources/url/identify-media", dependencies=[require_permission("sources.import")],
             response_model=SourcesJobStarted, responses=_ERRS,
             openapi_extra=_body_schema(SourcesIdentifyMediaRequest),
             summary="Job: list the video/audio/subtitle resources on an unknown page")
async def post_identify_media(request: Request):
    body = await _capped_json(request, SourcesIdentifyMediaRequest, _HTML_BODY_LIMIT)
    return await run_in_threadpool(svc.start_identify_media, body.url, body.html,
                                   local=is_local_request(request))


@router.get("/sources/url/identify-media/resource", dependencies=[local_only()],
            response_model=SourcesMediaResource, responses=_ERRS,
            summary="PC only: the full address of one identified resource, for the download")
def get_identified_resource(run_id: str = Query(min_length=1, max_length=40),
                            index: int = Query(ge=0, le=10_000)):
    return svc.resource_url(run_id, index)


@router.get("/sources/url/extractions", dependencies=[require_permission("admin.settings")],
            response_model=List[SourceExtraction], responses=_ERRS,
            summary="Recent pasted-URL imports: what worked, how, and why not")
def get_extractions(limit: int = Query(15, ge=1, le=svc.MAX_EXTRACTIONS)):
    return svc.recent_extractions(limit)


@router.post("/discover/bulk-extract/pasted", dependencies=[require_permission("jobs.start")],
             response_model=DiscoverJobStarted, responses=_ERRS,
             openapi_extra=_body_schema(DiscoverBulkPastedRequest),
             summary="Job: extract catalogue entries from pasted listing text")
async def post_bulk_extract_pasted(request: Request):
    body = await _capped_json(request, DiscoverBulkPastedRequest, _LISTING_BODY_LIMIT)
    require_engines_allowed(request, body.engine)
    return await run_in_threadpool(discover_svc.bulk_extract_pasted, body.text,
                                   body.source_label, body.engine)
