"""
api/routers/translate_routes.py -- Translate-standalone endpoints.

The two read-only routes: the engine list
(name/label/free/models/key_configured, never a key value) and
translate history. The translate action itself resolves
a server-side key per engine rather than accepting one from the
caller. Clearing history is also here -- a confirm-gated
delete (see services/translate_service.py's own docstring for why it
requires an explicit confirm=true rather than a bare DELETE).
"""

from fastapi import APIRouter, Query, Request
from api.auth import local_only, require_permission
from api.llm_slots import llm_slot
from api.schemas import (ClearHistoryResult, ErrorResponse, TranslateEngineListResponse,
                         TranslateHistoryResponse, TranslateRequest, TranslateResponse)
from services import settings_service, translate_service

router = APIRouter(prefix="/api/translate", tags=["translate"])


@router.get("/engines", dependencies=[require_permission("library.read")], response_model=TranslateEngineListResponse,
            summary="Available translate engines and whether each has a key configured")
def get_engines():
    return {"items": translate_service.list_engines(),
            "default_engine": settings_service.get_default_engine()}


@router.get("/history", dependencies=[require_permission("library.read")], response_model=TranslateHistoryResponse,
            summary="Standalone-translate history, most recent first")
def get_history(request: Request, limit: int = Query(50, ge=1, le=200)):
    return {"items": translate_service.list_history(limit=limit,
                                                    principal=request.state.principal)}


@router.post("", dependencies=[require_permission("engines.paid")], response_model=TranslateResponse,
            summary="Translate text with a chosen engine (server-side key resolution, D2)",
            responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                      429: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def post_translate(payload: TranslateRequest, request: Request):
    # Synchronous LLM call: capped by the shared slot pool (api/llm_slots.py).
    with llm_slot(request):
        return translate_service.translate(
            payload.text, payload.engine, payload.source_language, payload.target_language,
            model=payload.model, free_tier=payload.free_tier,
            principal=request.state.principal)


# Deletes are PC-only (docs/remote-access-decision.md).
@router.delete("/history", dependencies=[local_only()], response_model=ClearHistoryResult,
              summary="Clear standalone-translate history (requires confirm=true)",
              responses={422: {"model": ErrorResponse}})
def delete_history(confirm: bool = Query(False)):
    return translate_service.clear_history(confirm=confirm)
