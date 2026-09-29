"""
api/routers/media_routes.py -- audio/video upload (Slice 31) and Range playback (Slice 52) for one drama (Migration
Slice 31). Multipart body; see services/media_upload_service.py for the
filename/size/atomic-write rules. Returns name, size, kind and, for a
video, the job_id of the background audio extraction (B-09).
"""

import os
from typing import Optional

from fastapi import APIRouter, File, Form, Path, UploadFile
from fastapi.responses import FileResponse

from api.auth import local_only, require_permission
from pydantic import ValidationError

from api.schemas import (ErrorResponse, MediaStatus, MediaUploadResult, TranscribeRunRequest,
                         UploadAndTranscribeResult)
from services import media_playback_service, media_upload_service, transcribe_service
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/media", tags=["media"])


@router.post("/dramas/{drama_id}/upload", dependencies=[local_only()], response_model=MediaUploadResult,
             summary="Upload an audio/video file into one drama's folder",
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                        422: {"model": ErrorResponse}})
def post_upload_media(drama_id: int = Path(ge=1), file: UploadFile = File(...)):
    return media_upload_service.upload_media(drama_id, file.filename, file.file)


@router.get("/dramas/{drama_id}/status", dependencies=[require_permission("library.read")], response_model=MediaStatus,
            summary="Whether a drama has audio / a source video, and the upload size cap",
            responses={404: {"model": ErrorResponse}})
def get_media_status(drama_id: int = Path(ge=1)):
    return media_upload_service.get_media_status(drama_id)


@router.post("/dramas/{drama_id}/upload-and-transcribe", dependencies=[local_only()], response_model=UploadAndTranscribeResult,
             summary="Upload a file, then start the transcribe run",
             description="Options are the same as POST /api/transcribe/dramas/{id}/run and are "
                         "checked before the file is stored (422/400/503 on a bad option). An audio "
                         "file starts the run at once and returns its job_id; if the run still "
                         "fails to start, the error is returned and the upload is kept. A video "
                         "returns the audio-extraction job_id: that job extracts the audio, then "
                         "starts and follows the run, so a later failure shows as a job error.",
             responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse},
                        409: {"model": ErrorResponse}, 422: {"model": ErrorResponse},
                        503: {"model": ErrorResponse}})
def post_upload_and_transcribe(
        drama_id: int = Path(ge=1), file: UploadFile = File(...),
        source_language: Optional[str] = Form(None), chinese_script: Optional[str] = Form(None),
        transcript_text: Optional[str] = Form(None), run_diarize: bool = Form(False),
        expected_speakers: Optional[int] = Form(None), initial_prompt: str = Form(""),
        tesseract_cmd: Optional[str] = Form(None)):
    try:  # validate options up front so a bad option never leaves an orphan upload
        opts = TranscribeRunRequest(
            source_language=source_language, chinese_script=chinese_script,
            transcript_text=transcript_text, run_diarize=run_diarize,
            expected_speakers=expected_speakers, initial_prompt=initial_prompt,
            tesseract_cmd=tesseract_cmd)
    except ValidationError:
        raise InvalidInputError("Invalid transcribe options.")
    transcribe_service.validate_transcribe_options(drama_id, **opts.model_dump())
    upload = media_upload_service.upload_media(drama_id, file.filename, file.file,
                                               transcribe_options=opts.model_dump())
    if upload["job_id"]:  # video: the extraction job starts and follows the run
        return {"upload": upload, "job_id": upload["job_id"]}
    run_job_id = upload.pop("transcribe_job_id")  # audio: started under the upload claim
    return {"upload": upload, "job_id": run_job_id}


def _play(drama_id: int, kind: str) -> FileResponse:
    path, ctype = media_playback_service.resolve_media(drama_id, kind)
    # FileResponse streams in chunks and implements Range (206/416), If-Range
    # and HEAD (starlette>=0.39). The download name is generic, never the stored one.
    name = f"drama_{drama_id}_{kind}{os.path.splitext(path)[1].lower()}"
    return FileResponse(path, media_type=ctype, filename=name, content_disposition_type="inline",
                        headers={"X-Content-Type-Options": "nosniff"})


_STREAM = [require_permission("media.stream")]


@router.head("/dramas/{drama_id}/audio", dependencies=_STREAM, include_in_schema=False)
@router.get("/dramas/{drama_id}/audio", dependencies=_STREAM,
            summary="Stream a drama's audio with HTTP Range support",
            responses={200: {"content": {"audio/*": {}}}, 206: {"content": {"audio/*": {}}},
                       404: {"model": ErrorResponse}, 416: {"description": "Range not satisfiable"}})
def get_audio(drama_id: int = Path(ge=1)):
    return _play(drama_id, "audio")


@router.head("/dramas/{drama_id}/video", dependencies=_STREAM, include_in_schema=False)
@router.get("/dramas/{drama_id}/video", dependencies=_STREAM,
            summary="Stream a drama's source video with HTTP Range support",
            responses={200: {"content": {"video/*": {}}}, 206: {"content": {"video/*": {}}},
                       404: {"model": ErrorResponse}, 416: {"description": "Range not satisfiable"}})
def get_video(drama_id: int = Path(ge=1)):
    return _play(drama_id, "video")
