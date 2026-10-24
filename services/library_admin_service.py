"""
services/library_admin_service.py -- the Library and Library tools pages'
shared admin guards and bulk actions: the typed-confirm check, the maintenance
hold, duplicate-job refusal, finished-artifact lookup, bulk status/tags/delete,
bulk translate and storage cleanup.

Routes: api/routers/library_admin_routes.py. Backup, export and restore live in
library_backup_service, library_user_backup_service and
library_backup_restore_service, which build on this module. Rules this module
keeps:
  - Destructive actions need `confirm is True` plus an exact typed word
    (the migration decision: match today's UI bar with a typed confirm).
    A wrong or missing confirm raises InvalidInputError before anything
    is read for writing, so nothing changes.
  - Delete refuses any drama with a running/queued job
    (drama_service.job_running_for_drama); delete and cleanup refuse
    while a backup/export runs or a restore is in progress.
  - Writes are field-whitelisted: status and the organizational tags only,
    through db.update_drama / db.set_custom_tag.
  - Bulk calls return one result per requested id.
  - No result or error message carries a filesystem path.
    `latest_admin_artifact` returns name/size, and
    `admin_artifact_path` (server-side only) is what the download route
    streams.

No FastAPI import.
"""

import contextlib
import datetime
import os
import re
import shutil
from typing import Optional

import background_jobs
from jobs import job_launch
from lib import ids as lib_ids
import db
from jobs import job_store
import storage
import translate_engines
from services import (drama_service, keep_original_service, ownership_service, paid_job_service,
                      settings_service, translate_run_service, translate_service)
from services import library_translated_guard
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

STATUSES = ("not started", "aligned", "translated", "dubbed", "exported")
MAX_BULK_IDS = 500

DELETE_CONFIRM_TEXT = "DELETE"
RESTORE_CONFIRM_TEXT = "RESTORE"
CLEAN_CONFIRM_TEXT = "CLEAN"

BULK_TRANSLATE_JOB_ID = "bulk_series_translate"
EXPORT_JOB_ID = "library_export_zip"
BACKUP_JOB_ID = "library_backup"
DATABASE_BACKUP_JOB_ID = "library_db_backup"
USER_BACKUP_JOB_ID = "library_user_backup"
AUTO_BACKUP_JOB_ID = "library_auto_backup"  # services/auto_backup_service.py

# Both under LIBRARY_DIR/backups, which a full backup already skips, so an
# export or old backup is never zipped into the next backup.
ARTIFACT_SUBDIRS = {"backup": ("backups",), "export": ("backups", "exports"),
                     "database": ("backups", "database"),
                     "user_backup": ("backups", "user_backups")}
_ARTIFACT_EXT = {"backup": ".zip", "export": ".zip", "database": ".db", "user_backup": ".zip"}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def require_confirm(confirm, confirm_text, word: str, action: str):
    if confirm is not True or confirm_text != word:
        raise InvalidInputError(f"{action} needs confirm=true and confirm_text set to the "
                                f"word {word}, in capitals.")


def _check_ids(drama_ids) -> list:
    if not isinstance(drama_ids, (list, tuple)) or not drama_ids:
        raise InvalidInputError("Pick at least one title.")
    if len(drama_ids) > MAX_BULK_IDS:
        raise InvalidInputError(f"At most {MAX_BULK_IDS} titles per request.")
    out = []
    for did in drama_ids:
        lib_ids.check_id(did, "A title id is a positive whole number.")
        if did not in out:
            out.append(did)
    return out


def _bulk_apply(drama_ids, fn) -> dict:
    ids = _check_ids(drama_ids)
    results = []
    for did in ids:
        if db.get_drama(did) is None:
            results.append({"drama_id": did, "ok": False, "error": "not_found"})
            continue
        fn(did)
        results.append({"drama_id": did, "ok": True})
    return {"results": results, "updated": sum(r["ok"] for r in results)}


def any_job_running() -> bool:
    """In-process running/queued jobs, plus fresh running/queued
    job_records rows from another process (same staleness rule as
    drama_service.job_running_for_drama)."""
    return job_store.fresh_active()


def _job_id_running(job_id: str) -> bool:
    """This process's job, or a fresh running/queued job_records row."""
    return job_store.fresh_active(job_ids={job_id})


