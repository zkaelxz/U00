"""
api/routers/bug_report_routes.py -- "Report a problem" (the React header
dialog). Thin: see services/bug_report_service.py.

- POST /api/diagnostics/bug-reports is `library.read`: anyone who can use
  the app can report a problem from the device they found it on. The
  server-side section (git commit, setup summary, redacted log tail) is
  saved with the report but only returned to a caller holding
  `admin.diagnostics`, so a household user never reads the server log.
  Multipart: `report` (JSON, max 256 KB) and optional `screenshot`
  (PNG/JPEG, max 5 MB). Uploads are PC-only, so a `screenshot` part is
  accepted only from a direct loopback request (auth on) -- a remote one
  that sends one gets 403, not a silent drop; with auth off every request
  is already loopback. A request with Transfer-Encoding is refused, the
  Content-Length is checked first, and the body stream itself is counted,
  so a body past the cap is cut off with 413 whatever the headers say.
  At most MAX_REPORTS reports are kept (409 past that), and each principal
  may send RATE_MAX reports per RATE_WINDOW seconds (429).
- GET list and GET one (the markdown) are `admin.diagnostics`.
- POST .../{id}/delete is `local_only()` with confirm=true and the folder
  stamp from the list, like the other PC-only deletes.
"""

import json
from typing import List

from fastapi import APIRouter, Path, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from api.auth import is_auth_enabled, holds, is_local_request, local_only, require_permission
from api.schemas import (BugReportClient, BugReportDeleteConfirm, BugReportDeleted,
                         BugReportListItem, BugReportSaved, BugReportText, ErrorResponse)
from services import bug_report_service as svc
from services.auth_service import SlidingWindowRateLimiter
from services.service_errors import ForbiddenError, InvalidInputError

router = APIRouter(prefix="/api/diagnostics/bug-reports", tags=["diagnostics"])

_ERRS = {403: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
         409: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
         422: {"model": ErrorResponse}, 429: {"model": ErrorResponse}}
_MULTIPART_OVERHEAD = 64 * 1024
_MAX_BODY = svc.MAX_JSON_BYTES + svc.MAX_SCREENSHOT_BYTES + _MULTIPART_OVERHEAD
_TOO_LARGE = "The report is too large (text max 256 KB, screenshot max 5 MB)."
_REMOTE_SHOT = ("Screenshots can only be attached at the main PC. "
                "Send the report without it and attach the image to the GitHub issue.")
RATE_MAX, RATE_WINDOW = 5, 600
_limiter = SlidingWindowRateLimiter(RATE_MAX, RATE_WINDOW)

_OPENAPI_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["report"],
    "properties": {"report": {"type": "string", "description": "BugReportClient as JSON"},
                   "screenshot": {"type": "string", "format": "binary"}}}}}}}


class BodyTooLarge(Exception):
    pass


def capped(request: Request, limit: int) -> Request:
    """The same request with a receive channel that counts body bytes and
    stops once more than `limit` arrive, independent of any header."""
    receive, seen = request.receive, 0

    async def counting():
        nonlocal seen
        message = await receive()
        if message.get("type") == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise BodyTooLarge()
        return message

    return Request(request.scope, counting)


def _principal_key(request: Request) -> str:
    principal = getattr(request.state, "principal", None) or {}
    user_id = principal.get("user_id")
    return f"user:{user_id}" if user_id is not None else "local"


def _screenshot_allowed(request: Request) -> bool:
    """Uploads are PC-only: auth on, a direct loopback request; auth off,
    every request (off mode already refuses anything but direct loopback)."""
    return not is_auth_enabled(request.app) or is_local_request(request)


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
    if "transfer-encoding" in request.headers:
        raise InvalidInputError("Send the report with a Content-Length, not chunked.")
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise InvalidInputError("A report needs a Content-Length.")
    if length > _MAX_BODY:
        raise StarletteHTTPException(413, _TOO_LARGE)
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise InvalidInputError("Send the report as multipart/form-data.")
    _limiter.hit(_principal_key(request))
    # A remote caller may send no file part at all: with max_files=0 the
    # parser stops at the file's headers, before any of it is spooled.
    shots_ok = _screenshot_allowed(request)
    try:
        form = await capped(request, _MAX_BODY).form(
            max_files=1 if shots_ok else 0, max_fields=1, max_part_size=svc.MAX_JSON_BYTES)
    except BodyTooLarge:
        raise StarletteHTTPException(413, _TOO_LARGE)
    except (MultiPartException, StarletteHTTPException) as e:
        # Starlette re-raises the parser's MultiPartException as a 400.
        message = str(getattr(e, "detail", None) or getattr(e, "message", None) or e)
        if not shots_ok and message.startswith("Too many files"):
            raise ForbiddenError(_REMOTE_SHOT)
        raise InvalidInputError("The report form could not be read.")
    except Exception:
        raise InvalidInputError("The report form could not be read.")
    try:
        shot = form.get("screenshot")
        if shot is not None and not shots_ok:        # e.g. sent as a plain field
            raise ForbiddenError(_REMOTE_SHOT)
        raw = form.get("report")
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > svc.MAX_JSON_BYTES:
            raise InvalidInputError("The report text is missing or larger than 256 KB.")
        try:
            client = BugReportClient.model_validate(json.loads(raw))
        except (ValueError, RecursionError, ValidationError):
            raise InvalidInputError("The report is not valid (check the required fields).")
        data = await _read_upload(shot, svc.MAX_SCREENSHOT_BYTES) if isinstance(shot, UploadFile) else None
    finally:
        await form.close()
    return await run_in_threadpool(
        svc.create_report, client.model_dump(), data,
        include_server_in_response=holds(request, "admin.diagnostics"))


@router.get("", dependencies=[require_permission("admin.diagnostics")],
            response_model=List[BugReportListItem],
            summary="Saved problem reports, newest first (no paths)")
def list_bug_reports():
    return svc.list_reports()


@router.get("/{report_id}", dependencies=[require_permission("admin.diagnostics")],
            response_model=BugReportText, responses=_ERRS,
            summary="One saved problem report as markdown")
def get_bug_report(report_id: int = Path(ge=1, le=10**9)):
    return svc.get_report(report_id)


@router.post("/{report_id}/delete", dependencies=[local_only()],
             response_model=BugReportDeleted, responses=_ERRS,
             summary="PC only: delete a saved problem report (confirm=true)")
def delete_bug_report(body: BugReportDeleteConfirm, report_id: int = Path(ge=1, le=10**9)):
    return svc.delete_report(report_id, stamp=body.stamp, confirm=body.confirm)
