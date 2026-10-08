"""
api/routers/spend_history_routes.py -- paid-API spend by month. Thin: see
services/spend_history_service.py.

- `GET /api/settings/spend-history` (`admin.settings`): month table and the
  breakdowns for one month; numbers and titles only, writes nothing.
- `GET .../export.csv` (`local_only()`): the month table as a file, PC only.
"""

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from api.auth import local_only, require_permission
from api.schemas import ErrorResponse, SpendHistory
from services import spend_history_service

router = APIRouter(prefix="/api/settings/spend-history", tags=["settings"])


@router.get("", dependencies=[require_permission("admin.settings")], response_model=SpendHistory,
            summary="Estimated paid-API spend by month, with the operations, models and titles behind one month",
            responses={422: {"model": ErrorResponse}})
def spend_history(request: Request, month: Optional[str] = Query(None, max_length=7)):
    return spend_history_service.get_spend_history(month, principal=request.state.principal)


@router.get("/export.csv", dependencies=[local_only()], response_class=Response,
            summary="PC only: the month table as CSV")
def spend_history_csv(request: Request):
    body = spend_history_service.export_months_csv(principal=request.state.principal)
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="spend-history.csv"'})
