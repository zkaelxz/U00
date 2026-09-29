"""
api/routers/novel_files_routes.py -- the English novel translation
reference and the raw original-language novel for one drama (parity audit
B1 #3/#4). Thin adapters over services/novel_files_service.py.

Uploads and the reference removal are PC-only (`local_only()`, the user
rule "uploads, deletes and settings are PC-only"); the multipart POSTs need
X-Baihe-Local: 1, which the React upload helper sends. Status reads are
`library.read`. Raw-novel removal already lives in delete_routes.py.
Every write answers 409 while a job runs for the drama.
"""

from fastapi import APIRouter, File, Path, UploadFile

from api.auth import local_only, require_permission
from api.schemas import (DeleteConfirm, ErrorResponse, NovelFileStatus, NovelFileUploadResult,
                         NovelReferenceRemoveResult)
from services import novel_files_service as svc

router = APIRouter(prefix="/api/novel", tags=["novel"])
_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
        422: {"model": ErrorResponse}}
_NOT_FOUND = {404: {"model": ErrorResponse}}


@router.get("/dramas/{drama_id}/reference", dependencies=[require_permission("library.read")],
            response_model=NovelFileStatus, responses=_NOT_FOUND,
            summary="Whether an English novel reference is saved (booleans and counts only)")
def get_reference(drama_id: int = Path(ge=1)):
    return svc.get_reference_status(drama_id)


@router.post("/dramas/{drama_id}/reference", dependencies=[local_only()],
             response_model=NovelFileUploadResult, responses=_ERR,
             summary="Set or replace the English novel reference (.txt/.md; 409 while a job runs)")
def post_reference(drama_id: int = Path(ge=1), file: UploadFile = File(...)):
    return svc.upload_reference(drama_id, file.filename, file.file)


@router.post("/dramas/{drama_id}/reference/remove", dependencies=[local_only()],
             response_model=NovelReferenceRemoveResult, responses=_ERR,
             summary="Remove the English novel reference (confirm=true; 409 while a job runs)")
def remove_reference(body: DeleteConfirm, drama_id: int = Path(ge=1)):
    return svc.remove_reference(drama_id, confirm=body.confirm)


@router.get("/dramas/{drama_id}/raw-novel", dependencies=[require_permission("library.read")],
            response_model=NovelFileStatus, responses=_NOT_FOUND,
            summary="Whether a raw original-language novel is saved (booleans and counts only)")
def get_raw_novel(drama_id: int = Path(ge=1)):
    return svc.get_raw_novel_status(drama_id)


@router.post("/dramas/{drama_id}/raw-novel", dependencies=[local_only()],
             response_model=NovelFileUploadResult, responses=_ERR,
             summary="Set or replace the raw original-language novel (.txt/.md/.epub; 409 while a job runs)")
def post_raw_novel(drama_id: int = Path(ge=1), file: UploadFile = File(...)):
    return svc.upload_raw_novel(drama_id, file.filename, file.file)
