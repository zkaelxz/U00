"""
api/routers/notion_routes.py -- the Notion export. See
services/notion_service.py.

Every route is local_only(): the settings hold the owner's Notion token and
an export sends the drama's text off this PC to Notion, so none of it is
offered to a remote device (a new permission was not added: this keeps it
at the PC, the safer default). Setting or clearing the token also takes the
engine-key gate (`_require_local_admin`, key writes allowed in the API
settings) and confirm=true, like the engine keys. The token is never
returned.
"""

from fastapi import APIRouter, Path, Request

from api.auth import local_only
from api.notion_schemas import (NotionConfig, NotionConfigUpdate, NotionExportRequest,
                                NotionExportStarted, NotionExportStatus, NotionTestRequest,
                                NotionTestResult, NotionTokenClear, NotionTokenSet)
from api.routers.settings_routes import read_body, require_confirm, require_local_admin
from api.schemas import ErrorResponse
from services import notion_service

router = APIRouter(prefix="/api/notion", tags=["notion"])

_ERRS = {409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
         503: {"model": ErrorResponse}}


@router.get("/config", dependencies=[local_only()], response_model=NotionConfig,
            summary="PC only: the Notion export settings (the token as a boolean)")
def get_config():
    return notion_service.get_config()


@router.post("/config", dependencies=[local_only()], response_model=NotionConfig,
             summary="PC only: save the Notion target (a database or a parent page)",
             responses={422: {"model": ErrorResponse}})
def set_config(payload: NotionConfigUpdate):
    return notion_service.set_config(target_type=payload.target_type,
                                     target_id=payload.target_id)


@router.post("/token", dependencies=[local_only()], response_model=NotionConfig,
             summary="PC only: set the Notion integration token (write-only; same gate "
                     "as engine keys)",
             responses={422: {"model": ErrorResponse}})
async def set_token(request: Request):
    require_local_admin(request)
    body = await read_body(request, NotionTokenSet)
    require_confirm(body.confirm)
    return notion_service.set_token(body.value)


@router.post("/token/clear", dependencies=[local_only()], response_model=NotionConfig,
             summary="PC only: remove the Notion token from .env",
             responses={422: {"model": ErrorResponse}})
async def clear_token(request: Request):
    require_local_admin(request)
    body = await read_body(request, NotionTokenClear)
    require_confirm(body.confirm)
    return notion_service.clear_token()


@router.post("/test", dependencies=[local_only()], response_model=NotionTestResult,
             summary="PC only: check the Notion token and that the target is shared "
                     "with the integration", responses=_ERRS)
def test_connection(payload: NotionTestRequest):
    return notion_service.test_connection()


@router.get("/dramas/{drama_id}", dependencies=[local_only()],
            response_model=NotionExportStatus,
            summary="PC only: the Notion page this drama was last exported to",
            responses={404: {"model": ErrorResponse}})
def export_status(drama_id: int = Path(ge=1)):
    return notion_service.export_status(drama_id)


@router.post("/dramas/{drama_id}/export", dependencies=[local_only()],
             response_model=NotionExportStarted,
             summary="PC only: export the drama's transcript and details to Notion "
                     "(a job; a re-export updates the same page)",
             responses={**_ERRS, 404: {"model": ErrorResponse}})
def export(payload: NotionExportRequest, drama_id: int = Path(ge=1)):
    return notion_service.start_export(drama_id, field=payload.field)
