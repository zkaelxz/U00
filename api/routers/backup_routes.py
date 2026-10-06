"""
api/routers/backup_routes.py -- automatic backups and single-drama restore
over
services/auto_backup_service.py: settings, "Back up now", the rotating
copies' info, the dramas inside a copy, restoring one drama, deleting a
copy (or all of them), and importing chosen dramas from an uploaded backup
file (services/backup_import_service.py).

Thin adapter. Every route is local_only (backups and restores are PC-only).
Restore and delete need confirm=true plus the typed word (RESTORE, DELETE).
The drama to restore is a body field (an id inside the snapshot, not a live
drama), so there is no path parameter to guard. A copy is chosen by its
file name (query or body field), which the service matches against the
backup folder's own listing and never uses as a path. With no copy named,
the dramas list and the restore use the service's default pick, or answer
409 with details {reason: "choose_copy", candidates} when the newest copy
can't be told for sure.
The two import
routes read their multipart body themselves so the upload cap
(BAIHE_MAX_UPLOAD_MB) is enforced before the body is spooled.
"""

from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException

from api.auth import local_only
from api.backup_schemas import (
    AutoBackupSettings, AutoBackupSettingsUpdate, BackupFileDramaList, BackupJobStarted,
    BackupNowRequest, DeleteSnapshotDone, DeleteSnapshotRequest, ImportDramasDone,
    RestoreDramaDone, RestoreDramaRequest, SnapshotDramaList, SnapshotInfo)
from api.routers.bug_report_routes import BodyTooLarge, capped
from api.schemas import ErrorResponse
from services import auto_backup_service as abs_
from services import backup_import_service as bis
from services import media_upload_service
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/backups", tags=["backups"])

_ERR = {422: {"model": ErrorResponse}}
_ERR_409 = {409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_ERR_404 = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
            422: {"model": ErrorResponse}}


@router.get("/settings", dependencies=[local_only()], response_model=AutoBackupSettings,
            summary="Automatic backup settings, last and next run")
def get_settings():
    return abs_.settings_overview()


@router.post("/settings", dependencies=[local_only()], response_model=AutoBackupSettings,
             responses=_ERR, summary="Change the automatic backup settings")
def post_settings(body: AutoBackupSettingsUpdate):
    return abs_.set_settings(enabled=body.enabled, frequency=body.frequency,
                             include_media=body.include_media, folder=body.folder)


@router.post("/now", dependencies=[local_only()], response_model=BackupJobStarted,
             responses=_ERR_409,
             summary="Back up now: adds a new copy, then rotates the old ones "
                     "(replace is ignored)")
def post_now(body: BackupNowRequest = None):
    body = body or BackupNowRequest()
    return abs_.start_now(replace=body.replace, include_media=body.include_media)


@router.get("/snapshot", dependencies=[local_only()], response_model=SnapshotInfo,
            summary="The copies (newest first) and the newest copy's date, size and kind "
                    "(exists=false when there is none)")
def get_snapshot():
    return abs_.snapshot_info()


@router.get("/snapshot/dramas", dependencies=[local_only()], response_model=SnapshotDramaList,
            responses=_ERR_404,
            summary="The dramas inside a copy (snapshot=<name>, else the default copy; "
                    "409 choose_copy when there is none)")
def get_snapshot_dramas(snapshot: Optional[str] = Query(None, min_length=1, max_length=64)):
    return abs_.list_snapshot_dramas(snapshot)


@router.post("/snapshot/restore-drama", dependencies=[local_only()],
             response_model=RestoreDramaDone, responses=_ERR_404,
             summary="Restore one drama from a copy (confirm=true, confirm_text=RESTORE; "
                     "409 choose_copy when no copy is named and none is the clear default)")
def post_restore_drama(body: RestoreDramaRequest, request: Request):
    principal = getattr(request.state, "principal", None) or {}
    return abs_.restore_drama(body.drama_id, confirm=body.confirm,
                              confirm_text=body.confirm_text,
                              actor_id=principal.get("user_id"), snapshot=body.snapshot)


@router.post("/snapshot/delete", dependencies=[local_only()], response_model=DeleteSnapshotDone,
             responses=_ERR_404,
             summary="Delete one copy (snapshot=<name>) or every managed copy (all=true); "
                     "unmanaged copies need include_unmanaged=true; confirm=true, "
                     "confirm_text=DELETE")
