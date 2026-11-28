"""Which ear (left or right) each line of a stereo title comes from: worked out from the original file's two channels, cached in ear_marks.json beside audio_tags.json."""

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time

import numpy as np

import applog
import background_jobs
import db
import media_inspect
import storage
import video_export
from engine_backends.shared import redact_secrets
from jobs import job_launch
from lib.atomic_write import atomic_write
from lib.errors import NotFoundError, UnsupportedOperationError
from services import drama_service, media_playback_service, media_upload_service, ownership_service

EAR_MARKS_FILE = "ear_marks.json"
JOB_PREFIX = "earmarks_"   # in background_jobs.SIDE_JOB_PREFIXES
# Bumped when the measure changes, so every cached answer is worked out again.
ALGORITHM_VERSION = 1

# Whispered speech carries most of its energy between 2 and 4 kHz, so 8 kHz keeps it, at
# 32 KB a second of 16-bit stereo. One window of it is on disk at a time (about 19 MB).
SAMPLE_RATE = 8000
BLOCK_SAMPLES = 160                 # 20 ms; line edges are timed no finer than that
READ_BLOCKS = 3000                  # one minute of sound per read
BYTES_PER_FRAME = 4                 # s16le, two channels
WINDOW_SECONDS = 600.0              # a whole number of blocks
WINDOW_TIMEOUT_SECONDS = 600        # only stops a hung ffmpeg
# The file is read until a window comes back short (by more than a second), at most this far.
# Input-side -ss keeps windows in step with one pass even on VBR MP3 with no seek table
# (measured: within one 20 ms block over 35-62 minute files), so only one window is on disk.
MAX_SECONDS = 48 * 3600
END_SLACK_BLOCKS = 50
# After a run that could not finish (a timeout, an error), Watch's own start waits this long
# before trying the same file and timings again, so a broken file doesn't fail on every visit.
RETRY_AFTER_SECONDS = 6 * 3600

# 6 dB is twice the amplitude on one side: a voice placed in one ear. A centred binaural
# voice drifts by 1-3 dB with the dummy head's room and the actor's movement, which this
# leaves unmarked.
SIDE_DB = 6.0
# Left minus right this far under left plus right means the two channels carry one signal
# (mono saved as stereo, or a joint-stereo encode of it): no line is truly one-sided.
FAKE_STEREO_SIDE_DB = -30.0
# Quieter than this (-60 dBFS) a line is room tone; its balance says nothing.
MIN_LINE_RMS = 1e-3

SIDES = ("left", "right")

_failed_at = {}   # drama id -> (stamp, time.monotonic()) of its last run that could not finish
_failed_lock = threading.Lock()


def _media_path(drama_id: int):
    # A video title's audio file is the 16 kHz mono extract made for transcription, so the
    # video is the file that still has both channels.
    for kind in ("video", "audio"):
        try:
            return media_playback_service.resolve_media(drama_id, kind)[0]
        except NotFoundError:
            continue
    return None


def _timed(lines) -> list:
    return [(ln.id, float(ln.start), float(ln.end)) for ln in lines
            if getattr(ln, "id", None) is not None and ln.end > ln.start]


def _stamp(path: str, timed) -> str:
    st = os.stat(path)
    key = [ALGORITHM_VERSION, os.path.basename(path), st.st_mtime_ns, st.st_size,
           [[i, round(s, 3), round(e, 3)] for i, s, e in sorted(timed)]]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()


