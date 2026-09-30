"""
api/routers/stronger_engine_routes.py -- Step 99: suggest the stronger
translation engine for a hard line in Review. Thin: see
services/stronger_engine_service.py.

- `GET /api/stronger-engine/dramas/{drama_id}` (`lines.read`): which lines
  to suggest it for, why, and the per-line estimate. No engine call.
- `POST /api/stronger-engine/dramas/{drama_id}/lines/{line_id}/try`
  (`review.use`, plus `require_engines_allowed` on the engine Settings picked,
  since it can be paid): one single-line translate, returned for the user to
  accept -- it never writes the line. Takes a slot from the shared LLM cap
  (api/llm_slots.py; 429 when busy). The body names no engine, model or key.
"""

from fastapi import APIRouter, Path, Request

from api.auth import require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.schemas import ErrorResponse
from api.stronger_engine_schemas import (StrongerEngineSuggestions, StrongerLineResult,
                                         StrongerLineTryRequest)
from services import stronger_engine_service as svc

router = APIRouter(prefix="/api/stronger-engine", tags=["review"])

_ERRORS = {400: {"model": ErrorResponse}, 403: {"model": ErrorResponse},
           404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
           429: {"model": ErrorResponse}, 500: {"model": ErrorResponse},
           503: {"model": ErrorResponse}}


@router.get("/dramas/{drama_id}", dependencies=[require_permission("lines.read")],
            response_model=StrongerEngineSuggestions,
            summary="Lines where the stronger engine is suggested, and why (no engine call)",
            responses={404: {"model": ErrorResponse}})
def get_suggestions(drama_id: int = Path(ge=1)):
    return svc.get_suggestions(drama_id)


@router.post("/dramas/{drama_id}/lines/{line_id}/try",
             dependencies=[require_permission("review.use")],
             response_model=StrongerLineResult,
             summary="Translate one line with the stronger engine (writes nothing)",
             responses=_ERRORS)
def try_line(request: Request, body: StrongerLineTryRequest = None,
             drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    engine = svc.stronger_engine_name(drama_id)
    require_engines_allowed(request, engine)
    with llm_slot(request):
        # The checked name is passed on; the service refuses (409) if
        # Settings changed since, so the call can't use an unchecked engine.
        return svc.try_line(drama_id, line_id, engine)
