"""
services/url_media_service.py -- download a drama's audio/video from a URL
with yt-dlp (the Workspace Source stage's "From a URL"; Sources S-5
video). The route is
`local_only()` for now (docs/remote-access-decision.md).

Checks in the request, before any job or fetch: the pasted URL is public
(sources_url_service.check_public_url: http(s), no userinfo, <=2000 chars,
every resolved address global), the drama exists and works from audio
(`content_mode` audio_drama or streamer_vod), replacing existing audio or
video needs `confirm_replace_audio` (media_upload_service.has_media, as an
upload), yt-dlp is installed (unless the link is a
direct media link), and no job runs for the drama. The same checks run again
when the download actually starts (it may have waited in line).

Downloads queue: up to MAX_PARALLEL_DOWNLOADS run at once, each starting at
least START_SPACING_SECONDS after the previous start; a request that finds
every slot busy waits in line (the job shows "Queued: 2nd in line", Cancel
works). Waiting is inside the job's own thread, so the queue is lost on a
restart like every other in-memory job.

Job `urlmedia_<drama_id>` downloads into a fresh `.urldl_*` temp folder in
the drama folder (removed in `finally`) with capped yt-dlp options (see
`services.ytdlp_child.ydl_options`): one item, no live streams, at most 6 h long
(12 h with "Download anyway") and 2 h of wall clock (6 h for a clip past the cap or a
re-encoded section, see _WallClock), no byte cap (a free-disk-space floor, polled on a
timer, stops it instead, see _SpaceWatch), native downloader only (except a chosen
part, which yt-dlp hands to ffmpeg: refused when cookie login is on), no
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
write as the commit point (audio_filename, [source_video_filename] and,
only when both titles are empty (re-read just before writing), title_zh),
then the replaced files move to kept_media/. A blank author and release
date are then filled from the video's own metadata
(link_details_service.fill_after_download), never over typed text. The link is recorded after
that like any import's (drama_service.set_source_url_once: cleaned form,
raw form kept apart, never over a link the title already has).
Audio-only clears an older source_video_filename and moves that video there too.

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

import applog
import background_jobs
from jobs import job_launch
import db
import storage
import translate_engines
import video_export
from services import (drama_service, link_details_service, media_upload_service,
                      settings_service, ytdlp_child)
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, NotFoundError)
from services.sources_url_service import check_public_url

# Hold back the next start so a burst of links doesn't look like a scraper to
# YouTube and similar sites (their rate limits are per IP), and cap the
# parallelism because every download is its own yt-dlp child process.
MAX_PARALLEL_DOWNLOADS = 3
START_SPACING_SECONDS = 20.0
# Running plus waiting, so a pile of links can't pile up threads without limit.
MAX_QUEUED_DOWNLOADS = 20

JOB_PREFIX = "urlmedia_"
MAX_DURATION_SECONDS = ytdlp_child.MAX_DURATION_SECONDS
MAX_WALL_SECONDS = 2 * 60 * 60
SOCKET_TIMEOUT = ytdlp_child.SOCKET_TIMEOUT
_BUSY = "A job is running for this title. Wait for it to finish or cancel it."
_QUEUE_FULL = "The download queue is full. Wait for a download to finish or cancel one."
_NO_YTDLP = "Downloading from a URL needs yt-dlp, which isn't installed on this PC."
_FAILED = ("Couldn't download from that link. It may be private, region-locked or not "
           "supported, or yt-dlp may need an update.")
_SAVE_FAILED = ("Downloaded, but couldn't make it this title's audio. The title's audio and "
               "video are unchanged.")
_REJECTED = "That link can't be downloaded."
_REFUSALS = {
    "playlist": "That link is a playlist or channel, not one video. Paste the link of a single video.",
    "live": "That video is still live. Download it after the stream has ended.",
    "upcoming": "That video hasn't started yet (an upcoming stream or premiere). Try again after it has aired.",
}
_NO_ROOM = "There is not enough free disk space for this download."
_TOO_LARGE = "The download is larger than the upload limit, so it was stopped."
_DISK_NEARLY_FULL = "The drive is almost full, so the download was stopped."
MIN_FREE_BYTES = 2 * 1024 ** 3
SPACE_CHECK_SECONDS = 2.0
_TOO_SLOW = "The download took longer than 2 hours, so it was stopped."
# A "Download anyway" video, or a re-encoded exact cut (about real time), can't
# finish inside the normal wall clock.
MAX_WALL_SECONDS_LONG = 6 * 60 * 60
_DNS_BLOCKED = ("Your network or a DNS filter (for example Pi-hole) is blocking YouTube's video "
                "servers (googlevideo.com).")
_DNS_BLOCKED_OTHER = ("Your network or a DNS filter (for example Pi-hole) is blocking a server "
                      "this download needs.")
_NOW_BUSY = "This title now has audio or video. Start again and confirm replacing it."
# ffmpeg, which yt-dlp uses for a section, takes the media link and the cookies
# on its command line, where other programs on the PC can read them.
_PART_NO_COOKIES = ("Downloading only part of a video can't use your saved cookie login. Turn "
                    "cookie login off in Settings, or download the whole video.")
_EXTRACT_FAILED = "Could not read audio from the downloaded video."
_start_lock = threading.Lock()
_slots = threading.Condition()
_waiting = []        # job ids in line, oldest first; guarded by _slots
_running = set()     # job ids holding a slot; guarded by _slots
_last_start = float("-inf")
_clock = time.monotonic
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


class _SpaceWatch:
    """Free-disk-space floor for one download, polled from the child's
    progress events. A yt-dlp download has no byte cap (only a direct link
    has the upload cap): a long video is allowed, but never to the point of
    filling the drive. `floor` is how much must stay free: the other running
    downloads are still writing to the same drive."""

    def __init__(self, tmp_dir: str, clock=time.monotonic, floor=lambda: MIN_FREE_BYTES):
        self.tmp_dir, self.clock, self.floor = tmp_dir, clock, floor
        self._checked = float("-inf")

    def nearly_full(self) -> bool:
        now = self.clock()
        if now - self._checked < SPACE_CHECK_SECONDS:
            return False
        self._checked = now
        try:
            return shutil.disk_usage(self.tmp_dir).free < self.floor()
        except OSError:
            return False


def _extract_cmd(video_path: str, wav_path: str) -> list:
    return ["ffmpeg", "-y", *video_export.local_input(), "-i", video_path, "-vn",
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
    from lib import http
    from services import metadata_service as ms
    from sources import http_transport as shttp
    limit = media_upload_service.max_upload_bytes()
    started = clock()
    current = url
    for _ in range(MAX_DIRECT_REDIRECTS + 1):
        try:
            ip = ms.check_public_url(current)
            resp = http.pinned_get(current, ip, _DIRECT_HEADERS)
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
_DNS_RE = re.compile(r"failed to resolve|getaddrinfo|name or service not known|"
                     r"temporary failure in name resolution|nodename nor servname|"
                     r"no address associated with hostname", re.I)
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
     "The site refused the download. This is often temporary, so try again."),
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
    if _DNS_RE.search(haystack):
        return _DNS_BLOCKED if "googlevideo.com" in haystack.lower() else _DNS_BLOCKED_OTHER
    for rx, text in _FAILURE_RES:
        if rx.search(haystack):
            return text
    return ""


def _hm(seconds) -> str:
    seconds = int(seconds)
    h, m = seconds // 3600, seconds % 3600 // 60
    if h:
        return f"{h} h" if not m else f"{h} h {m} m"
    return f"{m} m" if seconds >= 60 else f"{seconds} s"


def _size(nbytes) -> str:
    gb = nbytes / 1024 ** 3
    return f"{gb:.1f} GB" if gb >= 1 else f"{max(nbytes // 1024 ** 2, 1)} MB"


def _refusal_text(detail: dict, part: bool, allow_long: bool) -> str:
    """The sentence for a refusal from the child. Only whitelisted numbers go
    in (the child's event is shape-checked), never page text."""
    reason = detail.get("reason")
    what = "This part" if part else "This video"
    if reason == "too_long":
        limit = ytdlp_child.HARD_MAX_DURATION_SECONDS if allow_long else ytdlp_child.MAX_DURATION_SECONDS
        text = f"{what} is {_hm(detail['duration'])}; the limit is {_hm(limit)}."
        if allow_long:
            return f"{text} Download only part of it instead."
        guess = detail.get("estimated_bytes")
        need = f" It needs about {_size(guess)} of free disk." if guess else ""
        return f"{text}{need} Download only part of it, or all of it anyway."
    if reason == "part_outside":
        return f"The part starts after the end of the video, which is {_hm(detail['duration'])} long."
    return _REFUSALS.get(reason, _REJECTED)


def _too_slow(wall) -> str:
    return _TOO_SLOW if wall == MAX_WALL_SECONDS else (
        f"The download took longer than {int(wall // 3600)} hours, so it was stopped.")


class _WallClock:
    """The time a yt-dlp download may take. The child process gets the
    ceiling as its hard cap; this decides when the parent stops earlier. The
    long clock is only for work that really is long: a re-encoded section
    with an explicit end (about real time), or a clip the child reports as
    past the normal length cap. A request that merely asks for a long clock
    (exact cuts with no end, "Download anyway" on a short video) keeps the
    normal one."""

    def __init__(self, extended: bool, may_extend: bool, clock=time.monotonic):
        self.clock, self.started = clock, clock()
        self.base, self.long = MAX_WALL_SECONDS, MAX_WALL_SECONDS_LONG
        self.extended = extended
        self.ceiling = self.long if (extended or may_extend) else self.base

    @property
    def allowed(self) -> float:
        return self.long if self.extended else self.base

    def extend(self) -> None:
        self.extended = True

    def expired(self) -> bool:
        return self.clock() - self.started > self.allowed


def _free_floor() -> int:
    """Other running downloads are still writing to the same drive, so each
    one raises the amount that must stay free."""
    with _slots:
        others = max(len(_running) - 1, 0)
    return MIN_FREE_BYTES * (1 + others)


# Failures YouTube and similar sites often clear on their own within seconds
# (a refused or throttled request, a dropped connection, a server error). A
# sign-in prompt, format or availability error is not in this list: a retry
# can't fix those.
_TRANSIENT_RE = re.compile(r"http error (403|429|5\d\d)|too many requests|forbidden|rate[- ]?limit|"
                           r"timed out|connection (reset|aborted|refused)|remote end closed|"
                           r"unable to download (video data|webpage)|incompleteread", re.I)
RETRY_BACKOFF_SECONDS = 8.0


def _retryable(result: dict, end: dict, state: dict) -> bool:
    if result.get("path") or result.get("rejected") or result.get("missing") or result.get("no_room"):
        return False
    if state["out_of_space"] or state["too_slow"] or end.get("timed_out") or end.get("cancelled"):
        return False
    error = result.get("error")
    return isinstance(error, str) and not _DNS_RE.search(error) and bool(_TRANSIENT_RE.search(error))


def _cancel_aware_sleep(job_id: str, seconds: float) -> bool:
    """False if Cancel was requested during the wait."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if background_jobs.is_cancel_requested(job_id):
            return False
        time.sleep(min(0.25, max(deadline - time.monotonic(), 0)))
    return not background_jobs.is_cancel_requested(job_id)


def _run_child(job_id: str, tmp: str, spec: dict, wall, watch) -> tuple:
    """One yt-dlp child run: (result, end, state) as collected from its events."""
    result, end = {}, {}
    state = {"out_of_space": False, "too_slow": False}

    def stop_now() -> bool:
        # Polled by the process reader on a timer, not per progress event: a
        # section is fetched by ffmpeg, which prints no progress yt-dlp can
        # report, and it must still stop at the free-disk floor.
        if background_jobs.is_cancel_requested(job_id):
            return True
        if wall.expired():
            state["too_slow"] = True
            return True
        if watch.nearly_full():
            state["out_of_space"] = True
            return True
        return False
    path_ignored_logged = False
    events = ytdlp_child.run_download(tmp, spec, wall.ceiling, stop_now)
    try:
        for item in events:
            event = item.get("event")
            if event is None:
                end = item
            elif "estimate" in event:
                if event.get("seconds", 0) > ytdlp_child.MAX_DURATION_SECONDS:
                    wall.extend()
                try:
                    free = shutil.disk_usage(tmp).free
                except OSError:
                    free = None
                if event["estimate"] and free is not None and free < event["estimate"] + _free_floor():
                    state["out_of_space"] = result["no_room"] = True
                    break
            elif "progress" in event:
                background_jobs.update_progress(
                    job_id, 0.05 + 0.8 * min(max(float(event["progress"]), 0.0), 1.0),
                    "Downloading...")
            elif "path" in event and "path" in result:
                if not path_ignored_logged:
                    path_ignored_logged = True
                    applog.get_logger().warning("The download child reported a second file; keeping the first.")
            else:
                result.update(event)
    finally:
        events.close()  # kills the child tree if we stopped early
    return result, end, state


def _download(job_id: str, url: str, tmp: str, audio_only: bool, options: dict = None) -> tuple:
    """Runs yt-dlp in a child process (services.ytdlp_child) that Cancel, the
    wall-clock cap and a nearly full drive kill, then returns (path, title,
    link), link being the video's ytdlp_child.link_fields or None."""
    options = options or {}
    ranged = options.get("part_start") is not None or options.get("part_end") is not None
    cookies = settings_service.get_cookie_settings()
    if ranged and (cookies.get("cookies_browser") or cookies.get("cookies_file")):
        raise RuntimeError(_PART_NO_COOKIES)
    section = ranged and options.get("part_end") is not None
    wall = _WallClock(extended=bool(options.get("exact_cuts") and section),
                      may_extend=bool(options.get("allow_long")))
    spec = {"url": url, "audio_only": audio_only, **cookies}
    if options.get("allow_long"):
        spec["max_seconds"] = ytdlp_child.HARD_MAX_DURATION_SECONDS
    for key in ("part_start", "part_end", "exact_cuts"):
        if options.get(key) is not None:
            spec[key] = options[key]
    watch = _SpaceWatch(tmp, floor=_free_floor)
    result, end, state = _run_child(job_id, tmp, spec, wall, watch)
    if _retryable(result, end, state):
        applog.get_logger().info("URL download failed with a likely temporary error; retrying once.")
        if _cancel_aware_sleep(job_id, RETRY_BACKOFF_SECONDS):
            background_jobs.update_progress(job_id, 0.02, "Retrying the download...")
            result, end, state = _run_child(job_id, tmp, spec, wall, watch)
    if background_jobs.is_cancel_requested(job_id):
        raise background_jobs.JobCancelled(job_id)
    if state["out_of_space"]:
        raise RuntimeError(_NO_ROOM if result.get("no_room") else _DISK_NEARLY_FULL)
    if state["too_slow"] or end.get("timed_out"):
        raise RuntimeError(_too_slow(wall.allowed))
    if end.get("cancelled"):
        raise background_jobs.JobCancelled(job_id)
    path = result.get("path")
    if not isinstance(path, str):
        detail = result.get("rejected")
        if detail:
            code = detail["reason"]
            if code == "too_long" and options.get("allow_long"):
                code = "too_long_max"
            background_jobs.set_result(job_id, {"kind": "url_media", "failed_reason": code})
            raise RuntimeError(_refusal_text(
                detail, options.get("part_start") is not None or options.get("part_end") is not None,
                bool(options.get("allow_long"))))
        if result.get("missing"):
            raise RuntimeError(_NO_YTDLP)
        reason = _failure_reason(RuntimeError(translate_engines.redact_secrets(
            str(result.get("error") or ""))))
        if reason in (_DNS_BLOCKED, _DNS_BLOCKED_OTHER):
            raise RuntimeError(reason)
        raise RuntimeError(f"{reason} {_FAILED}" if reason else _FAILED)
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(tmp) + os.sep) or not os.path.isfile(real):
        raise RuntimeError(_FAILED)
    title = result.get("title")
    return real, title if isinstance(title, str) else None, result.get("link")


