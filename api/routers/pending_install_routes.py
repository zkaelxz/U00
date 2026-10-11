"""
api/routers/pending_install_routes.py -- installs that wait for the next start
(thin; see services/pending_install_service.py). Every route is PC only: a
household user can't see, queue or cancel one. Bodies carry registry package
keys only, and responses carry no paths, URLs or secrets.
"""

from fastapi import APIRouter

from api.auth import local_only
from api.schemas import (ErrorResponse, PendingInstallPlan, PendingInstallPlanRequest,
                         PendingInstallQueued, PendingInstallQueueRequest, PendingInstallStatus)
from services import pending_install_service as svc

router = APIRouter(prefix="/api/diagnostics/pending-install", tags=["diagnostics"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}


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
