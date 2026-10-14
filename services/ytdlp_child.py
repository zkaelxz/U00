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
# A confirmed "Download anyway" raises the cap for that one download only, never past this.
HARD_MAX_DURATION_SECONDS = 12 * 60 * 60
SOCKET_TIMEOUT = 30
PROGRESS_SECONDS = 0.5
_PREFIX = "@@ytdlp-"
_SPEC_NAME = "ytdlp_spec.json"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def clip_seconds(info, part):
    """Seconds that would be downloaded: the whole video, or the requested
    part of it (`part` is (start, end) with end None for "to the end"), else None
    when yt-dlp gave no length and the part has no end."""
    duration = (info or {}).get("duration")
    duration = duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
    if not part:
        return duration
    start, end = part
    if end is None and duration is None:
        return None
    stop = min(end, duration) if end is not None and duration is not None else (
        end if end is not None else duration)
    return max(stop - start, 0)


def estimate_bytes(info, audio_only, seconds):
    """A rough disk need for `seconds` of this video, or None when yt-dlp
    reported no bitrates. Audio-only keeps the decoded WAV (about 192 kB/s at
    48 kHz stereo) next to the stream it came from; a video is merged from two
    files, so the stream part counts twice, and its 16 kHz mono audio track is
    extracted beside it."""
    if not seconds:
        return None
    formats = [f for f in (info or {}).get("formats") or [] if isinstance(f, dict)]

    def best(rows, key):
        rates = [f.get(key) for f in rows if isinstance(f.get(key), (int, float))
                 and not isinstance(f.get(key), bool)]
        return max(rates, default=0)

    video = [f for f in formats if f.get("vcodec") not in (None, "none")]
    audio = [f for f in formats if f.get("vcodec") == "none"]
    audio_kbps = best(audio, "abr") or best(audio, "tbr")
    if audio_only:
        if not audio_kbps and not formats:
            return None
        return int(seconds * (192_000 + audio_kbps * 125))
    video_kbps = best(video, "tbr")
    if not video_kbps:
        return None
    return int(seconds * ((video_kbps + audio_kbps) * 125 * 2 + 32_000))


def refusal_detail(info, max_seconds=MAX_DURATION_SECONDS, part=None):
    """Why a link is not downloaded, as {"reason": code, ...}, else None.
    Codes: playlist, live, upcoming, too_long (with "duration", the seconds
    that would be downloaded, and "limit"), part_outside (with "duration", the
    video's own length). Checked in that order: a playlist or channel page
    says nothing about being live."""
    info = info or {}
    max_seconds = min(max_seconds, HARD_MAX_DURATION_SECONDS)
    if info.get("_type") in ("playlist", "multi_video"):
        return {"reason": "playlist"}
    if info.get("live_status") == "is_upcoming":
        return {"reason": "upcoming"}
    if info.get("is_live") or info.get("live_status") == "is_live":
        return {"reason": "live"}
    duration = info.get("duration")
    whole = duration if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
    if part and whole is not None and part[0] >= whole:
        return {"reason": "part_outside", "duration": int(whole)}
    seconds = clip_seconds(info, part)
    if seconds is not None and seconds > max_seconds:
        return {"reason": "too_long", "duration": int(seconds), "limit": int(max_seconds)}
    return None


def refusal(info, max_seconds=MAX_DURATION_SECONDS, part=None):
    """yt-dlp's match filter wants a string to reject with; the reason that
    reaches the user is built by the parent from refusal_detail."""
    return "Refused." if refusal_detail(info, max_seconds, part) else None


def ydl_options(tmp_dir: str, progress_hook, match_filter, part=None, exact_cuts=False,
                audio_only=True, proxy=None) -> dict:
    """The options merged over video_download's own (they win). Never any
    cookie option, and every default extractor except `generic`. `part`
    (start, end-or-None seconds) downloads that section only. `proxy`: every
    request yt-dlp makes goes through it, because yt-dlp resolves DNS itself
    after the parent's URL check and a rebinding answer would otherwise
    reach a private address."""
    opts = {
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
    if proxy:
        opts["proxy"] = proxy
    if part:
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func(
            None, [(part[0], float("inf") if part[1] is None else part[1])])
        # Without a re-encode a cut can only start on a keyframe, and only an
        # H.264 stream cuts cleanly into the mp4 the rest of the app reads.
        opts["force_keyframes_at_cuts"] = bool(exact_cuts)
        if not audio_only and not exact_cuts:
            opts["format"] = "bestvideo[vcodec^=avc1]+bestaudio/bestvideo+bestaudio/best"
    return opts


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


_UPLOAD_DATE_CHARS = frozenset("0123456789")
# Longer than any real video; a bigger number is a site bug, not a length.
_DURATION_MAX = 10 * 24 * 60 * 60


def link_fields(info) -> dict:
    """What "Fill from link" reads, shape-checked and capped: never the
    description, a format, a thumbnail or any other URL."""
    info = info if isinstance(info, dict) else {}
    fields = live_fields(info)
    fields.pop("id")
    upload = info.get("upload_date")
    if not (isinstance(upload, str) and len(upload) == 8 and set(upload) <= _UPLOAD_DATE_CHARS):
        upload = ""
    duration = info.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) \
            or not math.isfinite(duration) or not 0 <= duration <= _DURATION_MAX:
        duration = None
    fields.update(upload_date=upload, duration=duration,
                  has_thumbnail=bool(info.get("thumbnail") or info.get("thumbnails")),
                  is_playlist=info.get("_type") in ("playlist", "multi_video"))
    return fields


