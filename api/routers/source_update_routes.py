"""
api/routers/source_update_routes.py -- "Update and restart" for a git working
copy started with start.bat (services/source_update_service.py).

system.update is an admin-only permission that, unlike the admin.* family, an
admin keeps on the household listener: updating from a phone is the point.
The update source is this copy's own git remote and no request field chooses
it. Responses carry short commit ids, counts and plain-words reasons only
(no URL, no path).
"""

from fastapi import APIRouter, Request

from api.auth import principal_user_id, require_permission
from api.schemas import ConfirmRequest, SourceUpdateApplyResponse, SourceUpdateStatus
from services import auth_service, source_update_service

router = APIRouter(prefix="/api/update", tags=["system"])

_PERMISSION = auth_service.UPDATE_PERMISSION


class SourceUpdateApplyRequest(ConfirmRequest):
    pass


@router.get("/source", dependencies=[require_permission(_PERMISSION)], response_model=SourceUpdateStatus,
            summary="This copy's commit, whether a newer one is waiting, and the last update's outcome")
def source_update_status():
    return SourceUpdateStatus(**source_update_service.status())


@router.post("/source/check", dependencies=[require_permission(_PERMISSION)],
             response_model=SourceUpdateStatus,
             summary="Ask GitHub whether a newer version is waiting (changes no file)")
def source_update_check():
    return SourceUpdateStatus(**source_update_service.check())


@router.post("/source/apply", dependencies=[require_permission(_PERMISSION)],
             response_model=SourceUpdateApplyResponse, status_code=202,
             summary="Stop Baihe cleanly and let start.bat update and start it again")
def source_update_apply(body: SourceUpdateApplyRequest, request: Request):
    return SourceUpdateApplyResponse(
        **source_update_service.apply(body.confirm, principal_user_id(request)))
