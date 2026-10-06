"""
api/routers/model_registry_routes.py -- model deprecation /
migration assistant. Thin: see services/model_registry_service.py.

- `GET /api/models/status` (`admin.diagnostics`): every configured model
  with its registry / last-check status. Reads cached data only; no call out.
- `POST /api/models/check` (`local_only()`): the manual provider check. It
  uses the owner's keys (in headers, fixed hosts, with a timeout), so it is
  PC-only; at most once a minute (429).
- `POST /api/models/presets/{preset_id}/switch` (`local_only()`,
  `confirm=true`): the user-confirmed switch of one preset's model to one
  its engine offers; 409 if the preset's model changed since the user
  looked. Nothing is ever switched automatically.
- `POST /api/models/overrides` and `POST /api/models/overrides/clear`
  (`local_only()`, `confirm=true`): choose a replacement for a built-in
  default or workflow-tier model, or go back to the built-in; 409 if the
  model changed since the user looked. Saved presets are not touched.
"""

from fastapi import APIRouter, Path

from api.auth import local_only, require_permission
from api.model_registry_schemas import (ModelOverrideClearRequest, ModelOverrideRequest,
                                        ModelOverrideResult, ModelStatus,
                                        PresetModelSwitchRequest, PresetModelSwitchResult)
from api.routers.settings_routes import require_confirm
from api.schemas import ErrorResponse
from services import model_registry_service as svc

router = APIRouter(prefix="/api/models", tags=["models"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}}


@router.get("/status", dependencies=[require_permission("admin.diagnostics")],
            response_model=ModelStatus,
            summary="Configured models: deprecated, retired or no longer listed (cached; no call out)")
def get_status():
    return svc.get_status()


@router.post("/check", dependencies=[local_only()], response_model=ModelStatus, responses=_ERRS,
             summary="PC only: ask each configured provider for its current model list")
def post_check():
    return svc.check_providers()


@router.post("/presets/{preset_id}/switch", dependencies=[local_only()],
             response_model=PresetModelSwitchResult, responses=_ERRS,
             summary="PC only: switch one preset's model to its replacement (confirm=true)")
def post_switch(body: PresetModelSwitchRequest, preset_id: int = Path(ge=1)):
    require_confirm(body.confirm)
    return svc.switch_preset_model(preset_id, body.from_model, body.to_model)


@router.post("/overrides", dependencies=[local_only()], response_model=ModelOverrideResult,
             responses=_ERRS,
             summary="PC only: use another model instead of a built-in default or tier model (confirm=true)")
def post_override(body: ModelOverrideRequest):
    require_confirm(body.confirm)
    return svc.set_model_override(body.kind, body.key, body.from_model, body.to_model)


@router.post("/overrides/clear", dependencies=[local_only()], response_model=ModelOverrideResult,
             responses=_ERRS,
             summary="PC only: go back to the built-in model (confirm=true)")
def post_override_clear(body: ModelOverrideClearRequest):
    require_confirm(body.confirm)
    return svc.clear_model_override(body.kind, body.key)
