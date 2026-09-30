"""
api/routers/notification_routes.py -- Step 44: Discord / ntfy job
notifications. Thin: see services/notification_service.py.

- `GET /api/settings/notifications` (`admin.settings`): configured booleans
  only, never a URL, host or topic.
- `POST /api/settings/notifications/test` (`local_only()`): sends a test
  message to every configured channel; returns an outcome word per channel.
- `POST /api/settings/notifications/{channel}` and `/{channel}/clear`
  (`local_only()` plus the same `_require_local_admin` gate as the engine
  key writes, so also off unless BAIHE_API_ALLOW_KEY_WRITES=1): the webhook
  URL / topic URL is a secret stored in .env, so writing it follows the key
  rules exactly. Bodies are parsed only after the gate; `confirm=true` is
  required.

- `POST /api/settings/notifications/categories` (`local_only()`): which
  events (jobs, new chapters, remote-access problems) go to Discord/ntfy. Not secret, so no
  key-write gate.
The in-app list (`GET /api/notifications`) is in notification_center_routes.

The local-ntfy switch (BAIHE_NTFY_ALLOW_LOCAL) has no route on purpose.
"""

from fastapi import APIRouter, Request

from api.auth import local_only, require_permission
from api.notification_schemas import NotificationCategoriesRequest, NotificationSettingsStatus
from api.routers.settings_routes import _read_body, _require_confirm, _require_local_admin
from api.schemas import (ErrorResponse, NotificationChannelClearRequest,
                         NotificationChannelResult, NotificationChannelSetRequest,
                         NotificationTestResult)
from services import notification_service as svc

router = APIRouter(prefix="/api/settings/notifications", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")],
            response_model=NotificationSettingsStatus,
            summary="Which notification channels are configured (booleans only)")
def get_status():
    return svc.get_status()


# Declared before /{channel} so "categories" and "test" are never taken for
# a channel name.
@router.post("/categories", dependencies=[local_only()],
             response_model=NotificationSettingsStatus,
             summary="PC only: choose which events go to Discord/ntfy",
             responses={422: {"model": ErrorResponse}})
def set_categories(body: NotificationCategoriesRequest):
    return svc.set_categories(jobs=body.jobs, chapters=body.chapters, remote=body.remote)


@router.post("/test", dependencies=[local_only()], response_model=NotificationTestResult,
             summary="PC only: send a test notification to every configured channel",
             responses={422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}})
def send_test():
    return svc.send_test()


@router.post("/{channel}", dependencies=[local_only()], response_model=NotificationChannelResult,
             summary="PC only: set a Discord webhook or ntfy topic URL (write-only; "
                     "disabled by default)",
             responses={422: {"model": ErrorResponse}})
async def set_channel(channel: str, request: Request):
    _require_local_admin(request)
    body = await _read_body(request, NotificationChannelSetRequest)
    _require_confirm(body.confirm)
    return svc.set_channel(channel, body.value)


@router.post("/{channel}/clear", dependencies=[local_only()],
             response_model=NotificationChannelResult,
             summary="PC only: remove a Discord webhook or ntfy topic URL from .env",
             responses={422: {"model": ErrorResponse}})
async def clear_channel(channel: str, request: Request):
    _require_local_admin(request)
    body = await _read_body(request, NotificationChannelClearRequest)
    _require_confirm(body.confirm)
    return svc.clear_channel(channel)
