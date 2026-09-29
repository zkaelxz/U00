"""
services/drama_service.py -- Create a drama and edit its metadata, shared
by the FastAPI drama routes and (eventually) the Streamlit Workspace tab
(`tabs/workspace_tab.py`'s New-drama form and Edit-metadata expander).

Migration Slice 35 (create/update) and 36 (delete). Create/update return the same drama detail dict
`library_service.get_library_drama` does, so a client sees one shape.

Whitelist rationale: `db.create_drama(**fields)` and `db.update_drama(id,
**fields)` interpolate their kwarg KEYS straight into SQL with no
whitelist, so this module never passes a client-supplied key through --
only the explicit sets below reach the db layer, and anything else raises
InvalidInputError naming the field (never echoing its value). Not
accepted on update: `status`, `content_mode`, `source_language` (owned by
source_service), any *_filename, `translation_engine`, and
`personal_notes` (per-profile; the API has no profile header yet).

Deliberately NOT here, by design:
  - Cover-art upload -- multipart needs python-multipart.
  - Metadata auto-fill -- added later as Slice 37 (services/metadata_service.py).
  - Series rename/unassign, presets CRUD, media analysis -- other slices.

Preset handling: only the preset's `translation_engine` has a per-drama
DB home, so only it is persisted. `style_preset`, `locale`,
`default_female_pronouns` and `include_genre_notes` are session-only in
Streamlit (`apply_preset_to_session`), so create_drama returns them as
`preset_defaults` for the client to hold.

No Streamlit or FastAPI import: plain dicts in, plain dicts out.
"""

import logging
import os
import shutil
import time
import uuid

import background_jobs
import db
from services import library_service, ownership_service
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

_SOURCE_LANGUAGES = ("zh", "ja", "ko")
# Copied from tabs/workspace_tab.py's MEDIA_TYPE_OPTIONS (a tab constant, so
# a service can't import it without pulling in Streamlit) -- drift risk: keep
# in sync by hand.
MEDIA_TYPE_OPTIONS = ("audio_drama", "video_drama", "anime", "novel", "manhwa", "manga",
                      "manhua", "asmr", "streamer_vod", "music", "other")
PUBLICATION_STATUSES = ("unknown", "ongoing", "completed", "hiatus")

_TEXT_FIELDS = ("title_en", "title_zh", "author", "studio", "director", "voice_actors",
                "summary", "genre", "custom_tags", "source_url", "episode_summary",
                "project_instructions")
_INT_FIELDS = ("chapter_count", "episode_number")

# Hardening H1: sqlite ints are 64-bit and a Python int above that raises
# OverflowError (a 500), so every id/count the client sends is capped well
# below it; text fields get length caps so a client can't store megabytes.
MAX_ID = 2**31 - 1
MAX_NAME_LEN = 300
MAX_LONG_TEXT_LEN = 5000
MAX_URL_LEN = 2000
_TEXT_CAPS = {"summary": MAX_LONG_TEXT_LEN, "episode_summary": MAX_LONG_TEXT_LEN,
              "project_instructions": MAX_LONG_TEXT_LEN, "source_url": MAX_URL_LEN,
              "custom_tags": MAX_URL_LEN}
_STRIPPED = ("title_en", "title_zh")
_UPDATABLE = frozenset(_TEXT_FIELDS + _INT_FIELDS
                       + ("media_type", "publication_status", "series_id"))


def _check_text(name, value):
    """Type, length cap and (source_url) scheme check; returns the value,
    stripped for titles. Messages name only the field, never the value."""
    if not isinstance(value, str):
        raise InvalidInputError(f"{name} must be text.")
    cap = _TEXT_CAPS.get(name, MAX_NAME_LEN)
    if len(value) > cap:
        raise InvalidInputError(f"{name} is too long (max {cap} characters).")
    if name == "source_url" and value and not value.startswith(("http://", "https://")):
        raise InvalidInputError("source_url must be empty or start with http:// or https://.")
    return value.strip() if name in _STRIPPED else value


def _check_id(name, value):
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidInputError(f"{name} must be a whole number.")
    if value < 1 or value > MAX_ID:
        raise InvalidInputError(f"{name} is out of range.")


def _check_media_type(value):
    if value not in MEDIA_TYPE_OPTIONS:
        raise InvalidInputError("Unknown media_type.",
                                details={"allowed": list(MEDIA_TYPE_OPTIONS)})


def _find_preset(preset_id):
    return next((p for p in db.list_presets() if p["id"] == preset_id), None)


