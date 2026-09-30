"""
api/routers/backup_routes.py -- automatic backups and single-drama restore
(roadmap Step 43 as redefined 2026-09-29) over
services/auto_backup_service.py: settings, "Back up now", the rotating
copies' info, the dramas inside a copy, restoring one drama, deleting a
copy (or all of them), and importing chosen dramas from an uploaded backup
file (services/backup_import_service.py).

Thin adapter. Every route is local_only (backups and restores are PC-only).
Restore and delete need confirm=true plus the typed word (RESTORE, DELETE).
The drama to restore is a body field (an id inside the snapshot, not a live
drama), so there is no path parameter to guard. A copy is chosen by its
file name (query or body field), which the service matches against the
backup folder's own listing and never uses as a path.
"""

from typing import List, Optional

from fastapi import APIRouter, File, Form, Query, Request, UploadFile

from api.auth import local_only
from api.backup_schemas import (
    AutoBackupSettings, AutoBackupSettingsUpdate, BackupFileDramaList, BackupJobStarted,
    BackupNowRequest, DeleteSnapshotDone, DeleteSnapshotRequest, ImportDramasDone,
    RestoreDramaDone, RestoreDramaRequest, SnapshotDramaList, SnapshotInfo)
from api.schemas import ErrorResponse
from services import auto_backup_service as abs_
from services import backup_import_service as bis
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
            summary="The dramas inside a copy (snapshot=<name>, else the newest readable)")
def get_snapshot_dramas(snapshot: Optional[str] = Query(None, min_length=1, max_length=64)):
    return abs_.list_snapshot_dramas(snapshot)


@router.post("/snapshot/restore-drama", dependencies=[local_only()],
             response_model=RestoreDramaDone, responses=_ERR_404,
             summary="Restore one drama from the snapshot (confirm=true, confirm_text=RESTORE)")
def post_restore_drama(body: RestoreDramaRequest, request: Request):
    principal = getattr(request.state, "principal", None) or {}
    return abs_.restore_drama(body.drama_id, confirm=body.confirm,
                              confirm_text=body.confirm_text,
                              actor_id=principal.get("user_id"), snapshot=body.snapshot)


@router.post("/snapshot/delete", dependencies=[local_only()], response_model=DeleteSnapshotDone,
             responses=_ERR_404,
             summary="Delete one copy (snapshot=<name>) or every copy (all=true); "
                     "confirm=true, confirm_text=DELETE")
def post_delete_snapshot(body: DeleteSnapshotRequest):
    return abs_.delete_snapshot(confirm=body.confirm, confirm_text=body.confirm_text,
                                snapshot=body.snapshot, all_copies=body.all)


@router.post("/import/list", dependencies=[local_only()], response_model=BackupFileDramaList,
             responses=_ERR,
             summary="List the dramas inside an uploaded backup file (.zip or library.db); "
                     "changes nothing")
def post_import_list(file: UploadFile = File(...)):
    return bis.list_backup_dramas(file.file)


@router.post("/import", dependencies=[local_only()], response_model=ImportDramasDone,
             responses=_ERR_404,
             summary="Import chosen dramas from an uploaded backup file as new dramas "
                     "(confirm=true, confirm_text=RESTORE)",
             description="Multipart: file, drama_ids (repeat the field; ids as listed by "
                         "/import/list), confirm (the string \"true\"), confirm_text.")
def post_import(request: Request, file: UploadFile = File(...),
                drama_ids: List[int] = Form(...),
                confirm: str = Form("", max_length=8),
                confirm_text: str = Form("", max_length=32)):
    # Checked before the upload is read.
    if confirm != "true" or confirm_text != bis.RESTORE_CONFIRM_TEXT:
        raise InvalidInputError(f"Importing dramas needs confirm=true and confirm_text set to "
                                f"the word {bis.RESTORE_CONFIRM_TEXT}, in capitals.")
    principal = getattr(request.state, "principal", None)
    return bis.import_dramas(file.file, drama_ids, confirm=True, confirm_text=confirm_text,
                             principal=principal)
