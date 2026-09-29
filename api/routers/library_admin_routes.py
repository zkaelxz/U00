"""
api/routers/library_admin_routes.py -- the Library tab's admin actions
(route batch 2A) over services/library_admin_service.py: bulk status, tags,
delete and translate, export zip, backup, artifact info/download, restore
and storage scan/cleanup.

Thin adapter. Destructive actions need confirm=true plus the typed word
(DELETE, RESTORE, CLEAN); deletes, exports, backups, downloads, restore and
cleanup are local_only (the owner at the PC). No response, header or log
line carries a filesystem path: artifacts are addressed by kind only and
streamed from an already-open file handle.
"""

import os
import re

from fastapi import APIRouter, File, Form, Path, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from api.auth import local_only, require_engines_allowed, require_permission
from api.schemas import (
    ErrorResponse, LibraryArtifactInfo, LibraryArtifactKind, LibraryBackupRequest,
    LibraryBulkDeleteRequest, LibraryBulkDeleteResult, LibraryBulkResult,
    LibraryBulkStatusRequest, LibraryBulkTagRequest, LibraryBulkTranslateRequest,
    LibraryBulkTranslateStarted, LibraryExportRequest, LibraryExportStarted, LibraryJobStarted,
    LibraryRestoreDone, LibraryStorageCleanRequest, LibraryStorageCleanResult,
    LibraryStoragePreset, LibraryStorageScan)
from services import library_admin_service as las
from services import media_upload_service
from services.service_errors import InvalidInputError, NotFoundError

router = APIRouter(prefix="/api/library/admin", tags=["library-admin"])

_CHUNK = 1024 * 1024
_ERR = {422: {"model": ErrorResponse}}
_ERR_409 = {409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_ERR_404 = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}}
_MEDIA_TYPES = {"backup": "application/zip", "export": "application/zip",
                "database": "application/octet-stream"}
_NO_ARTIFACT = "No artifact available."
_TOO_LARGE = "The uploaded file is too large."


def _redacted(result: dict) -> dict:
    """Per-id messages come from service errors; strip anything key-like."""
    from translate_engines import redact_secrets
    for item in result.get("results") or ():
        if item.get("message"):
            item["message"] = redact_secrets(item["message"])
    return result


@router.post("/bulk/status", dependencies=[require_permission("admin.library")],
             response_model=LibraryBulkResult, responses=_ERR,
             summary="Set the status of several dramas (per-id results)")
def post_bulk_status(body: LibraryBulkStatusRequest):
    return las.bulk_set_status(body.drama_ids, body.status)


@router.post("/bulk/tags", dependencies=[require_permission("admin.library")],
             response_model=LibraryBulkResult, responses=_ERR,
             summary="Add or remove one list tag on several dramas (per-id results)")
def post_bulk_tags(body: LibraryBulkTagRequest):
    return las.bulk_set_tags(body.drama_ids, body.tag, body.present)


@router.post("/bulk/delete", dependencies=[local_only()], response_model=LibraryBulkDeleteResult,
             responses=_ERR_409,
             summary="Permanently delete several dramas (confirm=true, confirm_text=DELETE)")
def post_bulk_delete(body: LibraryBulkDeleteRequest):
    return _redacted(las.bulk_delete(body.drama_ids, confirm=body.confirm,
                                     confirm_text=body.confirm_text))


@router.post("/bulk/translate", dependencies=[require_permission("jobs.start")],
             response_model=LibraryBulkTranslateStarted,
             responses={403: {"model": ErrorResponse}, **_ERR_409},
             summary="Start the bulk translate job for the picked untranslated dramas")
def post_bulk_translate(body: LibraryBulkTranslateRequest, request: Request):
    principal = request.state.principal
    plan = las.bulk_translate_engines(body.drama_ids, principal=principal)
    require_engines_allowed(request, *plan["engines"])
    return las.start_bulk_translate(body.drama_ids, body.default_locale,
                                    expected_engines=plan["by_drama"], principal=principal)