def create_drama(*, source_language, title_en="", title_zh="", author="", studio="",
                 director="", voice_actors="", summary="", media_type="audio_drama",
                 series_id=None, new_series_name=None, preset_id=None,
                 principal=None) -> dict:
    """Creates a drama (and optionally assigns a series and applies a
    preset) in one call. `source_language` is required (zh/ja/ko).
    `series_id` (must exist) and `new_series_name` are mutually exclusive;
    a whitespace-only `new_series_name` is rejected. Returns the drama
    detail plus `preset_defaults` (dict or None). All validation happens
    before anything is written, so a rejected call creates nothing.
    `principal` (None = auth off) goes through ownership_service: a series
    the caller can't see is a 404, as is a name taken by one (409). The
    new drama is stamped with `new_item_defaults(principal)` (auth B2): its
    creator (None = the PC owner) and private unless they share by default."""
    if source_language not in _SOURCE_LANGUAGES:
        raise InvalidInputError("source_language is required and must be one of zh, ja, ko.",
                                details={"allowed": list(_SOURCE_LANGUAGES)})
    texts = {"title_en": title_en, "title_zh": title_zh, "author": author, "studio": studio,
             "director": director, "voice_actors": voice_actors, "summary": summary}
    for name, value in texts.items():
        texts[name] = _check_text(name, value)
    _check_media_type(media_type)

    if series_id is not None and new_series_name is not None:
        raise InvalidInputError("Pass series_id or new_series_name, not both.")
    owned = ownership_service.new_item_defaults(principal)
    if series_id is not None:
        _check_id("series_id", series_id)
        ownership_service.check_series_assignment(principal, series_id, owned["owner_user_id"])
    if new_series_name is not None:
        new_series_name = _check_text("new_series_name", new_series_name).strip()
        if not new_series_name:
            raise InvalidInputError("new_series_name must not be blank.")
        taken = db.get_series_id_by_name(new_series_name)
        if taken is not None and not ownership_service.can_see_series(principal, taken):
            raise ConflictError("That series name is taken")

    preset = None
    if preset_id is not None:
        _check_id("preset_id", preset_id)
        preset = _find_preset(preset_id)
        if preset is None:
            raise NotFoundError("No preset with that id.")

    fields = dict(texts, media_type=media_type, source_language=source_language, **owned)
    if series_id is not None:
        fields["series_id"] = series_id
    if preset and preset.get("translation_engine"):
        fields["translation_engine"] = preset["translation_engine"]
    new_id = db.create_drama(**fields)
    # Hardening H1: a NEW series is created only after the drama row exists
    # (as the Streamlit form does), so a failed create can't leave a stray
    # series behind (db has no delete_series to clean one up).
    # The series step can still be refused (a name taken or made private
    # between the pre-check and here); undo the new drama so a rejected call
    # creates nothing and a retry can't leave a duplicate.
    if new_series_name is not None:
        try:
            ownership_service.assign_drama_series(
                principal, new_id,
                ownership_service.get_or_create_series_for(principal, new_series_name))
        except Exception:
            db.delete_drama(new_id)
            raise

    detail = library_service.get_library_drama(new_id)
    detail["preset_defaults"] = None if preset is None else {
        "style_preset": preset.get("style_preset"),
        "locale": preset.get("locale"),
        "default_female_pronouns": bool(preset.get("default_female_pronouns")),
        "include_genre_notes": bool(preset.get("include_genre_notes", True)),
        # The tab applies the preset's model only to the preset's own engine
        # (apply_preset_to_session), which is the engine saved on the drama.
        "engine_model": (preset.get("engine_model") or None)
        if preset.get("translation_engine") else None,
    }
    return detail


def update_drama_metadata(drama_id, *, principal=None, **partial) -> dict:
    """Field-scoped partial update of the whitelisted metadata columns.
    Only fields passed (value not None) are validated and written; text
    fields may be cleared with "", and `chapter_count`/`episode_number`
    take a non-negative int where 0 clears it to NULL (the tab's "0 = not
    set" convention; None can't mean both "not passed" and "clear"). Raises
    NotFoundError for an unknown drama or series, InvalidInputError for a
    non-whitelisted field or bad value, ConflictError for a move into a
    private series the drama's owner doesn't own (ownership_service; a
    series `principal` can't see is a 404). Returns the drama detail."""
    if isinstance(drama_id, int) and not isinstance(drama_id, bool) and drama_id > MAX_ID:
        raise InvalidInputError("drama_id is out of range.")
    library_service.get_library_drama(drama_id)  # id check + existence
    for key in partial:
        if key not in _UPDATABLE:
            raise InvalidInputError("That field cannot be updated here.")

    fields = {}
    for key, value in partial.items():
        if value is None:
            continue
        if key in _TEXT_FIELDS:
            value = _check_text(key, value)
        elif key in _INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InvalidInputError(f"{key} must be a non-negative whole number.")
            if value > MAX_ID:
                raise InvalidInputError(f"{key} is out of range.")
            value = value or None  # 0 = "not set", stored NULL, as the tab does
        elif key == "media_type":
            _check_media_type(value)
        elif key == "publication_status":
            if value not in PUBLICATION_STATUSES:
                raise InvalidInputError("Unknown publication_status.",
                                        details={"allowed": list(PUBLICATION_STATUSES)})
        elif key == "series_id":
            _check_id("series_id", value)
            drama = db.get_item_ownership("drama", drama_id)
            ownership_service.check_series_assignment(principal, value,
                                                      drama.get("owner_user_id"))
        fields[key] = value

    series_id = fields.pop("series_id", None)
    if series_id is not None:   # first: a refused move writes nothing
        ownership_service.assign_drama_series(principal, drama_id, series_id)
    if fields:
        db.update_drama(drama_id, **fields)
    return library_service.get_library_drama(drama_id)


