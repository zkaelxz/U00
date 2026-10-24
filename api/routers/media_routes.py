"""
api/routers/media_routes.py -- audio/video upload, Range playback and
the PC-only URL download (yt-dlp; services/url_media_service.py) for one drama. Multipart body; see services/media_upload_service.py for the
filename/size/atomic-write rules. Returns name, size, kind and, for a
video, the job_id of the background audio extraction.
"""

import os
import threading
from typing import Optional

import anyio
from fastapi import APIRouter, File, Form, Path, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from api.auth import local_only, require_permission
from api.error_handlers import error_body
from api.llm_slots import caller_key
from api.routers.transcribe_routes import check_run_allowed
from pydantic import ValidationError

from api.schemas import (ErrorResponse, MediaPeaks, MediaStatus, MediaUploadResult, MediaUrlDownloadRequest,
                         MediaUrlDownloadStarted, TranscribeRunRequest, UploadAndTranscribeResult, error_responses)
from services import (media_peaks_service, media_playback_service, media_upload_service, url_media_service,
                      transcribe_options_service)
from services.service_errors import InvalidInputError

router = APIRouter(prefix="/api/media", tags=["media"])


@router.post("/dramas/{drama_id}/upload", dependencies=[require_permission("media.upload")], response_model=MediaUploadResult,
             summary="Upload an audio/video file into one drama's folder",
             description="Replacing existing audio/video needs confirm_replace_audio=true (422 "
                         "with details.reason \"confirm_replace_audio\" otherwise); the old file "
                         "is kept in the drama folder, never overwritten.",
             responses=error_responses(404, 409, 422))
def post_upload_media(drama_id: int = Path(ge=1), file: UploadFile = File(...),
                      confirm_replace_audio: bool = Form(False)):
    return media_upload_service.upload_media(drama_id, file.filename, file.file,
                                             confirm_replace_audio=confirm_replace_audio)


@router.get("/dramas/{drama_id}/status", dependencies=[require_permission("library.read")], response_model=MediaStatus,
            summary="Whether a drama has audio / a source video, and the upload size cap",
            responses=error_responses(404))
def get_media_status(drama_id: int = Path(ge=1)):
    return media_upload_service.get_media_status(drama_id)


@router.post("/dramas/{drama_id}/upload-and-transcribe",
             dependencies=[require_permission("media.upload", "jobs.start")], response_model=UploadAndTranscribeResult,
             summary="Upload a file, then start the transcribe run",
             description="Options are the same as POST /api/transcribe/dramas/{id}/run and are "
                         "checked before the file is stored (422/400/503 on a bad option). An audio "
                         "file starts the run at once and returns its job_id; if the run still "
                         "fails to start, the error is returned and the upload is kept. A video "
                         "returns the audio-extraction job_id: that job extracts the audio, then "
                         "starts and follows the run, so a later failure shows as a job error. "
                         "Replacing existing audio/video needs confirm_replace_audio=true, as on "
                         "the plain upload.",
             responses=error_responses(400, 404, 409, 422, 503))
def post_upload_and_transcribe(
        request: Request, drama_id: int = Path(ge=1), file: UploadFile = File(...),
        source_language: Optional[str] = Form(None), chinese_script: Optional[str] = Form(None),
        transcript_text: Optional[str] = Form(None), run_diarize: bool = Form(False),
        expected_speakers: Optional[int] = Form(None), initial_prompt: str = Form(""),
        tesseract_cmd: Optional[str] = Form(None),
        extra_names: str = Form("", max_length=1000),
        min_speakers: Optional[int] = Form(None), max_speakers: Optional[int] = Form(None),
        confirm_replace_audio: bool = Form(False)):
    try:  # validate options up front so a bad option never leaves an orphan upload
        opts = TranscribeRunRequest(
            source_language=source_language, chinese_script=chinese_script,
            transcript_text=transcript_text, run_diarize=run_diarize,
            expected_speakers=expected_speakers, initial_prompt=initial_prompt,
            tesseract_cmd=tesseract_cmd, extra_names=extra_names,
            min_speakers=min_speakers, max_speakers=max_speakers)
    except ValidationError:
        raise InvalidInputError("Invalid transcribe options.")
    # First: the readiness check below reads the mode to know which models the run needs.
    media_upload_service.default_run_mode(drama_id, opts.transcript_text)
    check_run_allowed(request, drama_id, opts)
    transcribe_options_service.validate_transcribe_options(drama_id, **opts.model_dump())
    upload = media_upload_service.upload_media(drama_id, file.filename, file.file,
                                               transcribe_options=opts.model_dump(),
                                               confirm_replace_audio=confirm_replace_audio)
    if upload["job_id"]:  # video: the extraction job starts and follows the run
        return {"upload": upload, "job_id": upload["job_id"]}
    run_job_id = upload.pop("transcribe_job_id")  # audio: started under the upload claim
    return {"upload": upload, "job_id": run_job_id}