def _ordinal(n: int) -> str:
    if n % 100 in (11, 12, 13):
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _enqueue(job_id: str) -> None:
    with _slots:
        _waiting.append(job_id)


def _leave_queue(job_id: str) -> None:
    with _slots:
        if job_id in _waiting:
            _waiting.remove(job_id)
        _running.discard(job_id)
        _slots.notify_all()


def _queued_total() -> int:
    with _slots:
        return len(_waiting) + len(_running)


def _wait_for_slot(job_id: str, recheck) -> None:
    """Blocks until this job is first in line, a slot is free and the spacing
    since the last start has passed; then takes the slot. First in, first out:
    a later request never overtakes an earlier one. `recheck` runs once the
    slot is taken, outside the lock (it resolves DNS); if it raises, the job
    fails and its `finally` gives the slot back. Cancel is polled, so a
    waiting job stops within half a second."""
    global _last_start
    shown = None
    try:
        while True:
            if background_jobs.is_cancel_requested(job_id):
                raise background_jobs.JobCancelled(job_id)
            with _slots:
                position = _waiting.index(job_id) + 1
                spacing_left = START_SPACING_SECONDS - (_clock() - _last_start)
                free = len(_running) < MAX_PARALLEL_DOWNLOADS
                if position == 1 and free and spacing_left <= 0:
                    _waiting.remove(job_id)
                    _running.add(job_id)
                    _last_start = _clock()
                    _slots.notify_all()
                    break
            text = f"Queued: {_ordinal(position)} in line"
            if text != shown:
                shown = text
                background_jobs.update_progress(job_id, 0.0, text)
            with _slots:
                _slots.wait(min(0.5, max(spacing_left, 0.05)) if position == 1 and free else 0.5)
    except BaseException:
        _leave_queue(job_id)
        raise
    recheck()


