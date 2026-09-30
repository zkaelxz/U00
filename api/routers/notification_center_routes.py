"""
api/routers/notification_center_routes.py -- Step 44 item 5: the in-app
notification list behind the header bell. Thin: see
services/notification_service.list_recent.

- `GET /api/notifications` (`library.read`): the last few job-ended and
  new-chapter events kept in memory, newest first. A job event is listed
  only for a caller who can see that job (the same rule as /api/jobs);
  new-chapter events are household-wide, like the Sources notifications.
  Never a URL, path, job id or line text.
"""

from fastapi import APIRouter, Request

from api.auth import require_permission
from api.notification_schemas import NotificationList
from services import notification_service as svc

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", dependencies=[require_permission("library.read")],
            response_model=NotificationList,
            summary="Recent job and new-chapter events for the header bell")
def list_recent(request: Request):
    return {"items": svc.list_recent(principal=request.state.principal)}
