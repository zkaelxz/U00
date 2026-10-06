"""
api/routers/model_reeval_routes.py -- scheduled model
re-evaluation and promotion. Thin: see services/model_reeval_service.py.

- Reads (`admin.diagnostics`): the overview (production model, schedule,
  candidates with their recorded decisions, latest report), the decision
  history, and the pre-run estimate (a POST that spends nothing).
- Writes are `local_only()`: schedule settings, adding / rejecting /
  reopening a candidate, "Run now" (`confirm=true`; spends like any
  benchmark run, under the monthly cap when one is set), and promotion
  (`confirm=true`), the only call that changes the production model or
  Settings' default engine. Nothing is promoted by running.
"""

from fastapi import APIRouter, Path

from api.auth import local_only, require_permission
from api.model_reeval_schemas import (CandidateAddRequest, CandidateAddResult, CandidateResult,
                                      PromoteRequest, PromoteResult, RejectRequest,
                                      ReevalDecisionList, ReevalEstimate, ReevalOverview,
                                      ReevalRunRequest, ReevalRunStarted, ReevalSettingsRequest,
                                      ReevalSettingsSaved)
from api.routers.settings_routes import require_confirm
from api.schemas import ErrorResponse
from services import model_reeval_service as svc

router = APIRouter(prefix="/api/models/reeval", tags=["models"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}
_READ = [require_permission("admin.diagnostics")]


@router.get("", dependencies=_READ, response_model=ReevalOverview,
            summary="Production model, schedule, candidates and the latest re-evaluation report")
def get_overview():
    return svc.get_overview()


@router.get("/decisions", dependencies=_READ, response_model=ReevalDecisionList,
            summary="Every promote/reject decision, newest first")
def get_decisions():
    return svc.list_decisions()


@router.post("/estimate", dependencies=_READ, response_model=ReevalEstimate, responses=_ERRS,
             summary="What 'Run now' would cost (spends nothing)")
def post_estimate():
    return svc.estimate_run()


@router.post("/settings", dependencies=[local_only()], response_model=ReevalSettingsSaved,
             responses=_ERRS,
             summary="PC only: the re-evaluation schedule and golden set (turning the schedule "
                     "on needs a monthly cap or a per-run limit; a scheduled run is skipped above the "
                     "limit and stopped when its real spend reaches it)")
def post_settings(body: ReevalSettingsRequest):
    return svc.set_settings(body.schedule_enabled, body.interval_days, body.tier, body.set_name,
                            body.max_cost_usd)


@router.post("/candidates", dependencies=[local_only()], response_model=CandidateAddResult,
             responses=_ERRS, summary="PC only: register a candidate model")
def post_candidate(body: CandidateAddRequest):
    return svc.add_candidate(body.engine, body.model, body.note)


@router.post("/candidates/{model_candidate_id}/reject", dependencies=[local_only()],
             response_model=CandidateResult, responses=_ERRS,
             summary="PC only: reject a candidate (recorded with the reason)")
def post_reject(body: RejectRequest, model_candidate_id: int = Path(ge=1)):
    return svc.reject(model_candidate_id, body.reason)


@router.post("/candidates/{model_candidate_id}/reopen", dependencies=[local_only()],
             response_model=CandidateResult, responses=_ERRS,
             summary="PC only: put a rejected candidate back in the running")
def post_reopen(model_candidate_id: int = Path(ge=1)):
    return {"candidate": svc.reopen_candidate(model_candidate_id)}


@router.post("/candidates/{model_candidate_id}/promote", dependencies=[local_only()],
             response_model=PromoteResult, responses=_ERRS,
             summary="PC only: make a candidate the production model (confirm=true)")
def post_promote(body: PromoteRequest, model_candidate_id: int = Path(ge=1)):
    require_confirm(body.confirm)
    return svc.promote(model_candidate_id, confirm=True, reason=body.reason)


@router.post("/run", dependencies=[local_only()], response_model=ReevalRunStarted,
             responses=_ERRS,
             summary="PC only: re-evaluate production against every open candidate now (confirm=true)")
def post_run(body: ReevalRunRequest):
    require_confirm(body.confirm)
    return svc.run_now()