def run_probe_worker(spec: dict, emit) -> None:
    """Child side of a metadata-only read: emits {"live": live_fields(info)}
    (mode "live_status") or {"link": link_fields(info)} (mode "link_info"),
    or {"error": text}. Cookies only as the parent passes them (the saved
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
    if spec.get("mode") == "link_info":
        emit({"link": link_fields(info)})
    else:
        emit({"live": live_fields(info)})


def _part(spec):
    """(start, end-or-None) seconds from the spec, else None."""
    start, end = spec.get("part_start"), spec.get("part_end")
    if start is None and end is None:
        return None
    return (start or 0, end)


def run_worker(spec: dict, emit) -> None:
    """Child side. Emits {"progress": f}, {"rejected": refusal_detail},
    {"estimate": bytes, "seconds": n}, {"link": link_fields}, {"title": t},
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

    max_seconds = min(spec.get("max_seconds") or MAX_DURATION_SECONDS, HARD_MAX_DURATION_SECONDS)
    part = _part(spec)
    audio_only = spec["audio_only"]

    def match_filter(info, *args, **kwargs):
        detail = refusal_detail(info, max_seconds, part)
        if detail:
            if detail["reason"] == "too_long":
                detail["estimated_bytes"] = estimate_bytes(
                    info, audio_only, detail["duration"]) or 0
            emit({"rejected": detail})
            return "Refused."
        emit({"link": link_fields(info)})
        # The parent checks free disk against the estimate before any byte is
        # fetched, and lengthens its wall clock for a clip past the normal cap.
        if max_seconds > MAX_DURATION_SECONDS:
            seconds = clip_seconds(info, part)
            emit({"estimate": estimate_bytes(info, audio_only, seconds) or 0,
                  "seconds": int(seconds or 0)})
        return None

    try:
        path = video_download.download(
            spec["url"], spec["tmp"], audio_only=spec["audio_only"],
            title_cb=lambda t: emit({"title": t}),
            extra_opts=ydl_options(spec["tmp"], hook, match_filter, part=part,
                                   exact_cuts=bool(spec.get("exact_cuts")), audio_only=audio_only,
                                   proxy=spec.get("proxy")),
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

    probe = spec.get("mode") in ("live_status", "link_info")
    (run_probe_worker if probe else run_worker)(spec, emit_line)
    return 0


_EVENT_KEYS = frozenset({"progress", "path", "title", "error", "missing", "rejected", "live",
                         "estimate", "seconds", "link"})
_LINK_TEXT_KEYS = ("status", "title", "channel", "upload_date")
_LINK_KEYS = frozenset(_LINK_TEXT_KEYS + ("release_timestamp", "duration", "has_thumbnail",
                                          "is_playlist"))
_REFUSAL_REASONS = frozenset({"playlist", "live", "upcoming", "too_long", "part_outside"})
_REFUSAL_KEYS = frozenset({"reason", "duration", "limit", "estimated_bytes"})


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_link(link) -> bool:
    def number(value):
        return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)
                                 and math.isfinite(value))
    return (isinstance(link, dict) and set(link) == _LINK_KEYS
            and all(isinstance(link[k], str) and len(link[k]) <= _TEXT_MAX
                    for k in _LINK_TEXT_KEYS)
            and number(link["release_timestamp"]) and number(link["duration"])
            and isinstance(link["has_thumbnail"], bool) and isinstance(link["is_playlist"], bool))


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
    if "link" in event and not _valid_link(event["link"]):
        return False
    if any(k in event and not _count(event[k]) for k in ("estimate", "seconds")):
        return False
    if "rejected" in event:
        detail = event["rejected"]
        if not (isinstance(detail, dict) and detail.get("reason") in _REFUSAL_REASONS
                and set(detail) <= _REFUSAL_KEYS
                and all(_count(v) for k, v in detail.items() if k != "reason")):
            return False
    if "path" in event:
        # Only the media file video_download names; never the spec or a partial.
        if not isinstance(path, str):
            return False
        name = os.path.basename(path)
        if not name.startswith("downloaded_") or name.endswith(".part"):
            return False
    return True


def run_download(tmp_dir: str, spec: dict, timeout: float, cancel, max_output_chars=None,
                 on_start=None):
    """Parent side. Yields {"event": {...}} per event the child
    emits, then {"returncode", "timed_out", "cancelled"}.
    Stopping the iteration early kills the child tree. Events that fail the
    nonce or shape check are dropped and counted in the final item's "dropped".
    `max_output_chars`: once the child has printed more than this, its tree
    is killed and the final item carries "too_much_output": True.
    `on_start(pid)` runs once the child has started, and must not raise."""
    nonce = secrets.token_hex(16)
    prefix = f"{_PREFIX}{nonce} "
    dropped = 0
    printed = 0
    spec_path = os.path.join(tmp_dir, _SPEC_NAME)
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(dict(spec, tmp=tmp_dir, nonce=nonce), f)
    cmd = [sys.executable, "-m", "services.ytdlp_child", spec_path]
    stream = proc.stream_tree(cmd, timeout, cwd=_ROOT, cancel=cancel, on_start=on_start)
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


def run_live_probe(tmp_dir: str, spec: dict, timeout: float, cancel, max_output_chars: int,
                   mode: str = "live_status"):
    """run_download for a metadata-only read (run_probe_worker in the
    child); `mode` "link_info" for "Fill from link"."""
    return run_download(tmp_dir, dict(spec, mode=mode), timeout, cancel,
                        max_output_chars=max_output_chars)


if __name__ == "__main__":
    raise SystemExit(main())
