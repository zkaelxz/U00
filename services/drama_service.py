"""
services/drama_service.py -- Create a drama and edit its metadata, for the
drama routes.

Create/update return the same drama detail dict
`library_service.get_library_drama` does, so a client sees one shape.

Whitelist rationale: `db.create_drama(**fields)` and `db.update_drama(id,
**fields)` interpolate their kwarg KEYS straight into SQL with no
whitelist, so this module never passes a client-supplied key through --
only the explicit sets below reach the db layer, and anything else raises
InvalidInputError naming the field (never echoing its value). Not
accepted on update: `status`, `content_mode`, `source_language` (owned by
source_service), any *_filename, `translation_engine`, and
`personal_notes` (per-profile; the API has no profile header yet).

Preset handling: only the preset's `translation_engine` has a per-drama
DB home, so only it is persisted. `style_preset`, `locale`,
`default_female_pronouns` and `include_genre_notes` are not saved by
create_drama, so it returns them as `preset_defaults` for the client to
hold; the owner's later choice is saved on the drama (a run's toggles, or
update_drama_metadata) and every run reads it from there.

No FastAPI import: plain dicts in, plain dicts out.
"""

import contextlib
import datetime
import logging
import os
import re
import shutil
import time
import uuid

import background_jobs
import db
import storage
from jobs import job_store
from core import SOURCE_LANGUAGES
from services import (language_pack_service, library_service, ownership_service, settings_service,
                      source_link_service, vdb_names_service)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

MEDIA_TYPE_OPTIONS = ("audio_drama", "video_drama", "anime", "novel", "manhwa", "manga",
                      "manhua", "asmr", "streamer_vod", "music", "other")
PUBLICATION_STATUSES = ("unknown", "ongoing", "completed", "hiatus")

_TEXT_FIELDS = ("title_en", "title_zh", "author", "studio", "director", "voice_actors",
                "summary", "genre", "custom_tags", "source_url", "episode_summary",
                "project_instructions")
_INT_FIELDS = ("chapter_count", "episode_number")
# Stored 0/1: the Translate stage's two toggles (translate_run_service.save_style_toggles)
# and the Media stage's "Keep original files" pin (keep_original_service).
_BOOL_FIELDS = ("default_female_pronouns", "include_genre_notes", "keep_original",
                "translate_by_sentence")

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
_RELEASE_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UPDATABLE = frozenset(_TEXT_FIELDS + _INT_FIELDS + _BOOL_FIELDS
                       + ("media_type", "publication_status", "series_id",
                          "new_series_name", "release_date"))


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
    if name in _STRIPPED and "\x00" in value:
        raise InvalidInputError(f"{name} must not contain a NUL character.")
    return value.strip() if name in _STRIPPED else value


def check_release_date(value):
    """"" clears (unknown); otherwise a real calendar date as YYYY-MM-DD.
    The regex is needed because fromisoformat also accepts other spellings
    (e.g. 20260101) that would sort wrongly as text."""
    if not isinstance(value, str):
        raise InvalidInputError("release_date must be text.")
    if value and not (_RELEASE_DATE_RE.fullmatch(value) and _is_real_date(value)):
        raise InvalidInputError("release_date must be empty or a date as YYYY-MM-DD.")
    return value


def _is_real_date(value):
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


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


SENTENCE_SPLIT_DEFAULT_LANGUAGES = ("zh", "ja")


