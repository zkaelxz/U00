"""
services/ytdlp_child.py -- runs one yt-dlp download in a child process, so a
job's Cancel (or its time cap) can kill it: extraction, the JavaScript-runtime
challenge solving and the ffmpeg post-processing all run inside the child and
none of them can be interrupted from a thread. The same child also runs the
followed-channel live check (`run_live_probe`, mode "live_status"): metadata
only, no download, for services/live_follow_poller.py.

Why a worker module rather than `python -m yt_dlp` with CLI flags: the caps
are Python callables (a match filter, progress hooks) and `video_download.download`
already turns them into the exact yt-dlp options, error handling and output
name this app relies on; flags would re-express all of that and drift.

Parent side: `run_download` starts `python -m services.ytdlp_child <spec file>`
through lib.proc.stream_tree (own process group, whole tree killed on cancel,
timeout or an early stop) and yields the child's events. The job's own folder
holds the spec, because the link and any cookie path must not appear in the
child's command line. Child side: `run_worker` (a plain function, so tests can
run it in-process) prints one `@@ytdlp-<nonce> {json}` line per event; any other
output line is noise from yt-dlp or ffmpeg and is ignored. The nonce is random
per run and travels only in the spec file: yt-dlp's own error output shares the
stream and can carry site-controlled text, so a fixed prefix could be forged.
"""

import json
import math
import os
import secrets
import sys
import time

from lib import proc

MAX_DURATION_SECONDS = 6 * 60 * 60
SOCKET_TIMEOUT = 30
PROGRESS_SECONDS = 0.5
_PREFIX = "@@ytdlp-"
_SPEC_NAME = "ytdlp_spec.json"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def refusal(info):
    """Why a link is not downloaded (a live stream, a playlist or longer than
    6 hours), else None."""
    info = info or {}
    duration = info.get("duration")
    if (info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming")
            or info.get("_type") == "playlist"
            or (isinstance(duration, (int, float)) and duration > MAX_DURATION_SECONDS)):
        return "Refused: a live stream, a playlist or longer than 6 hours."
    return None


def ydl_options(tmp_dir: str, progress_hook, match_filter) -> dict:
    """The options merged over video_download's own (they win). Never any
    cookie option, and every default extractor except `generic`."""
    return {
        "allowed_extractors": ["default", "-generic"],
        "noplaylist": True,
        "playlistend": 1,
        "match_filter": match_filter,
        "progress_hooks": [progress_hook],
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


LIVE_STATUSES = ("is_live", "is_upcoming", "not_live")
_STREAM_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
_TEXT_MAX = 200


def probe_options() -> dict:
    """yt-dlp options for a live-status check: no download, no format
    check, one item. `ignore_no_formats_error` makes a scheduled stream
    return its metadata (live_status "is_upcoming", release_timestamp)
    instead of failing for want of formats."""
    return {
        "allowed_extractors": ["default", "-generic"],
        "skip_download": True,
        "check_formats": False,
        "ignore_no_formats_error": True,
        "noplaylist": True,
        "playlistend": 1,
        "extract_flat": "in_playlist",
        "socket_timeout": SOCKET_TIMEOUT,
        "retries": 1,
        "extractor_retries": 1,
        "quiet": True,
        "no_warnings": True,
    }


def _short(value) -> str:
    return value[:_TEXT_MAX] if isinstance(value, str) else ""


def live_fields(info) -> dict:
    """The few fields the poller reads, shape-checked. A playlist or channel
    page (what /live becomes when nothing is on) counts as not live."""
    info = info if isinstance(info, dict) else {}
    status = info.get("live_status")
    if status not in LIVE_STATUSES:
        status = "is_live" if info.get("is_live") is True else "not_live"
    if info.get("_type") == "playlist":
        status = "not_live"
    stream_id = info.get("id") if status != "not_live" else None
    if not (isinstance(stream_id, str) and 0 < len(stream_id) <= 64
            and set(stream_id) <= _STREAM_ID_CHARS):
        stream_id = ""
    start = info.get("release_timestamp")
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start):
        start = None
    return {"status": status, "id": stream_id, "title": _short(info.get("title")),
            "channel": _short(info.get("channel") or info.get("uploader")),
            "release_timestamp": start}


def run_probe_worker(spec: dict, emit) -> None:
    """Child side of the live check: emits {"live": live_fields(info)} or
    {"error": text}. Cookies only as the parent passes them (the saved
    Settings ones, for the PC owner's own follows)."""
    try:
        import yt_dlp
    except ImportError:
        emit({"error": "yt-dlp is not installed", "missing": True})
        return
    import video_download
    opts = dict(probe_options(), **video_download.cookie_options(
        spec.get("cookies_browser"), spec.get("cookies_file")))
    if spec.get("proxy"):
        opts["proxy"] = spec["proxy"]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(spec["url"], download=False)
    except Exception as exc:
        emit({"error": str(exc.__cause__ or exc)[:2000]})
        return
    emit({"live": live_fields(info)})


