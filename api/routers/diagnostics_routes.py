"""
api/routers/diagnostics_routes.py -- read-only Diagnostics endpoint
(Migration Slice 5).

The full overview `services.diagnostics_service` builds, and the last
remote-access health check (`services.remote_health_service`; the check
itself runs on a schedule, never from this read).
No admin action (install/upgrade/delete a cached model, etc.) is
exposed here -- those stay Streamlit-only per
docs/archive/migration-review.md's D5 (admin actions need explicit confirmation
and, for now, stay PC-local).
"""

from fastapi import APIRouter, Request
from api.auth import require_permission
from api.schemas import DiagnosticsOverview, RemoteHealth
from services import diagnostics_service, remote_health_service

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("", dependencies=[require_permission("admin.diagnostics")], response_model=DiagnosticsOverview,
            summary="Read-only Diagnostics overview (deps, GPU, versions, running jobs, log tail)")
def get_overview():
    return diagnostics_service.get_diagnostics_overview()


@router.get("/remote-health", dependencies=[require_permission("admin.diagnostics")],
            response_model=RemoteHealth,
            summary="Last remote-access health check (certificate, public name, household "
                    "listener); states and fixed messages only")
def get_remote_health(request: Request):
    settings = request.app.state.settings
    return remote_health_service.get_status(settings.public_url, settings.household_port)
