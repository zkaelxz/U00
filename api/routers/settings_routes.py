"""
api/routers/settings_routes.py -- Settings endpoints (Migration Slices 10, 23).

GET: whether each engine key/endpoint is configured, plus the app_settings
toggles. Never returns a key's value (D2). POST (Slice 23): non-secret
boolean toggles only -- writing a secret to disk over HTTP is a
separate, higher-risk slice of its own (see docs/migration-review.md).
"""

from fastapi import APIRouter

from api.schemas import SettingsOverview, SettingsUpdateRequest
from services import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOverview,
            summary="Read-only settings overview (engine key presence, job toggles)")
def get_overview():
    return settings_service.get_settings_overview()


@router.post("", response_model=SettingsOverview,
             summary="Update non-secret boolean settings (never keys/URLs/paths)")
def update_settings(body: SettingsUpdateRequest):
    return settings_service.set_settings(body.model_dump(exclude_unset=True))
