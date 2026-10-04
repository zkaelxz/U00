"""
services/media_playback_service.py -- locate the file behind a drama's audio
or source video for seekable playback. Returns a real
path for the router to hand to Starlette's FileResponse (which streams in
chunks and handles Range/HEAD); the path never reaches a response body.

Only a file that resolves (symlinks followed) inside that drama's own folder,
is a regular file and has a whitelisted media extension is served. Every
refusal is the same generic NotFoundError so nothing leaks about the layout.
Also holds Review & edit's burned-subtitle preview ASS text. No FastAPI
import.
"""
import os

import db
import subtitle_formats
from services.media_upload_service import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from services.service_errors import NotFoundError

_FIELDS = {"audio": ("audio_filename", AUDIO_EXTENSIONS),
           "video": ("source_video_filename", VIDEO_EXTENSIONS)}
_NO_FILE = "This drama has no {kind} file to play."
# Fixed map, not mimetypes: its answer for .mkv/.m4a/.flac depends on the
# host's own MIME tables (Windows differs), so it is not deterministic.
_CONTENT_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
                  ".flac": "audio/flac", ".ogg": "audio/ogg", ".mp4": "video/mp4",
                  ".mkv": "video/x-matroska", ".mov": "video/quicktime", ".webm": "video/webm"}


def resolve_media(drama_id: int, kind: str):
    """Return (absolute_path, content_type) or raise NotFoundError."""
    if kind not in _FIELDS:
        raise NotFoundError("Unknown media kind.")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    field, allowed = _FIELDS[kind]
    name = drama.get(field)
    missing = NotFoundError(_NO_FILE.format(kind=kind))
    if not name or not isinstance(name, str) or os.path.splitext(name)[1].lower() not in allowed:
        raise missing
    base = os.path.realpath(db.drama_dir(drama_id))
    path = os.path.realpath(os.path.join(base, name))
    try:
        inside = os.path.commonpath([base, path]) == base
    except ValueError:   # different Windows drives, or mixed absolute/relative
        inside = False
    if not inside or path == base or not os.path.isfile(path):
        raise missing
    ctype = _CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    return path, ctype


def burn_preview_ass(lines, line, style_state, pad=2.0):
    """(start, end, ass_text) for a short burned-subtitle preview
    around `line` -- `pad` seconds either side -- timed to the clip the
    same way the vertical export is. style_state: optional {style,
    speaker_colors, speaker_names, wrap_chars}; None or no style = the
    "Clean" preset."""
    style_state = style_state or {}
    start, end = max(line.start - pad, 0.0), line.end + pad
    field = "en" if line.en.strip() else "zh"
    clip_lines = subtitle_formats.lines_for_clip(
        subtitle_formats.clamp_overlaps(lines)[0], start, end)
    ass = subtitle_formats.lines_to_ass(
        clip_lines, style_state.get("style") or subtitle_formats.ASS_PRESETS["Clean"], field,
        speaker_colors=style_state.get("speaker_colors"),
        speaker_names=style_state.get("speaker_names"),
        wrap_chars=style_state.get("wrap_chars"))
    return start, end, ass
