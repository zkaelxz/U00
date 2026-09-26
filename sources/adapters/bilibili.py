"""
sources/adapters/bilibili.py -- Bilibili as a real VideoSource adapter
(Step 23d).

Built on yt-dlp's own maintained Bilibili extractor as the extraction
backend, not a hand-rolled scraper against Bilibili's private API/signing
system -- yt-dlp already handles WBI request signing, BVID/AVID
resolution, and DASH format extraction, and replicates the same API
calls Bilibili's own public web player makes to render a video for an
ordinary browser visit. That's reproducing a public client's normal
request-construction logic, not defeating an anti-bot challenge -- the
same distinction already drawn for manhuagui's plain-HTTP reachability.
No CAPTCHA solving, no anti-bot-challenge defeat, no signing-system
reimplementation happens here, or ever will.

Unlike the comic/novel adapters in this package, Bilibili is a VIDEO
source: the SourceAdapter base interface's search()/get_series()/
get_chapters()/get_pages()/download_page()/get_chapter_text() shape is
built around paginated text/image content and doesn't fit a video's own
metadata/format/multipart/subtitle shape. This adapter still subclasses
SourceAdapter (for registry/front-door participation, url_patterns,
capabilities()) but adds the video-specific method surface the roadmap
asks for as new methods, rather than forcing video into the comic/novel
shape.

Also unlike the other adapters, yt-dlp manages its own HTTP end to end
(it isn't built to run its network calls through an injectable
transport), so requests made through it don't go through
sources.http.SourceClient's pacing/retry/challenge-detection pipeline --
self.client exists (for interface consistency, and in case a future
metadata-only HTTP need arises) but every real network call here goes
through yt-dlp directly. Pacing/backoff for Bilibili's own known
transient failures (HTTP 412 and other risk-control responses) is
implemented at this adapter's own level instead, below.
"""

import os
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..base import SourceAdapter
from ..models import (AccessTier, CapabilityStatus, ContentType, FailureReason,
                      SourceError, TechnicalStatus)
from ..registry import register

_URL_PATTERNS = [
    r"bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)",
    r"bilibili\.com/bangumi/play/",
    r"b23\.tv/",
]

# Bilibili's own known transient risk-control responses (item 8) -- a
# small, capped number of retries with exponential backoff, never
# aggressive re-hitting. 500-504 are ordinary transient server trouble,
# included for the same reason.
_RETRYABLE_STATUS_RE = re.compile(r"\bHTTP Error (412|429|500|502|503|504)\b")
MAX_RETRIES = 3
BACKOFF_BASE = 2.0

INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

# height -> the quality label the UI offers for it (item 4). "Best
# available" and "Audio only" aren't height-gated, so they're handled
# separately from this map.
QUALITY_HEIGHTS = {"1080p": 1080, "720p": 720, "480p": 480, "360p": 360}


class BilibiliDownloadError(SourceError):
    """A Bilibili-specific failure translated to a plain message (item 7)
    -- extends video_download.DownloadError's existing "clear message,
    original exception chained for the debug log" pattern rather than
    inventing a new error shape."""


def sanitize_filename(name: str, max_len: int = 150) -> str:
    """Strips characters Windows can't have in a filename, and truncates
    an overlong title (item 11)."""
    name = INVALID_FILENAME_CHARS.sub("", name or "").strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip()
    return name or "untitled"


