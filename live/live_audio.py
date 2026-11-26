"""WAV handling for Live chunks: the overlap tail carried into the next chunk, and
the cap that keeps one Whisper call from being handed more audio than a chunk is
meant to hold."""
import contextlib
import os
import wave
from typing import NamedTuple, Optional


@contextlib.contextmanager
def _open_wav(path: str):
    """(reader, frames the file really holds). A chunk ffmpeg was killed while
    writing keeps its placeholder header (a data size of 4 GiB, read as 37 h of
    audio), so the header's count is capped by the bytes after it."""
    with open(path, "rb") as fh, wave.open(fh, "rb") as w:
        # wave.open leaves the file at the start of the sample data.
        frame_bytes = w.getsampwidth() * w.getnchannels()
        held = (os.path.getsize(path) - fh.tell()) // frame_bytes if frame_bytes else 0
        yield w, max(0, min(w.getnframes(), held))


def read_wav_tail(path: str, seconds: float):
    """
    The last `seconds` of a chunk's own PCM audio, kept in memory so it
    can be prepended to the NEXT chunk after this one's file is deleted.
    Returns {"params": (nchannels, sampwidth, framerate), "frames": bytes,
    "seconds": tail length actually read, "chunk_seconds": the whole
    chunk's length}, or None if the file can't be read as a WAV -- the
    caller then just skips the overlap for the next chunk rather than
    failing the job.
    """
    try:
        with _open_wav(path) as (w, nframes):
            nchannels, sampwidth, framerate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            tail_frames = min(nframes, int(round(seconds * framerate)))
            w.setpos(nframes - tail_frames)
            frames = w.readframes(tail_frames)
    except (OSError, EOFError, wave.Error):
        return None
    return {"params": (nchannels, sampwidth, framerate), "frames": frames,
            "seconds": tail_frames / framerate if framerate else 0.0,
            "chunk_seconds": nframes / framerate if framerate else 0.0}


