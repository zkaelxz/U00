"""
api/routers/translate_run_routes.py -- Translate-stage endpoints for one
drama: the read-only stage config and the advisory
pre-run cost estimate. Distinct from the standalone translator
under /api/translate. It also adds the start-translate job;
see services/translate_run_service.py for the scope decision.
It also adds "Apply tier" (lines.edit: per-drama stage config) and
"Save as preset" (admin.library, like preset rename: a library catalogue
write; a new name deletes nothing. Replacing a preset of the same name
needs overwrite=true, else 409, and overwrite is PC-only like other
deletes: refused with 403 from a non-loopback client when auth is on).
Editing a profile's fields replaces them like an overwrite, so it is PC-only
the same way; "Make default" is a library catalogue write (admin.library).
"Apply a preset" works on an existing drama (lines.edit, like
"Apply tier": it saves the profile's title settings on the drama and starts nothing).
The glossary-affected preview (`lines.read`: it carries line text) and its
run (`jobs.start`, engine-checked like the run above) re-translate only
the lines a glossary change affects; see services/glossary_retranslate_service.py.
The source-changed run (`jobs.start`, engine-checked the same way) re-translates
the lines flagged source_changed (see source_change.py).
"""

from typing import List, Optional

from fastapi import APIRouter, Path, Query, Request
from api.auth import (is_auth_enabled, holds_paid_engines, is_local_request,
                      require_cloud_model_allowed, require_engines_allowed, require_permission)
from api.schemas import (EpisodeSummaryGenerate, LibraryPreset, ProfileEditorFields, EpisodeSummaryGenerated, EpisodeSummaryOptions,
                         GlossaryFlaggedRedoStart, GlossaryFlaggedRedoStarted,
                         RetryFailedLinesStart, RetryFailedLinesStarted, GlossaryAffectedPreview, GlossaryAffectedRunStart,
                         GlossaryAffectedRunStarted, SourceChangedRetranslateStart,
                         SourceChangedRetranslateStarted, TranslateBulkCancelResult, TranslateBulkList,
                         TranslateBulkResumeResult, TranslateErrorsDismissed,
                         TranslatePresetApplied, TranslatePresetApply,
                         TranslatePresetDefault, TranslatePresetSave, TranslatePresetSaved,
                         TranslatePresetUpdate, TranslatePresetUpdated, TranslateRunConfig,
                         TranslateRunEstimate, TranslateRunStart, TranslateRunStarted,
                         WorkflowTierApplied, WorkflowTierApply, error_responses)
from api.llm_slots import llm_slot
from services import (episode_summary_service, failed_lines_service, glossary_flagged_service,
                      glossary_retranslate_service, ownership_service, settings_service,
                      source_change_service,
                      translate_preset_service, translate_run_service,
                      translate_thinking_service)
from lib.errors import ForbiddenError

router = APIRouter(prefix="/api/translate-run", tags=["translate-run"])


@router.get("/dramas/{drama_id}/config", dependencies=[require_permission("library.read")], response_model=TranslateRunConfig,
            summary="Read-only Translate-stage summary for one drama",
            responses=error_responses(404))
def get_translate_run_config(drama_id: int = Path(ge=1)):
    with settings_service.read_once():
        return {**translate_run_service.get_translate_config(drama_id),
                **translate_thinking_service.config_fields(drama_id),
                **failed_lines_service.summarize(drama_id),
                **glossary_flagged_service.config_fields(drama_id)}


@router.get("/dramas/{drama_id}/estimate", dependencies=[require_permission("library.read")], response_model=TranslateRunEstimate,
            summary="Advisory pre-run cost estimate for one drama",
            responses=error_responses(400, 404, 422))
def get_translate_run_estimate(drama_id: int = Path(ge=1),
                               engine: Optional[str] = None,
                               model: Optional[str] = None,
                               reflect: bool = False,
                               force_retranslate: bool = False,
                               bulk: bool = False,
                               thinking: Optional[bool] = None,
                               gemini_free_tier: Optional[bool] = None,
                               job_cost_cap_usd: Optional[float] = Query(None, ge=0),
                               batch_size: Optional[int] = Query(None, ge=1, le=translate_run_service.MAX_BATCH_SIZE)):
    return translate_thinking_service.annotate_estimate(
        translate_run_service.estimate_translate_cost(
            drama_id, engine_name=engine, model=model, reflect=reflect,
            force_retranslate=force_retranslate, bulk=bulk,
            gemini_free_tier=gemini_free_tier, job_cost_cap_usd=job_cost_cap_usd,
            batch_size=batch_size),
        drama_id, thinking, reflect)


