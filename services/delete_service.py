"""
services/delete_service.py -- the PC-only deletes:

  - remove_media: a drama's audio/video -- deletes the files named by
    `audio_filename` and `source_video_filename` and clears both fields;
    lines untouched.
  - remove_raw_novel: `raw_novel_context.txt`.
  - delete_translation_version.
  - delete_series_character: the series row goes, drama characters keep
    their copied name.
  - delete_preset / delete_voice_bank_entry; the voice-bank clip file is
    removed by db.delete_voice_bank_entry.
  - clear_reading_history.

Every function here needs `confirm is True` and nothing more (no typed
word). Order, as in drama_service.delete_drama: unknown or
foreign id -> NotFoundError; then no confirm -> InvalidInputError; then,
for the drama-scoped file/version deletes, a running job for the drama ->
ConflictError (drama_service.job_running_for_drama).

The owning services (review_records, characters, diagnostics, library)
are documented read-only or rename-only, so the deletes live here rather
than widening them. Results are plain dicts with ids and booleans; no
path, filename, key or URL is ever returned.

No FastAPI import.
"""
import logging
import os

import db
from services import drama_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

log = logging.getLogger(__name__)

MAX_ID = 2**31 - 1
RAW_NOVEL_FILENAME = "raw_novel_context.txt"  # sources/pipeline.py
_MEDIA_FIELDS = ("audio_filename", "source_video_filename")
_JOB_RUNNING = ("A job is running for this drama. Wait for it to finish or cancel it "
                "before deleting.")


