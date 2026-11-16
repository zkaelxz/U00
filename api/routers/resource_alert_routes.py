"""
api/routers/resource_alert_routes.py -- the PC alerts card (Settings > Notifications). Thin: see services/resource_monitor_service.py.

- `GET /api/settings/resource-alerts` (`admin.settings`): every alert's rule.
- `POST /api/settings/resource-alerts` (`local_only()`): change limits, minutes
  or switches. Not secret, so no key-write gate. A new limit applies on the
  monitor's next 30-second check.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.schemas import ResourceAlertRules, ResourceAlertsRequest, error_responses
from services import resource_monitor_service as svc

router = APIRouter(prefix="/api/settings/resource-alerts", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")],
            response_model=ResourceAlertRules,
            summary="Each PC alert's limit, minutes and switch, with ranges and defaults")
def get_rules():
    return {"rules": svc.describe_rules()}


@router.post("", dependencies=[local_only()], response_model=ResourceAlertRules,
             summary="PC only: change PC alert limits, minutes or switches",
             responses=error_responses(422))
def set_rules(body: ResourceAlertsRequest):
    changes = {name: change.model_dump(exclude_none=True) for name, change in body.rules.items()}
    return {"rules": svc.set_rules(changes)}
