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


def _write_srt_tempfile(srt_text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".srt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(srt_text)
    return path


def _escape_filter_path(path: str) -> str:
    # ffmpeg's subtitles filter needs escaped colons/backslashes in the path
    return path.replace("\\", "\\\\").replace(":", "\\:")


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


def mux_soft_subtitles(video_path: str, srt_text: str, out_path: str, language: str = "eng"):
    """Softsub: subtitles added as a selectable/toggleable track.
    Output must be .mp4 (mov_text codec) or .mkv (srt codec passthrough)."""
    srt_path = _write_srt_tempfile(srt_text)
    try:
        ext = os.path.splitext(out_path)[1].lower()
        sub_codec = "mov_text" if ext == ".mp4" else "srt"
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-i", srt_path,
            "-map", "0:v", "-map", "0:a", "-map", "1:s",
            "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
            "-metadata:s:s:0", f"language={language}",
            out_path,
        ]
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        os.unlink(srt_path)
    return out_path


def replace_audio_with_dub(video_path: str, dub_audio_path: str, out_path: str,
                            keep_original_at_db: float = None):
    """Swaps the video's audio track for the generated dub track. If
    keep_original_at_db is set (e.g. -20), mixes the original audio in
    quietly underneath instead of fully replacing it."""
    if keep_original_at_db is not None:
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-i", dub_audio_path,
            "-filter_complex",
            f"[0:a]volume={keep_original_at_db}dB[orig];[orig][1:a]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]", "-c:v", "copy", out_path,
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-i", dub_audio_path,
            "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-shortest", out_path,
        ]
    subprocess.run(cmd, check=True, capture_output=True)
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
