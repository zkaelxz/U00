"""
api/routers/blocked_retry_routes.py -- retry one line an engine blocked on
content-moderation grounds, usually with another engine (parity item R10).
Thin adapter over `services.blocked_retry_service`.

Synchronous (the tab ran it under a spinner). Declares `jobs.start` and
calls `require_engines_allowed` with the chosen engine, so a household user
without `engines.paid` can only retry with a free engine. Never accepts or
returns a key.
"""

from fastapi import APIRouter, Path, Request

from api.auth import require_engines_allowed, require_permission
from api.schemas import BlockedRetryRequest, BlockedRetryResult, ErrorResponse
from services import blocked_retry_service

router = APIRouter(prefix="/api/lines", tags=["lines"])

_ERRS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/lines/{line_id}/retry-blocked",
             dependencies=[require_permission("jobs.start")],
             response_model=BlockedRetryResult, responses=_ERRS,
             summary="Re-translate one content-blocked line with the chosen engine")
def post_retry_blocked(body: BlockedRetryRequest, request: Request,
                       drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine)
    return blocked_retry_service.retry_blocked_line(drama_id, line_id, body.engine, body.model,
                                                    body.gemini_free_tier)
