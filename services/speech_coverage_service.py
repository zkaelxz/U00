"""
services/speech_coverage_service.py -- "which stretches of the audio have
speech but no subtitle line?" for one title's stored audio. Read-only.

The speech regions come from the same Silero detector faster-whisper runs
before decoding, at a low threshold so quiet speech counts. They are
compared with the saved lines, and each gap is checked against the newest
raw transcript (written before splitting): text there means Whisper heard it
and it was lost afterwards (splitting, alignment, the repeat filter); no text
means Whisper never produced anything for that stretch.

Runs as a thread job without the GPU slot: Silero here is a CPU model, so it
may run while a GPU transcription of another title holds the GPU. It
decodes and scans the file in chunks so a multi-hour file never sits in
memory whole and progress and cancel keep moving. Writes nothing to the
lines or to disk; the result lives in the job record only.
"""
import os
import subprocess
from typing import Optional

import numpy as np

import background_jobs
import db
import raw_transcript
import vad_segments
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     UnsupportedOperationError)
from translate_engines import redact_secrets

# Low on purpose: the transcription default (0.5) is what may be dropping quiet speech.
VAD_THRESHOLD = 0.3
DEFAULT_MIN_GAP_SECONDS = 2.0
MIN_GAP_SECONDS_MIN = 0.5
MIN_GAP_SECONDS_MAX = 30.0
# A subtitle line's time range is a little tighter than its audio (word-timing
# trimming), so speech right beside a line is that line's, not a gap.
LINE_TOLERANCE_SECONDS = 0.3
# Leftover slivers beside a line, and speech pieces split by a short dip, are one stretch.
MIN_PIECE_SECONDS = 0.25
MERGE_PIECES_SECONDS = 1.0
# A raw segment has to overlap the gap by this much to count as "Whisper wrote something here".
RAW_OVERLAP_SECONDS = 0.2
RAW_TEXT_PREVIEW_CHARS = 80
MAX_GAPS = 500

_SAMPLE_RATE = 16000
_CHUNK_SECONDS = 600.0
_DECODE_TIMEOUT_SECONDS = 180
_PROBE_TIMEOUT_SECONDS = 30
_RUNNING_MAX = 0.99


def speech_coverage_job_id(drama_id: int) -> str:
    return f"speechcov_{drama_id}"


def _merge(spans, gap: float = 0.0) -> list:
    out = []
    for start, end in sorted(spans):
        if out and start - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _subtract(spans, cover) -> list:
    """The parts of `spans` outside `cover`; both sorted and non-overlapping."""
    out, j = [], 0
    for start, end in spans:
        cur = start
        while j < len(cover) and cover[j][1] <= cur:
            j += 1
        k = j
        while k < len(cover) and cover[k][0] < end:
            if cover[k][0] > cur:
                out.append((cur, cover[k][0]))
            cur = max(cur, cover[k][1])
            k += 1
        if cur < end:
            out.append((cur, end))
    return out


def _seconds(spans) -> float:
    return sum(end - start for start, end in spans)


def _raw_text_in(segments, start: float, end: float) -> str:
    texts = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        overlap = min(end, float(seg["end"])) - max(start, float(seg["start"]))
        if text and overlap > RAW_OVERLAP_SECONDS:
            texts.append(text)
    return " ".join(texts)


def find_gaps(speech_spans, lines, raw_segments, min_gap_seconds: float) -> dict:
    """Speech with no line over it. `lines`: Line objects (only the ones with
    text count as covering). `raw_segments`: the newest raw transcript's
    segments, or None when there is none. Returns the report dict the job
    stores (everything but audio_seconds)."""
    speech = _merge(speech_spans)
    covering = _merge(((max(0.0, ln.start - LINE_TOLERANCE_SECONDS), ln.end + LINE_TOLERANCE_SECONDS)
                       for ln in lines if (ln.zh or "").strip()))
    uncovered = [p for p in _subtract(speech, covering) if p[1] - p[0] >= MIN_PIECE_SECONDS]
    speech_total = _seconds(speech)
    covered_total = max(0.0, speech_total - _seconds(_subtract(speech, covering)))

    gaps = []
    group = []
    for piece in uncovered + [None]:
        if piece is not None and group and piece[0] - group[-1][1] <= MERGE_PIECES_SECONDS:
            group.append(piece)
            continue
        if group:
            start, end = group[0][0], group[-1][1]
            if end - start >= min_gap_seconds:
                gaps.append((start, end, _seconds(group)))
        group = [piece] if piece is not None else []

    ordered = sorted((ln for ln in lines if (ln.zh or "").strip()), key=lambda ln: ln.start)
    rows = []
    for start, end, speech_seconds in gaps:
        before = [ln for ln in ordered if ln.end <= start + LINE_TOLERANCE_SECONDS]
        after = [ln for ln in ordered if ln.start >= end - LINE_TOLERANCE_SECONDS]
        if raw_segments is None:
            raw_status, raw_text = "unknown", ""
        else:
            raw_text = _raw_text_in(raw_segments, start, end)
            raw_status = "lost_after" if raw_text else "none"
        rows.append({
            "start": round(start, 2), "end": round(end, 2), "seconds": round(end - start, 2),
            "speech_seconds": round(speech_seconds, 2),
            "raw_status": raw_status, "raw_text": raw_text[:RAW_TEXT_PREVIEW_CHARS],
            "after_line_id": before[-1].id if before else None,
            "before_line_id": after[0].id if after else None,
        })
    return {
        "speech_seconds": round(speech_total, 1), "covered_seconds": round(covered_total, 1),
        "covered_percent": round(100.0 * covered_total / speech_total, 1) if speech_total else None,
        "gaps_total": len(rows), "gaps": rows[:MAX_GAPS],
        "raw_available": raw_segments is not None,
    }


