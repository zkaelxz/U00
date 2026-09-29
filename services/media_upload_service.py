"""
services/media_upload_service.py -- upload an audio/video file into a
drama's folder (Migration Slice 31), mirroring the Streamlit Source tab's
"Upload a file" branch (`tabs/workspace_tab.py`, `run_prep`): the file is
saved as `source<ext>` in the drama folder; a video also gets its audio
track extracted to `audio.wav` and both filenames recorded, an audio file
records just `audio_filename`.

B-09: a video's audio extraction (ffmpeg) runs in background job
`extract_audio_<drama_id>`, not in the request. The job owns the whole
step (extraction, then the field-scoped DB write) and, for
upload-and-transcribe, starts the transcribe run and follows it, so the
client still polls one job id. Cancel kills the ffmpeg process tree.

The client's filename is never used for storage or returned: only its
extension is read, and only if it is on the whitelist. The body is streamed
to a temp file in the drama folder (capped at BAIHE_MAX_UPLOAD_MB, default
2048), then atomically renamed into place. No path is ever returned.

No FastAPI import: takes a binary file-like object.
"""
import os
import subprocess
import tempfile
import threading
import time

import background_jobs
import db
from services import drama_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
_CHUNK = 1024 * 1024
_DEFAULT_MAX_MB = 2048
_TOO_LARGE = "The uploaded file is too large."
# Streamlit offers the audio/video upload only when content_mode is one of
# these (tabs/workspace_tab.py `has_audio_pipeline`); keep in sync by hand.
_UPLOAD_CONTENT_MODES = ("audio_drama", "streamer_vod")
_NO_UPLOAD_MODE = ("This drama has no audio to upload (it is set to work from a novel). "
                   "Change what you are working from to Audio drama or Streamer/VOD first.")
_BAD_TYPE = "Unsupported file type. Upload an audio or video file."
_EXTRACT_FAILED = "Could not read audio from that video file."
EXTRACT_JOB_PREFIX = "extract_audio_"
# Wall-clock cap on one ffmpeg extraction, so a stuck ffmpeg can't leave the
# job "running" forever; cancel kills it sooner.
EXTRACT_TIMEOUT_SECONDS = 2 * 60 * 60
_FOLLOW_POLL_SECONDS = 0.5
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."
# Per-drama upload claim (B-09): held from the running-job check until the
# file is in place and any extraction job is registered, so a concurrent
# upload is refused before it touches `source<ext>`. In-process only;
# another process is covered by job_running_for_drama's job_records check.
_claims_lock = threading.Lock()
_claimed = set()


def max_upload_bytes() -> int:
    try:
        mb = float(os.environ.get("BAIHE_MAX_UPLOAD_MB", _DEFAULT_MAX_MB))
    except ValueError:
        mb = _DEFAULT_MAX_MB
    return int(max(mb, 0) * 1024 * 1024)


