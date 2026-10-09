"""WAV handling for Live chunks: the overlap tail carried into the next chunk, and
the cap that keeps one Whisper call from being handed more audio than a chunk is
meant to hold."""
import os
import wave
from typing import NamedTuple, Optional


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
        with wave.open(path, "rb") as w:
            nchannels, sampwidth, framerate, nframes = (
                w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes())
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
        with wave.open(chunk_path, "rb") as w:
            params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            frames = w.readframes(w.getnframes())
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
        with wave.open(path, "rb") as w:
            params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            nframes = w.getnframes()
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


def window_for_chunk(path: str, idx: int, out_dir: str, segment_seconds: float,
                     prev_tail, tail_seconds: float) -> ChunkWindow:
    """The audio one Whisper call gets for chunk `idx`: at most
    segment_seconds + overlap, however long the file on disk is."""
    tail = read_wav_tail(path, tail_seconds) if tail_seconds else None
    scratch, source, trimmed = [], path, 0.0
    length = tail["chunk_seconds"] if tail else None
    if length is None:
        try:
            with wave.open(path, "rb") as w:
                length = w.getnframes() / w.getframerate()
        except (OSError, EOFError, wave.Error, ZeroDivisionError):
            length = 0.0
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
    return ChunkWindow(tail, source, pad, text, trimmed, length + pad, scratch)