@router.post("/export", dependencies=[local_only()], response_model=LibraryExportStarted,
             responses=_ERR_409, summary="Start the export-all zip job")
def post_export(body: LibraryExportRequest = None):
    return las.start_export_zip(body.drama_ids if body else None)


@router.post("/backup", dependencies=[local_only()], response_model=LibraryJobStarted,
             responses=_ERR_409, summary="Start a full (or database-only) library backup job")
def post_backup(body: LibraryBackupRequest = None):
    if body is not None and body.database_only:
        return las.start_database_backup()
    return las.start_backup()


@router.get("/artifacts/{kind}/info", dependencies=[require_permission("admin.library")],
            response_model=LibraryArtifactInfo, responses=_ERR_404,
            summary="Name and size of the newest backup/export/database file (no path)")
def get_artifact_info(kind: LibraryArtifactKind = Path()):
    return las.latest_admin_artifact(kind.value)


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "download"


@router.get("/artifacts/{kind}", dependencies=[local_only()], responses=_ERR_404,
            summary="Download the newest backup/export/database file")
def get_artifact(kind: LibraryArtifactKind = Path()):
    info = las.admin_artifact_path(kind.value)
    try:
        fd = os.open(info["path"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                     | getattr(os, "O_BINARY", 0))
    except OSError:
        raise NotFoundError(_NO_ARTIFACT) from None
    fh = os.fdopen(fd, "rb")
    size = os.fstat(fh.fileno()).st_size

    def stream():
        with fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    return
                yield chunk

    return StreamingResponse(stream(), media_type=_MEDIA_TYPES[kind.value], headers={
        "Content-Disposition": f'attachment; filename="{_safe_name(info["name"])}"',
        "Content-Length": str(size), "X-Content-Type-Options": "nosniff"})


def _read_capped(upload: UploadFile) -> bytes:
    """The upload as one bytes object, refused (422) past BAIHE_MAX_UPLOAD_MB.
    One read of limit+1 bytes, so the peak is the upload itself, not a copy."""
    limit = media_upload_service.max_upload_bytes()
    size = getattr(upload, "size", None)
    if size is not None and size > limit:
        raise InvalidInputError(_TOO_LARGE)
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise InvalidInputError(_TOO_LARGE)
    return data


@router.post("/restore", dependencies=[local_only()], response_model=LibraryRestoreDone,
             responses=_ERR_409,
             summary="Replace the whole library with an uploaded backup "
                     "(confirm=true, confirm_text=RESTORE)",
             description="Multipart: file, confirm (the string \"true\"), confirm_text. Every "
                         "sign-in session is revoked afterwards.")
def post_restore(request: Request, file: UploadFile = File(...),
                 confirm: str = Form("", max_length=8),
                 confirm_text: str = Form("", max_length=32)):
    # Checked before the upload is read or validated.
    if confirm != "true" or confirm_text != las.RESTORE_CONFIRM_TEXT:
        raise InvalidInputError(f"Restoring a backup needs confirm=true and confirm_text set "
                                f"to the word {las.RESTORE_CONFIRM_TEXT}, in capitals.")
    data = _read_capped(file)
    principal = getattr(request.state, "principal", None) or {}
    return las.restore_backup(data, confirm=True, confirm_text=confirm_text,
                              actor_id=principal.get("user_id"))


@router.get("/storage", dependencies=[require_permission("admin.library")],
            response_model=LibraryStorageScan, responses=_ERR,
            summary="Dry run: what a storage cleanup preset would free")
def get_storage(preset: LibraryStoragePreset = Query("balanced")):
    return las.storage_scan(preset)


@router.post("/storage/clean", dependencies=[local_only()], response_model=LibraryStorageCleanResult,
             responses=_ERR_409,
             summary="Apply a storage cleanup preset (confirm=true, confirm_text=CLEAN)")
def post_storage_clean(body: LibraryStorageCleanRequest):
    return las.storage_cleanup(body.preset, confirm=body.confirm, confirm_text=body.confirm_text)
