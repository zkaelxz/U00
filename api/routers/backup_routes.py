"""
api/routers/backup_routes.py -- automatic backups and single-drama restore
(roadmap Step 43 as redefined 2026-09-29) over
services/auto_backup_service.py: settings, "Back up now", the one
snapshot's info, the dramas inside it, restoring one drama, deleting the
snapshot.

Thin adapter. Every route is local_only (backups and restores are PC-only).
Restore and delete need confirm=true plus the typed word (RESTORE, DELETE).
The drama to restore is a body field (an id inside the snapshot, not a live
drama), so there is no path parameter to guard.
"""

from fastapi import APIRouter, Request

from api.auth import local_only
from api.backup_schemas import (
    AutoBackupSettings, AutoBackupSettingsUpdate, BackupJobStarted, BackupNowRequest,
    DeleteSnapshotDone, DeleteSnapshotRequest, RestoreDramaDone, RestoreDramaRequest,
    SnapshotDramaList, SnapshotInfo)
from api.schemas import ErrorResponse
from services import auto_backup_service as abs_

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
             summary="Back up now, replacing the snapshot (replace=true when one exists)")
def post_now(body: BackupNowRequest = None):
    body = body or BackupNowRequest()
    return abs_.start_now(replace=body.replace, include_media=body.include_media)


@router.get("/snapshot", dependencies=[local_only()], response_model=SnapshotInfo,
            summary="The snapshot's date, size and kind (exists=false when there is none)")
def get_snapshot():
    return abs_.snapshot_info()


@router.get("/snapshot/dramas", dependencies=[local_only()], response_model=SnapshotDramaList,
            responses=_ERR_404, summary="The dramas inside the snapshot")
def get_snapshot_dramas():
    return abs_.list_snapshot_dramas()


@router.post("/snapshot/restore-drama", dependencies=[local_only()],
             response_model=RestoreDramaDone, responses=_ERR_404,
             summary="Restore one drama from the snapshot (confirm=true, confirm_text=RESTORE)")
def post_restore_drama(body: RestoreDramaRequest, request: Request):
    principal = getattr(request.state, "principal", None) or {}
    return abs_.restore_drama(body.drama_id, confirm=body.confirm,
                              confirm_text=body.confirm_text,
                              actor_id=principal.get("user_id"))


@router.post("/snapshot/delete", dependencies=[local_only()], response_model=DeleteSnapshotDone,
             responses=_ERR_404,
             summary="Delete the snapshot (confirm=true, confirm_text=DELETE)")
def post_delete_snapshot(body: DeleteSnapshotRequest):
    return abs_.delete_snapshot(confirm=body.confirm, confirm_text=body.confirm_text)
