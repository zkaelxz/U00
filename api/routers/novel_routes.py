"""
api/routers/novel_routes.py -- attach novel text/EPUB to a drama and OCR
chapter images (Migration Slice 38). See services/novel_attach_service.py
for the safety rules. OCR is a background job; poll GET /api/jobs/{id}.
"""

from typing import List, Optional

from fastapi import APIRouter, File, Form, Path, UploadFile

from api.schemas import (ErrorResponse, NovelAttachResult, NovelAttachTextRequest,
                         NovelOcrResult, NovelStatus)
from services import novel_attach_service

router = APIRouter(prefix="/api/novel", tags=["novel"])
_ERR = {404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
        422: {"model": ErrorResponse}}


@router.post("/dramas/{drama_id}/attach-text", response_model=NovelAttachResult,
             summary="Attach pasted novel text to one drama", responses=_ERR)
def post_attach_text(payload: NovelAttachTextRequest, drama_id: int = Path(ge=1)):
    return novel_attach_service.attach_text(drama_id, payload.text, payload.mode)


@router.post("/dramas/{drama_id}/attach-epub", response_model=NovelAttachResult,
             summary="Attach the plain text of an uploaded EPUB to one drama", responses=_ERR)
def post_attach_epub(drama_id: int = Path(ge=1), file: UploadFile = File(...),
                     mode: str = Form("replace")):
    return novel_attach_service.attach_epub(drama_id, file.file, mode)


@router.post("/dramas/{drama_id}/ocr-chapter", response_model=NovelOcrResult,
             summary="Start a background OCR job over uploaded chapter images",
             responses={**_ERR, 503: {"model": ErrorResponse}})
def post_ocr_chapter(drama_id: int = Path(ge=1), files: List[UploadFile] = File(...),
                     backend: str = Form("tesseract"), mode: str = Form("append"),
                     tesseract_cmd: Optional[str] = Form(None)):
    return novel_attach_service.start_ocr_chapter(
        drama_id, [(f.filename, f.file) for f in files], backend, mode, tesseract_cmd)


@router.get("/dramas/{drama_id}/status", response_model=NovelStatus,
            summary="Whether novel text is attached (booleans and counts only)",
            responses={404: {"model": ErrorResponse}})
def get_novel_status(drama_id: int = Path(ge=1)):
    return novel_attach_service.get_status(drama_id)
