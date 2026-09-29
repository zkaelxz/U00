"""
api/routers/bug_report_routes.py -- "Report a problem" (the React header
dialog). Thin: see services/bug_report_service.py.

- POST /api/diagnostics/bug-reports is `library.read`: anyone who can use
  the app can report a problem from the device they found it on. The
  server-side section (git commit, setup summary, redacted log tail) is
  saved with the report but only returned to a caller holding
  `admin.diagnostics`, so a household user never reads the server log.
  Multipart: `report` (JSON, max 256 KB) and optional `screenshot`
  (PNG/JPEG, max 5 MB). The Content-Length is checked before the body is
  parsed. At most MAX_REPORTS reports are kept (409 past that).
- GET list and GET one (the markdown) are `admin.diagnostics`.
- POST .../{id}/delete is `local_only()` with confirm=true, like the other
  PC-only deletes (the existing bug-bundles delete route is for the
  per-line replay bundles in the database, a different store).
"""

import json
from typing import List

from fastapi import APIRouter, Path, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from api.auth import _holds, local_only, require_permission
from api.schemas import (BugReportClient, BugReportDeleted, BugReportListItem, BugReportSaved,
                         DeleteConfirm, ErrorResponse)
from services import bug_report_service as svc
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/diagnostics/bug-reports", tags=["diagnostics"])

_ERRS = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
         422: {"model": ErrorResponse}}
_MULTIPART_OVERHEAD = 64 * 1024
_MAX_BODY = svc.MAX_JSON_BYTES + svc.MAX_SCREENSHOT_BYTES + _MULTIPART_OVERHEAD
_TOO_LARGE = "The report is too large (text max 256 KB, screenshot max 5 MB)."

_OPENAPI_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["report"],
    "properties": {"report": {"type": "string", "description": "BugReportClient as JSON"},
                   "screenshot": {"type": "string", "format": "binary"}}}}}}}


async def _read_upload(upload, limit: int) -> bytes:
    if getattr(upload, "size", None) is not None and upload.size > limit:
        raise InvalidInputError(_TOO_LARGE)
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise InvalidInputError(_TOO_LARGE)
    return data


@router.post("", dependencies=[require_permission("library.read")],
             response_model=BugReportSaved, responses=_ERRS, openapi_extra=_OPENAPI_BODY,
             summary="Save a problem report from the app; returns its id and markdown")
async def post_bug_report(request: Request):
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise InvalidInputError("A report needs a Content-Length.")
    if length > _MAX_BODY:
        raise InvalidInputError(_TOO_LARGE)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise InvalidInputError("Send the report as multipart/form-data.")
    try:
        form = await request.form(max_files=1, max_fields=1, max_part_size=svc.MAX_JSON_BYTES)
    except Exception:
        raise InvalidInputError("The report form could not be read.")
    try:
        raw = form.get("report")
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > svc.MAX_JSON_BYTES:
            raise InvalidInputError("The report text is missing or larger than 256 KB.")
        try:
            client = BugReportClient.model_validate(json.loads(raw))
        except (ValueError, ValidationError):
            raise InvalidInputError("The report is not valid (check the required fields).")
        shot = form.get("screenshot")
        data = await _read_upload(shot, svc.MAX_SCREENSHOT_BYTES) if isinstance(shot, UploadFile) else None
    finally:
        await form.close()
    return await run_in_threadpool(
        svc.create_report, client.model_dump(), data,
        include_server_in_response=_holds(request, "admin.diagnostics"))


@router.get("", dependencies=[require_permission("admin.diagnostics")],
            response_model=List[BugReportListItem],
            summary="Saved problem reports, newest first (no paths)")
def list_bug_reports():
    return svc.list_reports()


@router.get("/{report_id}", dependencies=[require_permission("admin.diagnostics")],
            response_model=BugReportSaved, responses=_ERRS,
            summary="One saved problem report as markdown")
def get_bug_report(report_id: int = Path(ge=1, le=10**9)):
    return svc.get_report(report_id)


@router.post("/{report_id}/delete", dependencies=[local_only()],
             response_model=BugReportDeleted, responses=_ERRS,
             summary="PC only: delete a saved problem report (confirm=true)")
def delete_bug_report(body: DeleteConfirm, report_id: int = Path(ge=1, le=10**9)):
    return svc.delete_report(report_id, confirm=body.confirm)
