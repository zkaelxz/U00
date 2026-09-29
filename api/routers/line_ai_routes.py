"""
api/routers/line_ai_routes.py -- per-line AI helpers for the Review stage
(Migration Slice 50): an improved-translation suggestion and a "why this
translation" explanation. Both are synchronous, address the line by
permanent id, never write, and never accept or return a key. Apply a
suggestion via the Slice 43 compare-and-set line patch. Logic lives in
services/line_ai_service.py. Both take a slot from the shared LLM cap
(api/llm_slots.py; 429 when busy).
"""

from fastapi import APIRouter, Path, Request
from api.auth import require_permission
from api.llm_slots import llm_slot
from api.schemas import (ErrorResponse, LineExplainRequest, LineExplanation,
                         LineImproveRequest, LineImprovement)
from services import line_ai_service

router = APIRouter(prefix="/api/line-ai", tags=["line-ai"])

_ERRORS = {400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
           422: {"model": ErrorResponse}, 429: {"model": ErrorResponse},
           503: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/lines/{line_id}/improve", dependencies=[require_permission("engines.paid")], response_model=LineImprovement,
             summary="Suggest a better translation for one line (writes nothing)",
             responses=_ERRORS)
def post_improve(body: LineImproveRequest, request: Request, drama_id: int = Path(ge=1),
                 line_id: int = Path(ge=1)):
    with llm_slot(request):
        return line_ai_service.improve_line(drama_id, line_id, body.engine, body.model,
                                            body.gemini_free_tier, body.issue)


@router.post("/dramas/{drama_id}/lines/{line_id}/explain", dependencies=[require_permission("engines.paid")], response_model=LineExplanation,
             summary="Explain how one line was translated (writes nothing)",
             responses=_ERRORS)
def post_explain(body: LineExplainRequest, request: Request, drama_id: int = Path(ge=1),
                 line_id: int = Path(ge=1)):
    with llm_slot(request):
        return line_ai_service.explain_line(drama_id, line_id, body.engine, body.model,
                                            body.gemini_free_tier)
