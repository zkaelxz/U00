"""
services/media_upload_service.py -- upload an audio/video file into a
drama's folder (Migration Slice 31), mirroring the Streamlit Source tab's
"Upload a file" branch (`tabs/workspace_tab.py`, `run_prep`): the file is
saved as `source<ext>` in the drama folder; a video also gets its audio
track extracted to `audio.wav` and both filenames recorded, an audio file
records just `audio_filename`.

The client's filename is never used for storage or returned: only its
extension is read, and only if it is on the whitelist. The body is streamed
to a temp file in the drama folder (capped at BAIHE_MAX_UPLOAD_MB, default
2048), then atomically renamed into place. No path is ever returned.

No FastAPI import: takes a binary file-like object.
"""
import os
import tempfile

import core as core_module
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


def upload_media(drama_id, client_filename, fileobj) -> dict:
    ext = _safe_extension(client_filename)
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in _UPLOAD_CONTENT_MODES:
        raise InvalidInputError(_NO_UPLOAD_MODE)
    if drama_service._job_running_for_drama(drama_id):
        raise ConflictError("A job is running for this drama. Wait for it to finish or cancel it.")
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
    if ext in VIDEO_EXTENSIONS:
        try:
            core_module.extract_audio_from_video(final_path, os.path.join(ddir, "audio.wav"))
        except Exception:
            os.remove(final_path)
            raise InvalidInputError("Could not read audio from that video file.")
        db.update_drama(drama_id, audio_filename="audio.wav", source_video_filename=f"source{ext}")
        kind = "video"
    else:
        db.update_drama(drama_id, audio_filename=f"source{ext}")
        kind = "audio"
    return {"name": f"source{ext}", "size": size, "kind": kind}


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