@contextlib.contextmanager
def maintenance(action: str):
    """Held around bulk delete / storage cleanup: refuses (Conflict) while a
    restore holds the library, or a backup/export is reading it, and keeps
    a restore from starting until the operation ends."""
    if not job_launch.enter_maintenance():
        raise ConflictError(f"A restore is in progress; {action} is not possible right now.")
    try:
        for job_id, label in ((BACKUP_JOB_ID, "backup"), (AUTO_BACKUP_JOB_ID, "backup"),
                              (USER_BACKUP_JOB_ID, "backup"),
                              (EXPORT_JOB_ID, "library export")):
            if _job_id_running(job_id):
                raise ConflictError(f"A {label} is running, so {action} is not possible "
                                    f"right now. Try again when the {label} finishes.")
        yield
    finally:
        job_launch.exit_maintenance()


def refuse_during_maintenance(label: str):
    """A backup/export must not read the library while a bulk delete or
    storage cleanup is changing it (_maintenance refuses the reverse)."""
    if job_launch.maintenance_active():
        raise ConflictError(f"Titles are being deleted or storage cleaned; the {label} "
                            f"can start when that finishes.")


def _refuse_duplicate(job_id: str, label: str):
    job = background_jobs.get_status(job_id)
    if job and job["status"] in ("running", "queued"):
        raise ConflictError(f"A {label} is already running.")


def _artifact_dir(kind: str, create: bool) -> str:
    if kind not in ARTIFACT_SUBDIRS:
        raise InvalidInputError("Unknown artifact kind.")
    path = os.path.join(db.LIBRARY_DIR, *ARTIFACT_SUBDIRS[kind])
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def _timestamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def admin_artifact_path(kind: str) -> dict:
    """Newest finished file of `kind` ("backup"/"export"/"database"): {path
    (SERVER-SIDE ONLY, never put in a response), name, size}. Symlinks
    and anything outside the folder are ignored."""
    base = _artifact_dir(kind, create=False)
    base_real = os.path.realpath(base)
    best = None
    try:
        names = os.listdir(base)
    except OSError:
        raise NotFoundError("No artifact available.")
    for name in names:
        if not name.endswith(_ARTIFACT_EXT[kind]) or name.startswith("."):
            continue
        path = os.path.join(base, name)
        if (os.path.islink(path) or not os.path.isfile(path)
                or os.path.dirname(os.path.realpath(path)) != base_real):
            continue
        mtime = os.path.getmtime(path)
        if best is None or mtime > best[0]:
            best = (mtime, name, path)
    if best is None:
        raise NotFoundError("No artifact available.")
    return {"path": best[2], "name": best[1], "size": os.path.getsize(best[2])}


def latest_admin_artifact(kind: str) -> dict:
    """Same as admin_artifact_path without the path: {kind, name, size}."""
    info = admin_artifact_path(kind)
    return {"kind": kind, "name": info["name"], "size": info["size"]}


# --------------------------------------------------------------------------
# bulk status / tags / delete / translate
# --------------------------------------------------------------------------

def bulk_set_status(drama_ids, status: str) -> dict:
    """Sets only `status` on each drama. Per-id results."""
    if status not in STATUSES:
        raise InvalidInputError("Unknown status.", details={"allowed": list(STATUSES)})
    return library_translated_guard.set_status(_bulk_apply, drama_ids, status)


def bulk_set_tags(drama_ids, tag: str, present: bool) -> dict:
    """Adds (present=True) or removes one organizational list tag
    (db.ORGANIZATIONAL_TAGS), leaving every other tag untouched."""
    if tag not in db.ORGANIZATIONAL_TAGS:
        raise InvalidInputError("Unknown list.", details={"allowed": list(db.ORGANIZATIONAL_TAGS)})
    if not isinstance(present, bool):
        raise InvalidInputError("present must be true or false.")
    return _bulk_apply(drama_ids, lambda did: db.set_custom_tag(did, tag, present))


