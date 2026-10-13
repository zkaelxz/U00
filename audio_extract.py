"""
audio_extract.py -- ffmpeg helpers that pull a whole audio track out of a video
or cut a short slice out of an audio file.
"""

import video_export


# Pulling the audio out of a multi-hour video; only stops a hung ffmpeg.
EXTRACT_AUDIO_TIMEOUT_SECONDS = 4 * 3600


def extract_audio_from_video(video_path: str, out_path: str):
    """Pulls the audio track out of a video file via ffmpeg, so the
    same timing/alignment pipeline can run on it as on audio-only files."""
    import subprocess
    cmd = ["ffmpeg", "-y", *video_export.local_input(), "-i", video_path, "-vn", "-acodec", "pcm_s16le",
           "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=EXTRACT_AUDIO_TIMEOUT_SECONDS)
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
    import subprocess
    cmd = ["ffmpeg", "-y", *video_export.local_input(), "-i", audio_path,
           "-ss", str(max(start, 0.0)), "-to", str(end),
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return out_path
