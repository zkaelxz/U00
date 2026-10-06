"""
services/url_media_service.py -- download a drama's audio/video from a URL
with yt-dlp (the Workspace Source stage's "From a URL"; Sources S-5
video). The route is
`local_only()` for now (docs/remote-access-decision.md).

Checks in the request, before any job or fetch: the pasted URL is public
(sources_url_service.check_public_url: http(s), no userinfo, <=2000 chars,
every resolved address global), the drama exists and works from audio
(`content_mode` audio_drama or streamer_vod), replacing existing audio
needs `confirm_replace_audio`, yt-dlp is installed (unless the link is a
direct media link), no job runs for the
drama, and no other URL download runs in this process.

Job `urlmedia_<drama_id>` downloads into a fresh `.urldl_*` temp folder in
the drama folder (removed in `finally`) with capped yt-dlp options (see
`ydl_options`): one item, no live streams, at most 6 h long, at most the
upload cap in bytes and 2 h of wall clock, native downloader only, no
cookie option in those caps (the saved Settings yt-dlp cookies, a browser
or a cookies.txt path, are passed separately: the route is PC-only), and
never yt-dlp's `generic` extractor (security review LOW-1: it
follows any embedded media URL and redirect without our address guard).
A plain direct media link (the URL path ends in an audio/video extension
the upload accepts) is fetched by `_direct_download` instead, without
yt-dlp: every hop re-validated and pinned (services.metadata_service,
the same rule as safe_fetch), streamed under the same byte, wall-clock and
cancel caps; its audio is converted with ffmpeg. Audio only: the extracted
WAV becomes `source.wav`. Video: the
audio is extracted with ffmpeg inside the temp folder first, then the
video becomes `source<ext>` and the audio `audio.wav`. Files go in place
through media_upload_service.install_media, as an upload does: never over
an existing file (`-2`... while the old one is there), one field-scoped DB
write as the commit point (audio_filename, [source_video_filename],
source_url and, only when both titles are empty (re-read just before
writing), title_zh), then the replaced files move to kept_media/.
Audio-only leaves an older source_video_filename as is.

Errors are fixed strings: never the URL, a path or yt-dlp's raw text.
"""

import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit

import background_jobs
import db
import storage
from services import drama_service, media_upload_service, settings_service
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
_SAVE_FAILED = ("Downloaded, but couldn't save the file into this title's folder. The title's "
               "audio and video are unchanged.")
_REJECTED = "That link is a live stream, a playlist or longer than 6 hours, so it was not downloaded."
_NO_ROOM = "There is not enough free disk space for this download."
_TOO_LARGE = "The download is larger than the upload limit, so it was stopped."
_DISK_NEARLY_FULL = "The drive is almost full, so the download was stopped."
MIN_FREE_BYTES = 2 * 1024 ** 3
SPACE_CHECK_SECONDS = 2.0
_TOO_SLOW = "The download took longer than 2 hours, so it was stopped."
_EXTRACT_FAILED = "Could not read audio from the downloaded video."
_start_lock = threading.Lock()
DIRECT_MEDIA_EXTENSIONS = (media_upload_service.AUDIO_EXTENSIONS
                           + media_upload_service.VIDEO_EXTENSIONS)