def _check_request(drama_id, url, audio_only, confirm_replace_audio, own_job_id=None):
    """The per-request checks, run when the request arrives and again when the
    download starts. Returns the cleaned URL and the drama."""
    url = check_public_url(url)
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    if (drama.get("content_mode") or "audio_drama") not in media_upload_service.UPLOAD_CONTENT_MODES:
        raise InvalidInputError(media_upload_service.NO_UPLOAD_MODE)
    if media_upload_service.has_media(drama, drama_id) and not confirm_replace_audio:
        if own_job_id:
            raise InvalidInputError(_NOW_BUSY)
        raise InvalidInputError("This title already has audio. Confirm replacing it first.",
                                details={"reason": "confirm_replace_audio"})
    if direct_media_ext(url) is None and not _yt_dlp_installed():
        raise DependencyUnavailableError(_NO_YTDLP)
    if drama_service.job_running_for_drama(drama_id, exclude_job_id=own_job_id):
        raise ConflictError(_BUSY)
    return url, drama


def _recheck_at_start(job_id, drama_id, url, audio_only, confirm_replace_audio):
    """A queued download may start long after it was asked for: the title may
    have got a job or an upload, new audio, or been deleted since. Fails the
    job with the same plain text the request would have got. Also the free-disk
    floor, which counts the downloads already running."""
    try:
        _check_request(drama_id, url, audio_only, confirm_replace_audio, own_job_id=job_id)
    except (ConflictError, InvalidInputError, NotFoundError, DependencyUnavailableError) as exc:
        raise RuntimeError(str(exc)) from None
    try:
        free = shutil.disk_usage(storage.temp_root()).free
    except OSError:
        return
    if free < _free_floor():
        raise RuntimeError(_NO_ROOM)


