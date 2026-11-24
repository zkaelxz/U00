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

import db
from lib.errors import NotFoundError
from services import ownership_service

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


def clip_for_entry(entry_id: int, principal=None):
    """(absolute path, content type, extension) for one entry's clip.
    NotFoundError for an unknown entry, a missing file, a non-audio file,
    or a path that escapes the voice-bank folder."""
    entry = db.get_voice_bank_entry(entry_id)
    if not ownership_service.can_see_voice_entry(principal, entry):
        raise NotFoundError(_NOT_FOUND)
    name = (entry or {}).get("clip_filename") or ""
    ext = os.path.splitext(name)[1].lower()
    path = db.voice_bank_clip_path(name) if entry and ext in AUDIO_TYPES else None
    if path is None:
        raise NotFoundError(_NOT_FOUND)
    return path, AUDIO_TYPES[ext], ext
