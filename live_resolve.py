"""
live_resolve.py -- turns a Live stream's page link into the direct media URL
the capture fetches, through yt-dlp, without downloading anything; and the
error Live capture raises with a message meant for the owner.
"""
import re


class LiveCaptureError(RuntimeError):
    """Raised when the stream URL can't be resolved or capture can't
    start, so callers can show a clear message instead of a raw
    yt-dlp/subprocess traceback."""


# Attempted in order until one returns a usable format. "No video formats
# found" on a confirmed-live, confirmed-up-to-date-yt-dlp stream is a real,
# widely-reported YouTube-side issue, not a bug specific to this app: since
# 2024 YouTube has increasingly required a proof-of-origin ("PO") token for
# the default web player client, and yt-dlp's own maintainers' documented
# workaround is to request formats through a different player client that
# doesn't need one -- which one currently works shifts over time as YouTube
# and yt-dlp keep adjusting, so several are tried rather than betting on one.
_YOUTUBE_CLIENT_FALLBACKS = ["android", "tv", "web_safari"]

# Deliberately small: these fail identically on every attempt (wrong link,
# no access, gone), so retrying would just waste time. Everything else --
# including yt-dlp/YouTube error strings not seen before, like "no video
# formats found" or "the page needs to be reloaded" -- is assumed to be a
# transient/format-availability issue worth retrying through the
# player-client fallbacks below, rather than hardcoding each new error
# string as it's discovered.
_NON_RETRYABLE_STREAM_ERROR_PHRASES = (
    "private video",
    "video unavailable",
    "video has been removed",
    "this video is no longer available",
    "not available in your country",
)


def _reason(exc) -> str:
    """yt-dlp's own first line, for the first line of the error: the job status
    shows only that line, and "Couldn't resolve" alone does not say why."""
    lines = [ln.strip() for ln in str(exc).splitlines() if ln.strip()]
    text = re.sub(r"^ERROR:\s*", "", lines[0]) if lines else ""
    return text or type(exc).__name__


def resolve_stream_url(url: str, cookies_browser: str = None, cookies_file: str = None,
                       proxy: str = None, ladder=None) -> str:
    """
    Resolves a page URL (YouTube live, or anything yt-dlp supports) to a
    direct, ffmpeg-playable media URL WITHOUT downloading anything -- the
    piece that lets ffmpeg read an ongoing live broadcast the same way it
    reads a file.

    cookies_browser/cookies_file: pass yt-dlp the person's own login (see
    video_download.cookie_options()) for a stream page that needs it.
    proxy: when given, every request yt-dlp makes (redirects included) goes
    through this proxy URL (yt-dlp's `proxy` option).
    """
    try:
        import yt_dlp
    except ImportError as exc:
        raise ImportError("Live capture needs yt-dlp: pip install yt-dlp") from exc

    import video_download
    cookie_opts = video_download.cookie_options(cookies_browser, cookies_file)

    def _try(fmt, player_client=None):
        opts = {"format": fmt, "quiet": True, "no_warnings": True,
                # Since late 2025, YouTube downloads need an external JS
                # runtime through yt-dlp's EJS system, or formats silently
                # go missing -- likely the real cause behind at least some
                # of the "no video formats found" failures this already
                # works around below. Deno is yt-dlp's own default; listing
                # the others too means it still works if only one of them
                # happens to be installed.
                "js_runtimes": {"deno": {}, "node": {}, "bun": {}, "quickjs": {}},
                **cookie_opts}
        if proxy:
            opts["proxy"] = proxy
        if player_client:
            opts["extractor_args"] = {"youtube": {"player_client": [player_client]}}
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    last_exc = None
    for fmt, player_client in (
        ladder or [("bestaudio/best", None), ("best", None),
                   *(("best", c) for c in _YOUTUBE_CLIENT_FALLBACKS)]
    ):
        try:
            info = _try(fmt, player_client)
            break
        except Exception as exc:
            last_exc = exc
            if any(phrase in str(exc).lower() for phrase in _NON_RETRYABLE_STREAM_ERROR_PHRASES):
                raise LiveCaptureError(
                    f"Couldn't resolve that stream URL: {_reason(exc)}\n\n{type(exc).__name__}: {exc}\n\n"
                    "Common causes: the link is wrong/private/region-locked, the stream "
                    "hasn't started yet, or it's already ended."
                ) from exc
    else:
        raise LiveCaptureError(
            f"Couldn't resolve that stream URL: {_reason(last_exc)}\n\n"
            f"{type(last_exc).__name__}: {last_exc}\n\n"
            "Still failed after trying every known player-client workaround "
            f"({', '.join(_YOUTUBE_CLIENT_FALLBACKS)}). This usually means either YouTube's "
            "proof-of-origin token requirement, or that yt-dlp has no JavaScript runtime to "
            "use (Deno, Node, Bun or QuickJS; check the Diagnostics tab). If you don't have "
            "one, install Deno (https://deno.land) and run `pip install -U yt-dlp`. Otherwise "
            "this is a known, actively-shifting YouTube/yt-dlp issue, not specific to this "
            "app -- check https://github.com/yt-dlp/yt-dlp/issues for the current recommended "
            "workaround (often a specific --extractor-args player_client value, or supplying "
            "browser cookies via --cookies-from-browser), since which client currently works "
            "changes as both sides keep adjusting."
        ) from last_exc

    stream_url = info.get("url")
    if not stream_url:
        raise LiveCaptureError(
            "yt-dlp resolved the page but didn't return a direct stream URL -- "
            "this can happen for a stream that hasn't gone live yet."
        )
    return stream_url
