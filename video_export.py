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

# Step 6e: vertical/shorts export -- a selection longer than this gets a
# soft "consider a shorter clip" prompt instead of a hard block, since
# re-encoding a full multi-hour episode vertically by accident is slow and
# heavy, but exporting a long vertical clip is still a real (if less
# common) use case.
LONG_CLIP_THRESHOLD_SECONDS = 20 * 60


def probe_duration_seconds(video_path: str) -> float:
    """Total duration of a video/audio file, via ffprobe (bundled with the
    ffmpeg install this app already requires)."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    return float(out.stdout.strip())


def _fmt_mmss(seconds: float) -> str:
    m, s = divmod(int(round(max(seconds, 0))), 60)
    return f"{m}:{s:02d}"


def estimate_vertical_export(duration_seconds: float) -> dict:
    """A rough, honest estimate for rendering a vertical clip of this
    length -- software video encoding speed varies enormously by CPU and
    resolution, so this is a range scaled to the clip's own length and
    typical H.264/AAC bitrates, not a benchmarked number for any specific
    machine (the same "honest range, not false precision" approach as the
    diarization-duration estimate in tabs/workspace_tab.py).

    Returns {"time_note", "size_note", "is_long"}.
    """
    duration_seconds = max(duration_seconds, 0.0)
    time_note = (
        f"Usually takes roughly {_fmt_mmss(duration_seconds * 0.5)}"
        f"–{_fmt_mmss(duration_seconds * 3)} to render, depending on your CPU and the "
        f"clip's resolution (software video encoding -- there's no progress bar for this)."
    )
    # Typical H.264 bitrates for a vertical short: ~2 Mbps (heavily
    # compressed) to ~8 Mbps (high quality), plus ~128kbps AAC audio.
    low_mb = duration_seconds * (2_000_000 + 128_000) / 8 / 1_000_000
    high_mb = duration_seconds * (8_000_000 + 128_000) / 8 / 1_000_000
    size_note = (f"Estimated output size: ~{low_mb:.0f}–{high_mb:.0f} MB "
                 "(H.264 video + AAC audio, typical bitrates).")
    return {
        "time_note": time_note,
        "size_note": size_note,
        "is_long": duration_seconds > LONG_CLIP_THRESHOLD_SECONDS,
    }


def render_vertical_clip(video_path: str, ass_text: str, out_path: str,
                          start: float = 0.0, end: float = None,
                          crop_position: float = 0.5):
    """Renders a 9:16 vertical clip: trims to [start, end), centre-crops
    the width down to a 9:16 frame (full height kept -- only meant for a
    source wider than 9:16, i.e. ordinary landscape video), and burns
    `ass_text` (the same subtitle_formats.lines_to_ass output used
    everywhere else -- burn_ass's own per-speaker-colour styling, sized
    for the new frame automatically: the ASS header's PlayResY scales
    font sizes to the output's actual height, which cropping only the
    width never changes).

    crop_position: 0.0 keeps the left edge, 1.0 the right edge, 0.5 (the
    default) centres the crop -- the "manual crop-position adjustment per
    drama" the roadmap calls for, rather than auto-detecting a subject.
    end=None renders to the end of the source.
    """
    crop_position = min(max(crop_position, 0.0), 1.0)
    fd, ass_path = tempfile.mkstemp(suffix=".ass")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ass_text)
    try:
        crop = f"crop=ih*9/16:ih:(iw-ih*9/16)*{crop_position:.4f}:0"
        vf = f"{crop},subtitles='{_escape_filter_path(ass_path)}'"
        cmd = ["ffmpeg", "-y", "-ss", str(max(start, 0.0)), "-i", video_path]
        if end is not None:
            cmd += ["-t", str(max(end - start, 0.1))]
        cmd += ["-vf", vf, "-c:v", "libx264", "-c:a", "aac", out_path]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        os.unlink(ass_path)
    return out_path


PREVIEW_CLIP_TIMEOUT_SECONDS = 120.0
# ffmpeg stops writing the preview clip at this size (-fs), so a
# pathological source can't fill the disk within the timeout.
PREVIEW_CLIP_MAX_BYTES = 200 * 1024 * 1024


def render_preview_clip(video_path: str, ass_text: str, out_path: str, start: float, end: float,
                        timeout: float = PREVIEW_CLIP_TIMEOUT_SECONDS):
    """Step 12c: a short [start, end) cut of the source with `ass_text`
    burned in -- for checking the current subtitle style over real video
    before a full export. `ass_text` must already be timed to the clip
    (subtitle_formats.lines_for_clip), the same contract as
    render_vertical_clip, just without the 9:16 crop. Re-encoded at a fast
    preset since it's thrown away after viewing. ffmpeg is killed after
    `timeout` seconds and TimeoutError (fixed text, no paths) is raised, so
    a hung ffmpeg can't keep a preview job running forever. The input is
    read with the file protocol only, and the output stops at
    PREVIEW_CLIP_MAX_BYTES."""
    fd, ass_path = tempfile.mkstemp(suffix=".ass")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ass_text)
    try:
        # -protocol_whitelist file: the input is only ever read as a local
        # file (never a playlist/concat reaching out over http or another
        # protocol); -fs bounds the output size.
        cmd = ["ffmpeg", "-y", "-protocol_whitelist", "file",
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


def _write_srt_tempfile(srt_text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".srt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(srt_text)
    return path


def _escape_filter_path(path: str) -> str:
    # ffmpeg's subtitles filter needs escaped colons/backslashes in the path.
    # Callers wrap the result in single quotes (subtitles='...'), where a
    # backslash can't escape a quote: close the quote, add \', reopen it.
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\\\\\''")


def burn_subtitles(video_path: str, srt_text: str, out_path: str,
                    font_size: int = 24, font_color: str = "white",
                    outline_color: str = "black", font_name: str = None,
                    bold: bool = False, italic: bool = False, outline_width: int = 2,
                    alignment: int = 2):
    """Hardsub: subtitles permanently drawn into the video. Most
    compatible option -- plays correctly on literally anything. The style
    arguments become one flat force_style for every line; for per-speaker
    colours, export ASS and use burn_ass instead."""
    srt_path = _write_srt_tempfile(srt_text)
    try:
        escaped = _escape_filter_path(srt_path)
        style = (
            (f"FontName={font_name.replace(',', ' ')}," if font_name else "")
            + f"FontSize={font_size},PrimaryColour=&H{_bgr_hex(font_color)}&,"
            f"OutlineColour=&H{_bgr_hex(outline_color)}&,BorderStyle=1,Outline={outline_width},"
            f"Bold={-1 if bold else 0},Italic={-1 if italic else 0},Alignment={alignment}"
        )
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"subtitles='{escaped}':force_style='{style}'",
            "-c:a", "copy", out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        os.unlink(srt_path)
    return out_path


def burn_ass(video_path: str, ass_text: str, out_path: str):
    """Hardsub from an ASS file: libass reads every style -- including one
    colour per speaker -- straight from the file, so no force_style is
    passed (it would flatten them all back to one look)."""
    fd, ass_path = tempfile.mkstemp(suffix=".ass")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(ass_text)
    try:
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"subtitles='{_escape_filter_path(ass_path)}'",
            "-c:a", "copy", out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        os.unlink(ass_path)
    return out_path


# An input option, so it goes before each -i: a file named .mp4 could really
# be an HLS playlist naming network URLs; ffmpeg may only open local files.
_FILE_ONLY = ("-protocol_whitelist", "file")


def mux_soft_subtitles_cmd(video_path: str, srt_path: str, out_path: str,
                           language: str = "eng") -> list:
    """The ffmpeg argument list mux_soft_subtitles runs (also used by the
    API's softsub export job, which runs it cancellably with a timeout)."""
    ext = os.path.splitext(out_path)[1].lower()
    sub_codec = "mov_text" if ext == ".mp4" else "srt"
    return [
        "ffmpeg", "-y", *_FILE_ONLY, "-i", video_path, *_FILE_ONLY, "-i", srt_path,
        "-map", "0:v", "-map", "0:a", "-map", "1:s",
        "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
        "-metadata:s:s:0", f"language={language}",
        out_path,
    ]


def mux_soft_subtitles(video_path: str, srt_text: str, out_path: str, language: str = "eng"):
    """Softsub: subtitles added as a selectable/toggleable track.
    Output must be .mp4 (mov_text codec) or .mkv (srt codec passthrough)."""
    srt_path = _write_srt_tempfile(srt_text)
    try:
        subprocess.run(mux_soft_subtitles_cmd(video_path, srt_path, out_path, language),
                       check=True, capture_output=True)
    finally:
        os.unlink(srt_path)
    return out_path


def replace_audio_with_dub_cmd(video_path: str, dub_audio_path: str, out_path: str,
                               keep_original_at_db: float = None) -> list:
    """The ffmpeg argument list replace_audio_with_dub runs (also used by
    the API's dubbed-video export job)."""
    if keep_original_at_db is not None:
        return [
            "ffmpeg", "-y", *_FILE_ONLY, "-i", video_path, *_FILE_ONLY, "-i", dub_audio_path,
            "-filter_complex",
            f"[0:a]volume={float(keep_original_at_db)}dB[orig];[orig][1:a]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", out_path,
        ]
    return [
        "ffmpeg", "-y", *_FILE_ONLY, "-i", video_path, *_FILE_ONLY, "-i", dub_audio_path,
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", out_path,
    ]


def replace_audio_with_dub(video_path: str, dub_audio_path: str, out_path: str,
                            keep_original_at_db: float = None):
    """Swaps the video's audio track for the generated dub track. If
    keep_original_at_db is set (e.g. -20), mixes the original audio in
    quietly underneath instead of fully replacing it."""
    subprocess.run(replace_audio_with_dub_cmd(video_path, dub_audio_path, out_path,
                                              keep_original_at_db),
                   check=True, capture_output=True)
    return out_path


_COLOR_MAP = {"white": "FFFFFF", "black": "000000", "yellow": "FFFF00"}


def _bgr_hex(color_name_or_hex: str) -> str:
    """ASS/SSA styling uses BGR hex order, not RGB -- flip a friendly
    color name or #RRGGBB into what libass expects."""
    hexval = _COLOR_MAP.get(color_name_or_hex.lower(), color_name_or_hex.lstrip("#"))
    if len(hexval) != 6:
        hexval = "FFFFFF"
    r, g, b = hexval[0:2], hexval[2:4], hexval[4:6]
    return f"{b}{g}{r}".upper()