def load_sidecar(drama_dir: str):
    try:
        with open(os.path.join(drama_dir, EAR_MARKS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _fresh(drama_id: int, lines=None):
    """(sidecar, timed lines, media path, stamp): the sidecar only when it was made
    from the title's current file and line timings, else None."""
    path = _media_path(drama_id)
    if path is None:
        return None, [], None, None
    timed = _timed(db.load_line_objects(drama_id) if lines is None else lines)
    try:
        stamp = _stamp(path, timed)
    except OSError:
        return None, timed, None, None
    data = load_sidecar(db.drama_dir(drama_id))
    if not data or data.get("stamp") != stamp:
        data = None
    return data, timed, path, stamp


def _sides(data) -> dict:
    marks = (data or {}).get("marks")
    if not isinstance(marks, dict):
        return {}
    return {int(k): v for k, v in marks.items() if str(k).isdigit() and v in SIDES}


def fresh_sides(drama_id: int, lines=None) -> dict:
    """{line_id: "left" | "right"} from an up-to-date sidecar, else {}."""
    return _sides(_fresh(drama_id, lines)[0])


def _job_id(drama_id: int) -> str:
    return f"{JOB_PREFIX}{drama_id}"


def get_ear_marks(drama_id: int) -> dict:
    """The title's ear marks by line id. state: "ready" (the marks are current, maybe
    none), "working" (being worked out), "needed" (none yet, or the lines' timing or
    the file changed) or "unavailable" (no sound file or no timed lines)."""
    ownership_service.require_drama(drama_id)
    data, timed, path, _ = _fresh(drama_id)
    out = {"drama_id": drama_id, "marks": [], "one_sided": 0, "total_lines": len(timed)}
    if path is None or not timed:
        return {**out, "state": "unavailable"}
    if background_jobs.is_running(_job_id(drama_id)):
        return {**out, "state": "working"}
    if data is None:
        return {**out, "state": "needed"}
    sides = _sides(data)
    known = {i for i, _, _ in timed}
    marks = [{"line_id": i, "side": s} for i, s in sorted(sides.items()) if i in known]
    return {**out, "state": "ready", "marks": marks, "one_sided": len(marks)}


def _failed_lately(drama_id: int, stamp) -> bool:
    with _failed_lock:
        seen = _failed_at.get(drama_id)
    return bool(seen and seen[0] == stamp and time.monotonic() - seen[1] < RETRY_AFTER_SECONDS)


def _note_failure(drama_id: int, stamp: str) -> None:
    with _failed_lock:
        _failed_at[drama_id] = (stamp, time.monotonic())


def start_ear_marks(drama_id: int) -> dict:
    """Starts working out the marks unless they are current, already being worked out,
    or the same file and timings failed lately."""
    ownership_service.require_drama(drama_id)
    data, timed, path, stamp = _fresh(drama_id)
    if path is None or not timed:
        raise UnsupportedOperationError("This title has no sound file and timed lines to place.")
    job_id = _job_id(drama_id)
    # Without ffmpeg the marks wait, unasked for, until it is installed. While another job or an
    # upload may be replacing the file they wait for a later visit: reading it then would keep
    # it open (on Windows the replace fails) and measure a file about to go.
    if (data is not None or not _tools_present() or _failed_lately(drama_id, stamp)
            or drama_service.job_running_for_drama(drama_id)
            or media_upload_service.upload_in_progress(drama_id)):
        return {"job_id": job_id, "drama_id": drama_id, "started": False}
    started = job_launch.start_job(
        job_id, _run_job, job_id, drama_id, path, timed, stamp, db.drama_dir(drama_id),
        description=f"Ear marks (title #{drama_id})", line_fields=())
    return {"job_id": job_id, "drama_id": drama_id, "started": bool(started)}


def _tools_present() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe(path: str):
    """(has a track of two or more channels, length in seconds or 0 when unknown)."""
    found = media_inspect.analysis_from_probe(media_inspect.run_ffprobe(path), os.path.basename(path))
    return any((t.channels or 0) >= 2 for t in found.audio_tracks), found.duration_seconds


def _decode_window(job_id: str, path: str, start: float, out_path: str) -> None:
    # Downmixed to two channels, so 5.1 keeps its left and right; ffmpeg picks the audio
    # stream with the most channels.
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{WINDOW_SECONDS:.3f}",
           *video_export.local_input(), "-i", path, "-vn",
           "-ac", "2", "-ar", str(SAMPLE_RATE), "-f", "s16le", out_path]
    background_jobs.run_cancellable(job_id, cmd, timeout=WINDOW_TIMEOUT_SECONDS)


def block_energies(stream):
    """(left, right) sum of squares per BLOCK_SAMPLES block, and the whole stream's
    (side, mid) energy, from s16le stereo PCM read in chunks."""
    left, right = [], []
    side = mid = 0.0
    while True:
        raw = stream.read(READ_BLOCKS * BLOCK_SAMPLES * BYTES_PER_FRAME)
        if not raw:
            break
        frames = np.frombuffer(raw[:len(raw) - len(raw) % BYTES_PER_FRAME], dtype="<i2")
        frames = frames.reshape(-1, 2).astype(np.float64) / 32768.0
        pad = -len(frames) % BLOCK_SAMPLES
        if pad:
            frames = np.vstack([frames, np.zeros((pad, 2))])
        blocks = (frames ** 2).reshape(-1, BLOCK_SAMPLES, 2).sum(axis=1)
        left.append(blocks[:, 0])
        right.append(blocks[:, 1])
        side += float(((frames[:, 0] - frames[:, 1]) ** 2).sum())
        mid += float(((frames[:, 0] + frames[:, 1]) ** 2).sum())
    empty = np.zeros(0)
    return (np.concatenate(left) if left else empty, np.concatenate(right) if right else empty,
            side, mid)


def _db(a: float, b: float) -> float:
    return 10.0 * np.log10(max(a, 1e-20) / max(b, 1e-20))


def classify(left, right, side: float, mid: float, timed) -> dict:
    """{"stereo", "side_db", "marks": {line_id: side}} from block_energies' output."""
    side_db = round(float(_db(side, mid)), 1) if mid > 0 else None
    if side_db is None or side_db < FAKE_STEREO_SIDE_DB:
        return {"stereo": False, "side_db": side_db, "marks": {}}
    cum_l = np.concatenate([[0.0], np.cumsum(left)])
    cum_r = np.concatenate([[0.0], np.cumsum(right)])
    n = len(left)
    block_seconds = BLOCK_SAMPLES / SAMPLE_RATE
    marks = {}
    for line_id, start, end in timed:
        b0 = min(n, max(0, int(start / block_seconds)))
        b1 = min(n, max(b0, int(np.ceil(end / block_seconds))))
        if b1 <= b0:
            continue
        e_l, e_r = cum_l[b1] - cum_l[b0], cum_r[b1] - cum_r[b0]
        if np.sqrt(max(e_l, e_r) / ((b1 - b0) * BLOCK_SAMPLES)) < MIN_LINE_RMS:
            continue
        balance = _db(e_l, e_r)
        if balance >= SIDE_DB:
            marks[line_id] = "left"
        elif balance <= -SIDE_DB:
            marks[line_id] = "right"
    return {"stereo": True, "side_db": side_db, "marks": marks}


def _fit(blocks, n: int):
    # Every window but the last spans exactly n blocks, so the next one's blocks keep their own time.
    return blocks[:n] if len(blocks) >= n else np.concatenate([blocks, np.zeros(n - len(blocks))])


def measure(job_id: str, path: str, timed) -> dict:
    """classify()'s answer for the file at `path`, decoded one window at a time."""
    stereo, seconds = _probe(path)
    if not stereo:
        return {"stereo": False, "side_db": None, "marks": {}}
    window_blocks = round(WINDOW_SECONDS * SAMPLE_RATE / BLOCK_SAMPLES)
    left, right, side, mid = [], [], 0.0, 0.0
    start = 0.0
    with storage.job_workdir(job_id) as work:
        pcm = os.path.join(work, "window.pcm")
        while start < MAX_SECONDS:
            if background_jobs.is_cancel_requested(job_id):
                raise background_jobs.JobCancelled(job_id)
            if seconds:
                done = min(start / seconds, 1.0)
                background_jobs.update_progress(job_id, 0.95 * done,
                                                f"Measuring left and right... {100 * done:.0f}%")
            _decode_window(job_id, path, start, pcm)
            with open(pcm, "rb") as f:
                l_blocks, r_blocks, s_energy, m_energy = block_energies(f)
            got = len(l_blocks)
            left.append(_fit(l_blocks, window_blocks))
            right.append(_fit(r_blocks, window_blocks))
            side, mid = side + s_energy, mid + m_energy
            # The end is where the sound runs out, never ffprobe's length: an MP3 with no
            # Xing header gets a length guessed from its first frames (35 minutes probed
            # as 27, an hour as 36). A window a frame or two short is still a middle one.
            if got == 0 or got < window_blocks - min(END_SLACK_BLOCKS, window_blocks // 10):
                break
            start += WINDOW_SECONDS
    empty = np.zeros(0)
    return classify(np.concatenate(left) if left else empty, np.concatenate(right) if right else empty,
                    side, mid, timed)


def _run_job(job_id, drama_id, path, timed, stamp, drama_dir):
    log = applog.get_logger()
    try:
        found = measure(job_id, path, timed)
    except background_jobs.JobCancelled:
        raise
    except media_inspect.ProbeError as exc:
        # The texts name paths, so only the log has them.
        log.warning(f"ear marks failed for title #{drama_id}: {redact_secrets(str(exc))[:200]}")
        if exc.reason != "unreadable":
            _note_failure(drama_id, stamp)
            raise RuntimeError("The sound could not be checked for ear marks; Watch tries again later.") from None
        # A file ffprobe can't read gets no marks until the file or the timings change, rather
        # than a failed job on every Watch visit.
        found = {"stereo": False, "side_db": None, "marks": {}, "failed": True}
    except (subprocess.SubprocessError, OSError) as exc:
        # Maybe passing (a full disk also makes ffmpeg fail, a slow one time out): nothing
        # is stored, and Watch tries again after RETRY_AFTER_SECONDS.
        _note_failure(drama_id, stamp)
        log.warning(f"ear marks stopped for title #{drama_id}: {redact_secrets(str(exc))[:200]}")
        raise RuntimeError("Reading the sound for ear marks failed or took too long; "
                           "Watch tries again later.") from None
    except BaseException:
        _note_failure(drama_id, stamp)
        raise
    marks = found["marks"]
    left = sum(1 for s in marks.values() if s == "left")
    sidecar = {"version": ALGORITHM_VERSION, "stamp": stamp, "stereo": found["stereo"],
               "failed": bool(found.get("failed")),
               "side_db": found["side_db"], "total_lines": len(timed), "one_sided": len(marks),
               "left": left, "right": len(marks) - left,
               "marks": {str(k): v for k, v in sorted(marks.items())}}
    atomic_write(os.path.join(drama_dir, EAR_MARKS_FILE), json.dumps(sidecar, indent=1))
    # Kept in the log too: whether real titles have many one-sided lines decides if
    # stereo-aware transcription is worth testing.
    log.info(f"ear marks for title #{drama_id}: {len(marks)} of {len(timed)} lines one-sided "
             f"(left {left}, right {len(marks) - left}), stereo={found['stereo']}, "
             f"side/mid {found['side_db']} dB")
    background_jobs.set_result(job_id, {"one_sided": len(marks), "total_lines": len(timed)})