# A job_records row still saying running/queued but untouched this long is
# treated as left behind by a crashed process (records have no resume, see
# db.save_job_record), so it must not block a delete forever. Generous
# enough that a real long job (which rewrites its record on state changes)
# is unlikely to be older than this.
_STALE_JOB_RECORD_SECONDS = 6 * 60 * 60
_DELETE_CONFIRM_TEXT = "DELETE"
_LEFTOVER_FILES_MESSAGE = ("The drama was deleted from the library, but some of its files "
                           "could not be removed (a file may be in use). Close anything "
                           "using them and remove the leftover folder manually.")


def job_running_for_drama(drama_id) -> bool:
    """In-process jobs, plus fresh running/queued job_records rows written
    by another process (the API server and Streamlit are separate
    processes; the in-memory tracker only sees its own)."""
    if background_jobs.any_job_running_for_drama(drama_id):
        return True
    job_ids = {f"{prefix}{drama_id}" for prefix in background_jobs.DRAMA_JOB_PREFIXES}
    cutoff = time.time() - _STALE_JOB_RECORD_SECONDS
    for rec in db.list_job_records():
        if (rec.get("job_id") in job_ids and rec.get("status") in ("running", "queued")
                and (rec.get("updated_at") or 0) >= cutoff):
            return True
    return False


def _hard_delete_drama(drama_id) -> bool:
    """The single place a drama is actually removed, so roadmap Step 43's
    soft-delete can replace just this function. Order (B-14): rename the
    drama folder to a tombstone name, drop the DB row (db.delete_drama's own
    rmtree then finds nothing), then rmtree the tombstone. If the DB delete
    fails the folder name is restored, so nothing is half-deleted. If the
    final folder removal fails the row is already gone; that is logged (with
    the path, server-side only) and reported as a warning, not an error.
    Returns True when a leftover folder could not be removed."""
    folder = os.path.join(db.DRAMAS_DIR, str(drama_id))
    tomb = None
    if os.path.isdir(folder):
        tomb = f"{folder}.deleting-{uuid.uuid4().hex[:8]}"
        try:
            os.rename(folder, tomb)
        except OSError as e:
            # Nothing changed yet (row and folder intact); no paths in the message.
            raise ServiceError("The drama's files are in use, so it was not deleted. "
                               "Close anything using them and try again.") from e
    try:
        db.delete_drama(drama_id)
    except Exception as e:
        if tomb is not None:
            try:
                os.rename(tomb, folder)
            except OSError:
                log.exception("Could not restore drama folder %s after a failed delete", folder)
        raise ServiceError("The drama could not be deleted.") from e
    if db.get_drama(drama_id) is not None:
        if tomb is not None:
            try:
                os.rename(tomb, folder)
            except OSError:
                log.exception("Could not restore drama folder %s after a failed delete", folder)
        raise ServiceError("The drama could not be deleted.")
    if tomb is None:
        return False
    try:
        shutil.rmtree(tomb)
    except OSError:
        log.exception("Drama %s deleted but leftover folder %s could not be removed",
                      drama_id, tomb)
        return True
    return False


def delete_drama(drama_id, confirm=False, confirm_text="") -> dict:
    """Permanently deletes a drama and its folder. Order: unknown id ->
    NotFoundError (always, even without confirmation); then
    InvalidInputError unless `confirm is True` and `confirm_text` is
    exactly "DELETE" (the tab's checkbox + typed word); then ConflictError
    if a job is running for the drama. Returns {"deleted": True,
    "drama_id": id}, plus a non-secret "warning" when the row is gone but
    leftover files could not be removed."""
    library_service.get_library_drama(drama_id)  # id check + existence
    if confirm is not True or confirm_text != _DELETE_CONFIRM_TEXT:
        raise InvalidInputError("Deleting a drama needs confirm=true and confirm_text set to "
                                "the word DELETE, in capitals.")
    if job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this drama -- wait for it "
                            "to finish or cancel it before deleting.")
    leftover = _hard_delete_drama(drama_id)
    result = {"deleted": True, "drama_id": drama_id}
    if leftover:
        result["warning"] = _LEFTOVER_FILES_MESSAGE
    return result
