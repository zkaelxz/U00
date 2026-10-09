"""
api/routers/disk_usage_routes.py -- /api/data-usage/*: what is taking space in
the app's data folder, move an item into Baihe's Trash folder, restore or
permanently delete from it, move the automatic-backup folder, and list unused
voice clips (GET /unused-voice-clips) and move them to Trash
(POST /unused-voice-clips/to-trash). Thin adapter over services/disk_usage_service.py
and services/disk_usage_clips_service.py.

Every route is local_only(): it reads and removes files on the PC. Paths in
requests and responses are relative to the data folder; the path guard
(drama/series ids) has nothing to check here, so the service validates every
path itself (no '..', drive letters, absolute paths or links). The scan walks
the disk, so it runs in the thread pool (a bounded, read-only walk: 2,000,000
entries or 30 s, then partial=true) rather than as a background job, which
would also block the clear and move it exists to serve.
"""

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool

from api.auth import local_only
from api.disk_usage_schemas import (
    DiskUsageClearDone, DiskUsageClearRequest, DiskUsageMoveDone, DiskUsageMoveRequest,
    DiskUsageScan, DiskUsageTrashEmptyDone, DiskUsageTrashEmptyRequest, DiskUsageTrashList,
    DiskUsageTrashPurgeDone, DiskUsageTrashPurgeRequest, DiskUsageTrashRestoreDone,
    DiskUsageTrashRestoreRequest, TempCleanRequest, UnusedVoiceClipList, UnusedVoiceClipTrashDone,
    UnusedVoiceClipTrashRequest)
from api.schemas import ErrorResponse, TempCleanResult
from services import disk_usage_clips_service as clips_svc
from services import disk_usage_service as svc
from services import temp_cleanup_service

router = APIRouter(prefix="/api/data-usage", tags=["disk-usage"])

_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
        422: {"model": ErrorResponse}}


@router.get("", dependencies=[local_only()], response_model=DiskUsageScan, responses=_ERR,
            summary="Size of each item in a data-folder folder (path=<relative>, default the "
                    "data folder), biggest first; partial=true when a walk limit was hit")
async def get_scan(path: Optional[str] = Query(None, max_length=1024)):
    return await run_in_threadpool(svc.scan, path or "")


@router.post("/to-trash", dependencies=[local_only()], response_model=DiskUsageClearDone,
             responses=_ERR,
             summary="Move one item into Baihe's Trash folder (confirm=true and the size and "
                     "file count shown; 409 when it changed or a job runs). Frees nothing.")
async def post_to_trash(body: DiskUsageClearRequest):
    return await run_in_threadpool(
        svc.clear, body.path, confirm=body.confirm,
        expected_size_bytes=body.expected_size_bytes,
        expected_file_count=body.expected_file_count,
        confirm_irreplaceable=body.confirm_irreplaceable)


@router.post("/move", dependencies=[local_only()], response_model=DiskUsageMoveDone,
             responses=_ERR,
             summary="Move a movable item (the automatic-backup folder) to another folder and "
                     "repoint Baihe at it (confirm=true)")
async def post_move(body: DiskUsageMoveRequest):
    return await run_in_threadpool(svc.move, body.path, body.destination, confirm=body.confirm)


@router.get("/trash", dependencies=[local_only()], response_model=DiskUsageTrashList,
            responses=_ERR,
            summary="What is in Baihe's Trash folder: where each item came from, its size and "
                    "whether it can be restored now")
async def get_trash():
    return await run_in_threadpool(svc.trash_list)


@router.post("/trash/restore", dependencies=[local_only()],
             response_model=DiskUsageTrashRestoreDone, responses=_ERR,
             summary="Put a Trash item back where it came from (confirm=true; 409 with the "
                     "reason when the old place is gone, taken or protected, or a job runs)")
async def post_trash_restore(body: DiskUsageTrashRestoreRequest):
    return await run_in_threadpool(svc.trash_restore, body.id, confirm=body.confirm)


@router.post("/trash/purge", dependencies=[local_only()],
             response_model=DiskUsageTrashPurgeDone, responses=_ERR,
             summary="PERMANENTLY delete one Trash item (confirm_text='DELETE' and the size "
                     "shown; 409 when it changed or a job runs)")
async def post_trash_purge(body: DiskUsageTrashPurgeRequest):
    return await run_in_threadpool(
        svc.trash_purge, body.id, confirm_text=body.confirm_text,
        expected_size_bytes=body.expected_size_bytes)


@router.post("/trash/empty", dependencies=[local_only()],
             response_model=DiskUsageTrashEmptyDone, responses=_ERR,
             summary="PERMANENTLY delete everything in Trash (confirm_text='DELETE' and "
                     "the item count and size shown; 409 when it changed or a job runs)")
async def post_trash_empty(body: DiskUsageTrashEmptyRequest):
    return await run_in_threadpool(
        svc.trash_empty, confirm_text=body.confirm_text,
        expected_item_count=body.expected_item_count,
        expected_size_bytes=body.expected_size_bytes)


@router.get("/unused-voice-clips", dependencies=[local_only()],
            response_model=UnusedVoiceClipList, responses=_ERR,
            summary="Voice clips no speaker uses, per title: type, size and date only (no "
                    "file names or paths), with an opaque id for each")
async def get_unused_voice_clips():
    return await run_in_threadpool(clips_svc.unused_voice_clips)


@router.post("/unused-voice-clips/to-trash", dependencies=[local_only()],
             response_model=UnusedVoiceClipTrashDone, responses=_ERR,
             summary="Move listed unused clips into Baihe's Trash folder (confirm=true and the "
                     "size shown for each). A clip that is used or changed meanwhile is skipped.")
async def post_unused_voice_clips_to_trash(body: UnusedVoiceClipTrashRequest):
    return await run_in_threadpool(
        clips_svc.trash_unused_voice_clips,
        [{"id": c.id, "expected_size_bytes": c.expected_size_bytes} for c in body.clips],
        confirm=body.confirm)


@router.post("/clean-temp", dependencies=[local_only()], response_model=TempCleanResult,
             responses=_ERR,
             summary="Delete everything in Baihe's own temp folder (confirm=true; 409 while a job "
                     "runs); returns how many entries and how many MB, never a path")
async def post_clean_temp(body: TempCleanRequest):
    return await run_in_threadpool(temp_cleanup_service.clean_now)