def write_padded_chunk(tail: dict, chunk_path: str, out_path: str) -> float:
    """
    Writes `tail`'s audio (read_wav_tail() of the previous chunk)
    followed by `chunk_path`'s own audio into `out_path`, and returns how
    many seconds of padding were prepended -- the offset process_chunk
    needs to put this chunk's timestamps back on the stream's timeline.
    Returns 0.0 (and writes nothing) if the chunk can't be read or its
    format doesn't match the tail's, so the chunk is transcribed
    unpadded instead.
    """
    if not tail or not tail["frames"]:
        return 0.0
    try:
        with _open_wav(chunk_path) as (w, nframes):
            params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames = w.readframes(nframes)
    except (OSError, EOFError, wave.Error):
        return 0.0
    if params != tail["params"]:
        return 0.0
    with wave.open(out_path, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        out.writeframes(tail["frames"] + frames)
    return tail["seconds"]


# ffmpeg cuts at the next packet boundary after segment_time, so a chunk runs
# a little over; anything beyond this is a different problem (a timestamp jump
# in the stream) and gets trimmed rather than transcribed whole.
OVERSIZE_SLACK_SECONDS = 2.0


def write_newest(path: str, out_path: str, seconds: float):
    """Writes the last `seconds` of `path` to `out_path`; returns how many
    seconds were left out of the front. 0.0 (and nothing written) when the
    file is not longer than that or can't be read as a WAV."""
    try:
        with _open_wav(path) as (w, nframes):
            params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            keep = int(round(seconds * params[2]))
            if keep <= 0 or nframes <= keep:
                return 0.0
            w.setpos(nframes - keep)
            frames = w.readframes(keep)
    except (OSError, EOFError, wave.Error):
        return 0.0
    with wave.open(out_path, "wb") as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        out.writeframes(frames)
    return (nframes - keep) / params[2]


class ChunkWindow(NamedTuple):
    """tail: this chunk's own tail for the next chunk (None without overlap or
    when unreadable). path: the audio to transcribe. pad_seconds: previous tail
    prepended to it. tail_text: what the previous chunk emitted for that tail.
    trimmed_seconds: start of an over-long chunk that was dropped, which the
    caller moves its timestamps on by. audio_seconds: length of `path`
    (0.0 when unreadable). scratch: temporary files to delete afterwards."""
    tail: Optional[dict]
    path: str
    pad_seconds: float
    tail_text: str
    trimmed_seconds: float
    audio_seconds: float
    scratch: list
    chunk_seconds: float = 0.0


def wav_seconds(path: str) -> Optional[float]:
    """Length of a WAV file in seconds, or None when it can't be read as one."""
    try:
        with _open_wav(path) as (w, nframes):
            return nframes / w.getframerate()
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None


# Cuts land within one audio packet (tens of ms) of their slot, so a chunk this
# much short of its slot only comes from a forward timestamp jump.
SHORTFALL_SLACK_SECONDS = 0.5


# The first chunk has no earlier audio to overlap, so it closes sooner to put
# the first subtitles on screen earlier; Whisper hears less context there.
# Chinese only: Japanese measured no faster to the first cue and 5.7 points
# worse on the first minute (the early cut moved every later cut).
FIRST_CHUNK_LANGUAGES = frozenset({"zh"})
FIRST_CHUNK_FRACTION = 0.5
MIN_FIRST_CHUNK_SECONDS = 2.0


def first_chunk_lead_in(segment_seconds: float, language: str = "zh") -> float:
    """Seconds of silence to put before the audio so the first chunk closes
    after max(MIN_FIRST_CHUNK_SECONDS, half a segment) of real audio instead of
    a whole segment. 0.0 for languages that did not gain, or when a segment is
    too short to gain a second."""
    if language not in FIRST_CHUNK_LANGUAGES:
        return 0.0
    first = max(MIN_FIRST_CHUNK_SECONDS, segment_seconds * FIRST_CHUNK_FRACTION)
    lead = segment_seconds - first
    return lead if lead >= 1.0 else 0.0


def jump_seconds(chunk_seconds: float, segment_seconds: float) -> float:
    """How far a chunk moves every later chunk off its index's slot in real
    time. ffmpeg cuts at fixed points on the stream's clock, so after a
    backward timestamp jump one chunk runs on until the clock reaches its cut
    again (positive), and after a small forward jump the chunks around it hold
    less audio than their slots (negative; ffmpeg itself smooths jumps over
    10 s in MPEG-TS). An ordinary overrun (under OVERSIZE_SLACK_SECONDS) is
    taken back by the next cut, so it moves nothing. A length of 0 or less is
    an unreadable chunk: nothing is known about it."""
    if chunk_seconds <= 0:
        return 0.0
    if chunk_seconds > segment_seconds + OVERSIZE_SLACK_SECONDS:
        return chunk_seconds - segment_seconds
    if chunk_seconds < segment_seconds - SHORTFALL_SLACK_SECONDS:
        return chunk_seconds - segment_seconds
    return 0.0


def window_for_chunk(path: str, idx: int, out_dir: str, segment_seconds: float,
                     prev_tail, tail_seconds: float) -> ChunkWindow:
    """The audio one Whisper call gets for chunk `idx`: at most
    segment_seconds + overlap, however long the file on disk is."""
    tail = read_wav_tail(path, tail_seconds) if tail_seconds else None
    scratch, source, trimmed = [], path, 0.0
    length = tail["chunk_seconds"] if tail else (wav_seconds(path) or 0.0)
    chunk_seconds = length
    if length > segment_seconds + OVERSIZE_SLACK_SECONDS:
        newest = os.path.join(out_dir, f"padded_{idx:05d}_newest.wav")
        scratch.append(newest)
        trimmed = write_newest(path, newest, segment_seconds)
        if trimmed:
            source, length = newest, length - trimmed
    pad, text = 0.0, ""
    if prev_tail and prev_tail["index"] == idx - 1 and not trimmed:
        padded = os.path.join(out_dir, f"padded_{idx:05d}.wav")
        scratch.append(padded)
        pad = write_padded_chunk(prev_tail, source, padded)
        if pad:
            source, text = padded, prev_tail["text"]
    return ChunkWindow(tail, source, pad, text, trimmed, length + pad, scratch, chunk_seconds)
