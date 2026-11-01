"""Shot cuts (scene changes) in a time window of one drama's video, for snapping line edges to them."""
import collections
import os
import re
import shutil
import threading

import video_export
from lib import proc
from services import media_playback_service
from lib.errors import (
    DependencyUnavailableError, InvalidInputError, NotFoundError, RateLimitedError)

MAX_WINDOW_SECONDS = 120.0
MIN_WINDOW_SECONDS = 0.5
# ffmpeg's scene score runs 0-1; 0.4 is its documented starting point for hard cuts.
SCENE_THRESHOLD = 0.4
DECODE_TIMEOUT_SECONDS = 60
CACHE_ENTRIES = 64
MAX_CUTS = 2000
BUSY = "Shot detection is busy. Try again in a moment."

# A video decode is far heavier than the waveform's audio decode, so one at a time server-wide.
_slots = threading.BoundedSemaphore(1)
_cache = collections.OrderedDict()
_cache_lock = threading.Lock()
_PTS = re.compile(r"pts_time:(\d+(?:\.\d+)?)")


def _check_window(start, end):
    for name, v in (("start", start), ("end", end)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e7:
            raise InvalidInputError(f"{name} must be a number of seconds.")
    if start < 0:
        raise InvalidInputError("start must not be negative.")
    if not MIN_WINDOW_SECONDS <= round(end - start, 3) <= MAX_WINDOW_SECONDS:
        raise InvalidInputError(
            f"The window must be {MIN_WINDOW_SECONDS:g} to {MAX_WINDOW_SECONDS:g} seconds long.")


def parse_cuts(stderr: str, start: float, end: float) -> list:
    """Cut times from showinfo output. The -ss before -i restarts timestamps at 0, so the
    window start is added back; a cut in the first frame is the window opening, not a cut."""
    cuts = sorted({round(start + float(m), 3) for m in _PTS.findall(stderr or "")})
    return [t for t in cuts if start < t <= end][:MAX_CUTS]


def _detect(path: str, start: float, end: float) -> list:
    span = f"{end - start:.3f}"
    cmd = ["ffmpeg", "-v", "info", "-nostats", *video_export.local_input(), "-ss", f"{start:.3f}",
           "-t", span, "-i", path, "-an", "-sn",
           "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo", "-f", "null", "-"]
    try:
        run = proc.run_captured(cmd, DECODE_TIMEOUT_SECONDS)
    except OSError:
        raise DependencyUnavailableError("Couldn't read this video for shot changes.")
    if run.timed_out:
        raise DependencyUnavailableError("Looking for shot changes took too long.")
    if run.returncode != 0:
        raise DependencyUnavailableError("Couldn't read this video for shot changes.")
    return parse_cuts(run.stderr, start, end)


def get_shot_changes(drama_id: int, start: float, end: float) -> dict:
    """{"start", "end", "cuts": [seconds, ...]} for the window. NotFoundError without a video,
    InvalidInputError for a bad window, RateLimitedError while another detection runs,
    DependencyUnavailableError when ffmpeg is missing or fails. Cached per file version."""
    _check_window(start, end)
    start, end = round(float(start), 3), round(float(end), 3)
    path = media_playback_service.resolve_media(drama_id, "video")[0]
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError("ffmpeg is not installed or not on PATH.")
    try:
        st = os.stat(path)
    except OSError:
        raise NotFoundError("This title has no video file.")
    key = (path, st.st_mtime_ns, st.st_size, start, end)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is None:
        if not _slots.acquire(blocking=False):
            raise RateLimitedError(BUSY)
        try:
            hit = _detect(path, start, end)
        finally:
            _slots.release()
        with _cache_lock:
            _cache[key] = hit
            while len(_cache) > CACHE_ENTRIES:
                _cache.popitem(last=False)
    return {"start": start, "end": end, "cuts": list(hit)}
