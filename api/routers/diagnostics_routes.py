"""
api/routers/diagnostics_routes.py -- read-only Diagnostics endpoint.

The full overview `services.diagnostics_service` builds, and the last
remote-access health check (`services.remote_health_service`; the check
itself runs on a schedule, never from this read).
No admin action (install/upgrade/delete a cached model, etc.) is
exposed here: those are PC-only routes in diagnostics_gaps_routes.py and
diagnostics_installs_routes.py (`local_only()` plus confirm=true).

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
from api.routers.settings_routes import read_body, require_confirm, require_local_admin
from api.schemas import (DiagnosticsOverview, GpuReading, PortsOverview, QualityCheck, QualityHistory, RemoteHealth, ResourceReadings,
                         RemoteIpCheckClearRequest, RemoteIpCheckSetRequest,
                         RemoteIpCheckStatus, RemoteIpCheckTestResult, RemoteSetup,
                         RemoteSetupSignInRequest,
                         error_responses)
from services import (diagnostics_service, quality_check_service, remote_health_service, remote_setup_service,
                      resource_monitor_service, settings_service)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("", dependencies=[require_permission("admin.diagnostics")], response_model=DiagnosticsOverview,
            summary="Read-only Diagnostics overview (deps, GPU, versions, running jobs, log tail)")
def get_overview():
    return diagnostics_service.get_diagnostics_overview()


@router.get("/gpu", dependencies=[local_only()], response_model=GpuReading,
            summary="GPU name and memory only (nvidia-smi), without the full overview")
def get_gpu():
    return diagnostics_service.get_gpu_reading()


@router.get("/resources", dependencies=[require_permission("admin.diagnostics")],
            response_model=ResourceReadings,
            summary="The resource monitor's last readings (GPU, RAM, CPU, disk) and each alert rule's standing")
def get_resources():
    return resource_monitor_service.snapshot()


@router.get("/quality", dependencies=[require_permission("admin.diagnostics")], response_model=QualityCheck,
            summary="The newest release check result in the data folder, each score against its baseline")
def get_quality():
    return quality_check_service.latest()


@router.get("/quality/history", dependencies=[require_permission("admin.diagnostics")], response_model=QualityHistory,
            summary="Every release check result in the data folder as per-clip score trends, grouped by language")
def get_quality_history():
    return quality_check_service.history()


@router.get("/remote-health", dependencies=[require_permission("admin.diagnostics")],
            response_model=RemoteHealth,
            summary="Last remote-access health check (certificate, public name, household "
                    "listener); states and fixed messages only")
def get_remote_health(request: Request):
    settings = request.app.state.settings
    status = remote_health_service.get_status(settings.public_url, settings.household_port)
    if settings.public_url_error and status["state"] == "off":
        status = {**status, "message": "Remote sign-in is off because BAIHE_PUBLIC_URL "
                                       "in .env is not valid."}
    return status


@router.get("/remote-setup", dependencies=[local_only()], response_model=RemoteSetup,
            summary="PC only: where the remote-access setup stands, one state per step")
def get_remote_setup(request: Request):
    return remote_setup_service.get_setup_status(request.app.state.settings)


@router.post("/remote-setup/sign-in", dependencies=[local_only()], response_model=RemoteSetup,
             summary="PC only: save the public name and the Google client id and secret in "
                     ".env (write-only; key writes must be on)",
             responses=error_responses(422))
async def save_remote_sign_in(request: Request):
    require_local_admin(request)
    body = await read_body(request, RemoteSetupSignInRequest)
    require_confirm(body.confirm)
    settings = request.app.state.settings
    return await run_in_threadpool(
        remote_setup_service.save_sign_in, settings, body.public_url,
        body.google_client_id, body.google_client_secret)


@router.get("/ports", dependencies=[local_only()], response_model=PortsOverview,
            summary="PC only: the ports Baihe uses, whether each is active, and how to change it")
def get_ports(request: Request):
    settings = request.app.state.settings
    return settings_service.baihe_ports(settings.port, settings.household_port, settings.public_url)


@router.get("/remote-health/ip-check", dependencies=[local_only()],
            response_model=RemoteIpCheckStatus,
            summary="PC only: whether the public-address check is set (boolean only)")
def get_ip_check():
    return remote_health_service.ip_check_status()


@router.post("/remote-health/ip-check", dependencies=[local_only()],
             response_model=RemoteIpCheckStatus,
             summary="PC only: set the https address that reports this PC's public IP "
                     "(write-only; key writes must be on)",
             responses=error_responses(422))
async def set_ip_check(request: Request):
    require_local_admin(request)
    body = await read_body(request, RemoteIpCheckSetRequest)
    require_confirm(body.confirm)
    # The DNS check and the .env rewrite block: off the event loop, which the
    # admin and household servers share.
    return await run_in_threadpool(remote_health_service.set_ip_check_url, body.value)


@router.post("/remote-health/ip-check/clear", dependencies=[local_only()],
             response_model=RemoteIpCheckStatus,
             summary="PC only: remove the public-address check from .env",
             responses=error_responses(422))
async def clear_ip_check(request: Request):
    require_local_admin(request)
    body = await read_body(request, RemoteIpCheckClearRequest)
    require_confirm(body.confirm)
    return await run_in_threadpool(remote_health_service.clear_ip_check_url)


@router.post("/remote-health/ip-check/test", dependencies=[local_only()],
             response_model=RemoteIpCheckTestResult,
             summary="PC only: run the public-address check once now (state only)",
             responses=error_responses(409, 422, 429))
def test_ip_check(request: Request):
    return remote_health_service.run_ip_check_test(request.app.state.settings.public_url)
