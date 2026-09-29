"""
api/routers/line_ai_routes.py -- per-line AI helpers for the Review stage
(Migration Slice 50): an improved-translation suggestion and a "why this
translation" explanation. Both are synchronous, address the line by
permanent id, never write, and never accept or return a key. Apply a
suggestion via the Slice 43 compare-and-set line patch. Logic lives in
services/line_ai_service.py. Both take a slot from the shared LLM cap
(api/llm_slots.py; 429 when busy).

Also here (review parity R17-R19): "Alternatives" and "Grammar" are
read-only (`lines.read`) but call an LLM, so the handler also runs
`require_engines_allowed` on the engine the call will use: the named one,
else the drama's own translation engine (resolved here, then passed on). "Pronounce" returns an edge-tts MP3 of the line's source
text (free service, bounded length and time; services/line_tools_service.py).
All three take an LLM slot, since each holds a worker thread on a network call.
"""

from fastapi import APIRouter, Path, Request
from fastapi.responses import Response
from api.auth import require_engines_allowed, require_permission
from api.llm_slots import llm_slot
from api.schemas import (ErrorResponse, LineAlternatives, LineExplainRequest, LineExplanation,
                         LineGrammar, LineImproveRequest, LineImprovement)
from services import line_ai_service, line_tools_service

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


@router.post("/dramas/{drama_id}/lines/{line_id}/alternatives", dependencies=[require_permission("lines.read")],
             response_model=LineAlternatives,
             summary="Other valid translations of one line (writes nothing)",
             responses={**_ERRORS, 403: {"model": ErrorResponse}})
def post_alternatives(body: LineExplainRequest, request: Request, drama_id: int = Path(ge=1),
                      line_id: int = Path(ge=1)):
    engine = line_ai_service.tool_engine_name(drama_id, body.engine)
    require_engines_allowed(request, engine)
    with llm_slot(request):
        return line_ai_service.alternatives_for_line(drama_id, line_id, engine, body.model,
                                                     body.gemini_free_tier)


@router.post("/dramas/{drama_id}/lines/{line_id}/grammar", dependencies=[require_permission("lines.read")],
             response_model=LineGrammar,
             summary="Word-by-word breakdown of one line's source text (writes nothing)",
             responses={**_ERRORS, 403: {"model": ErrorResponse}})
def post_grammar(body: LineExplainRequest, request: Request, drama_id: int = Path(ge=1),
                 line_id: int = Path(ge=1)):
    engine = line_ai_service.tool_engine_name(drama_id, body.engine)
    require_engines_allowed(request, engine)
    with llm_slot(request):
        return line_ai_service.grammar_for_line(drama_id, line_id, engine, body.model,
                                                body.gemini_free_tier)


@router.post("/dramas/{drama_id}/lines/{line_id}/pronounce", dependencies=[require_permission("lines.read")],
             response_class=Response,
             summary="An MP3 of one line's source text read aloud (writes nothing)",
             responses={200: {"content": {"audio/mpeg": {}}}, **_ERRORS})
def post_pronounce(request: Request, drama_id: int = Path(ge=1), line_id: int = Path(ge=1)):
    with llm_slot(request, "Another audio or AI request is running; try again in a moment."):
        audio = line_tools_service.pronounce_line(drama_id, line_id)
    return Response(content=audio, media_type="audio/mpeg",
                    headers={"Cache-Control": "no-store"})
