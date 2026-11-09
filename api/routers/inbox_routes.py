"""
api/routers/inbox_routes.py -- the watch-folder inbox settings, over services/inbox_service.py.

    GET  /api/inbox   PC only   state; the folder's path is never returned
    POST /api/inbox   PC only   turn on or off, pick the folder, language, series and "move when done"

PC only because the folder is on the Baihe PC and a dropped file becomes a title owned by the PC owner.
"""

from fastapi import APIRouter

from api.auth import local_only
from api.schemas import InboxState, InboxUpdate, error_responses
from services import inbox_service

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

_ERRS = error_responses(403, 422)


@router.get("", dependencies=[local_only()], response_model=InboxState, responses=_ERRS,
            summary="The watch folder's settings and last result")
def get_inbox():
    return inbox_service.get_state()


@router.post("", dependencies=[local_only()], response_model=InboxState, responses=_ERRS,
             summary="Change the watch folder's settings")
def set_inbox(body: InboxUpdate):
    return inbox_service.configure(body.on, body.path, body.source_language, body.series_id, body.move)