def _download_job(job_id: str, drama_id: int, url: str, audio_only: bool,
                  confirm_replace_audio: bool = False, options: dict = None):
    try:
        _wait_for_slot(job_id, lambda: _recheck_at_start(
            job_id, drama_id, url, audio_only, confirm_replace_audio))
        _run_download_job(job_id, drama_id, url, audio_only, options)
    finally:
        _leave_queue(job_id)


def _run_download_job(job_id: str, drama_id: int, url: str, audio_only: bool, options: dict = None):
    tmp = storage.new_workdir(job_id)
    try:
        background_jobs.update_progress(job_id, 0.02, "Starting the download...")
        direct_ext = direct_media_ext(url)
        if direct_ext:
            path, title, link = _direct_download(job_id, url, tmp, direct_ext), None, None
        else:
            path, title, link = _download(job_id, url, tmp, audio_only, options)
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
        fields = {}
        drama = db.get_drama(drama_id) or {}  # re-read just before writing
        if title and not (drama.get("title_en") or drama.get("title_zh")):
            fields["title_zh"] = str(title).strip()[:300]
        try:
            media_upload_service.install_media(drama_id, new_files, **fields)
        except (OSError, sqlite3.Error):
            raise RuntimeError(_SAVE_FAILED) from None
        drama_service.set_source_url_once(drama_id, url)
        filled = link_details_service.fill_after_download(drama_id, link)
        background_jobs.set_result(job_id, {"kind": "url_media", "audio_only": bool(audio_only),
                                            "title_filled": "title_zh" in fields,
                                            "details_filled": filled})
        background_jobs.update_progress(job_id, 1.0, "Downloaded.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_CLOCK_RE = re.compile(r"^\d{1,3}(:\d{1,2}){0,2}$")


def parse_clock(text, label: str):
    """Seconds from "h:mm:ss", "m:ss" or plain seconds; None for empty."""
    if text is None or not str(text).strip():
        return None
    text = str(text).strip()
    if not _CLOCK_RE.match(text):
        raise InvalidInputError(f"The {label} time must look like 1:02:30 (hours:minutes:seconds).")
    parts = [int(p) for p in text.split(":")]
    if any(p >= 60 for p in parts[1:]):
        raise InvalidInputError(f"The {label} time must look like 1:02:30 (minutes and seconds under 60).")
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def start_url_download(drama_id, url, audio_only, confirm_replace_audio=False, allow_long=False,
                       part_start=None, part_end=None, exact_cuts=False) -> dict:
    """Starts `urlmedia_<drama_id>`; poll GET /api/jobs/{job_id}. The job
    waits in line when MAX_PARALLEL_DOWNLOADS are running or the last start
    was under START_SPACING_SECONDS ago. 422 bad or private URL, wrong
    content_mode, bad part times, or audio or video exists without
    confirm_replace_audio (details.reason "confirm_replace_audio"); 503 the
    host doesn't resolve or yt-dlp is missing; 404 no drama; 409 while a
    job or upload runs for the drama, or the queue is full.
    allow_long raises the 6 h cap to 12 h for this download only;
    part_start/part_end (h:mm:ss) download just that section."""
    flags = (audio_only, confirm_replace_audio, allow_long, exact_cuts)
    if not all(isinstance(f, bool) for f in flags):
        raise InvalidInputError("audio_only, confirm_replace_audio, allow_long and exact_cuts "
                                "must be true or false.")
    start, end = parse_clock(part_start, "start"), parse_clock(part_end, "end")
    if end is not None and end <= (start or 0):
        raise InvalidInputError("The end of the part must be after its start.")
    if end is not None and end - (start or 0) > ytdlp_child.HARD_MAX_DURATION_SECONDS:
        raise InvalidInputError("The part is longer than 12 hours. Choose a shorter part.")
    ranged = start is not None or end is not None
    if exact_cuts and not ranged:
        raise InvalidInputError("Exact cuts only apply when you choose a part.")
    url, _ = _check_request(drama_id, url, audio_only, confirm_replace_audio)
    if direct_media_ext(url) is not None and (ranged or exact_cuts):
        raise InvalidInputError("Choosing a part works for video-site links, not direct file links.")
    if ranged:
        cookies = settings_service.get_cookie_settings()
        if cookies.get("cookies_browser") or cookies.get("cookies_file"):
            raise InvalidInputError(_PART_NO_COOKIES)
    options = {"allow_long": allow_long, "part_start": start if ranged else None,
               "part_end": end, "exact_cuts": exact_cuts if ranged else None}
    with media_upload_service.claims_lock:
        if drama_id in media_upload_service.claimed:
            raise ConflictError("Another upload is in progress for this title.")
        media_upload_service.claimed.add(drama_id)
    try:
        with _start_lock:
            if drama_service.job_running_for_drama(drama_id):
                raise ConflictError(_BUSY)
            if _queued_total() >= MAX_QUEUED_DOWNLOADS:
                raise ConflictError(_QUEUE_FULL)
            job_id = job_id_for(drama_id)
            background_jobs.clear_job(job_id)
            _enqueue(job_id)
            try:
                started = job_launch.start_job(
                    job_id, _download_job, job_id, drama_id, url, audio_only,
                    confirm_replace_audio, options, description=f"URL download (title #{drama_id})")
            except BaseException:
                _leave_queue(job_id)
                raise
            if not started:
                _leave_queue(job_id)
                raise ConflictError(_BUSY)
        return {"job_id": job_id}
    finally:
        with media_upload_service.claims_lock:
            media_upload_service.claimed.discard(drama_id)