def _check_id(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ID:
        raise InvalidInputError(f"A {what} id is a positive whole number.")
    return value


def _require_confirm(confirm, what: str):
    if confirm is not True:
        raise InvalidInputError(f"Deleting {what} needs confirm=true.")


def _require_drama(drama_id) -> dict:
    _check_id(drama_id, "drama")
    drama = db.get_drama(drama_id)
    if drama is None:
        raise NotFoundError(f"No drama with id {drama_id}.")
    return drama


def _require_idle(drama_id):
    if drama_service.job_running_for_drama(drama_id):
        raise ConflictError(_JOB_RUNNING)


def _drama_folder(drama_id) -> str:
    # Not db.drama_dir: that creates the folder, and a delete must not.
    return os.path.join(db.DRAMAS_DIR, str(drama_id))


def file_in_folder(folder: str, filename) -> str:
    """The stored filename's path only if it is a plain name inside the
    drama folder; None for anything else (never follow a stored path out)."""
    if not isinstance(filename, str) or not filename or filename != os.path.basename(filename):
        return None
    if filename in (".", "..") or "\\" in filename or "/" in filename:
        return None
    path = os.path.join(folder, filename)
    root = os.path.realpath(folder)
    if os.path.dirname(os.path.realpath(path)) != root:
        return None
    return path


def _remove_file(path) -> bool:
    """True when a file was removed. A failure is logged server-side (with
    the path) and never reaches the client."""
    if not path or not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError:
        log.exception("Could not remove %s", path)
        return False
    return True


# ---------------------------------------------------------------------------
# Drama files
# ---------------------------------------------------------------------------

def remove_media(drama_id, confirm=False) -> dict:
    """Deletes the drama's audio and kept video files and clears both
    filename fields (`db.update_drama(audio_filename=None,
    source_video_filename=None)`, a fixed field set). Lines, transcript and
    translation are untouched. 404 when neither field is set."""
    drama = _require_drama(drama_id)
    if not any(drama.get(f) for f in _MEDIA_FIELDS):
        raise NotFoundError("This drama has no audio or video to remove.")
    _require_confirm(confirm, "the drama's audio/video")
    _require_idle(drama_id)
    folder = _drama_folder(drama_id)
    removed = {f: _remove_file(file_in_folder(folder, drama.get(f))) for f in _MEDIA_FIELDS}
    db.update_drama(drama_id, audio_filename=None, source_video_filename=None)
    return {"drama_id": drama_id, "removed": True,
            "audio_file_removed": removed["audio_filename"],
            "video_file_removed": removed["source_video_filename"],
            "has_audio": False, "has_video": False}


def remove_raw_novel(drama_id, confirm=False) -> dict:
    """Deletes raw_novel_context.txt (Whisper priming / Sources imports).
    404 when there is none."""
    _require_drama(drama_id)
    path = os.path.join(_drama_folder(drama_id), RAW_NOVEL_FILENAME)
    if not os.path.isfile(path):
        raise NotFoundError("This drama has no raw novel text saved.")
    _require_confirm(confirm, "the raw novel text")
    _require_idle(drama_id)
    try:
        os.remove(path)
    except OSError as e:
        log.exception("Could not remove raw novel text for drama %s", drama_id)
        raise ConflictError("The raw novel text is in use and could not be removed. "
                            "Close anything using it and try again.") from e
    return {"drama_id": drama_id, "removed": True, "has_raw_novel_context": False}


# ---------------------------------------------------------------------------
# Translation versions
# ---------------------------------------------------------------------------

def delete_translation_version(drama_id, version_id, confirm=False) -> dict:
    """A version of another drama is a NotFoundError, same as a missing one
    (db.get_translation_version has no ownership check)."""
    _require_drama(drama_id)
    _check_id(version_id, "version")
    v = db.get_translation_version(version_id)
    if v is None or v.get("drama_id") != drama_id:
        raise NotFoundError(f"No translation version {version_id} for drama {drama_id}.")
    _require_confirm(confirm, "a translation version")
    _require_idle(drama_id)
    db.delete_translation_version(version_id)
    return {"drama_id": drama_id, "version_id": version_id, "deleted": True,
            "was_active": bool(v.get("is_active"))}


# ---------------------------------------------------------------------------
# Series characters
# ---------------------------------------------------------------------------

def delete_series_character(series_id, character_id, confirm=False) -> dict:
    """Removes the series-level record only (db.delete_series_character
    unlinks drama characters, which keep their name). A character of
    another series is a NotFoundError."""
    _check_id(series_id, "series")
    _check_id(character_id, "series character")
    if not any(s["id"] == series_id for s in db.list_series()):
        raise NotFoundError(f"No series with id {series_id}.")
    if not any(c["id"] == character_id for c in db.list_series_characters(series_id)):
        raise NotFoundError(f"No character {character_id} in series {series_id}.")
    _require_confirm(confirm, "a series character")
    db.delete_series_character(character_id)
    return {"series_id": series_id, "character_id": character_id, "deleted": True}


# ---------------------------------------------------------------------------
# Library presets and voice bank
# ---------------------------------------------------------------------------

def delete_preset(preset_id, confirm=False) -> dict:
    """No drama references a preset id (db.delete_preset), so dramas it was
    applied to are unaffected."""
    _check_id(preset_id, "preset")
    if not any(p["id"] == preset_id for p in db.list_presets()):
        raise NotFoundError(f"No preset with id {preset_id}.")
    _require_confirm(confirm, "a preset")
    db.delete_preset(preset_id)
    return {"preset_id": preset_id, "deleted": True}


def delete_voice_bank_entry(entry_id, confirm=False) -> dict:
    """Removes the entry and the bank's own copy of its clip; a drama that
    already applied it keeps its own copy (db.apply_voice_bank_entry)."""
    _check_id(entry_id, "voice bank entry")
    if db.get_voice_bank_entry(entry_id) is None:
        raise NotFoundError(f"No voice bank entry with id {entry_id}.")
    _require_confirm(confirm, "a voice bank entry")
    db.delete_voice_bank_entry(entry_id)
    return {"entry_id": entry_id, "deleted": True}


# ---------------------------------------------------------------------------
# Reading history
# ---------------------------------------------------------------------------

def clear_reading_history(confirm=False) -> dict:
    """Clears the default profile's reading history (the Library tools
    page's "Clear reading history"). Reading progress, and so the Continue
    reading shelf, is kept."""
    _require_confirm(confirm, "reading history")
    removed = len(db.list_reading_history(limit=-1))
    db.clear_reading_history()
    return {"cleared": True, "removed": removed}
