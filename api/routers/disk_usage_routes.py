"""
api/routers/disk_usage_routes.py -- /api/data-usage/*: what is taking space in
the app's data folder, send an item to the Recycle Bin, move the automatic-
backup folder. Thin adapter over services/disk_usage_service.py.

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
    DiskUsageScan)
from api.schemas import ErrorResponse
from services import disk_usage_service as svc

router = APIRouter(prefix="/api/data-usage", tags=["disk-usage"])

_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
        422: {"model": ErrorResponse}}


@router.get("", dependencies=[local_only()], response_model=DiskUsageScan, responses=_ERR,
            summary="Size of each item in a data-folder folder (path=<relative>, default the "
                    "data folder), biggest first; partial=true when a walk limit was hit")
async def get_scan(path: Optional[str] = Query(None, max_length=1024)):
    return await run_in_threadpool(svc.scan, path or "")


@router.post("/recycle", dependencies=[local_only()], response_model=DiskUsageClearDone,
             responses=_ERR,
             summary="Send one item to the Recycle Bin (confirm=true and the size and file "
                     "count shown; 409 when it changed or a job runs)")
async def post_recycle(body: DiskUsageClearRequest):
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
