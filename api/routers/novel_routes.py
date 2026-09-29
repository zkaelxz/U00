"""
api/routers/novel_routes.py -- attach novel text/EPUB to a drama and OCR
chapter images (Migration Slice 38). See services/novel_attach_service.py
for the safety rules. OCR is a background job; poll GET /api/jobs/{id}.
"""

from typing import List, Optional

from fastapi import APIRouter, File, Form, Path, UploadFile
from api.auth import local_only, require_permission
from api.schemas import (ErrorResponse, NovelAttachFromSourcesRequest, NovelAttachResult,
                         NovelAttachTextRequest, NovelOcrResult, NovelStatus)
from services import novel_attach_service

router = APIRouter(prefix="/api/novel", tags=["novel"])
_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
        422: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/attach-text", dependencies=[local_only()], response_model=NovelAttachResult,
             response_model_exclude_none=True,
             summary="Attach pasted novel text to one drama", responses=_ERR)
def post_attach_text(payload: NovelAttachTextRequest, drama_id: int = Path(ge=1)):
    return novel_attach_service.attach_text(drama_id, payload.text, payload.mode)


@router.post("/dramas/{drama_id}/attach-epub", dependencies=[local_only()], response_model=NovelAttachResult,
             response_model_exclude_none=True,
             summary="Attach the plain text of an uploaded EPUB (optionally a chapter range) to one drama",
             responses=_ERR)
def post_attach_epub(drama_id: int = Path(ge=1), file: UploadFile = File(...),
                     mode: str = Form("replace"),
                     chapter_from: Optional[int] = Form(None, ge=1, le=100000),
                     chapter_to: Optional[int] = Form(None, ge=1, le=100000)):
    return novel_attach_service.attach_epub(drama_id, file.file, mode, chapter_from, chapter_to)


@router.post("/dramas/{drama_id}/attach-from-sources", dependencies=[local_only()],
             response_model=NovelAttachResult, response_model_exclude_none=True,
             summary="Use the chapters imported in Sources as the narration text", responses=_ERR)
def post_attach_from_sources(payload: NovelAttachFromSourcesRequest, drama_id: int = Path(ge=1)):
    return novel_attach_service.attach_from_sources(drama_id, payload.mode)


@router.post("/dramas/{drama_id}/ocr-chapter", dependencies=[local_only()], response_model=NovelOcrResult,
             summary="Start a background OCR job over uploaded chapter images",
             responses={**_ERR, 503: {"model": ErrorResponse}})
def post_ocr_chapter(drama_id: int = Path(ge=1), files: List[UploadFile] = File(...),
                     backend: str = Form("tesseract"), mode: str = Form("append"),
                     tesseract_cmd: Optional[str] = Form(None)):
    return novel_attach_service.start_ocr_chapter(
        drama_id, [(f.filename, f.file) for f in files], backend, mode, tesseract_cmd)


@router.get("/dramas/{drama_id}/status", dependencies=[require_permission("library.read")], response_model=NovelStatus,
            summary="Whether novel text is attached (booleans and counts only)",
            responses={404: {"model": ErrorResponse}})
def get_novel_status(drama_id: int = Path(ge=1)):
    return novel_attach_service.get_status(drama_id)
