"""
api/routers/translate_routes.py -- Translate-standalone endpoints.

Migration Slice 11 added the two read-only routes: the engine list
(name/label/free/models/key_configured, never a key value -- D2) and
translate history. Migration Slice 13 adds the translate action itself,
resolving a server-side key per engine rather than accepting one from the
caller. Clearing history (a write) stays out of scope; see
services/translate_service.py's own docstring for the scope decision.
"""

from fastapi import APIRouter, Query

from api.schemas import (ErrorResponse, TranslateEngineListResponse,
                         TranslateHistoryResponse, TranslateRequest, TranslateResponse)
from services import translate_service

router = APIRouter(prefix="/api/translate", tags=["translate"])


@router.get("/engines", response_model=TranslateEngineListResponse,
            summary="Available translate engines and whether each has a key configured")
def get_engines():
    return {"items": translate_service.list_engines()}


@router.get("/history", response_model=TranslateHistoryResponse,
            summary="Standalone-translate history, most recent first")
def get_history(limit: int = Query(50, ge=1, le=200)):
    return {"items": translate_service.list_history(limit=limit)}


@router.post("", response_model=TranslateResponse,
            summary="Translate text with a chosen engine (server-side key resolution, D2)",
            responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                      503: {"model": ErrorResponse}})
def post_translate(payload: TranslateRequest):
    return translate_service.translate(
        payload.text, payload.engine, payload.source_language, payload.target_language,
        model=payload.model, free_tier=payload.free_tier, base_url=payload.base_url)
