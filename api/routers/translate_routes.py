"""
api/routers/translate_routes.py -- Translate-standalone endpoints.

Migration Slice 11 added the two read-only routes: the engine list
(name/label/free/models/key_configured, never a key value -- D2) and
translate history. Migration Slice 13 added the translate action itself,
resolving a server-side key per engine rather than accepting one from the
caller. Migration Slice 17 adds clearing history -- a confirm-gated
delete (see services/translate_service.py's own docstring for why it
requires an explicit confirm=true rather than a bare DELETE).
"""

from fastapi import APIRouter, Query
from api.auth import local_only, require_permission
from api.schemas import (ClearHistoryResult, ErrorResponse, TranslateEngineListResponse,
                         TranslateHistoryResponse, TranslateRequest, TranslateResponse)
from services import translate_service

router = APIRouter(prefix="/api/translate", tags=["translate"])


@router.get("/engines", dependencies=[require_permission("library.read")], response_model=TranslateEngineListResponse,
            summary="Available translate engines and whether each has a key configured")
def get_engines():
    return {"items": translate_service.list_engines()}


@router.get("/history", dependencies=[require_permission("library.read")], response_model=TranslateHistoryResponse,
            summary="Standalone-translate history, most recent first")
def get_history(limit: int = Query(50, ge=1, le=200)):
    return {"items": translate_service.list_history(limit=limit)}


@router.post("", dependencies=[require_permission("engines.paid")], response_model=TranslateResponse,
            summary="Translate text with a chosen engine (server-side key resolution, D2)",
            responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                      503: {"model": ErrorResponse}})
def post_translate(payload: TranslateRequest):
    return translate_service.translate(
        payload.text, payload.engine, payload.source_language, payload.target_language,
        model=payload.model, free_tier=payload.free_tier)


@router.delete("/history", dependencies=[local_only()], response_model=ClearHistoryResult,
              summary="Clear standalone-translate history (requires confirm=true)",
              responses={422: {"model": ErrorResponse}})
def delete_history(confirm: bool = Query(False)):
    return translate_service.clear_history(confirm=confirm)
