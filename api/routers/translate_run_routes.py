"""
api/routers/translate_run_routes.py -- Translate-stage endpoints for one
drama (Migration Slice 39): the read-only stage config and the advisory
pre-run cost estimate. Distinct from Slice 13's standalone translator
under /api/translate. Slice 40 adds the start-translate job;
see services/translate_run_service.py for the scope decision.
Parity X02/X22 add "Apply tier" (lines.edit: per-drama stage config) and
"Save as preset" (admin.library, like preset rename: a library catalogue
write; a new name deletes nothing. Replacing a preset of the same name
needs overwrite=true, else 409, and overwrite is PC-only like other
deletes: refused with 403 from a non-loopback client when auth is on).
"""

from typing import Optional

from fastapi import APIRouter, Path, Query, Request
from api.auth import (_auth_enabled, holds_paid_engines, is_local_request,
                      require_engines_allowed, require_permission)
from api.schemas import (ErrorResponse, TranslateBulkCancelResult, TranslateBulkList,
                         TranslateBulkResumeResult, TranslateErrorsDismissed,
                         TranslatePresetSave, TranslatePresetSaved, TranslateRunConfig,
                         TranslateRunEstimate, TranslateRunStart, TranslateRunStarted,
                         WorkflowTierApplied, WorkflowTierApply)
from services import ownership_service, translate_run_service
from services.service_errors import ForbiddenError

router = APIRouter(prefix="/api/translate-run", tags=["translate-run"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=TranslateRunConfig,
            summary="Read-only Translate-stage summary for one drama",
            responses={404: {"model": ErrorResponse}})
def get_translate_run_config(drama_id: int = Path(ge=1)):
    return translate_run_service.get_translate_config(drama_id)


@router.get("/dramas/{drama_id}/estimate", dependencies=[require_permission("library.read")], response_model=TranslateRunEstimate,
            summary="Advisory pre-run cost estimate for one drama",
            responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                       422: {"model": ErrorResponse}})
def get_translate_run_estimate(drama_id: int = Path(ge=1),
                               engine: Optional[str] = None,
                               model: Optional[str] = None,
                               reflect: bool = False,
                               force_retranslate: bool = False,
                               bulk: bool = False,
                               gemini_free_tier: Optional[bool] = None,
                               job_cost_cap_usd: Optional[float] = Query(None, ge=0)):
    return translate_run_service.estimate_translate_cost(
        drama_id, engine_name=engine, model=model, reflect=reflect,
        force_retranslate=force_retranslate, bulk=bulk,
        gemini_free_tier=gemini_free_tier, job_cost_cap_usd=job_cost_cap_usd)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=TranslateRunStarted,
             summary="Start a translation (normal, Reflect and/or bulk) as a background job",
             responses={400: {"model": ErrorResponse},
                        404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
def start_translate_run(body: TranslateRunStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine,
                            *[f.engine for f in (body.fallback_chain or ())])
    return translate_run_service.start_translate_run(
        drama_id, engine_name=body.engine, model=body.model,
        style_preset=body.style_preset, style_note=body.style_note,
        locale=body.locale, force_retranslate=body.force_retranslate,
        context_window=body.context_window,
        context_window_ahead=body.context_window_ahead, batch_size=body.batch_size,
        line_ids=body.line_ids, gemini_free_tier=body.gemini_free_tier,
        job_cost_cap_usd=body.job_cost_cap_usd,
        fallback_chain=[f.model_dump() for f in body.fallback_chain]
        if body.fallback_chain else None,
        reflect=body.reflect, bulk=body.bulk,
        default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes,
        # The Settings episode-summary engine may be a cloud one: skipped
        # for a caller without engines.paid rather than refusing the run.
        allow_paid_summary=holds_paid_engines(request))


@router.post("/dramas/{drama_id}/bulk/resume", dependencies=[require_permission("jobs.start")], response_model=TranslateBulkResumeResult,
             summary="Resume polling this drama's pending bulk jobs after a restart",
             responses={404: {"model": ErrorResponse}})
def resume_bulk_translations(drama_id: int = Path(ge=1)):
    return translate_run_service.resume_bulk_translations(drama_id)


@router.get("/dramas/{drama_id}/bulk", dependencies=[require_permission("library.read")],
            response_model=TranslateBulkList,
            summary="List this drama's bulk batches with their last recorded state",
            responses={404: {"model": ErrorResponse}})
def list_bulk_translations(drama_id: int = Path(ge=1)):
    return translate_run_service.list_bulk_translations(drama_id)


@router.post("/dramas/{drama_id}/bulk/{bulk_job_id}/cancel",
             dependencies=[require_permission("jobs.cancel")],
             response_model=TranslateBulkCancelResult,
             summary="Cancel one pending bulk batch (at the provider when possible)",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def cancel_bulk_translation(drama_id: int = Path(ge=1), bulk_job_id: int = Path(ge=1)):
    return translate_run_service.cancel_bulk_translation(drama_id, bulk_job_id)


@router.post("/dramas/{drama_id}/errors/dismiss", dependencies=[require_permission("lines.edit")],
             response_model=TranslateErrorsDismissed,
             summary="Dismiss the last run's failed-batch notice (clears only that record)",
             responses={404: {"model": ErrorResponse}})
def dismiss_translate_errors(request: Request, drama_id: int = Path(ge=1, le=2**31 - 1)):
    # A drama the caller can't see is a 404, the same as a missing one.
    ownership_service.require_visible(request.state.principal, "drama", drama_id)
    return translate_run_service.dismiss_translate_errors(drama_id)


@router.post("/dramas/{drama_id}/workflow-tier", dependencies=[require_permission("lines.edit")],
             response_model=WorkflowTierApplied,
             summary="Apply a workflow tier: saves its engine on the drama, returns the form values",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def apply_workflow_tier(body: WorkflowTierApply, drama_id: int = Path(ge=1, le=2**31 - 1)):
    return translate_run_service.apply_workflow_tier(drama_id, body.tier)


@router.post("/presets", dependencies=[require_permission("admin.library")],
             response_model=TranslatePresetSaved,
             summary="Save the Translate form's settings as a named preset",
             responses={403: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def save_translate_preset(body: TranslatePresetSave, request: Request):
    if body.overwrite and _auth_enabled(request.app) and not is_local_request(request):
        raise ForbiddenError("Replacing a preset is only allowed at the PC.")
    return translate_run_service.save_translate_preset(
        body.name, body.translation_engine, engine_model=body.engine_model,
        style_preset=body.style_preset, locale=body.locale,
        default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes, overwrite=body.overwrite)