def _safe_extension(client_filename) -> str:
    """Only the extension of the last path component, lowercased, and only
    if whitelisted; everything else in the client's name is discarded."""
    name = str(client_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if any(ord(c) < 32 or ord(c) == 127 for c in name):  # includes NUL
        raise InvalidInputError(_BAD_TYPE)
    ext = os.path.splitext(name)[1].lower()
    if ext not in AUDIO_EXTENSIONS + VIDEO_EXTENSIONS:
        raise InvalidInputError(_BAD_TYPE)
    return ext


def _save_upload(drama_id, client_filename, fileobj):
    ext = _safe_extension(client_filename)
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in _UPLOAD_CONTENT_MODES:
        raise InvalidInputError(_NO_UPLOAD_MODE)
    limit = max_upload_bytes()
    ddir = db.drama_dir(drama_id)
    fd, tmp_path = tempfile.mkstemp(prefix=".upload_", suffix=".part", dir=ddir)
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = fileobj.read(_CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise InvalidInputError(_TOO_LARGE)
                out.write(chunk)
        if size == 0:
            raise InvalidInputError("The uploaded file is empty.")
        final_path = os.path.join(ddir, f"source{ext}")
        os.replace(tmp_path, final_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return ext, size


def upload_media(drama_id, client_filename, fileobj, transcribe_options=None) -> dict:
    """Only one upload per drama at a time, and none while a drama job runs.
    Saves the upload. An audio file is recorded at once (job_id None).
    A video starts job `extract_audio_<id>` and returns its job_id before
    extraction runs (poll GET /api/jobs/{job_id}). With transcribe_options
    (kwargs for transcribe_service.start_transcribe_run), that same job
    starts and follows the transcribe run once the audio is extracted; for
    an audio file the run is started here, under the upload claim, and its
    id returned as "transcribe_job_id" (the upload is kept if it fails)."""
    with _claims_lock:
        if drama_id in _claimed:
            raise ConflictError("Another upload is in progress for this drama.")
        _claimed.add(drama_id)
    try:
        if drama_service.job_running_for_drama(drama_id):
            raise ConflictError(_BUSY)
        ext, size = _save_upload(drama_id, client_filename, fileobj)
        if ext not in VIDEO_EXTENSIONS:
            db.update_drama(drama_id, audio_filename=f"source{ext}")
            result = {"name": f"source{ext}", "size": size, "kind": "audio", "job_id": None}
            if transcribe_options is not None:  # started while the claim is still held
                from services import transcribe_service
                run = transcribe_service.start_transcribe_run(drama_id, **transcribe_options)
                result["transcribe_job_id"] = run["job_id"]
            return result
        job_id = f"{EXTRACT_JOB_PREFIX}{drama_id}"
        started = background_jobs.start_job(
            job_id, _extract_audio_job, job_id, drama_id, ext, transcribe_options,
            description=f"Audio extraction (drama #{drama_id})")
        if not started:  # unreachable while the claim and running-job check hold
            raise ConflictError(_BUSY)
        return {"name": f"source{ext}", "size": size, "kind": "video", "job_id": job_id}
    finally:
        with _claims_lock:
            _claimed.discard(drama_id)


def _extract_audio_job(job_id, drama_id, ext, transcribe_options=None):
    ddir = db.drama_dir(drama_id)
    video_path = os.path.join(ddir, f"source{ext}")
    part_path = os.path.join(ddir, ".audio.extract.wav")
    background_jobs.update_progress(job_id, 0.05, "Extracting audio from the video...")
    # -protocol_whitelist file: an upload named .mp4 could really be an HLS
    # playlist naming network URLs; ffmpeg may only open local files.
    cmd = ["ffmpeg", "-y", "-protocol_whitelist", "file", "-i", video_path, "-vn",
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", part_path]
    try:
        background_jobs.run_cancellable(job_id, cmd, cwd=ddir, timeout=EXTRACT_TIMEOUT_SECONDS)
        os.replace(part_path, os.path.join(ddir, "audio.wav"))
    except BaseException as exc:
        for path in (part_path, video_path):
            if os.path.exists(path):
                os.remove(path)
        if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError)):
            raise RuntimeError(_EXTRACT_FAILED) from None
        raise
    db.update_drama(drama_id, audio_filename="audio.wav", source_video_filename=f"source{ext}")
    if transcribe_options is None:
        background_jobs.update_progress(job_id, 1.0, "Audio extracted.")
        return
    from services import transcribe_service
    if background_jobs.is_cancel_requested(job_id):  # never start a GPU run after a cancel
        raise background_jobs.JobCancelled(job_id)
    background_jobs.update_progress(job_id, 0.1, "Audio extracted. Starting transcription...")
    run = transcribe_service.start_transcribe_run(drama_id, **transcribe_options)
    _follow_job(job_id, run["job_id"])


def _follow_job(job_id, child_id):
    """Mirrors child_id's progress onto job_id until the child ends and
    forwards a cancel to it; a child error or cancel ends job_id the same way.
    A queued child is removed outright by cancel_queued, so once a cancel
    has been forwarded a vanished child also counts as cancelled."""
    forwarded = False
    while True:
        child = background_jobs.get_status(child_id) or {}
        status = child.get("status")
        if status not in ("running", "queued"):
            break
        if background_jobs.is_cancel_requested(job_id) and not forwarded:
            forwarded = True
            if not background_jobs.cancel_queued(child_id):
                background_jobs.request_cancel(child_id)
            continue
        background_jobs.update_progress(job_id, 0.1 + 0.9 * float(child.get("progress") or 0.0),
                                        child.get("message") or "Transcribing...")
        time.sleep(_FOLLOW_POLL_SECONDS)
    if status == "cancelled" or (forwarded and status is None):
        raise background_jobs.JobCancelled(job_id)
    if status != "done":
        raise RuntimeError(child.get("error") or "Transcription failed.")
    # A "done" run can still carry result.failed_reason; pass it through.
    background_jobs.set_result(job_id, child.get("result"))


def get_media_status(drama_id) -> dict:
    """Booleans and the upload cap only -- never a path or filename."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    audio = drama.get("audio_filename")
    return {
        "drama_id": drama_id,
        "has_audio": bool(audio and os.path.exists(os.path.join(db.drama_dir(drama_id), audio))),
        "has_source_video": bool(drama.get("source_video_filename")),
        "upload_max_mb": max_upload_bytes() // (1024 * 1024),
    }
