"""
api/routers/settings_routes.py -- read-only Settings endpoint (Migration
Slice 10).

One route: whether each engine key/endpoint is configured, plus the two
Slice-9 app_settings toggles. Never returns a key's value (D2) and has no
write route in this slice -- writing a secret to disk over HTTP is a
separate, higher-risk slice of its own (see docs/migration-review.md).
"""

from fastapi import APIRouter

from api.schemas import SettingsOverview
from services import settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsOverview,
            summary="Read-only settings overview (engine key presence, job toggles)")
def get_overview():
    return settings_service.get_settings_overview()
