"""
api/routers/media_routes.py -- audio/video upload for one drama (Migration
Slice 31). Multipart body; see services/media_upload_service.py for the
filename/size/atomic-write rules. Returns name, size and kind only.
"""

from fastapi import APIRouter, File, Path, UploadFile

from api.schemas import ErrorResponse, MediaUploadResult
from services import media_upload_service

router = APIRouter(prefix="/api/media", tags=["media"])


@router.post("/dramas/{drama_id}/upload", response_model=MediaUploadResult,
             summary="Upload an audio/video file into one drama's folder",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_upload_media(drama_id: int = Path(ge=1), file: UploadFile = File(...)):
    return media_upload_service.upload_media(drama_id, file.filename, file.file)
