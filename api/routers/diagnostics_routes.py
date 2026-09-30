"""
api/routers/diagnostics_routes.py -- read-only Diagnostics endpoint
(Migration Slice 5).

One route: the full overview `services.diagnostics_service` builds.
No admin action (install/upgrade/delete a cached model, etc.) is
exposed here -- those stay Streamlit-only per
docs/archive/migration-review.md's D5 (admin actions need explicit confirmation
and, for now, stay PC-local).
"""

from fastapi import APIRouter
from api.auth import require_permission
from api.schemas import DiagnosticsOverview
from services import diagnostics_service

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("", dependencies=[require_permission("admin.diagnostics")], response_model=DiagnosticsOverview,
            summary="Read-only Diagnostics overview (deps, GPU, versions, running jobs, log tail)")
def get_overview():
    return diagnostics_service.get_diagnostics_overview()
