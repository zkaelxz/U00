"""
video_export.py -- turns your reviewed lines + a source video into a
finished, fully-subtitled episode file. Two modes:

  - Hardsub (burn-in): subtitles are drawn permanently into the video
    frames. Always visible, works everywhere, can't be toggled off.
  - Softsub (mux): subtitles are added as a separate selectable track
    inside the video container. Toggleable in players that support it
    (VLC, most desktop players); less universally supported on mobile/
    some streaming apps than hardsub.

Requires ffmpeg on PATH (same requirement as the rest of the app).
"""

import os
import subprocess
import tempfile

# What a stored source (an upload or a download with one of
# media_upload_service's AUDIO/VIDEO_EXTENSIONS) is read as, plus aac,
# mpegts and flv, which a mislabelled download often really is, and avi,
# asf, mpeg, aiff, w64 and caf, which a library from before the extension
# check can still hold. Not hls, dash, concat or any other format that
# names further files or URLs.
LOCAL_MEDIA_FORMATS = "mov,matroska,mp3,wav,flac,ogg,aac,mpegts,flv,avi,asf,mpeg,aiff,w64,caf"


def local_input(formats: str = LOCAL_MEDIA_FORMATS) -> list:
    """Input options for the -i after them. A file named .mp4 could
    really be a playlist or manifest naming network URLs, so ffmpeg may
    open only local files, and only as `formats`: the protocol whitelist
    alone is not enough, since ffmpeg 6.1's DASH demuxer opens http
    fragment URLs even under `-protocol_whitelist file`."""
    return ["-protocol_whitelist", "file", "-format_whitelist", formats]


PREVIEW_CLIP_TIMEOUT_SECONDS = 120.0
# ffmpeg stops writing the preview clip at this size (-fs), so a
# pathological source can't fill the disk within the timeout.
PREVIEW_CLIP_MAX_BYTES = 200 * 1024 * 1024


def render_preview_clip(video_path: str, ass_text: str, out_path: str, start: float, end: float,
                        timeout: float = PREVIEW_CLIP_TIMEOUT_SECONDS):
    """A short [start, end) cut of the source with `ass_text`
    burned in -- for checking the current subtitle style over real video
    before a full export. `ass_text` must already be timed to the clip
    (subtitle_formats.lines_for_clip). Re-encoded at a fast
    preset since it's thrown away after viewing. ffmpeg is killed after
    `timeout` seconds and TimeoutError (fixed text, no paths) is raised, so
    a hung ffmpeg can't keep a preview job running forever. The input is
    read with the file protocol only, and the output stops at
    PREVIEW_CLIP_MAX_BYTES."""
    fd, ass_path = tempfile.mkstemp(suffix=".ass")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ass_text)
    try:
        # -fs bounds the output size.
        cmd = ["ffmpeg", "-y", *local_input(),
               "-ss", str(max(start, 0.0)), "-i", video_path,
               "-t", str(max(end - start, 0.1)),
               "-vf", f"subtitles='{_escape_filter_path(ass_path)}'",
               "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac",
               "-fs", str(PREVIEW_CLIP_MAX_BYTES), out_path]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # subprocess.run has already killed ffmpeg; fixed text (no paths).
            raise TimeoutError("ffmpeg took too long rendering the preview clip and was "
                               "stopped.") from None
    finally:
        os.unlink(ass_path)
    return out_path


def _escape_filter_path(path: str) -> str:
    # ffmpeg's subtitles filter needs escaped colons/backslashes in the path.
    # Callers wrap the result in single quotes (subtitles='...'), where a
    # backslash can't escape a quote: close the quote, add \', reopen it.
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\\\\\''")


def softsub_output_extension(video_path: str) -> str:
    """.mp4 and .mkv sources keep their container; everything else becomes
    .mkv, because MP4 refuses stream-copied VP8/VP9/Opus and the like while
    Matroska accepts whatever codec the source holds."""
    ext = os.path.splitext(video_path)[1].lower()
    return ext if ext in (".mp4", ".mkv") else ".mkv"


def mux_soft_subtitles_cmd(video_path: str, srt_path: str, out_path: str,
                           language: str = "eng", sub_format: str = "srt") -> list:
    """The ffmpeg argument list the API's softsub export job runs
    (cancellably, with a timeout).
    sub_format "ass" keeps the ASS styling and needs a .mkv output: MP4's
    mov_text cannot hold it, and ffmpeg would silently drop the styling."""
    ext = os.path.splitext(out_path)[1].lower()
    if sub_format == "ass":
        if ext != ".mkv":
            raise ValueError("ASS soft subtitles need a .mkv output.")
        sub_codec = "ass"
    elif sub_format == "srt":
        sub_codec = "mov_text" if ext == ".mp4" else "srt"
    else:
        raise ValueError(f"Unknown subtitle format: {sub_format}")
    return [
        "ffmpeg", "-y", *local_input(), "-i", video_path, *local_input(sub_format), "-i", srt_path,
        "-map", "0:v", "-map", "0:a", "-map", "1:s",
        "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
        "-metadata:s:s:0", f"language={language}",
        out_path,
    ]


def replace_audio_with_dub_cmd(video_path: str, dub_audio_path: str, out_path: str,
                               keep_original_at_db: float = None) -> list:
    """The ffmpeg argument list the API's dubbed-video export job runs."""
    if keep_original_at_db is not None:
        return [
            "ffmpeg", "-y", *local_input(), "-i", video_path, *local_input("wav"),
            "-i", dub_audio_path,
            "-filter_complex",
            # normalize=0 stops amix scaling each input by 1/N (dub ~6 dB quiet); the
            # limiter guards the now-unscaled sum, and level=0 stops its auto makeup gain.
            f"[0:a]volume={float(keep_original_at_db)}dB[orig];[orig][1:a]amix=inputs=2:duration=first:normalize=0,"
            "alimiter=limit=0.977:level=0[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", out_path,
        ]
    return [
        "ffmpeg", "-y", *local_input(), "-i", video_path, *local_input("wav"),
        "-i", dub_audio_path,
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", out_path,
    ]
