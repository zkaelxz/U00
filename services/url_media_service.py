"""
services/url_media_service.py -- download a drama's audio/video from a URL
with yt-dlp (the Workspace Source stage's "From a URL", Streamlit
`tabs/workspace_tab.py` "Video URL"; Sources S-5 video). The route is
`local_only()` for now (docs/remote-access-decision.md).

Checks in the request, before any job or fetch: the pasted URL is public
(sources_url_service.check_public_url: http(s), no userinfo, <=2000 chars,
every resolved address global), the drama exists and works from audio
(`content_mode` audio_drama or streamer_vod), replacing existing audio
needs `confirm_replace_audio`, yt-dlp is installed, no job runs for the
drama, and no other URL download runs in this process.

Job `urlmedia_<drama_id>` downloads into a fresh `.urldl_*` temp folder in
the drama folder (removed in `finally`) with capped yt-dlp options (see
`ydl_options`): one item, no live streams, at most 6 h long, at most the
upload cap in bytes and 2 h of wall clock, native downloader only, never
cookies. Audio only: the extracted WAV becomes `source.wav`. Video: the
audio is extracted with ffmpeg inside the temp folder first, then the
video becomes `source<ext>` and the audio `audio.wav`. The one DB write is
field-scoped: audio_filename, [source_video_filename], source_url and,
only when both titles are empty (re-read just before writing), title_zh.
Audio-only leaves an older source_video_filename as is (Streamlit parity).

Errors are fixed strings: never the URL, a path or yt-dlp's raw text.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

import background_jobs
import db
from services import drama_service, media_upload_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)
from services.sources_url_service import check_public_url

JOB_PREFIX = "urlmedia_"
MAX_DURATION_SECONDS = 6 * 60 * 60
MAX_WALL_SECONDS = 2 * 60 * 60
SOCKET_TIMEOUT = 30
_BUSY = "A job is running for this drama. Wait for it to finish or cancel it."
_ONE_AT_A_TIME = "Another URL download is running. Wait for it to finish or cancel it."
_NO_YTDLP = "Downloading from a URL needs yt-dlp, which isn't installed on this PC."
_FAILED = ("Couldn't download from that link. It may be private, region-locked or not "
           "supported, or yt-dlp may need an update.")
_REJECTED = "That link is a live stream, a playlist or longer than 6 hours, so it was not downloaded."
_TOO_LARGE = "The download is larger than the upload limit, so it was stopped."
_TOO_SLOW = "The download took longer than 2 hours, so it was stopped."
_EXTRACT_FAILED = "Could not read audio from the downloaded video."
_start_lock = threading.Lock()


def job_id_for(drama_id: int) -> str:
    return f"{JOB_PREFIX}{int(drama_id)}"


def _yt_dlp_installed() -> bool:
    """Without importing it (yt-dlp is an optional dependency)."""
    try:
        return importlib.util.find_spec("yt_dlp") is not None
    except (ImportError, ValueError):  # ValueError: in sys.modules with no __spec__
        return "yt_dlp" in sys.modules


def _any_url_download_running() -> bool:
    for jid in background_jobs.list_all_jobs():
        if str(jid).startswith(JOB_PREFIX):
            st = background_jobs.get_status(jid) or {}
            if st.get("status") in ("running", "queued"):
                return True
    return False


def _has_audio(drama: dict, drama_id: int) -> bool:
    audio = drama.get("audio_filename")
    return bool(audio and os.path.exists(os.path.join(db.drama_dir(drama_id), audio)))


class _Caps:
    """The progress hook and match filter for one download: byte cap, wall
    clock cap and cancel (raise video_download.DownloadAborted), and the
    live/playlist/duration filter (remembers that it rejected)."""

    def __init__(self, job_id: str, limit_bytes: int, clock=time.monotonic):
        self.job_id, self.limit, self.clock = job_id, limit_bytes, clock
        self.started = clock()
        self.rejected = False

    def hook(self, d):
        import video_download
        if background_jobs.is_cancel_requested(self.job_id):
            raise video_download.DownloadAborted("cancelled")
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise video_download.DownloadAborted("time")
        got = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if got > self.limit or total > self.limit:
            raise video_download.DownloadAborted("size")
        if d.get("status") == "downloading":
            frac = min(got / total, 1.0) if total else 0.0
            background_jobs.update_progress(self.job_id, 0.05 + 0.8 * frac, "Downloading...")

    def match_filter(self, info, *args, **kwargs):
        info = info or {}
        duration = info.get("duration")
        if (info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming")
                or info.get("_type") == "playlist"
                or (isinstance(duration, (int, float)) and duration > MAX_DURATION_SECONDS)):
            self.rejected = True
            return "Refused: a live stream, a playlist or longer than 6 hours."
        return None


def ydl_options(tmp_dir: str, caps: _Caps) -> dict:
    """The options merged over video_download's own (they win). Never any
    cookie option. `allowed_extractors` is left at yt-dlp's default while
    the route is PC-only (see docs/migration-review.md)."""
    return {
        "noplaylist": True,
        "playlistend": 1,
        "match_filter": caps.match_filter,
        "max_filesize": caps.limit,
        "progress_hooks": [caps.hook],
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 1,
        "external_downloader": {"default": "native"},
        "paths": {"home": tmp_dir, "temp": tmp_dir},
        "restrictfilenames": True,
        "quiet": True,
        "no_warnings": True,
    }


def _extract_cmd(video_path: str, wav_path: str) -> list:
    """-protocol_whitelist file: a downloaded "mp4" could really be an HLS
    playlist naming network URLs; ffmpeg may only open local files."""
    return ["ffmpeg", "-y", "-protocol_whitelist", "file", "-i", video_path, "-vn",
            "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path]


def _download(job_id: str, url: str, tmp: str, audio_only: bool) -> tuple:
    import video_download
    caps = _Caps(job_id, media_upload_service.max_upload_bytes())
    fetched = {}
    try:
        path = video_download.download(
            url, tmp, audio_only=audio_only,
            title_cb=lambda t: fetched.setdefault("title", t),
            extra_opts=ydl_options(tmp, caps))
    except video_download.DownloadAborted as e:
        reason = str(e)
        if reason == "cancelled":
            raise background_jobs.JobCancelled(job_id) from None
        raise RuntimeError(_TOO_LARGE if reason == "size" else _TOO_SLOW) from None
    except ImportError:
        raise RuntimeError(_NO_YTDLP) from None
    except Exception:
        raise RuntimeError(_REJECTED if caps.rejected else _FAILED) from None
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(tmp) + os.sep) or not os.path.isfile(real):
        raise RuntimeError(_FAILED)
    if os.path.getsize(real) > caps.limit:
        raise RuntimeError(_TOO_LARGE)
    return real, fetched.get("title")


def _download_job(job_id: str, drama_id: int, url: str, audio_only: bool):
    ddir = db.drama_dir(drama_id)
    tmp = tempfile.mkdtemp(dir=ddir, prefix=".urldl_")
    try:
        background_jobs.update_progress(job_id, 0.02, "Starting the download...")
        path, title = _download(job_id, url, tmp, audio_only)
        if audio_only:
            os.replace(path, os.path.join(ddir, "source.wav"))
            fields = {"audio_filename": "source.wav"}
        else:
            ext = os.path.splitext(path)[1].lower()
            if ext not in media_upload_service.VIDEO_EXTENSIONS:
                raise RuntimeError(_FAILED)
            background_jobs.update_progress(job_id, 0.88, "Extracting audio from the video...")
            wav = os.path.join(tmp, "audio.wav")
            try:
                background_jobs.run_cancellable(
                    job_id, _extract_cmd(path, wav), cwd=tmp,
                    timeout=media_upload_service.EXTRACT_TIMEOUT_SECONDS)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
                raise RuntimeError(_EXTRACT_FAILED) from None
            os.replace(path, os.path.join(ddir, f"source{ext}"))
            os.replace(wav, os.path.join(ddir, "audio.wav"))
            fields = {"audio_filename": "audio.wav", "source_video_filename": f"source{ext}"}
        fields["source_url"] = url
        drama = db.get_drama(drama_id) or {}  # re-read just before writing
        if title and not (drama.get("title_en") or drama.get("title_zh")):
            fields["title_zh"] = str(title).strip()[:300]
        db.update_drama(drama_id, **fields)
        background_jobs.set_result(job_id, {"kind": "url_media", "audio_only": bool(audio_only),
                                            "title_filled": "title_zh" in fields})
        background_jobs.update_progress(job_id, 1.0, "Downloaded.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def start_url_download(drama_id, url, audio_only, confirm_replace_audio=False) -> dict:
    """Starts `urlmedia_<drama_id>`; poll GET /api/jobs/{job_id}. 422 bad or
    private URL, wrong content_mode, or audio exists without
    confirm_replace_audio (details.reason "confirm_replace_audio"); 503 the
    host doesn't resolve or yt-dlp is missing; 404 no drama; 409 while a
    job or upload runs for the drama, or another URL download runs."""
    if not isinstance(audio_only, bool) or not isinstance(confirm_replace_audio, bool):
        raise InvalidInputError("audio_only and confirm_replace_audio must be true or false.")
    url = check_public_url(url)
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in media_upload_service._UPLOAD_CONTENT_MODES:
        raise InvalidInputError(media_upload_service._NO_UPLOAD_MODE)
    if _has_audio(drama, drama_id) and not confirm_replace_audio:
        raise InvalidInputError("This drama already has audio. Confirm replacing it first.",
                                details={"reason": "confirm_replace_audio"})
    if not _yt_dlp_installed():
        raise DependencyUnavailableError(_NO_YTDLP)
    with media_upload_service._claims_lock:
        if drama_id in media_upload_service._claimed:
            raise ConflictError("Another upload is in progress for this drama.")
        media_upload_service._claimed.add(drama_id)
    try:
        with _start_lock:
            if drama_service.job_running_for_drama(drama_id):
                raise ConflictError(_BUSY)
            if _any_url_download_running():
                raise ConflictError(_ONE_AT_A_TIME)
            job_id = job_id_for(drama_id)
            background_jobs.clear_job(job_id)
            if not background_jobs.start_job(job_id, _download_job, job_id, drama_id, url,
                                             audio_only,
                                             description=f"URL download (drama #{drama_id})"):
                raise ConflictError(_BUSY)
        return {"job_id": job_id}
    finally:
        with media_upload_service._claims_lock:
            media_upload_service._claimed.discard(drama_id)
