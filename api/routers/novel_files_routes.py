"""
api/routers/novel_files_routes.py -- the English novel translation
reference and the raw original-language novel for one drama (parity audit
B1 #3/#4). Thin adapters over services/novel_files_service.py.

Uploads, pastes and the reference removal are PC-only (`local_only()`, the
user rule "uploads, deletes and settings are PC-only"); the multipart POSTs
need X-Baihe-Local: 1, which the React upload helper sends. Status and chapter
reads are `library.read`. Raw-novel removal already lives in delete_routes.py.
Every write answers 409 while a job runs for the drama.

The paste routes (`.../text`) read the JSON body themselves, streamed and
capped at svc.MAX_TEXT_BYTES, so an oversized paste is refused before it
is buffered whole or parsed; the local_only guard has already run by then.
"""

from fastapi import APIRouter, File, Path, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from api.auth import local_only, require_permission
from api.schemas import (DeleteConfirm, ErrorResponse, NovelFileStatus, NovelFileTextRequest,
                         NovelChapterList, NovelChapterText, NovelFileUploadResult,
                         NovelReferenceRemoveResult)
from services import novel_chapters_service as chapters_svc
from services import novel_files_service as svc
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/novel", tags=["novel"])
_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
        422: {"model": ErrorResponse}}
_NOT_FOUND = {404: {"model": ErrorResponse}}
_TOO_LARGE = "The pasted text is too large."
# The body is read by hand (see the module docstring); this keeps it in the docs.
_TEXT_BODY = {"requestBody": {"required": True, "content": {"application/json": {
    "schema": NovelFileTextRequest.model_json_schema()}}}}


async def _read_text_body(request: Request) -> str:
    """Streams the JSON body with a byte cap, then validates it. Errors
    never echo the input."""
    cap = svc.MAX_TEXT_BYTES
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > cap:
        raise InvalidInputError(_TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > cap:
            raise InvalidInputError(_TOO_LARGE)
    try:
        return NovelFileTextRequest.model_validate_json(bytes(body)).text
    except ValidationError:
        raise InvalidInputError('Send JSON: {"text": "..."}.') from None


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


@router.post("/dramas/{drama_id}/reference/text", dependencies=[local_only()],
             response_model=NovelFileUploadResult, responses=_ERR, openapi_extra=_TEXT_BODY,
             summary="Set or replace the English novel reference from pasted text (409 while a job runs)")
async def post_reference_text(request: Request, drama_id: int = Path(ge=1)):
    text = await _read_text_body(request)
    return await run_in_threadpool(svc.save_reference_text, drama_id, text)


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


@router.get("/dramas/{drama_id}/raw-novel/chapters", dependencies=[require_permission("library.read")],
            response_model=NovelChapterList, responses=_NOT_FOUND,
            summary="The chapters saved in the raw novel, one page of rows (no text, no paths)")
def get_raw_novel_chapters(drama_id: int = Path(ge=1), offset: int = Query(0, ge=0),
                           limit: int = Query(chapters_svc.DEFAULT_PAGE, ge=1,
                                              le=chapters_svc.MAX_PAGE)):
    return chapters_svc.list_chapters(drama_id, offset, limit)


@router.get("/dramas/{drama_id}/raw-novel/chapters/{number}",
            dependencies=[require_permission("library.read")],
            response_model=NovelChapterText, responses=_NOT_FOUND,
            summary="A bounded slice of one saved raw chapter's text, for preview")
def get_raw_novel_chapter(drama_id: int = Path(ge=1), number: int = Path(ge=1),
                          offset: int = Query(0, ge=0),
                          limit: int = Query(chapters_svc.DEFAULT_SLICE_CHARS, ge=1,
                                             le=chapters_svc.MAX_SLICE_CHARS)):
    return chapters_svc.read_chapter(drama_id, number, offset, limit)


@router.post("/dramas/{drama_id}/raw-novel", dependencies=[local_only()],
             response_model=NovelFileUploadResult, responses=_ERR,
             summary="Set or replace the raw original-language novel (.txt/.md/.epub; 409 while a job runs)")
def post_raw_novel(drama_id: int = Path(ge=1), file: UploadFile = File(...)):
    return svc.upload_raw_novel(drama_id, file.filename, file.file)


@router.post("/dramas/{drama_id}/raw-novel/text", dependencies=[local_only()],
             response_model=NovelFileUploadResult, responses=_ERR, openapi_extra=_TEXT_BODY,
             summary="Set or replace the raw original-language novel from pasted text (409 while a job runs)")
async def post_raw_novel_text(request: Request, drama_id: int = Path(ge=1)):
    text = await _read_text_body(request)
    return await run_in_threadpool(svc.save_raw_novel_text, drama_id, text)
