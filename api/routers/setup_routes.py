"""
api/routers/setup_routes.py -- what is installed, as booleans, for the controls that need it.

`library.read`, not an admin permission: a household member needs it to see why a
button is disabled. It returns booleans only (api/schemas/setup.py); installing is
a separate, PC-only Diagnostics action.
"""

from fastapi import APIRouter

from api.auth import require_permission
from api.schemas.setup import SetupReadiness
from services import setup_readiness_service

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/readiness", dependencies=[require_permission("library.read")], response_model=SetupReadiness,
            summary="Which optional packages, programs and keys are present (booleans only)")
def get_readiness():
    return setup_readiness_service.get_readiness()
