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

The remote-access public-address check (`/remote-health/ip-check`) is PC
only (`local_only()`): its status is a boolean; setting and clearing it
write .env, so they also sit behind the key-write gate
(`_require_local_admin`) and need `confirm=true`, like the notification
URLs; "Test" runs one check now and returns a state and a fixed message.
None of them ever returns the address.
"""

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from api.auth import local_only, require_permission
from api.routers.settings_routes import _read_body, _require_confirm, _require_local_admin
from api.schemas import (DiagnosticsOverview, ErrorResponse, RemoteHealth,
                         RemoteIpCheckClearRequest, RemoteIpCheckSetRequest,
                         RemoteIpCheckStatus, RemoteIpCheckTestResult)
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


@router.get("/remote-health/ip-check", dependencies=[local_only()],
            response_model=RemoteIpCheckStatus,
            summary="PC only: whether the public-address check is set (boolean only)")
def get_ip_check():
    return remote_health_service.ip_check_status()


@router.post("/remote-health/ip-check", dependencies=[local_only()],
             response_model=RemoteIpCheckStatus,
             summary="PC only: set the https address that reports this PC's public IP "
                     "(write-only; key writes must be on)",
             responses={422: {"model": ErrorResponse}})
async def set_ip_check(request: Request):
    _require_local_admin(request)
    body = await _read_body(request, RemoteIpCheckSetRequest)
    _require_confirm(body.confirm)
    # The DNS check and the .env rewrite block: off the event loop, which the
    # admin and household servers share.
    return await run_in_threadpool(remote_health_service.set_ip_check_url, body.value)


@router.post("/remote-health/ip-check/clear", dependencies=[local_only()],
             response_model=RemoteIpCheckStatus,
             summary="PC only: remove the public-address check from .env",
             responses={422: {"model": ErrorResponse}})
async def clear_ip_check(request: Request):
    _require_local_admin(request)
    body = await _read_body(request, RemoteIpCheckClearRequest)
    _require_confirm(body.confirm)
    return await run_in_threadpool(remote_health_service.clear_ip_check_url)


@router.post("/remote-health/ip-check/test", dependencies=[local_only()],
             response_model=RemoteIpCheckTestResult,
             summary="PC only: run the public-address check once now (state only)",
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        429: {"model": ErrorResponse}})
def test_ip_check(request: Request):
    return remote_health_service.run_ip_check_test(request.app.state.settings.public_url)
