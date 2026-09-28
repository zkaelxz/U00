"""
api/routers/translate_run_routes.py -- Translate-stage endpoints for one
drama (Migration Slice 39): the read-only stage config and the advisory
pre-run cost estimate. Distinct from Slice 13's standalone translator
under /api/translate. The start-translate job is a later slice (Slice 40);
see services/translate_run_service.py for the scope decision.
"""

from typing import Optional

from fastapi import APIRouter, Path, Query

from api.schemas import ErrorResponse, TranslateRunConfig, TranslateRunEstimate
from services import translate_run_service

router = APIRouter(prefix="/api/translate-run", tags=["translate-run"])


@router.get("/dramas/{drama_id}/config", response_model=TranslateRunConfig,
            summary="Read-only Translate-stage summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_translate_run_config(drama_id: int = Path(ge=1)):
    return translate_run_service.get_translate_config(drama_id)


@router.get("/dramas/{drama_id}/estimate", response_model=TranslateRunEstimate,
            summary="Advisory pre-run cost estimate for one drama",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                       422: {"model": ErrorResponse}})
def get_translate_run_estimate(drama_id: int = Path(ge=1),
                               engine: Optional[str] = None,
                               model: Optional[str] = None,
                               reflect: bool = False,
                               force_retranslate: bool = False,
                               bulk: bool = False,
                               gemini_free_tier: bool = False,
                               job_cost_cap_usd: Optional[float] = Query(None, ge=0)):
    return translate_run_service.estimate_translate_cost(
        drama_id, engine_name=engine, model=model, reflect=reflect,
        force_retranslate=force_retranslate, bulk=bulk,
        gemini_free_tier=gemini_free_tier, job_cost_cap_usd=job_cost_cap_usd)
