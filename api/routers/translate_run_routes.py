"""
api/routers/translate_run_routes.py -- Translate-stage endpoints for one
drama (Migration Slice 39): the read-only stage config and the advisory
pre-run cost estimate. Distinct from Slice 13's standalone translator
under /api/translate. Slice 40 adds the start-translate job;
see services/translate_run_service.py for the scope decision.
"""

from typing import Optional

from fastapi import APIRouter, Path, Query

from api.schemas import (ErrorResponse, TranslateRunConfig, TranslateRunEstimate,
                         TranslateRunStart, TranslateRunStarted)
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


@router.post("/dramas/{drama_id}/run", response_model=TranslateRunStarted,
             summary="Start a normal translation as a background job",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def start_translate_run(body: TranslateRunStart, drama_id: int = Path(ge=1)):
    return translate_run_service.start_translate_run(
        drama_id, engine_name=body.engine, model=body.model,
        style_preset=body.style_preset, style_note=body.style_note,
        locale=body.locale, force_retranslate=body.force_retranslate,
        context_window=body.context_window,
        context_window_ahead=body.context_window_ahead, batch_size=body.batch_size,
        line_ids=body.line_ids, gemini_free_tier=body.gemini_free_tier,
        job_cost_cap_usd=body.job_cost_cap_usd)