def create_drama(*, source_language, title_en="", title_zh="", author="", studio="",
                 director="", voice_actors="", summary="", media_type="audio_drama", release_date="",
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
    if source_language not in SOURCE_LANGUAGES:
        raise InvalidInputError("source_language is required and must be one of zh, ja, ko.",
                                details={"allowed": list(SOURCE_LANGUAGES)})
    texts = {"title_en": title_en, "title_zh": title_zh, "author": author, "studio": studio,
             "director": director, "voice_actors": voice_actors, "summary": summary}
    for name, value in texts.items():
        texts[name] = _check_text(name, value)
    _check_media_type(media_type)
    release_date = check_release_date(release_date)

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
        if taken is not None and not ownership_service.can_edit(principal, "series", taken):
            raise ConflictError("That series name is taken")

    preset = None
    if preset_id is not None:
        _check_id("preset_id", preset_id)
        preset = _find_preset(preset_id)
        if preset is None:
            raise NotFoundError("No preset with that id.")

    fields = dict(texts, media_type=media_type, source_language=source_language, **owned)
    if release_date:
        fields["release_date"] = release_date
    if series_id is not None:
        fields["series_id"] = series_id
    if source_language in SENTENCE_SPLIT_DEFAULT_LANGUAGES:
        # Chinese and Japanese speech runs long between pauses; without it a
        # Whisper or Whisper + Qwen title comes back as minute-long lines.
        # Existing titles keep their stored value.
        fields["split_by_sentences"] = 1
    if preset and preset.get("translation_engine"):
        fields["translation_engine"] = preset["translation_engine"]
    else:
        # Settings > Defaults for new dramas (the column's own default is
        # claude, so an unstamped drama would never see the setting).
        fields["translation_engine"] = settings_service.get("default_engine")
    try:
        new_id = db.create_drama(**fields)
    except db.DramaFolderConflict as exc:
        raise ConflictError(str(exc)) from None
    # Hardening H1: a NEW series is created only after the drama row exists
    # so a failed create can't leave a stray
    # series behind (db has no delete_series to clean one up).
    # The series step can still be refused (a name taken or made private
    # between the pre-check and here); undo the new drama so a rejected call
    # creates nothing and a retry can't leave a duplicate.
    if new_series_name is not None:
        try:
            series_id = ownership_service.get_or_create_series_for(principal, new_series_name)
            ownership_service.assign_drama_series(principal, new_id, series_id)
        except Exception:
            db.delete_drama(new_id)
            raise
    if series_id is not None:
        _inherit_series_defaults("inherit_new", new_id, series_id, skip=(
            ("translation_engine",) if preset and preset.get("translation_engine") else ()))

    detail = library_service.get_library_drama(new_id)
    detail["preset_defaults"] = None if preset is None else {
        "style_preset": preset.get("style_preset"),
        "locale": preset.get("locale"),
        "default_female_pronouns": bool(preset.get("default_female_pronouns")),
        "include_genre_notes": bool(preset.get("include_genre_notes", True)),
        # The preset's model applies only to the preset's own engine, which
        # is the engine saved on the drama.
        "engine_model": (preset.get("engine_model") or None)
        if preset.get("translation_engine") else None,
    }
    return detail


def update_drama_metadata(drama_id, *, principal=None, **partial) -> dict:
    """Field-scoped partial update of the whitelisted metadata columns.
    Only fields passed (value not None) are validated and written; text
    fields may be cleared with "", and `chapter_count`/`episode_number`
    take a non-negative int where 0 clears it to NULL ("0 = not set";
    None can't mean both "not passed" and "clear"). Raises
    NotFoundError for an unknown drama or series, InvalidInputError for a
    non-whitelisted field or bad value, ConflictError for a move into a
    private series the drama's owner doesn't own (ownership_service; a
    series `principal` can't see is a 404). Returns the drama detail.

    Parity P11/X09: `series_id=0` takes the drama out of its series (0 =
    clear, as for the counts; a drama leaving a private series stays
    private). `new_series_name` ("+ New series") moves it into the series
    of that name, creating it for the principal if none exists; it can't be
    combined with `series_id`."""
    if isinstance(drama_id, int) and not isinstance(drama_id, bool) and drama_id > MAX_ID:
        raise InvalidInputError("drama_id is out of range.")
    library_service.get_library_drama(drama_id)  # id check + existence
    for key in partial:
        if key not in _UPDATABLE:
            raise InvalidInputError("That field cannot be updated here.")
    new_series_name = partial.pop("new_series_name", None)
    if new_series_name is not None:
        if partial.get("series_id") is not None:
            raise InvalidInputError("Pass series_id or new_series_name, not both.")
        new_series_name = _check_text("new_series_name", new_series_name).strip()
        if not new_series_name:
            raise InvalidInputError("new_series_name must not be blank.")
        drama = db.get_item_ownership("drama", drama_id)
        ownership_service.check_new_series_assignment(principal, new_series_name,
                                                      drama.get("owner_user_id"))

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
            value = value or None  # 0 = "not set", stored NULL
        elif key in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise InvalidInputError(f"{key} must be true or false.")
            value = int(value)
        elif key == "release_date":
            value = check_release_date(value) or None  # "" clears to NULL
        elif key == "media_type":
            _check_media_type(value)
        elif key == "publication_status":
            if value not in PUBLICATION_STATUSES:
                raise InvalidInputError("Unknown publication_status.",
                                        details={"allowed": list(PUBLICATION_STATUSES)})
        elif key == "series_id" and value == 0 and not isinstance(value, bool):
            pass                # 0 = take the drama out of its series
        elif key == "series_id":
            _check_id("series_id", value)
            drama = db.get_item_ownership("drama", drama_id)
            ownership_service.check_series_assignment(principal, value,
                                                      drama.get("owner_user_id"))
        fields[key] = value

    series_id = fields.pop("series_id", None)
    if new_series_name is not None:   # first: a refused move writes nothing
        series_id = ownership_service.get_or_create_series_for(principal, new_series_name)
    moved = None
    if series_id == 0:
        ownership_service.unassign_drama_series(principal, drama_id)
    elif series_id is not None:
        if db.get_drama(drama_id).get("series_id") != series_id:
            moved = series_id
        ownership_service.assign_drama_series(principal, drama_id, series_id)
    if "source_url" in fields:
        fields["source_url"], fields["source_url_full"] = \
            source_link_service.split_source_url(fields["source_url"])
    if fields:
        db.update_drama(drama_id, **fields)
    if moved is not None:
        _inherit_series_defaults("inherit_moved", drama_id, moved)
    return library_service.get_library_drama(drama_id)


def _inherit_series_defaults(how, drama_id, series_id, **kw) -> None:
    """Best-effort: a create or move has already happened, so a failure here
    (the series deleted meanwhile, a stored default no longer valid) must not
    fail it or lose its other edits; the defaults can be applied later from
    the Series page."""
    from services import series_defaults_service   # circular import via series_service
    try:
        getattr(series_defaults_service, how)(drama_id, series_id, **kw)
    except Exception:
        log.warning("Could not apply series defaults to title %s", drama_id, exc_info=True)


def inherit_series_defaults_on_join(drama_id, series_id) -> None:
    """For paths that put a title in a series without update_drama_metadata
    (a site-named series from link details or the extension): the same
    fill-only-blanks inheritance a move gets."""
    _inherit_series_defaults("inherit_moved", drama_id, series_id)


def set_source_url_once(drama_id, url) -> bool:
    """Records where an import came from, only while the title has no link,
    so what the owner typed is never overwritten. Stores the display-safe
    form (no query, userinfo or token-like path), plus the raw link in
    source_url_full when cleaning changed it (source_link_service); a link
    that leaves nothing usable is ignored. Never raises: a link is a
    convenience and must not fail the import that called it."""
    safe, full = source_link_service.split_source_url(url)
    if not safe or len(safe) > MAX_URL_LEN:
        return False
    if len(full) > MAX_URL_LEN:
        full = ""
    try:
        # One conditional statement: a link the owner saves between a read
        # and a write would otherwise be overwritten by the import.
        with contextlib.closing(db.get_conn()) as conn:
            cur = conn.execute(
                "UPDATE dramas SET source_url = ?, source_url_full = ?, updated_at = ? "
                "WHERE id = ? AND TRIM(COALESCE(source_url, '')) = ''",
                (safe, full, datetime.datetime.utcnow().isoformat(), drama_id))
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        return False


_DELETE_CONFIRM_TEXT = "DELETE"
LEFTOVER_FILES_MESSAGE = ("The title was deleted from the library, but some of its files "
                           "could not be removed (a file may be in use). Close anything "
                           "using them and remove the leftover folder manually.")


def job_running_for_drama(drama_id, exclude_job_id=None) -> bool:
    """In-process jobs, plus fresh running/queued job_records rows written
    by another process (e.g. the CLI; the in-memory tracker only sees its
    own). exclude_job_id:
    a running job asking whether any other job is on its drama."""
    if (background_jobs.any_job_running_for_drama(drama_id, exclude_job_id=exclude_job_id)
            if exclude_job_id else background_jobs.any_job_running_for_drama(drama_id)):
        return True
    return job_store.fresh_active(
        job_ids={f"{prefix}{drama_id}" for prefix in background_jobs.DRAMA_JOB_PREFIXES},
        exclude=exclude_job_id)


def hard_delete_drama(drama_id) -> bool:
    """The single place a drama is actually removed, so a
    soft-delete can replace just this function. Order: rename the
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
            raise ServiceError("The title's files are in use, so it was not deleted. "
                               "Close anything using them and try again.") from e
        # A rename keeps the folder's old mtime; stamp it now so the startup
        # sweep (cleanup_stale_tombstones) never mistakes an in-flight delete
        # for a leftover.
        with contextlib.suppress(OSError):
            os.utime(tomb, None)
    try:
        db.delete_drama(drama_id)
    except Exception as e:
        if tomb is not None:
            try:
                os.rename(tomb, folder)
            except OSError:
                log.exception("Could not restore drama folder %s after a failed delete", folder)
        raise ServiceError("The title could not be deleted.") from e
    if db.get_drama(drama_id) is not None:
        if tomb is not None:
            try:
                os.rename(tomb, folder)
            except OSError:
                log.exception("Could not restore drama folder %s after a failed delete", folder)
        raise ServiceError("The title could not be deleted.")
    language_pack_service.forget_title(drama_id)
    vdb_names_service.forget_title(drama_id)
    if tomb is None:
        return False
    try:
        shutil.rmtree(tomb)
    except OSError:
        log.exception("Drama %s deleted but leftover folder %s could not be removed",
                      drama_id, tomb)
        return True
    return False


_TOMBSTONE_RE = re.compile(r"^\d+\.deleting-[0-9a-f]{8}$")
TOMBSTONE_MAX_AGE_SECONDS = 24 * 3600


def cleanup_stale_tombstones(max_age: float = TOMBSTONE_MAX_AGE_SECONDS, now: float = None) -> int:
    """Leftover cleanup: removes `<id>.deleting-<hex>` folders in DRAMAS_DIR
    that a delete renamed aside but could not remove, once they are older
    than max_age (a day), so an in-flight delete is never touched. Symlinks,
    anything else, and a tombstone whose drama row still exists (a failed
    delete whose rename-back also failed) are left alone. Returns how many were removed; never
    raises."""
    now = time.time() if now is None else now

    def stale_tombstone(name, path):
        if not _TOMBSTONE_RE.match(name) or not os.path.isdir(path):
            return False
        try:
            # A delete whose row delete failed AND whose rename-back also failed
            # leaves a live drama's files here: keep them for manual recovery.
            if db.get_drama(int(name.split(".", 1)[0])) is not None:
                log.warning("A leftover deleted-drama folder belongs to a drama that still "
                            "exists; it was kept")
                return False
        except (ValueError, OverflowError):
            # An absurdly long all-digit name can't be looked up; skip it and keep sweeping.
            log.warning("Could not check a leftover deleted-drama folder")
            return False
        return True

    # realpath: a dramas folder moved to another drive behind a link is still swept.
    return storage._sweep_folder(os.path.realpath(db.DRAMAS_DIR), stale_tombstone, max_age, now,
                                 set(), False)["removed"]


def delete_drama(drama_id, confirm=False, confirm_text="") -> dict:
    """Permanently deletes a drama and its folder. Order: unknown id ->
    NotFoundError (always, even without confirmation); then
    InvalidInputError unless `confirm is True` and `confirm_text` is
    exactly "DELETE"; then ConflictError
    if a job is running for the drama. Returns {"deleted": True,
    "drama_id": id}, plus a non-secret "warning" when the row is gone but
    leftover files could not be removed."""
    library_service.get_library_drama(drama_id)  # id check + existence
    if confirm is not True or confirm_text != _DELETE_CONFIRM_TEXT:
        raise InvalidInputError("Deleting a title needs confirm=true and confirm_text set to "
                                "the word DELETE, in capitals.")
    if job_running_for_drama(drama_id):
        raise ConflictError("A background job is still running for this title -- wait for it "
                            "to finish or cancel it before deleting.")
    leftover = hard_delete_drama(drama_id)
    result = {"deleted": True, "drama_id": drama_id}
    if leftover:
        result["warning"] = LEFTOVER_FILES_MESSAGE
    return result
