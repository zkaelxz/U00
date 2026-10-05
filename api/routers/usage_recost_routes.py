"""
api/routers/usage_recost_routes.py -- opt-in re-cost of past spend estimates.
Thin: see services/usage_recost_service.py.

- `GET /api/settings/usage-recost` (`admin.settings`): what a re-cost would
  change; writes nothing. Numbers and model ids only.
- `POST .../apply` and `POST .../undo` (`local_only()`): they rewrite stored
  spend figures, so they are PC-only like `POST /api/settings`.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.schemas import ErrorResponse, UsageRecostApplyRequest, UsageRecostPreview, UsageRecostResult
from services import usage_recost_service

router = APIRouter(prefix="/api/settings/usage-recost", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=UsageRecostPreview,
            summary="Preview re-costing past usage rows for models without a listed price (writes nothing)")
def preview_recost():
    return usage_recost_service.preview()


@router.post("/apply", dependencies=[local_only()], response_model=UsageRecostResult,
             summary="PC only: re-cost past usage rows (needs confirm=true; Undo restores them)",
             responses={422: {"model": ErrorResponse}})
def apply_recost(body: UsageRecostApplyRequest):
    return usage_recost_service.apply(body.confirm)


@router.post("/undo", dependencies=[local_only()], response_model=UsageRecostResult,
             summary="PC only: put back the costs a re-cost replaced")
def undo_recost():
    return usage_recost_service.undo()