@router.post("/dramas/{drama_id}/download-url", dependencies=[local_only()],
             response_model=MediaUrlDownloadStarted,
             summary="Job: download a drama's audio/video from a URL with yt-dlp (PC only)",
             description="The URL must be a public http(s) address (checked before anything "
                         "starts). Replacing existing audio needs confirm_replace_audio=true "
                         "(422 with details.reason \"confirm_replace_audio\" otherwise). Poll "
                         "GET /api/jobs/{job_id}; cancel with POST /api/jobs/{job_id}/cancel. Up to 3 "
                         "downloads run at once, started at least 20 s apart; more wait in line "
                         "(the job message says \"Queued: 2nd in line\"). allow_long raises the 6 h "
                         "cap to 12 h for this download; part_start/part_end (h:mm:ss) download "
                         "only that section, exact_cuts re-encodes it for precise cuts.",
             responses=error_responses(404, 409, 422, 503))
def post_download_url(body: MediaUrlDownloadRequest, drama_id: int = Path(ge=1)):
    return url_media_service.start_url_download(
        drama_id, body.url, body.audio_only, body.confirm_replace_audio, body.allow_long,
        body.part_start, body.part_end, body.exact_cuts)


# Each open stream holds a connection and a file read, so one signed-in user
# (or a stolen account) can't hold the household listener with them. The PC
# owner and auth off (no user id) aren't capped.
MAX_STREAMS_PER_USER = 3
_streams_lock = threading.Lock()
_open_streams = {}   # user id -> responses being sent


class _CappedFileResponse(FileResponse):
    """Takes the user's stream slot when sending starts and frees it when
    sending ends, so a response that is never sent can't leak one. A client
    that hangs up (a seek drops the old request) frees it at once: uvicorn
    drops writes after a disconnect, so the send would otherwise go on reading
    the file to its end."""

    def __init__(self, *args, user_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_id = user_id

    async def __call__(self, scope, receive, send):
        if self.user_id is None:
            return await super().__call__(scope, receive, send)
        with _streams_lock:
            busy = _open_streams.get(self.user_id, 0) >= MAX_STREAMS_PER_USER
            if not busy:
                _open_streams[self.user_id] = _open_streams.get(self.user_id, 0) + 1
        if busy:
            refused = JSONResponse(status_code=429, content=error_body(
                "rate_limited", f"You already have {MAX_STREAMS_PER_USER} media streams open. "
                                "Close one, then try again."))
            return await refused(scope, receive, send)
        try:
            async with anyio.create_task_group() as tasks:
                async def stop_on_hang_up():
                    while (await receive())["type"] != "http.disconnect":
                        pass
                    tasks.cancel_scope.cancel()

                tasks.start_soon(stop_on_hang_up)
                await super().__call__(scope, receive, send)
                tasks.cancel_scope.cancel()
        finally:
            with _streams_lock:
                left = _open_streams.get(self.user_id, 1) - 1
                if left > 0:
                    _open_streams[self.user_id] = left
                else:
                    _open_streams.pop(self.user_id, None)


def _play(request: Request, drama_id: int, kind: str) -> FileResponse:
    path, ctype = media_playback_service.resolve_media(drama_id, kind)
    # FileResponse streams in chunks and implements Range (206/416), If-Range
    # and HEAD (starlette>=0.39). The download name is generic, never the stored one.
    name = f"drama_{drama_id}_{kind}{os.path.splitext(path)[1].lower()}"
    principal = getattr(request.state, "principal", None) or {}
    return _CappedFileResponse(path, media_type=ctype, filename=name,
                               content_disposition_type="inline",
                               headers={"X-Content-Type-Options": "nosniff"},
                               user_id=principal.get("user_id"))


_STREAM = [require_permission("media.stream")]


@router.head("/dramas/{drama_id}/audio", dependencies=_STREAM, include_in_schema=False)
@router.get("/dramas/{drama_id}/audio", dependencies=_STREAM,
            summary="Stream a drama's audio with HTTP Range support",
            responses={200: {"content": {"audio/*": {}}}, 206: {"content": {"audio/*": {}}},
                       404: {"model": ErrorResponse}, 416: {"description": "Range not satisfiable"},
                       429: {"model": ErrorResponse}})
def get_audio(request: Request, drama_id: int = Path(ge=1)):
    return _play(request, drama_id, "audio")


@router.get("/dramas/{drama_id}/peaks", dependencies=_STREAM, response_model=MediaPeaks,
            summary="Downsampled loudness peaks of a time window of the audio (waveform timeline)",
            description="Window of 0.5 to 120 seconds, 16 to 2000 buckets. A window past the "
                        "end of the audio reads as silence.",
            responses=error_responses(404, 422, 429, 503))
def get_peaks(request: Request, drama_id: int = Path(ge=1), start: float = Query(ge=0),
              end: float = Query(gt=0), buckets: int = Query(500)):
    return media_peaks_service.get_peaks(drama_id, start, end, buckets, caller_key(request))


@router.head("/dramas/{drama_id}/video", dependencies=_STREAM, include_in_schema=False)
@router.get("/dramas/{drama_id}/video", dependencies=_STREAM,
            summary="Stream a drama's source video with HTTP Range support",
            responses={200: {"content": {"video/*": {}}}, 206: {"content": {"video/*": {}}},
                       404: {"model": ErrorResponse}, 416: {"description": "Range not satisfiable"},
                       429: {"model": ErrorResponse}})
def get_video(request: Request, drama_id: int = Path(ge=1)):
    return _play(request, drama_id, "video")
