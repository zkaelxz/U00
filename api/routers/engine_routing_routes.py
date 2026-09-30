"""
api/routers/engine_routing_routes.py -- Step 36: "Which engine does what".
Thin: see services/engine_routing_service.py.

- `GET /api/settings/engine-routing` (`admin.settings`): each capability's
  engine and choices, and each engine's status (key configured, last Test
  result). Booleans, engine names and short redacted text only.
- `POST /api/settings/engine-routing/capabilities/{capability}`
  (`local_only()`, like every settings write): pick the engine for a task,
  or `null` to go back to the default.
- `POST /api/settings/engine-routing/engines/{engine}/test` (`local_only()`):
  one short real call with the key saved on this PC; spends a tiny amount on
  a paid engine, so it is PC-only like the key it tests.
"""

from fastapi import APIRouter

from api.auth import local_only, require_permission
from api.engine_routing_schemas import (CapabilityEngineRequest, CapabilityRoute,
                                        EngineRouteStatus, EngineRouting, EngineTestRequest)
from api.schemas import ErrorResponse
from services import engine_routing_service as svc

router = APIRouter(prefix="/api/settings/engine-routing", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=EngineRouting,
            summary="Which engine each task uses, and each engine's status")
def get_routing():
    return svc.get_routing()


@router.post("/capabilities/{capability}", dependencies=[local_only()],
             response_model=CapabilityRoute,
             summary="PC only: choose the engine for a task (null = default)",
             responses={404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def set_capability(capability: str, body: CapabilityEngineRequest):
    return svc.set_capability_engine(capability, body.engine)


@router.post("/engines/{engine}/test", dependencies=[local_only()],
             response_model=EngineRouteStatus,
             summary="PC only: test an engine with one short real call",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def test_engine(engine: str, body: EngineTestRequest = None):
    return svc.test_engine(engine, body.model if body else None)
