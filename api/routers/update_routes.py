"""
api/routers/update_routes.py -- app updates from the public GitHub Releases
(services/update_service.py).

Every route is local_only(): checking makes an outbound request for the PC,
and downloading or starting Setup changes the PC. Under /api/system/, which
the household reverse proxy already refuses. Responses carry names, numbers
and booleans only (no URL, no path).
"""

from fastapi import APIRouter

from api.auth import local_only
from api.schemas import (UpdateInstallRequest, UpdateInstallResponse, UpdateSettingsRequest,
                         UpdateStatus)
from services import update_service
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/system/update", tags=["system"])


@router.get("", dependencies=[local_only()], response_model=UpdateStatus,
            summary="Installed version and the last update check (no network)")
def update_status():
    return UpdateStatus(**update_service.status())


@router.post("/check", dependencies=[local_only()], response_model=UpdateStatus,
             summary="Ask GitHub for the latest release (never downloads)")
def update_check():
    return UpdateStatus(**update_service.check())


@router.post("/settings", dependencies=[local_only()], response_model=UpdateStatus,
             summary="Turn the once-a-day check on or off")
def update_settings(body: UpdateSettingsRequest):
    return UpdateStatus(**update_service.set_auto_check(body.auto_check))


@router.post("/download", dependencies=[local_only()], response_model=UpdateStatus,
             status_code=202, summary="Download and verify the newer installer")
def update_download():
    return UpdateStatus(**update_service.start_download())


@router.post("/install", dependencies=[local_only()], response_model=UpdateInstallResponse,
             summary="Open the verified installer's Setup (Windows)")
def update_install(body: UpdateInstallRequest):
    if not body.confirm:
        raise InvalidInputError("Confirm to open Setup.")
    return UpdateInstallResponse(**update_service.install())
