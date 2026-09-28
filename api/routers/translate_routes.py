"""
api/routers/translate_routes.py -- read-only Translate-standalone endpoints
(Migration Slice 11).

Two routes: the engine list (name/label/free/models/key_configured, never
a key value -- D2) and translate history. Actually performing a
translation (a real network call) and clearing history (a write) are
deliberately not exposed here -- see services/translate_service.py's own
docstring for the scope decision.
"""

from fastapi import APIRouter, Query

from api.schemas import TranslateEngineListResponse, TranslateHistoryResponse
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
