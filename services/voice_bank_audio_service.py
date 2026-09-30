"""
services/voice_bank_audio_service.py -- the voice-bank clip preview (L19):
where one voice-bank entry's saved clip is, for streaming only (the path is
never returned to a client).

A clip is served only when it is a regular file that resolves inside the
voice-bank folder (after following any symlink, so a link pointing out of
the folder is refused, as is a symlinked voice-bank folder), and only for
an audio extension in AUDIO_TYPES; the content type comes from that table,
never from the file or the request. Anything else is a plain 404.
"""

import os
from pathlib import Path

import db
from services.service_errors import NotFoundError

AUDIO_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".webm": "audio/webm",
}
_NOT_FOUND = "No playable clip for that voice-bank entry."


def clip_for_entry(entry_id: int):
    """(absolute path, content type, extension) for one entry's clip.
    NotFoundError for an unknown entry, a missing file, a non-audio file,
    or a path that escapes the voice-bank folder."""
    entry = db.get_voice_bank_entry(entry_id)
    name = (entry or {}).get("clip_filename") or ""
    ext = os.path.splitext(name)[1].lower()
    if not entry or ext not in AUDIO_TYPES or os.path.basename(name) != name:
        raise NotFoundError(_NOT_FOUND)
    folder = Path(db.VOICE_BANK_DIR)
    candidate = folder / name
    try:
        if folder.is_symlink() or candidate.is_symlink():
            raise NotFoundError(_NOT_FOUND)
        root = folder.resolve(strict=True)
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise NotFoundError(_NOT_FOUND) from None
    if not path.is_relative_to(root) or path.parent != root or not path.is_file():
        raise NotFoundError(_NOT_FOUND)
    return str(path), AUDIO_TYPES[ext], ext
