"""
services/media_peaks_service.py -- downsampled loudness peaks for a time window
of one drama's audio, for the Review waveform timeline. ffmpeg decodes just
that window to mono 8 kHz PCM; the peak of each bucket comes back as 0-255.
The file is located by media_playback_service (same containment rules as
playback); the path never reaches a result or an error message. No FastAPI
import.
"""
import array
import collections
import os
import shutil
import subprocess
import threading

from services import media_playback_service
from services.service_errors import DependencyUnavailableError, InvalidInputError, NotFoundError

MAX_WINDOW_SECONDS = 600.0
MIN_WINDOW_SECONDS = 0.5
MIN_BUCKETS = 16
MAX_BUCKETS = 2000
SAMPLE_RATE = 8000
DECODE_TIMEOUT_SECONDS = 30
CACHE_ENTRIES = 64
# Each decode holds up to MAX_WINDOW_SECONDS of PCM (~10 MB) in memory, so a
# burst of drags can't fan out into unbounded ffmpeg processes.
_MAX_CONCURRENT = 2

_slots = threading.BoundedSemaphore(_MAX_CONCURRENT)
_cache = collections.OrderedDict()
_cache_lock = threading.Lock()


def _check_window(start, end, buckets):
    for name, v in (("start", start), ("end", end)):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e7:
            raise InvalidInputError(f"{name} must be a number of seconds.")
    if isinstance(buckets, bool) or not isinstance(buckets, int):
        raise InvalidInputError("buckets must be a whole number.")
    if start < 0:
        raise InvalidInputError("start must not be negative.")
    if not MIN_WINDOW_SECONDS <= end - start <= MAX_WINDOW_SECONDS:
        raise InvalidInputError(
            f"The window must be {MIN_WINDOW_SECONDS:g} to {MAX_WINDOW_SECONDS:g} seconds long.")
    if not MIN_BUCKETS <= buckets <= MAX_BUCKETS:
        raise InvalidInputError(f"buckets must be {MIN_BUCKETS} to {MAX_BUCKETS}.")


def bucket_peaks(pcm: bytes, buckets: int) -> list:
    """Peak |sample| of each of `buckets` equal slices of s16le mono PCM,
    scaled to 0-255. Short or empty input gives zeros for the missing tail."""
    samples = array.array("h")
    samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
    if samples.itemsize != 2:  # pragma: no cover -- 'h' is 2 bytes on every supported platform
        raise RuntimeError("unexpected sample width")
    n = len(samples)
    out = []
    for i in range(buckets):
        lo, hi = n * i // buckets, n * (i + 1) // buckets
        peak = max((abs(s) for s in samples[lo:hi]), default=0)
        out.append(min(255, (peak * 255 + 16383) // 32767))
    return out


def _decode(path: str, start: float, end: float) -> bytes:
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", path,
           "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"]
    try:
        return subprocess.run(cmd, check=True, capture_output=True,
                              timeout=DECODE_TIMEOUT_SECONDS).stdout
    except subprocess.TimeoutExpired:
        raise DependencyUnavailableError("Reading the audio took too long.")
    except (subprocess.CalledProcessError, OSError):
        # str() of these carries the ffmpeg command line, i.e. absolute paths.
        raise DependencyUnavailableError("Couldn't read this audio for the waveform.")


def _locate(drama_id: int):
    try:
        return media_playback_service.resolve_media(drama_id, "audio")[0]
    except NotFoundError:
        # A video-only title still has sound to draw.
        return media_playback_service.resolve_media(drama_id, "video")[0]


def get_peaks(drama_id: int, start: float, end: float, buckets: int) -> dict:
    """{"start", "end", "buckets", "peaks": [0-255, ...]} for the window.
    NotFoundError without audio/video, InvalidInputError for a window or
    bucket count out of bounds, DependencyUnavailableError when ffmpeg is
    missing or fails. Results are cached per file version (mtime and size)."""
    _check_window(start, end, buckets)
    start, end = round(float(start), 3), round(float(end), 3)
    path = _locate(drama_id)
    if shutil.which("ffmpeg") is None:
        raise DependencyUnavailableError("ffmpeg is not installed or not on PATH.")
    try:
        st = os.stat(path)
    except OSError:
        raise NotFoundError("This drama has no audio file to draw.")
    key = (path, st.st_mtime_ns, st.st_size, start, end, buckets)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is None:
        if not _slots.acquire(timeout=DECODE_TIMEOUT_SECONDS):
            raise DependencyUnavailableError("The waveform is busy. Try again.")
        try:
            hit = bucket_peaks(_decode(path, start, end), buckets)
        finally:
            _slots.release()
        with _cache_lock:
            _cache[key] = hit
            while len(_cache) > CACHE_ENTRIES:
                _cache.popitem(last=False)
    return {"start": start, "end": end, "buckets": buckets, "peaks": list(hit)}