def _audio_seconds(path) -> Optional[float]:
    try:
        import media_inspect
        info = media_inspect.run_ffprobe(path, timeout=_PROBE_TIMEOUT_SECONDS)
        return float((info.get("format") or {}).get("duration") or 0.0) or None
    except Exception:
        return None


def _decode_chunk(path: str, start: float, seconds: float):
    """16 kHz mono float samples of [start, start + seconds) via ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds), "-i", path,
           "-f", "f32le", "-ac", "1", "-ar", str(_SAMPLE_RATE), "-"]
    out = subprocess.run(cmd, check=True, capture_output=True, timeout=_DECODE_TIMEOUT_SECONDS)
    return np.frombuffer(out.stdout, dtype=np.float32)


def _detect_speech(chunk) -> list:
    return [tuple(s) for s in vad_segments.speech_spans(
        chunk, _SAMPLE_RATE, threshold=VAD_THRESHOLD, pad_ms=0)]


def _scan_speech(job_id: str, path: str, total_seconds: float) -> list:
    spans = []
    start = 0.0
    while start < total_seconds:
        if background_jobs.is_cancel_requested(job_id):
            raise background_jobs.JobCancelled(job_id)
        background_jobs.update_progress(
            job_id, min(start / total_seconds, 1.0) * _RUNNING_MAX,
            f"Finding speech... {min(start / total_seconds, 1.0) * 100:.0f}%")
        chunk = _decode_chunk(path, start, _CHUNK_SECONDS)
        spans.extend((start + s, start + e) for s, e in _detect_speech(chunk))
        start += _CHUNK_SECONDS
    # A span cut at a chunk edge arrives as two touching pieces.
    return _merge(spans, gap=0.05)


def _run_job(job_id: str, drama_id: int, audio_path: str, min_gap_seconds: float):
    total = _audio_seconds(audio_path)
    if not total:
        background_jobs.set_result(job_id, {"failed_reason": "unreadable"})
        return
    try:
        spans = _scan_speech(job_id, audio_path, total)
    except background_jobs.JobCancelled:
        raise
    except ImportError:
        # The job runner turns this reason into an error status with this sentence.
        background_jobs.set_result(job_id, {
            "failed_reason": "dependency_missing",
            "detail": "The speech coverage check needs faster-whisper, which isn't installed."})
        return
    except (subprocess.SubprocessError, OSError) as exc:
        background_jobs.set_result(job_id, {"failed_reason": "decode",
                                            "detail": redact_secrets(str(exc))[:200]})
        return
    raw = raw_transcript.load_latest(db.drama_dir(drama_id))
    segments = raw.get("segments") or [] if raw else None
    report = find_gaps(spans, db.load_line_objects(drama_id), segments, min_gap_seconds)
    report.update({"audio_seconds": round(total, 1), "vad_threshold": VAD_THRESHOLD,
                   "min_gap_seconds": min_gap_seconds})
    background_jobs.set_result(job_id, report)


def start_speech_coverage(drama_id: int, min_gap_seconds: float = DEFAULT_MIN_GAP_SECONDS) -> dict:
    """Starts the check over this title's stored audio. NotFoundError,
    UnsupportedOperationError (no stored audio), InvalidInputError (gap
    length out of range), ConflictError (a transcription of this title is
    running, so its lines are about to change, or a check is already running)."""
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    if (isinstance(min_gap_seconds, bool) or not isinstance(min_gap_seconds, (int, float))
            or not MIN_GAP_SECONDS_MIN <= min_gap_seconds <= MIN_GAP_SECONDS_MAX):
        raise InvalidInputError(
            f"min_gap_seconds must be between {MIN_GAP_SECONDS_MIN:g} and {MIN_GAP_SECONDS_MAX:g}.")
    audio_filename = drama.get("audio_filename")
    audio_path = os.path.join(db.drama_dir(drama_id), audio_filename) if audio_filename else None
    if not audio_path or not os.path.exists(audio_path):
        raise UnsupportedOperationError(f"No audio available for title {drama_id}.")
    if background_jobs.is_running(f"transcribe_{drama_id}"):
        raise ConflictError("A transcription is running for this title. "
                            "Check the coverage when it finishes.")
    job_id = speech_coverage_job_id(drama_id)
    if not background_jobs.start_job(
            job_id, _run_job, job_id, drama_id, audio_path, float(min_gap_seconds),
            description=f"Speech coverage check (title #{drama_id})"):
        raise ConflictError("A coverage check is already running for this title.")
    return {"job_id": job_id}


def get_speech_coverage(drama_id: int) -> dict:
    """{job_id, status, progress, message, result}; status "idle" (job_id "")
    when no check is held in this app session. result is the report only when
    the job is done; a failed run is {"failed_reason": ...}."""
    if db.get_drama(drama_id) is None:
        raise NotFoundError(f"No title with id {drama_id}.")
    job_id = speech_coverage_job_id(drama_id)
    job = background_jobs.get_status(job_id)
    if not job:
        return {"job_id": "", "status": "idle", "progress": 0.0, "message": "", "result": None}
    status = job.get("status")
    result = job.get("result") if status == "done" and isinstance(job.get("result"), dict) else None
    message = job.get("error") if status == "error" else job.get("message")
    return {"job_id": job_id, "status": status, "progress": job.get("progress"),
            "message": redact_secrets(str(message)) if message else "", "result": result}
