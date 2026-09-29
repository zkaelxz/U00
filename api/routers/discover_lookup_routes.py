"""
api/routers/discover_lookup_routes.py -- Discover's network helpers (spec
D-2; API batch 1). Thin: see services/discover_lookup_service.py. Same
/api/discover prefix as the catalog routes (discover_routes.py), no path
overlap.

Permissions:
- baihehub search (fixed host, no LLM) and reading a job's result:
  `library.read`.
- translate-query is an LLM call with no URL: `library.read`, plus
  `require_engines_allowed` on the named engine (none = the default Claude,
  paid) so only `engines.paid` holders spend.
- import-suggestion, bulk-extract and navigation-help fetch a caller-chosen
  public URL from this PC (through services/safe_fetch) and call an LLM:
  `media.import_url` plus `require_engines_allowed`.
- bulk-commit writes the known-titles catalogue: `admin.library`, like the
  catalogue's own create/seed routes.
Engine keys are resolved server-side and never accepted or returned.
"""

from fastapi import APIRouter, Request

from api.auth import require_engines_allowed, require_permission
from api.schemas import (DiscoverBaihehubResult, DiscoverBaihehubSearchRequest,
                         DiscoverBulkCommitRequest, DiscoverBulkCommitResult,
                         DiscoverBulkExtractRequest, DiscoverImportSuggestion,
                         DiscoverImportSuggestionRequest, DiscoverJobResult,
                         DiscoverJobStarted, DiscoverNavigationHelpRequest,
                         DiscoverTranslateQueryRequest, DiscoverTranslateQueryResult,
                         ErrorResponse)
from services import discover_lookup_service as svc

router = APIRouter(prefix="/api/discover", tags=["discover"])

_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/translate-query", dependencies=[require_permission("library.read")],
             response_model=DiscoverTranslateQueryResult,
             summary="Translate a search query to Chinese (button-driven; LLM)", responses=_ERRS)
def post_translate_query(body: DiscoverTranslateQueryRequest, request: Request):
    require_engines_allowed(request, body.engine)
    return svc.translate_query(body.q, body.engine)


@router.post("/baihehub-search", dependencies=[require_permission("library.read")],
             response_model=DiscoverBaihehubResult,
             summary="Search baihehub (fixed host; no LLM)", responses=_ERRS)
def post_baihehub_search(body: DiscoverBaihehubSearchRequest):
    return svc.baihehub_search(body.q)


@router.post("/import-suggestion", dependencies=[require_permission("media.import_url")],
             response_model=DiscoverImportSuggestion,
             summary="Read a public page and suggest catalogue fields (writes nothing)",
             responses=_ERRS)
def post_import_suggestion(body: DiscoverImportSuggestionRequest, request: Request):
    require_engines_allowed(request, body.engine)
    return svc.import_suggestion(body.url, body.engine)


@router.post("/bulk-extract", dependencies=[require_permission("media.import_url")],
             response_model=DiscoverJobStarted,
             summary="Job: extract catalogue entries from up to 10 listing pages",
             responses=_ERRS)
def post_bulk_extract(body: DiscoverBulkExtractRequest, request: Request):
    require_engines_allowed(request, body.engine)
    return svc.bulk_extract(body.urls, body.source_label, body.engine)


@router.get("/bulk-extract/result", dependencies=[require_permission("library.read")],
            response_model=DiscoverJobResult,
            summary="The bulk-extract job's status and result (this process only)",
            responses=_ERRS)
def get_bulk_extract_result():
    return svc.bulk_extract_result()


@router.post("/bulk-commit", dependencies=[require_permission("admin.library")],
             response_model=DiscoverBulkCommitResult,
             summary="Add reviewed entries to the catalogue (deduped by title/URL)",
             responses=_ERRS)
def post_bulk_commit(body: DiscoverBulkCommitRequest):
    return svc.bulk_commit([e.model_dump() for e in body.entries], body.source_label)


@router.post("/navigation-help", dependencies=[require_permission("media.import_url")],
             response_model=DiscoverJobStarted,
             summary="Job: step-by-step help for a site's own interface (LLM)",
             responses=_ERRS)
def post_navigation_help(body: DiscoverNavigationHelpRequest, request: Request):
    require_engines_allowed(request, body.engine)
    return svc.navigation_help(body.url, body.goal, body.target_language, body.engine)


@router.get("/navigation-help/result", dependencies=[require_permission("library.read")],
            response_model=DiscoverJobResult,
            summary="The navigation-help job's status and result (this process only)",
            responses=_ERRS)
def get_navigation_help_result():
    return svc.navigation_help_result()
