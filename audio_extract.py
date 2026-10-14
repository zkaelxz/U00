"""
audio_extract.py -- ffmpeg helpers that pull a whole audio track out of a video
or cut a short slice out of an audio file.
"""

import subprocess

import video_export


# Pulling the audio out of a multi-hour video; only stops a hung ffmpeg.
EXTRACT_AUDIO_TIMEOUT_SECONDS = 4 * 3600


class FfmpegError(subprocess.CalledProcessError):
    """ffmpeg failed. str() is fixed text because the default one carries the
    whole command, with absolute media and temp paths, and job errors store str(exc)."""

    def __str__(self):
        return f"ffmpeg failed (exit code {self.returncode})"


class FfmpegTimeout(subprocess.TimeoutExpired):
    """ffmpeg ran past its limit; str() leaves out the command for the same reason."""

    def __str__(self):
        return f"ffmpeg timed out after {self.timeout:g} seconds"


def _run_ffmpeg(cmd, timeout):
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise FfmpegError(exc.returncode, ["ffmpeg"]) from None
    except subprocess.TimeoutExpired as exc:
        raise FfmpegTimeout(["ffmpeg"], exc.timeout) from None


def extract_audio_from_video(video_path: str, out_path: str):
    """Pulls the audio track out of a video file via ffmpeg, so the
    same timing/alignment pipeline can run on it as on audio-only files."""
    _run_ffmpeg(video_export.extract_audio_cmd(video_path, out_path), EXTRACT_AUDIO_TIMEOUT_SECONDS)
    return out_path


# A line-sized slice takes well under a second; this only stops a hung ffmpeg.
SLICE_TIMEOUT_SECONDS = 120.0


def extract_audio_slice(audio_path: str, start: float, end: float, out_path: str,
                        timeout: float = SLICE_TIMEOUT_SECONDS):
    """Cuts a [start, end) slice of audio via ffmpeg. Shared by
    forced_align.py (per-chunk forced alignment) and asr_backend.py
    (per-segment Qwen3-ASR re-transcription), both of which need to hand
    a short audio clip to a model that only accepts a few minutes at a
    time, rather than the whole file. `timeout` (seconds, default
    SLICE_TIMEOUT_SECONDS; None disables it) raises
    subprocess.TimeoutExpired if ffmpeg runs longer."""
    start = max(start, 0.0)
    # -ss before -i seeks in the input; after it ffmpeg decodes everything up to
    # `start` for every slice, which grows quadratically over a long file.
    cmd = ["ffmpeg", "-y", "-ss", str(start), *video_export.local_input(), "-i", audio_path,
           "-t", str(max(end - start, 0.0)),
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path]
    _run_ffmpeg(cmd, timeout)
    return out_path