def _may_remember(request: Request, body) -> bool:
    return translate_thinking_service.may_remember(holds_paid_engines(request), body.thinking)


@router.post("/dramas/{drama_id}/run", dependencies=[require_permission("jobs.start")], response_model=TranslateRunStarted,
             summary="Start a translation (normal, Reflect and/or bulk) as a background job",
             responses=error_responses(400, 404, 409, 422, 503))
def start_translate_run(body: TranslateRunStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine,
                            *[f.engine for f in (body.fallback_chain or ())])
    require_cloud_model_allowed(request, (body.engine, body.model),
                                *[(f.engine, f.model) for f in (body.fallback_chain or ())])
    return translate_thinking_service.start_with_thinking(
        translate_run_service.start_translate_run, drama_id, body.thinking,
        _may_remember(request, body),
        engine_name=body.engine, model=body.model,
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


@router.get("/dramas/{drama_id}/glossary-affected", dependencies=[require_permission("lines.read")],
            response_model=GlossaryAffectedPreview,
            summary="Preview the translated lines the glossary affects, with a cost estimate",
            responses=error_responses(400, 404, 422))
def get_glossary_affected(drama_id: int = Path(ge=1),
                          term_ids: Optional[List[int]] = Query(None, max_length=10000),
                          engine: Optional[str] = Query(None, max_length=40),
                          model: Optional[str] = Query(None, max_length=200),
                          reflect: bool = False,
                          gemini_free_tier: Optional[bool] = None,
                          job_cost_cap_usd: Optional[float] = Query(None, ge=0)):
    return glossary_retranslate_service.find_glossary_affected_lines(
        drama_id, term_ids=term_ids, engine_name=engine, model=model, reflect=reflect,
        gemini_free_tier=gemini_free_tier, job_cost_cap_usd=job_cost_cap_usd)


@router.post("/dramas/{drama_id}/glossary-affected/run", dependencies=[require_permission("jobs.start")],
             response_model=GlossaryAffectedRunStarted,
             summary="Re-translate only the chosen lines the glossary affects",
             responses=error_responses(400, 404, 409, 422, 503))
def start_glossary_affected_run(body: GlossaryAffectedRunStart, request: Request,
                                drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine,
                            *[f.engine for f in (body.fallback_chain or ())])
    require_cloud_model_allowed(request, (body.engine, body.model),
                                *[(f.engine, f.model) for f in (body.fallback_chain or ())])
    return translate_thinking_service.start_with_thinking(
        glossary_retranslate_service.start_affected_retranslate, drama_id, body.thinking,
        _may_remember(request, body), body.line_ids, body.preview_hash,
        include_hand_edited=body.include_hand_edited, term_ids=body.term_ids,
        engine_name=body.engine, model=body.model, style_preset=body.style_preset,
        style_note=body.style_note, locale=body.locale, context_window=body.context_window,
        context_window_ahead=body.context_window_ahead, batch_size=body.batch_size,
        gemini_free_tier=body.gemini_free_tier, job_cost_cap_usd=body.job_cost_cap_usd,
        fallback_chain=[f.model_dump() for f in body.fallback_chain]
        if body.fallback_chain else None,
        reflect=body.reflect, default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes,
        allow_paid_summary=holds_paid_engines(request))


@router.post("/dramas/{drama_id}/source-changed/run", dependencies=[require_permission("jobs.start")],
             response_model=SourceChangedRetranslateStarted,
             summary="Re-translate the lines whose source changed after they were translated",
             responses=error_responses(400, 404, 409, 422, 503))
def start_source_changed_run(body: SourceChangedRetranslateStart, request: Request,
                             drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine,
                            *[f.engine for f in (body.fallback_chain or ())])
    require_cloud_model_allowed(request, (body.engine, body.model),
                                *[(f.engine, f.model) for f in (body.fallback_chain or ())])
    return translate_thinking_service.start_with_thinking(
        source_change_service.start_retranslate, drama_id, body.thinking,
        _may_remember(request, body), body.line_ids,
        engine_name=body.engine, model=body.model, style_preset=body.style_preset,
        style_note=body.style_note, locale=body.locale, context_window=body.context_window,
        context_window_ahead=body.context_window_ahead, batch_size=body.batch_size,
        gemini_free_tier=body.gemini_free_tier, job_cost_cap_usd=body.job_cost_cap_usd,
        fallback_chain=[f.model_dump() for f in body.fallback_chain]
        if body.fallback_chain else None,
        reflect=body.reflect, default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes,
        allow_paid_summary=holds_paid_engines(request))


@router.post("/dramas/{drama_id}/retry-failed/run", dependencies=[require_permission("jobs.start")],
             response_model=RetryFailedLinesStarted,
             summary="Translate again exactly the lines the last run failed on",
             responses=error_responses(400, 404, 409, 422, 503))
def start_retry_failed(body: RetryFailedLinesStart, request: Request, drama_id: int = Path(ge=1)):
    require_engines_allowed(request, body.engine,
                            *[f.engine for f in (body.fallback_chain or ())])
    require_cloud_model_allowed(request, (body.engine, body.model),
                                *[(f.engine, f.model) for f in (body.fallback_chain or ())])
    return translate_thinking_service.start_with_thinking(
        failed_lines_service.start_retry, drama_id, body.thinking, _may_remember(request, body),
        engine_name=body.engine, model=body.model, style_preset=body.style_preset,
        style_note=body.style_note, locale=body.locale, context_window=body.context_window,
        context_window_ahead=body.context_window_ahead, batch_size=body.batch_size,
        gemini_free_tier=body.gemini_free_tier, job_cost_cap_usd=body.job_cost_cap_usd,
        fallback_chain=[f.model_dump() for f in body.fallback_chain]
        if body.fallback_chain else None,
        default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes,
        allow_paid_summary=holds_paid_engines(request))


@router.post("/dramas/{drama_id}/glossary-flagged/run", dependencies=[require_permission("jobs.start")],
             response_model=GlossaryFlaggedRedoStarted,
             summary="Redo the lines flagged for a missing glossary name with the stronger engine",
             responses=error_responses(400, 404, 409, 422, 503))
def start_glossary_flagged_redo(request: Request, body: GlossaryFlaggedRedoStart = None,
                                drama_id: int = Path(ge=1)):
    engine = glossary_flagged_service.engine_name(drama_id)
    require_engines_allowed(request, engine)
    require_cloud_model_allowed(request, (engine, None))
    # The checked name is passed on, so a Settings change in between is refused.
    return translate_thinking_service.start_with_thinking(
        glossary_flagged_service.start_redo, drama_id, None, False, engine,
        allow_paid_summary=holds_paid_engines(request))


@router.get("/dramas/{drama_id}/episode-summary", dependencies=[require_permission("library.read")],
            response_model=EpisodeSummaryOptions,
            summary="Engines that can write this title's running episode summary",
            responses=error_responses(404))
def get_episode_summary_options(drama_id: int = Path(ge=1)):
    return episode_summary_service.options(drama_id)


@router.post("/dramas/{drama_id}/episode-summary/generate",
             dependencies=[require_permission("lines.edit", "lines.read")],
             response_model=EpisodeSummaryGenerated,
             summary="Write the running episode summary from the current lines (saves nothing)",
             responses=error_responses(400, 404, 422, 429, 503))
def generate_episode_summary(body: EpisodeSummaryGenerate, request: Request,
                             drama_id: int = Path(ge=1, le=2**31 - 1)):
    ownership_service.require_visible(request.state.principal, "drama", drama_id)
    require_engines_allowed(request, body.engine)
    require_cloud_model_allowed(request, (body.engine, body.model))
    with llm_slot(request):
        return episode_summary_service.generate(drama_id, body.engine, body.model)


@router.post("/dramas/{drama_id}/bulk/resume", dependencies=[require_permission("jobs.start")], response_model=TranslateBulkResumeResult,
             summary="Resume polling this drama's pending bulk jobs after a restart",
             responses=error_responses(403, 404))
def resume_bulk_translations(request: Request, drama_id: int = Path(ge=1)):
    for engine, model in translate_run_service.pending_bulk_engines(drama_id):
        require_engines_allowed(request, engine, model=model)
    return translate_run_service.resume_bulk_translations(drama_id)


@router.get("/dramas/{drama_id}/bulk", dependencies=[require_permission("library.read")],
            response_model=TranslateBulkList,
            summary="List this drama's bulk batches with their last recorded state",
            responses=error_responses(404))
def list_bulk_translations(drama_id: int = Path(ge=1)):
    return translate_run_service.list_bulk_translations(drama_id)


@router.post("/dramas/{drama_id}/bulk/{bulk_job_id}/cancel",
             dependencies=[require_permission("jobs.cancel")],
             response_model=TranslateBulkCancelResult,
             summary="Cancel one pending bulk batch (at the provider when possible)",
             responses=error_responses(403, 404, 409))
def cancel_bulk_translation(request: Request, drama_id: int = Path(ge=1), bulk_job_id: int = Path(ge=1)):
    return translate_run_service.cancel_bulk_translation(drama_id, bulk_job_id,
                                                         request.state.principal)


@router.post("/dramas/{drama_id}/errors/dismiss", dependencies=[require_permission("lines.edit")],
             response_model=TranslateErrorsDismissed,
             summary="Dismiss the last run's failed-batch notice (clears only that record)",
             responses=error_responses(404))
def dismiss_translate_errors(request: Request, drama_id: int = Path(ge=1, le=2**31 - 1)):
    # A drama the caller can't see is a 404, the same as a missing one.
    ownership_service.require_visible(request.state.principal, "drama", drama_id)
    return translate_run_service.dismiss_translate_errors(drama_id)


@router.post("/dramas/{drama_id}/workflow-tier", dependencies=[require_permission("lines.edit")],
             response_model=WorkflowTierApplied,
             summary="Apply a workflow tier: saves its engine on the drama, returns the form values",
             responses=error_responses(404, 422))
def apply_workflow_tier(body: WorkflowTierApply, drama_id: int = Path(ge=1, le=2**31 - 1)):
    return translate_preset_service.apply_workflow_tier(drama_id, body.tier)


@router.post("/dramas/{drama_id}/apply-preset", dependencies=[require_permission("lines.edit")],
             response_model=TranslatePresetApplied,
             summary="Apply a saved profile: saves its title settings on the drama, returns the form values",
             responses=error_responses(404, 422))
def apply_translate_preset(body: TranslatePresetApply, drama_id: int = Path(ge=1, le=2**31 - 1)):
    return translate_preset_service.apply_translate_preset(drama_id, body.preset_id)


@router.post("/presets", dependencies=[require_permission("admin.library")],
             response_model=TranslatePresetSaved,
             summary="Save the Translate form's settings as a named preset",
             responses=error_responses(403, 409, 422))
def save_translate_preset(body: TranslatePresetSave, request: Request):
    if body.overwrite and is_auth_enabled(request.app) and not is_local_request(request):
        raise ForbiddenError("Replacing a preset is only allowed at the PC.")
    require_cloud_model_allowed(request, (body.translation_engine, body.engine_model))
    return translate_preset_service.save_translate_preset(
        body.name, body.translation_engine, engine_model=body.engine_model,
        style_preset=body.style_preset, locale=body.locale,
        default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes, overwrite=body.overwrite,
        options=body.options)


@router.get("/profile-fields", dependencies=[require_permission("library.read")],
            response_model=ProfileEditorFields,
            summary="What the profile editor offers: engines, styles and transcribe option specs")
def get_profile_editor_fields():
    return translate_preset_service.profile_editor_fields()


@router.post("/presets/{preset_id}/update", dependencies=[require_permission("admin.library")],
             response_model=TranslatePresetUpdated,
             summary="Edit a saved profile: replaces every field and the name",
             responses=error_responses(403, 404, 409, 422))
def update_translate_preset(body: TranslatePresetUpdate, request: Request,
                            preset_id: int = Path(ge=1, le=2**31 - 1)):
    if is_auth_enabled(request.app) and not is_local_request(request):
        raise ForbiddenError("Editing a preset is only allowed at the PC.")
    require_cloud_model_allowed(request, (body.translation_engine, body.engine_model))
    return translate_preset_service.update_translate_preset(
        preset_id, body.name, body.translation_engine, engine_model=body.engine_model,
        style_preset=body.style_preset, locale=body.locale,
        default_female_pronouns=body.default_female_pronouns,
        include_genre_notes=body.include_genre_notes, options=body.options)


@router.post("/presets/{preset_id}/default", dependencies=[require_permission("admin.library")],
             response_model=LibraryPreset,
             summary="Make a saved profile the default for New title, or clear it",
             responses=error_responses(404, 422))
def set_default_translate_preset(body: TranslatePresetDefault,
                                 preset_id: int = Path(ge=1, le=2**31 - 1)):
    return translate_preset_service.set_default_preset(preset_id, body.is_default)