MAX_DIRECT_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)
_CHUNK = 65_536
_DIRECT_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BaiheStudio/1.0)",
                   "Accept-Encoding": "identity"}


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
    """The progress hook and match filter for one download: wall clock cap,
    a free-disk-space floor and cancel (raise video_download.DownloadAborted),
    and the live/playlist/duration filter (remembers that it rejected). There
    is no size cap: a long video is allowed, but never to the point of filling
    the drive."""

    def __init__(self, job_id: str, tmp_dir: str, clock=time.monotonic):
        self.job_id, self.tmp_dir, self.clock = job_id, tmp_dir, clock
        self.started = clock()
        self.rejected = False
        self._space_checked = float("-inf")

    def _drive_nearly_full(self) -> bool:
        now = self.clock()
        if now - self._space_checked < SPACE_CHECK_SECONDS:
            return False
        self._space_checked = now
        try:
            return shutil.disk_usage(self.tmp_dir).free < MIN_FREE_BYTES
        except OSError:
            return False

    def hook(self, d):
        import video_download
        if background_jobs.is_cancel_requested(self.job_id):
            raise video_download.DownloadAborted("cancelled")
        if self.clock() - self.started > MAX_WALL_SECONDS:
            raise video_download.DownloadAborted("time")
        if self._drive_nearly_full():
            raise video_download.DownloadAborted("space")
        got = d.get("downloaded_bytes") or 0
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
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
    cookie option, and every default extractor except `generic`."""
    return {
        "allowed_extractors": ["default", "-generic"],
        "noplaylist": True,
        "playlistend": 1,
        "match_filter": caps.match_filter,
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


def direct_media_ext(url: str):
    """The extension when the URL's path ends in an accepted audio/video
    extension (a plain direct link), else None."""
    try:
        path = urlsplit(url).path
    except ValueError:
        return None
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in DIRECT_MEDIA_EXTENSIONS else None


def _direct_download(job_id: str, url: str, tmp: str, ext: str, clock=time.monotonic) -> str:
    """A direct media link, without yt-dlp: each hop (first included) must
    be http(s) with only public addresses and is connected to the checked
    address; at most MAX_DIRECT_REDIRECTS hops. The body is streamed into
    `tmp` under the upload byte cap and the 2 h wall clock, cancel checked
    between chunks. Raw bytes are read and decoded here (a server may still
    gzip/deflate the body despite `identity`; any other encoding is
    refused), so the cancel and wall-clock checks run after every raw chunk
    and every decode step, and both raw and decoded bytes count against the
    cap (security review M-1). Fixed-text errors only."""
    from services import metadata_service as ms
    from sources import http as shttp
    limit = media_upload_service.max_upload_bytes()
    started = clock()
    current = url
    for _ in range(MAX_DIRECT_REDIRECTS + 1):
        try:
            ip = ms.check_public_url(current)
            resp = ms.pinned_get(current, ip, _DIRECT_HEADERS)
        except Exception:
            raise RuntimeError(_FAILED) from None
        try:
            if resp.status_code in _REDIRECT_CODES:
                location = resp.headers.get("Location")
                if not location:
                    raise RuntimeError(_FAILED)
                current = urljoin(current, location)
                continue
            if resp.status_code != 200:
                raise RuntimeError(_FAILED)
            length = str(resp.headers.get("Content-Length") or "").strip()
            if length.isdigit() and int(length) > limit:
                raise RuntimeError(_TOO_LARGE)
            if length.isdigit():
                try:
                    media_upload_service.check_room_for(tmp, int(length))
                except InvalidInputError:
                    raise RuntimeError(_NO_ROOM) from None

            def check():
                if background_jobs.is_cancel_requested(job_id):
                    raise background_jobs.JobCancelled(job_id)
                if clock() - started > MAX_WALL_SECONDS:
                    raise RuntimeError(_TOO_SLOW)

            try:
                decoder = shttp.BodyDecoder(resp.headers.get("Content-Encoding"), limit, check)
            except shttp.ResponseRefused:
                raise RuntimeError(_FAILED) from None
            path = os.path.join(tmp, "downloaded" + ext)
            raw_got = got = 0
            total = int(length) if length.isdigit() else 0
            with open(path, "xb") as out:
                while True:
                    check()
                    try:
                        chunk = shttp.read_raw_chunk(resp.raw, _CHUNK)
                    except Exception:
                        raise RuntimeError(_FAILED) from None
                    last = not chunk
                    raw_got += len(chunk)
                    if raw_got > limit:
                        raise RuntimeError(_TOO_LARGE)
                    try:
                        data = decoder.finish() if last else decoder.feed(chunk)
                    except shttp.ResponseTooLarge:
                        raise RuntimeError(_TOO_LARGE) from None
                    except shttp.FetchFailed:
                        raise RuntimeError(_FAILED) from None
                    got += len(data)
                    out.write(data)
                    if last:
                        break
                    background_jobs.update_progress(
                        job_id, 0.05 + 0.8 * (min(raw_got / total, 1.0) if total else 0.0),
                        "Downloading...")
            if got == 0:
                raise RuntimeError(_FAILED)
            return path
        finally:
            resp.close()
    raise RuntimeError(_FAILED)  # too many redirects


def _extract_audio(job_id: str, src: str, wav: str, cwd: str):
    try:
        background_jobs.run_cancellable(
            job_id, _extract_cmd(src, wav), cwd=cwd,
            timeout=media_upload_service.EXTRACT_TIMEOUT_SECONDS)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise RuntimeError(_EXTRACT_FAILED) from None


# yt-dlp's message pattern -> a fixed sentence. Only these sentences are ever
# shown, so no yt-dlp text, link, id or local path can reach the screen.
_FAILURE_REASONS = (
    (r"sign in|confirm you.re not a bot|login required|log in",
     "The site asked to sign in, or is treating this PC as a bot."),
    (r"members[- ]only|join this channel|premium",
     "The video is members-only."),
    (r"age[- ]restrict|confirm your age|inappropriate for some users",
     "The video is age-restricted."),
    (r"not available in your country|geo[- ]?restrict|blocked it in your country|region",
     "The video isn't available in this region."),
    (r"requested format is not available|no video formats|no formats|javascript runtime|"
     r"challenge solving|n.?sig",
     "No downloadable format was found: yt-dlp may need an update or a JavaScript runtime."),
    (r"http error 429|too many requests|rate[- ]?limit",
     "The site is rate-limiting this PC. Wait a while and try again."),
    (r"http error 40[13]|forbidden",
     "The site refused the download."),
    (r"unsupported url|no suitable extractor",
     "yt-dlp doesn't support that site."),
    (r"private video|video unavailable|removed|has been terminated|copyright|"
     r"no longer available|does not exist",
     "The video is private or unavailable."),
)
_FAILURE_RES = tuple((re.compile(pattern, re.I), text) for pattern, text in _FAILURE_REASONS)


def _failure_reason(exc: BaseException) -> str:
    """A fixed sentence for a recognised yt-dlp failure, else ''."""
    haystack = " ".join(str(e) for e in (exc.__cause__, exc) if e is not None)
    for rx, text in _FAILURE_RES:
        if rx.search(haystack):
            return text
    return ""


def _download(job_id: str, url: str, tmp: str, audio_only: bool) -> tuple:
    import video_download
    caps = _Caps(job_id, tmp)
    fetched = {}
    try:
        path = video_download.download(
            url, tmp, audio_only=audio_only,
            title_cb=lambda t: fetched.setdefault("title", t),
            extra_opts=ydl_options(tmp, caps), **settings_service.get_cookie_settings())
    except video_download.DownloadAborted as e:
        reason = str(e)
        if reason == "cancelled":
            raise background_jobs.JobCancelled(job_id) from None
        raise RuntimeError(_DISK_NEARLY_FULL if reason == "space" else _TOO_SLOW) from None
    except ImportError:
        raise RuntimeError(_NO_YTDLP) from None
    except Exception as e:
        if caps.rejected:
            raise RuntimeError(_REJECTED) from None
        reason = _failure_reason(e)
        raise RuntimeError(f"{reason} {_FAILED}" if reason else _FAILED) from None
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(tmp) + os.sep) or not os.path.isfile(real):
        raise RuntimeError(_FAILED)
    return real, fetched.get("title")


def _download_job(job_id: str, drama_id: int, url: str, audio_only: bool):
    tmp = storage.new_workdir(job_id)
    try:
        background_jobs.update_progress(job_id, 0.02, "Starting the download...")
        direct_ext = direct_media_ext(url)
        if direct_ext:
            path, title = _direct_download(job_id, url, tmp, direct_ext), None
        else:
            path, title = _download(job_id, url, tmp, audio_only)
        ext = os.path.splitext(path)[1].lower()
        if audio_only and not direct_ext:
            new_files = {"audio_filename": (path, "source", ".wav")}
        elif direct_ext and (audio_only or ext in media_upload_service.AUDIO_EXTENSIONS):
            background_jobs.update_progress(job_id, 0.88, "Converting the audio...")
            wav = os.path.join(tmp, "converted.wav")
            _extract_audio(job_id, path, wav, tmp)
            new_files = {"audio_filename": (wav, "source", ".wav")}
        else:
            if ext not in media_upload_service.VIDEO_EXTENSIONS:
                raise RuntimeError(_FAILED)
            background_jobs.update_progress(job_id, 0.88, "Extracting audio from the video...")
            wav = os.path.join(tmp, "audio.wav")
            _extract_audio(job_id, path, wav, tmp)
            new_files = {"source_video_filename": (path, "source", ext),
                         "audio_filename": (wav, "audio", ".wav")}
        fields = {"source_url": url}
        drama = db.get_drama(drama_id) or {}  # re-read just before writing
        if title and not (drama.get("title_en") or drama.get("title_zh")):
            fields["title_zh"] = str(title).strip()[:300]
        try:
            media_upload_service.install_media(drama_id, new_files, **fields)
        except (OSError, sqlite3.Error):
            raise RuntimeError(_SAVE_FAILED) from None
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
    if (drama.get("content_mode") or "audio_drama") not in media_upload_service.UPLOAD_CONTENT_MODES:
        raise InvalidInputError(media_upload_service.NO_UPLOAD_MODE)
    if _has_audio(drama, drama_id) and not confirm_replace_audio:
        raise InvalidInputError("This drama already has audio. Confirm replacing it first.",
                                details={"reason": "confirm_replace_audio"})
    if direct_media_ext(url) is None and not _yt_dlp_installed():
        raise DependencyUnavailableError(_NO_YTDLP)
    with media_upload_service.claims_lock:
        if drama_id in media_upload_service.claimed:
            raise ConflictError("Another upload is in progress for this drama.")
        media_upload_service.claimed.add(drama_id)
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
        with media_upload_service.claims_lock:
            media_upload_service.claimed.discard(drama_id)
