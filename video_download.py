"""
video_download.py -- fetches audio/video from a URL (YouTube and the many
other sites yt-dlp supports) directly into a drama's folder, so you don't
need to run yt-dlp on the command line yourself and then upload the
result through the file picker.

Wraps yt-dlp's Python API (not the CLI) so progress can be reported to a
background job the same way translation/dubbing already do (progress_cb
pattern -- see engine_backends.translate_pipeline.translate_lines_with_engine, dub.py).

SETUP: pip install yt-dlp
Needs ffmpeg on PATH for the audio-extraction postprocessor -- already a
hard requirement of this project (used throughout core.py, video_export.py).

LEGAL: this is a generic download tool, same as running yt-dlp by hand
would be -- only point it at content you actually have the right to use
(your own recordings, purchased/licensed copies, or platforms whose
terms permit it).
"""

import contextlib
import os
import threading
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    # download() casts its options to this: the TypedDict is narrower than the
    # dict callers extend through extra_opts.
    from yt_dlp import _Params


class DownloadError(RuntimeError):
    """Raised when yt-dlp fails to fetch/extract, so callers can show a
    clear message instead of yt-dlp's raw exception text."""


class DownloadAborted(Exception):
    """Raised by a caller's progress hook (passed in extra_opts) to stop a
    download -- a size or time cap, or a cancel. download() re-raises it
    as is, never wrapped in DownloadError, so the caller can tell why."""