def post_delete_snapshot(body: DeleteSnapshotRequest):
    return abs_.delete_snapshot(confirm=body.confirm, confirm_text=body.confirm_text,
                                snapshot=body.snapshot, all_copies=body.all,
                                include_unmanaged=body.include_unmanaged)


# Room for the multipart boundaries and the small form fields.
_MULTIPART_OVERHEAD = 64 * 1024
_UPLOAD_TOO_LARGE = "The uploaded file is too large."
_FORM_HELP = ("Send the backup as multipart/form-data: a 'file' part, plus drama_ids, "
              "confirm and confirm_text for an import.")
_LIST_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file"],
    "properties": {"file": {"type": "string", "format": "binary"}}}}}}}
_IMPORT_BODY = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object", "required": ["file", "drama_ids"],
    "properties": {"file": {"type": "string", "format": "binary"},
                   "drama_ids": {"type": "array", "items": {"type": "integer"}},
                   "confirm": {"type": "string"}, "confirm_text": {"type": "string"}}}}}}}


async def _read_form(request: Request, max_fields: int):
    """The multipart form, refused (413) past BAIHE_MAX_UPLOAD_MB: the
    Content-Length is checked before any of the body is read (chunked
    bodies are refused) and the body stream itself is counted."""
    if "transfer-encoding" in request.headers:
        raise InvalidInputError("Send the backup file with a Content-Length, not chunked.")
    try:
        length = int(request.headers.get("content-length", ""))
    except ValueError:
        raise InvalidInputError("An upload needs a Content-Length.")
    cap = media_upload_service.max_upload_bytes() + _MULTIPART_OVERHEAD
    if length > cap:
        raise StarletteHTTPException(413, _UPLOAD_TOO_LARGE)
    try:
        return await capped(request, cap).form(max_files=1, max_fields=max_fields,
                                                max_part_size=1024)
    except BodyTooLarge:
        raise StarletteHTTPException(413, _UPLOAD_TOO_LARGE)
    except (MultiPartException, StarletteHTTPException):
        raise InvalidInputError(_FORM_HELP) from None


def _upload(form) -> UploadFile:
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise InvalidInputError(_FORM_HELP)
    return upload


def _drama_ids(form) -> list:
    out = []
    for raw in form.getlist("drama_ids"):
        if not isinstance(raw, str) or not (raw.isascii() and raw.isdigit()) or len(raw) > 10:
            raise InvalidInputError("A drama id is a positive whole number.")
        out.append(int(raw))
    return out


@router.post("/import/list", dependencies=[local_only()], response_model=BackupFileDramaList,
             openapi_extra=_LIST_BODY,
             responses={413: {"model": ErrorResponse}, **_ERR},
             summary="List the dramas inside an uploaded backup file (.zip or library.db); "
                     "changes nothing")
async def post_import_list(request: Request):
    form = await _read_form(request, max_fields=0)
    try:
        return await run_in_threadpool(bis.list_backup_dramas, _upload(form).file)
    finally:
        await form.close()


@router.post("/import", dependencies=[local_only()], response_model=ImportDramasDone,
             openapi_extra=_IMPORT_BODY,
             responses={413: {"model": ErrorResponse}, **_ERR_404},
             summary="Import chosen dramas from an uploaded backup file as new dramas "
                     "(confirm=true, confirm_text=RESTORE)",
             description="Multipart: file, drama_ids (repeat the field; ids as listed by "
                         "/import/list), confirm (the string \"true\"), confirm_text.")
async def post_import(request: Request):
    form = await _read_form(request, max_fields=bis.MAX_DRAMAS_PER_IMPORT + 2)
    try:
        # Checked before the uploaded file is opened or the library is locked.
        if (form.get("confirm") != "true"
                or form.get("confirm_text") != bis.RESTORE_CONFIRM_TEXT):
            raise InvalidInputError(f"Importing dramas needs confirm=true and confirm_text set "
                                    f"to the word {bis.RESTORE_CONFIRM_TEXT}, in capitals.")
        upload, drama_ids = _upload(form), _drama_ids(form)
        principal = getattr(request.state, "principal", None)
        return await run_in_threadpool(
            bis.import_dramas, upload.file, drama_ids, confirm=True,
            confirm_text=bis.RESTORE_CONFIRM_TEXT, principal=principal)
    finally:
        await form.close()
