"""
api/routers/diagnostics_browser_routes.py -- "Install browser support"
(thin; see services/browser_install_service.py). The status read is
`admin.diagnostics`; the install is `local_only()` + confirm=true, refused
while any job runs, and downloads Playwright's Chromium into the app's own
folder as a background job. Nothing but the confirm comes from the request.

Progress is also visible through GET /api/jobs/{job_id}; cancel through
POST /api/jobs/{job_id}/cancel.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.schemas import ErrorResponse
from api.schemas.browser import BrowserInstallRequest, BrowserInstallStarted, BrowserInstallStatus
from services import browser_install_service as svc

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])


@router.get("/browser", dependencies=[require_permission("admin.diagnostics")],
            response_model=BrowserInstallStatus,
            summary="Browser support for JavaScript-heavy sites: what is installed, the install job")
def get_browser():
    return svc.get_status()


@router.post("/browser/install", dependencies=[local_only()],
             response_model=BrowserInstallStarted,
             summary="PC only: download Chromium for JavaScript-heavy sites as a background job "
                     "(confirm=true)",
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def post_browser_install(body: BrowserInstallRequest):
    return svc.start_install(confirm=body.confirm)
