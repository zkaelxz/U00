"""
api/routers/backup_routes.py -- automatic backups and single-drama restore
(roadmap Step 43 as redefined 2026-09-29) over
services/auto_backup_service.py: settings, "Back up now", the rotating
copies' info, the dramas inside a copy, restoring one drama, deleting a
copy (or all of them).

Thin adapter. Every route is local_only (backups and restores are PC-only).
Restore and delete need confirm=true plus the typed word (RESTORE, DELETE).
The drama to restore is a body field (an id inside the snapshot, not a live
drama), so there is no path parameter to guard. A copy is chosen by its
file name (query or body field), which the service matches against the
backup folder's own listing and never uses as a path. With no copy named,
the dramas list and the restore use the service's default pick, or answer
409 with details {reason: "choose_copy", candidates} when the newest copy
can't be told for sure.
"""

from typing import Optional

from fastapi import APIRouter, Query, Request

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