def dedupe_path(path: str) -> str:
    """Never silently overwrites an existing file (item 11) -- appends
    " (2)", " (3)", ... until a free name is found."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def _retry_with_backoff(fn, max_retries: int = MAX_RETRIES, backoff_base: float = BACKOFF_BASE,
                        sleep=time.sleep):
    """Runs fn() once, retrying only Bilibili's known transient failures
    (item 8) with exponential backoff, up to max_retries times. Anything
    else -- including a non-transient failure -- propagates immediately,
    on the first attempt, same as calling fn() directly."""
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            if not _RETRYABLE_STATUS_RE.search(str(exc)) or attempt >= max_retries:
                raise
            sleep(backoff_base * (2 ** attempt))
            attempt += 1


def classify_failure(exc: Exception) -> FailureReason:
    """Maps a yt-dlp exception's message to one of this app's own named
    failure reasons (item 7), so the UI shows a plain message instead of
    yt-dlp's raw exception text -- which is still kept, chained, for the
    debug log."""
    msg = str(exc).lower()
    if "412" in msg or "429" in msg or "risk control" in msg:
        return FailureReason.RATE_LIMIT
    if "login" in msg or "vip" in msg or "member" in msg or "authenticat" in msg:
        return FailureReason.AUTHENTICATION_REQUIRED
    if "region" in msg or "geo" in msg or "not available in your" in msg:
        return FailureReason.GEO_RESTRICTION
    if "private" in msg or "unavailable" in msg or "deleted" in msg or "not found" in msg:
        return FailureReason.ACCESS_DENIED
    if "timed out" in msg or "timeout" in msg or "network" in msg or "connection" in msg:
        return FailureReason.TIMEOUT
    if "unsupported url" in msg or "no video formats" in msg:
        return FailureReason.UNKNOWN
    return FailureReason.UNKNOWN


_FAILURE_MESSAGES = {
    FailureReason.RATE_LIMIT: "Bilibili is rate-limiting this request. Try again in a few minutes.",
    FailureReason.ACCESS_DENIED: "This video is unavailable, deleted, or private.",
    FailureReason.AUTHENTICATION_REQUIRED:
        "This video needs Bilibili login -- configure browser cookies in Settings.",
    FailureReason.GEO_RESTRICTION: "This video is region-restricted and isn't available from here.",
    FailureReason.TIMEOUT: "The connection to Bilibili timed out.",
    FailureReason.UNKNOWN: "Couldn't fetch that Bilibili video. It may be an unsupported "
                           "or malformed URL, or yt-dlp may need updating (pip install -U yt-dlp).",
}


@register
class BilibiliSource(SourceAdapter):
    name = "bilibili"
    display_name = "Bilibili"
    content_types = [ContentType.VIDEO.value]
    languages = ["zh"]
    url_patterns = _URL_PATTERNS
    auth_supported = True  # cookie-based, reusing Step 9b's planned mechanism (item 6)

    def __init__(self, client=None, ydl_factory=None, url_resolver=None, sleep=time.sleep,
                **client_kwargs):
        super().__init__(client, **client_kwargs)
        self._ydl_factory = ydl_factory or self._real_ydl_factory
        self._url_resolver = url_resolver or self._real_url_resolver
        self._sleep = sleep

    @staticmethod
    def _real_ydl_factory(opts: dict):
        import yt_dlp
        return yt_dlp.YoutubeDL(opts)

    @staticmethod
    def _real_url_resolver(url: str) -> str:
        import requests
        resp = requests.head(url, allow_redirects=True, timeout=10)
        return resp.url

    # -- URL handling ---------------------------------------------------------

    def can_handle(self, url: str) -> bool:
        return self.matches_url(url)

    def normalize_url(self, url: str) -> str:
        """Resolves a b23.tv short link, strips tracking params, and
        keeps only an explicit ?p=N part selector (item 1, item 3)."""
        url = (url or "").strip()
        if "b23.tv" in url:
            url = self._url_resolver(url)
        parts = urlsplit(url)
        kept = [(k, v) for k, v in parse_qsl(parts.query) if k == "p"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))

    # -- metadata/info ----------------------------------------------------------

    def _extract(self, url: str, download: bool = False, extra_opts: dict = None) -> dict:
        opts = {"quiet": True, "no_warnings": True, "skip_download": not download}
        if extra_opts:
            opts.update(extra_opts)

        def _do():
            ydl = self._ydl_factory(opts)
            with ydl:
                return ydl.extract_info(url, download=download)
        try:
            return _retry_with_backoff(_do, sleep=self._sleep)
        except Exception as exc:
            reason = classify_failure(exc)
            raise BilibiliDownloadError(_FAILURE_MESSAGES[reason], reason) from exc

    def extract_info(self, url: str) -> dict:
        """Metadata-only, never downloads (item 2)."""
        return self._extract(self.normalize_url(url), download=False)

    def get_metadata(self, url: str) -> dict:
        info = self.extract_info(url)
        return {
            "title": info.get("title"), "uploader": info.get("uploader"),
            "upload_date": info.get("upload_date"), "description": info.get("description"),
            "duration": info.get("duration"), "thumbnail": info.get("thumbnail"),
            "bvid": info.get("id"), "webpage_url": info.get("webpage_url") or url,
        }

    def list_formats(self, url: str) -> list:
        info = self.extract_info(url)
        out = []
        for f in (info.get("formats") or []):
            out.append({
                "format_id": f.get("format_id"),
                "resolution": f.get("resolution") or f.get("format_note"),
                "height": f.get("height"), "fps": f.get("fps"),
                "vcodec": f.get("vcodec"), "acodec": f.get("acodec"),
                "tbr": f.get("tbr"), "filesize": f.get("filesize") or f.get("filesize_approx"),
            })
        return out

    def available_qualities(self, url: str) -> list:
        """Which quality labels this specific video actually has a
        format for (item 4) -- "Best available" is always offered, the
        rest only if list_formats() actually reported a matching height,
        and "Audio only" only if a real audio-only format exists."""
        formats = self.list_formats(url)
        heights = {f["height"] for f in formats if f.get("height")}
        has_audio_only = any(
            (f.get("vcodec") in (None, "none")) and (f.get("acodec") not in (None, "none"))
            for f in formats)
        qualities = ["Best available"]
        for label, height in QUALITY_HEIGHTS.items():
            if height in heights:
                qualities.append(label)
        if has_audio_only:
            qualities.append("Audio only")
        return qualities

    def resolve_quality(self, url: str, requested: str):
        """(actual_quality, message). message is None unless a fallback
        happened; actual_quality is always something available_qualities()
        actually reported (item 4)."""
        available = self.available_qualities(url)
        if requested in available:
            return requested, None
        if requested not in QUALITY_HEIGHTS:
            return "Best available", None
        requested_height = QUALITY_HEIGHTS[requested]
        lower = [q for q in available if q in QUALITY_HEIGHTS and QUALITY_HEIGHTS[q] < requested_height]
        if lower:
            best_lower = max(lower, key=lambda q: QUALITY_HEIGHTS[q])
            return best_lower, f"{requested} isn't available for this video -- using {best_lower} instead."
        return ("Best available",
                f"{requested} isn't available for this video -- using the best available quality instead.")

    def get_subtitles(self, url: str) -> list:
        """Tags each track's subtitle_type so the UI never presents an
        ASR-sourced Bilibili subtitle as a verbatim human transcript
        (item 5). An empty list (no subtitles) is not an error."""
        info = self.extract_info(url)
        out = []
        for lang, tracks in (info.get("subtitles") or {}).items():
            for t in tracks:
                out.append({"language": lang, "url": t.get("url"), "ext": t.get("ext"),
                           "subtitle_type": "human"})
        for lang, tracks in (info.get("automatic_captions") or {}).items():
            for t in tracks:
                out.append({"language": lang, "url": t.get("url"), "ext": t.get("ext"),
                           "subtitle_type": "ai_generated"})
        return out

    def get_parts(self, url: str) -> list:
        """Multipart/anthology detection (item 3): [{"index","part_id",
        "title"}, ...] in order, titles preserved. A single video (or a
        URL with an explicit ?p=N, which yt-dlp's own extractor already
        resolves to one entry) returns a one-item list."""
        info = self.extract_info(url)
        entries = info.get("entries")
        if not entries:
            return [{"index": 1, "part_id": info.get("id"), "title": info.get("title")}]
        return [{"index": i, "part_id": e.get("id"), "title": e.get("title") or f"Part {i}"}
                for i, e in enumerate(entries, start=1)]

    def _format_string_for(self, quality: str) -> str:
        if quality == "Audio only":
            return "bestaudio/best"
        if quality in QUALITY_HEIGHTS:
            h = QUALITY_HEIGHTS[quality]
            return f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
        return "bestvideo+bestaudio/best"

    # -- download -----------------------------------------------------------

    def download(self, url: str, out_dir: str, options: dict = None) -> dict:
        """Downloads and returns the standardized media-output object
        (item 9): source, source_url, bvid, title, duration, part/
        part_title, path, thumbnail, subtitles, raw_metadata.

        options: quality (one of available_qualities(url), default "Best
        available"), part_index (1-based, for a multipart URL with no
        explicit ?p=N -- defaults to part 1, never the whole anthology),
        include_subtitles (bool), cookies_browser/cookies_file (item 6).
        """
        options = options or {}
        url = self.normalize_url(url)
        quality = options.get("quality", "Best available")
        audio_only = quality == "Audio only"
        resolved_quality, quality_message = self.resolve_quality(url, quality)

        os.makedirs(out_dir, exist_ok=True)
        info = self.extract_info(url)  # metadata before any download (item 2)
        part_title = None
        target_url = info.get("webpage_url") or url
        if info.get("entries"):
            entries = info["entries"]
            idx = max(0, min(options.get("part_index", 1) - 1, len(entries) - 1))
            part_info = entries[idx]
            part_title = part_info.get("title")
            target_url = part_info.get("webpage_url") or part_info.get("url") or target_url

        title = sanitize_filename(info.get("title") or info.get("id") or "bilibili_video")
        fname_base = f"[Bilibili] {title}"
        if part_title:
            fname_base += f" - {sanitize_filename(part_title)}"
        dest = dedupe_path(os.path.join(out_dir, f"{fname_base}.{'wav' if audio_only else 'mp4'}"))

        ydl_opts = {
            "format": self._format_string_for(resolved_quality),
            "outtmpl": os.path.splitext(dest)[0] + ".%(ext)s",
            "quiet": True, "no_warnings": True, "noplaylist": True,
        }
        if audio_only:
            ydl_opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]
        else:
            ydl_opts["merge_output_format"] = "mp4"
        if options.get("cookies_browser"):
            ydl_opts["cookiesfrombrowser"] = (options["cookies_browser"],)
        if options.get("cookies_file"):
            ydl_opts["cookiefile"] = options["cookies_file"]

        def _do():
            ydl = self._ydl_factory(ydl_opts)
            with ydl:
                return ydl.extract_info(target_url, download=True)
        try:
            result_info = _retry_with_backoff(_do, sleep=self._sleep)
        except Exception as exc:
            reason = classify_failure(exc)
            raise BilibiliDownloadError(_FAILURE_MESSAGES[reason], reason) from exc

        final_ext = "wav" if audio_only else (result_info.get("ext") or "mp4")
        final_path = os.path.splitext(dest)[0] + "." + final_ext
        if not os.path.exists(final_path):
            raise BilibiliDownloadError(
                f"yt-dlp reported success but the expected output file is missing: {final_path}",
                FailureReason.UNKNOWN)
        subtitles = self.get_subtitles(url) if options.get("include_subtitles") else []

        return {
            "source": self.name, "source_url": url,
            "bvid": result_info.get("id") or info.get("id"),
            "title": result_info.get("title") or title,
            "duration": result_info.get("duration"),
            "part": options.get("part_index") if part_title else None,
            "part_title": part_title,
            "path": final_path,
            "thumbnail": result_info.get("thumbnail"),
            "subtitles": subtitles,
            "raw_metadata": result_info,
            "quality": resolved_quality,
            "quality_message": quality_message,
        }

    # -- capabilities ------------------------------------------------------------

    def capabilities(self):
        caps = super().capabilities()
        caps.status = CapabilityStatus.VERIFIED.value
        caps.technical_status = TechnicalStatus.SUPPORTED.value
        caps.access_method = AccessTier.STATIC_HTTP.value
        caps.technical["extraction_method"] = (
            "yt-dlp's maintained Bilibili extractor -- replicates the same API calls "
            "Bilibili's own public web player makes (including WBI request signing), "
            "not a defeat of any anti-bot challenge.")
        caps.technical["no_challenge_defeat"] = True
        caps.terms["checked"] = (
            "Not independently re-read for this adapter -- yt-dlp's own maintained "
            "extractor is the basis for treating ordinary public-video access as "
            "supported; a video that needs login shows a clear message rather than "
            "attempting to work around the requirement.")
        return caps