def _find_aborted(exc):
    """The DownloadAborted behind `exc`, if any: yt-dlp may wrap an
    exception raised in a hook (a DownloadError whose exc_info holds it)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, DownloadAborted):
            return exc
        info = getattr(exc, "exc_info", None)
        inner = info[1] if isinstance(info, tuple) and len(info) > 1 else None
        exc = inner if isinstance(inner, BaseException) else (exc.__cause__ or exc.__context__)
    return None


# yt-dlp's own supported browser names for --cookies-from-browser.
COOKIE_BROWSERS = ["chrome", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]


def cookie_options(browser: str | None = None, cookies_file: str | None = None) -> dict:
    """yt-dlp options for cookie-based login, for a site that blocks
    unauthenticated requests (TikTok and Instagram in particular, far
    more aggressively than YouTube does) -- previously only ever
    mentioned as a workaround in an error message, never something the
    user could actually turn on. A cookies FILE wins if somehow both are
    given (it's the more specific choice); {} if neither is set, which
    is the existing unauthenticated behavior, unchanged."""
    if cookies_file:
        return {"cookiefile": cookies_file}
    if browser:
        return {"cookiesfrombrowser": (browser,)}
    return {}


THUMBNAIL_MAX_BYTES = 10 * 1024 * 1024
THUMBNAIL_SECONDS = 15.0


def save_thumbnail(ydl, info, path: str, max_bytes: int = THUMBNAIL_MAX_BYTES,
                   seconds: float = THUMBNAIL_SECONDS) -> bool:
    """Best-effort copy of the page's own thumbnail to `path` (raw bytes; the cover code
    checks that they are a picture). Read through `ydl`, so the same proxy and guard as the
    media apply. yt-dlp's own `writethumbnail` has no size or time limit, and a thumbnail
    that never ends must not fail a download whose media already arrived, so the read runs
    in a thread that is abandoned after `seconds` and stops at `max_bytes`. Any failure
    returns False and leaves no file."""
    url = info.get("thumbnail") if isinstance(info, dict) else None
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return False
    result: dict = {}
    # The time limit is the thread join below (yt-dlp's opener takes no timeout argument).
    open_url = ydl.urlopen

    def read():
        try:
            response = open_url(url)
            try:
                # yt-dlp decodes a compressed body whole before the first read, so the byte
                # cap below would not bound memory for one.
                headers = getattr(response, "headers", None)
                if headers is not None and (headers.get("Content-Encoding") or "identity").lower() != "identity":
                    return
                data = bytearray()
                while len(data) <= max_bytes:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    data += chunk
                result["data"] = bytes(data)
            finally:
                response.close()
        except Exception:
            pass

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(seconds)
    data = result.get("data")
    if reader.is_alive() or not data or len(data) > max_bytes:
        return False
    part = path + ".part"
    try:
        with open(part, "wb") as f:
            f.write(data)
        os.replace(part, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(part)
        return False
    return True


def _report_metadata(ydl, info, title_cb, thumbnail_path) -> None:
    """Runs while the YoutubeDL session is still open: the thumbnail is read through it."""
    if title_cb and (title := info.get("title")):
        title_cb(title)
    if thumbnail_path:
        save_thumbnail(ydl, info, thumbnail_path)


def download(url: str, out_dir: str, audio_only: bool = True, progress_cb=None,
             title_cb=None, cookies_browser: str | None = None, cookies_file: str | None = None,
             extra_opts: dict | None = None, thumbnail_path: str | None = None) -> str:
    """Downloads `url` into `out_dir` and returns the path to the
    resulting file.

    audio_only=True (the default -- matches most of this project's audio-
    drama use case): extracts to a single downloaded_audio.flac via the
    same ffmpeg this project already requires, so it drops straight into
    the existing audio pipeline with no separate extraction step.

    audio_only=False: downloads the best available muxed video instead,
    as downloaded_video.<ext> -- the CALLER is responsible for running it
    through the same video->audio extraction step already used for
    uploaded video files (audio_extract.extract_audio_from_video), exactly as if
    it had been picked with the file uploader.

    progress_cb: optional callable(fraction: float, message: str), same
    shape as the progress_cb used elsewhere in this project, so callers
    can report it the same way.

    title_cb: optional callable(title: str), invoked with yt-dlp's own
    title for this URL (the video/stream title, e.g. a YouTube video's
    title) once the download's metadata is available -- lets a caller
    auto-fill a drama's title fields without a second network request
    to re-fetch what this call already knows. Not invoked at all if
    yt-dlp's response has no title (rare, but not guaranteed).

    cookies_browser/cookies_file: pass yt-dlp the person's own login
    (see cookie_options()) for a site that blocks unauthenticated
    requests -- TikTok and Instagram in particular. Both default to
    None, the existing unauthenticated behavior.

    extra_opts: yt-dlp options merged LAST (they win), except
    "progress_hooks", which are appended after this function's own hook.
    A hook may raise DownloadAborted to stop; it is re-raised unwrapped.

    Raises ImportError if yt-dlp isn't installed, or DownloadError (with
    the original exception chained) if the download/extraction itself
    fails.
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise ImportError("Downloading from a URL needs yt-dlp: pip install yt-dlp") from exc

    os.makedirs(out_dir, exist_ok=True)

    def _hook(d):
        if not progress_cb:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            frac = (downloaded / total) if total else 0.0
            speed = (d.get("_speed_str") or "").strip()
            progress_cb(frac * 0.9, f"Downloading... {(d.get('_percent_str') or '').strip()} {speed}")
        elif status == "finished":
            progress_cb(0.9, "Download complete, extracting audio..." if audio_only
                        else "Download complete.")

    # Since late 2025, YouTube downloads need an external JS runtime
    # through yt-dlp's EJS system, or formats silently go missing. Deno
    # is yt-dlp's own default; listing the others too means this still
    # works if only one of them happens to be installed (see Diagnostics
    # for which, if any, is on PATH).
    js_runtimes = {"deno": {}, "node": {}, "bun": {}, "quickjs": {}}

    if audio_only:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(out_dir, "downloaded_audio.%(ext)s"),
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "flac"}],
            "progress_hooks": [_hook],
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "no_color": True,  # no ANSI codes in error text shown in the UI/log
            "js_runtimes": js_runtimes,
        }
    else:
        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": os.path.join(out_dir, "downloaded_video.%(ext)s"),
            "progress_hooks": [_hook],
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "no_color": True,  # no ANSI codes in error text shown in the UI/log
            "merge_output_format": "mp4",
            "js_runtimes": js_runtimes,
        }
    ydl_opts.update(cookie_options(cookies_browser, cookies_file))
    extra = dict(extra_opts or {})
    extra_hooks = list(extra.pop("progress_hooks", None) or [])
    ydl_opts.update(extra)
    ydl_opts["progress_hooks"] = [_hook] + extra_hooks

    try:
        with yt_dlp.YoutubeDL(cast("_Params", ydl_opts)) as ydl:
            info = ydl.extract_info(url, download=True)
            final_path = ydl.prepare_filename(info)
            _report_metadata(ydl, info, title_cb, thumbnail_path)
    except Exception as exc:
        aborted = _find_aborted(exc)
        if aborted is not None:
            raise aborted from None
        cookie_hint = ("" if (cookies_browser or cookies_file) else
                      " If the site needs you to be signed in (TikTok and Instagram "
                      "especially), turn on cookie-based login in Settings.")
        if "format" in str(exc).lower():
            raise DownloadError(
                f"Couldn't download from that URL.\n\n{type(exc).__name__}: {exc}\n\n"
                "Missing formats on a YouTube URL usually means yt-dlp has no JavaScript "
                "runtime to use (Deno, Node, Bun or QuickJS; check the Diagnostics tab). "
                "Install Deno (https://deno.land) and run `pip install -U yt-dlp`. If you "
                "already have one, the link may just be wrong/private/region-locked, or "
                f"the site isn't supported by yt-dlp.{cookie_hint}"
            ) from exc
        raise DownloadError(
            f"Couldn't download from that URL.\n\n{type(exc).__name__}: {exc}\n\n"
            "Common causes: the link is wrong/private/region-locked, the site isn't "
            "supported by yt-dlp, or yt-dlp is out of date for a site that changed "
            f"recently -- try `pip install -U yt-dlp` first.{cookie_hint}"
        ) from exc

    if audio_only:
        # FFmpegExtractAudio rewrites the extension to the target codec
        # after the fact -- prepare_filename() reports the pre-conversion
        # name, so swap it to what's actually on disk.
        final_path = os.path.splitext(final_path)[0] + ".flac"

    if not os.path.exists(final_path):
        raise DownloadError(
            f"yt-dlp reported success but the expected output file is missing: {final_path}"
        )

    if progress_cb:
        progress_cb(1.0, "Done.")
    return final_path