def bulk_delete(drama_ids, confirm=False, confirm_text="") -> dict:
    """Permanently deletes each drama (drama_service._hard_delete_drama).
    Needs confirm=True and confirm_text "DELETE" (the same word the
    single-drama delete uses); checked before anything is touched. A drama
    with a running/queued job is skipped with error "job_running"."""
    ids = _check_ids(drama_ids)
    require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting titles")
    with maintenance("deleting titles"):
        results = []
        for did in ids:
            if db.get_drama(did) is None:
                results.append({"drama_id": did, "ok": False, "error": "not_found"})
                continue
            try:
                with job_launch.hold_title(did, "job_running"):
                    if drama_service.job_running_for_drama(did):
                        raise job_launch.TitleBusy("job_running")
                    leftover = drama_service.hard_delete_drama(did)
            except job_launch.TitleBusy:
                results.append({"drama_id": did, "ok": False, "error": "job_running"})
                continue
            except ServiceError as e:
                results.append({"drama_id": did, "ok": False, "error": "delete_failed",
                                "message": str(e)})
                continue
            entry = {"drama_id": did, "ok": True}
            if leftover:
                entry["warning"] = drama_service.LEFTOVER_FILES_MESSAGE
            results.append(entry)
        return {"results": results, "deleted": sum(r["ok"] for r in results)}


def _bulk_translate_plan(ids, principal=None):
    """(queued ids, skipped [{drama_id, reason}], {id: engine}) for already
    checked ids. A drama with any running or queued job (e.g. a translate
    waiting for the GPU) is skipped, so the bulk job never adopts or
    cancels a job the user started. A drama `principal` can't see is reported exactly like a missing one."""
    queued, skipped, engines = [], [], {}
    for did in ids:
        drama = db.get_drama(did)
        if drama is None or not ownership_service.can_edit_drama(principal, did):
            skipped.append({"drama_id": did, "reason": "not_found"})
        elif drama.get("status") != "aligned":
            skipped.append({"drama_id": did, "reason": "not_aligned"})
        elif drama_service.job_running_for_drama(did):
            skipped.append({"drama_id": did, "reason": "job_running"})
        else:
            queued.append(did)
            engines[did] = drama.get("translation_engine") or settings_service.get("default_engine")
    return queued, skipped, engines


def bulk_translate_engines(drama_ids, principal=None) -> dict:
    """The engines start_bulk_translate(drama_ids) would use:
    {engines: sorted set, by_drama: {drama_id: engine}} from each queued
    drama's saved translation_engine (default "claude"). For the route
    layer's per-engine permission check; pass by_drama back as
    start_bulk_translate(expected_engines=...)."""
    by_drama = _bulk_translate_plan(_check_ids(drama_ids), principal)[2]
    return {"engines": sorted(set(by_drama.values())), "by_drama": by_drama}


def _check_expected_engines(expected) -> dict:
    if not isinstance(expected, dict) or len(expected) > MAX_BULK_IDS:
        raise InvalidInputError("expected_engines maps title ids to engine names.")
    out = {}
    for k, v in expected.items():
        try:
            did = int(k)
        except (TypeError, ValueError):
            raise InvalidInputError("expected_engines maps title ids to engine names.") from None
        if not isinstance(v, str):
            raise InvalidInputError("expected_engines maps title ids to engine names.")
        out[did] = v
    return out


def start_bulk_translate(drama_ids, default_locale: Optional[str] = None,
                         expected_engines=None, allow_paid_summary: bool = True,
                         principal=None, include_genre_notes: bool = None,
                         default_female_pronouns: bool = None) -> dict:
    """Starts the existing bulk-series translate job
    (workspace_job_service.run_bulk_series_translate_job) for the picked
    dramas whose status is "aligned" and
    that have no running or queued job.
    Keys, Ollama URL, monthly cap and Gemini free tier come from Settings
    server-side. expected_engines ({drama_id: engine}, from
    bulk_translate_engines): a drama whose engine differs now, or later
    when the job reaches it, is skipped ("engine_changed"), so the engines
    a caller was authorized for are the only ones used; allow_paid_summary=
    False (no engines.paid) likewise skips a cloud episode-summary engine.
    Returns {job_id, queued: [ids], skipped: [{drama_id, reason}]}."""
    ids = _check_ids(drama_ids)
    locale_is_explicit = default_locale is not None
    if default_locale is None:
        default_locale = settings_service.get("default_locale")
    if not isinstance(default_locale, str) or not re.fullmatch(r"[A-Za-z]{2}(-[A-Za-z]{2})?",
                                                               default_locale):
        raise InvalidInputError("default_locale looks like en-US.")
    _refuse_duplicate(BULK_TRANSLATE_JOB_ID, "bulk translation")
    queued, skipped, engine_by_id = _bulk_translate_plan(ids, principal)
    if expected_engines is not None:
        expected = _check_expected_engines(expected_engines)
        for did in list(queued):
            if expected.get(did) != engine_by_id[did]:
                queued.remove(did)
                skipped.append({"drama_id": did, "reason": "engine_changed"})
        expected_engines = {did: expected[did] for did in queued}
    if not queued:
        raise InvalidInputError("None of the picked titles are untranslated (status 'aligned').")
    api_keys = {}
    for engine in translate_engines.ENGINES:
        try:
            api_keys[engine] = translate_service.resolve_api_key(engine)
        except Exception:
            api_keys[engine] = None
    cap = paid_job_service.require_monthly_cap(engine_by_id[d] for d in queued)
    started = job_launch.start_job(
        BULK_TRANSLATE_JOB_ID, wjs.run_bulk_series_translate_job,
        BULK_TRANSLATE_JOB_ID, queued, api_keys,
        default_locale=default_locale,
        ollama_base_url=settings_service.ollama_url(),
        gemini_free_tier=settings_service.get("gemini_free_tier"),
        models={}, monthly_cap=cap, expected_engines=expected_engines,
        allow_paid_summary=allow_paid_summary,
        include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns, principal=principal,
        locale_is_explicit=locale_is_explicit, line_fields=("en", "flag", "flag_note"))
    if not started:
        raise ConflictError("A bulk translation is already running.")
    for did in queued:
        translate_run_service.save_style_toggles(did, include_genre_notes,
                                                 default_female_pronouns)
    return {"job_id": BULK_TRANSLATE_JOB_ID, "queued": queued, "skipped": skipped}


