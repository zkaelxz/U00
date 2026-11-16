"""
api/routers/pending_install_routes.py -- installs that wait for the next start
(thin; see services/pending_install_service.py). Every route is PC only: a
household user can't see, queue or cancel one. Bodies carry registry package
keys only, and responses carry no paths, URLs or secrets.
"""

from fastapi import APIRouter, Request

from api.auth import local_only, principal_user_id
from api.schemas import (PendingInstallPlan, PendingInstallPlanRequest,
                         PendingInstallQueued, PendingInstallQueueRequest, PendingInstallRestartRequest,
                         PendingInstallRestartStarted, PendingInstallStatus, error_responses)
from services import pending_install_service as svc

router = APIRouter(prefix="/api/diagnostics/pending-install", tags=["diagnostics"])

_ERRS = error_responses(404, 409, 422)


@router.get("", dependencies=[local_only()], response_model=PendingInstallStatus,
            summary="PC only: the install waiting for the next start and the last outcome")
def get_pending():
    return svc.get_status()


@router.post("/plan", dependencies=[local_only()], response_model=PendingInstallPlan,
             summary="PC only: preview what installing these packages would change",
             responses=_ERRS)
def post_plan(body: PendingInstallPlanRequest):
    return svc.preview(body.packages)


@router.post("/queue", dependencies=[local_only()], response_model=PendingInstallQueued,
             summary="PC only: queue an install for the next start (confirm=true)",
             responses=_ERRS)
def post_queue(body: PendingInstallQueueRequest):
    return svc.queue(body.packages, confirm=body.confirm, accept_risk=body.accept_risk)


@router.post("/cancel", dependencies=[local_only()],
             summary="PC only: cancel the queued install before restart", responses=_ERRS)
def post_cancel():
    return svc.cancel()


@router.post("/dismiss", dependencies=[local_only()],
             summary="PC only: clear the last install outcome", responses=_ERRS)
def post_dismiss():
    return svc.dismiss_result()


@router.post("/restart", dependencies=[local_only()], response_model=PendingInstallRestartStarted,
             status_code=202, responses=_ERRS,
             summary="PC only: restart Baihe now so the queued install runs (confirm=true)")
def post_restart(body: PendingInstallRestartRequest, request: Request):
    return svc.restart_and_apply(body.confirm, body.stop_jobs, principal_user_id(request))