def run_worker(spec: dict, emit) -> None:
    """Child side. Emits {"progress": f}, {"rejected": true}, {"title": t},
    then {"path": p} or {"error": text}. The error text may hold the link or
    a path; the parent only pattern-matches it and never shows it."""
    import video_download

    last_emit = [float("-inf")]

    def hook(d):
        # yt-dlp calls the hook per chunk; the parent needs a few lines a second.
        now = time.monotonic()
        if d.get("status") == "downloading" and now - last_emit[0] >= PROGRESS_SECONDS:
            last_emit[0] = now
            got = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            emit({"progress": min(got / total, 1.0) if total else 0.0})

    def match_filter(info, *args, **kwargs):
        reason = refusal(info)
        if reason:
            emit({"rejected": True})
        return reason

    try:
        path = video_download.download(
            spec["url"], spec["tmp"], audio_only=spec["audio_only"],
            title_cb=lambda t: emit({"title": t}),
            extra_opts=ydl_options(spec["tmp"], hook, match_filter),
            cookies_browser=spec.get("cookies_browser"), cookies_file=spec.get("cookies_file"))
    except ImportError:
        emit({"error": "yt-dlp is not installed", "missing": True})
    except Exception as exc:
        # The chained yt-dlp message, not DownloadError's own text: that holds
        # generic advice ("region-locked", "private") which would match every
        # cause pattern in the parent.
        emit({"error": str(exc.__cause__ or exc)})
    else:
        emit({"path": path})


def main(argv=None) -> int:
    with open((argv or sys.argv)[1], encoding="utf-8") as f:
        spec = json.load(f)
    prefix = f"{_PREFIX}{spec['nonce']} "

    def emit_line(event: dict) -> None:
        # Leading newline: a half-written yt-dlp line must not swallow the prefix.
        sys.stdout.write("\n" + prefix + json.dumps(event) + "\n")
        sys.stdout.flush()

    (run_probe_worker if spec.get("mode") == "live_status" else run_worker)(spec, emit_line)
    return 0


_EVENT_KEYS = frozenset({"progress", "path", "title", "error", "missing", "rejected", "live"})


def _valid_event(event) -> bool:
    """Shape check on a nonce-authenticated event, so a malformed one is
    dropped instead of reaching code that would raise with its content."""
    # Unknown keys mean the line was not written by emit_line.
    if not isinstance(event, dict) or not event or not set(event) <= _EVENT_KEYS:
        return False
    progress, path = event.get("progress"), event.get("path")
    if "progress" in event and (isinstance(progress, bool)
                                or not isinstance(progress, (int, float))):
        return False
    if "live" in event and not isinstance(event["live"], dict):
        return False
    if "path" in event:
        # Only the media file video_download names; never the spec or a partial.
        if not isinstance(path, str):
            return False
        name = os.path.basename(path)
        if not name.startswith("downloaded_") or name.endswith(".part"):
            return False
    return True


def run_download(tmp_dir: str, spec: dict, timeout: float, cancel, max_output_chars=None):
    """Parent side. Yields {"event": {...}} per event the child
    emits, then {"returncode", "timed_out", "cancelled"}.
    Stopping the iteration early kills the child tree. Events that fail the
    nonce or shape check are dropped and counted in the final item's "dropped".
    `max_output_chars`: once the child has printed more than this, its tree
    is killed and the final item carries "too_much_output": True."""
    nonce = secrets.token_hex(16)
    prefix = f"{_PREFIX}{nonce} "
    dropped = 0
    printed = 0
    spec_path = os.path.join(tmp_dir, _SPEC_NAME)
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(dict(spec, tmp=tmp_dir, nonce=nonce), f)
    cmd = [sys.executable, "-m", "services.ytdlp_child", spec_path]
    stream = proc.stream_tree(cmd, timeout, cwd=_ROOT, cancel=cancel)
    try:
        for item in stream:
            line = item.get("line")
            if line is not None and max_output_chars is not None:
                printed += len(line) + 1
                if printed > max_output_chars:
                    yield {"returncode": None, "timed_out": False, "cancelled": False,
                           "too_much_output": True, "dropped": dropped}
                    return
            if line is None:
                yield dict(item, dropped=dropped)
            elif line.startswith(prefix):
                try:
                    event = json.loads(line[len(prefix):])
                except ValueError:
                    event = None
                if _valid_event(event):
                    yield {"event": event}
                else:
                    dropped += 1
    finally:
        stream.close()      # kills the tree now when the output cap ended the read
        # The spec holds the link and any cookie path; it must go on a cancel or
        # kill too, which end this generator through close() rather than falling off.
        try:
            os.remove(spec_path)
        except OSError:
            pass


def run_live_probe(tmp_dir: str, spec: dict, timeout: float, cancel, max_output_chars: int):
    """run_download for a live-status check (run_probe_worker in the child)."""
    return run_download(tmp_dir, dict(spec, mode="live_status"), timeout, cancel,
                        max_output_chars=max_output_chars)


if __name__ == "__main__":
    raise SystemExit(main())