RESTORE_DISK_MARGIN_BYTES = 256 * 1024 ** 2


def has_disk_room(folder: str, need: int) -> bool:
    """Whether `folder`'s drive has `need` bytes plus RESTORE_DISK_MARGIN_BYTES
    free. True when the free space can't be read, so a drive that doesn't
    report it never blocks a restore."""
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return True
    return free >= need + RESTORE_DISK_MARGIN_BYTES


# --------------------------------------------------------------------------
# storage cleanup
# --------------------------------------------------------------------------

def _check_preset(preset: str) -> list:
    if preset not in storage.STORAGE_QUALITY_PRESETS:
        raise InvalidInputError("Unknown storage preset.",
                                details={"allowed": list(storage.STORAGE_QUALITY_PRESETS)})
    return storage.categories_for_preset(preset)


def storage_scan(preset: str = "balanced") -> dict:
    """Dry run: what `storage_cleanup(preset, ...)` would remove, with
    sizes, per category and per drama (no paths, nothing removed)."""
    cats = _check_preset(preset)
    scan = keep_original_service.scan_library()
    per_drama = []
    for d in scan["per_drama"]:
        would = sum(d["categories"].get(k, 0) for k in cats)
        per_drama.append({"drama_id": d["drama_id"], "total_bytes": d["total_bytes"],
                          "would_free_bytes": would,
                          "job_running": drama_service.job_running_for_drama(d["drama_id"])})
    return {
        "preset": preset, "categories_to_clean": cats,
        "total_bytes": scan["total_bytes"], "reclaimable_bytes": scan["reclaimable_bytes"],
        "would_free_bytes": sum(scan["categories"].get(k, 0) for k in cats),
        "categories": [{"key": k, "label": cfg["label"], "note": cfg["note"],
                        "bytes": scan["categories"].get(k, 0), "selected": k in cats}
                       for k, cfg in storage.CLEANABLE_CATEGORIES.items()],
        "per_drama": per_drama,
    }


def storage_cleanup(preset: str, confirm=False, confirm_text="") -> dict:
    """Applies the preset's cleanup to every drama folder. Needs
    confirm=True and confirm_text "CLEAN". A drama with a running job is
    skipped. Returns per-drama freed bytes and the total."""
    cats = _check_preset(preset)
    require_confirm(confirm, confirm_text, CLEAN_CONFIRM_TEXT, "Cleaning storage")
    with maintenance("cleaning storage"):
        results, freed = [], 0
        for d in db.list_dramas():
            did = d["id"]
            try:
                with job_launch.hold_title(did, "job_running"):
                    if drama_service.job_running_for_drama(did):
                        raise job_launch.TitleBusy("job_running")
                    r = keep_original_service.clean_title_storage(did, cats)
            except job_launch.TitleBusy:
                results.append({"drama_id": did, "ok": False, "error": "job_running"})
                continue
            freed += r["freed_bytes"]
            results.append({"drama_id": did, "ok": True, "freed_bytes": r["freed_bytes"]})
        return {"preset": preset, "freed_bytes": freed, "results": results}
