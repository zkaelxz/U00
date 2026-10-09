"""
services/library_admin_service.py -- the Library and Library tools pages'
destructive and admin actions (migration E0 remainder): bulk
status/tags/delete, bulk translate, export-all zip, full backup, backup of one person's items,
restore and storage cleanup.

Routes: api/routers/library_admin_routes.py. Rules this module keeps:
  - Destructive actions need `confirm is True` plus an exact typed word
    (the migration decision: match today's UI bar with a typed confirm).
    A wrong or missing confirm raises InvalidInputError before anything
    is read for writing, so nothing changes.
  - Delete refuses any drama with a running/queued job
    (drama_service.job_running_for_drama); delete and cleanup refuse
    while a backup/export runs or a restore is in progress. Restore
    refuses while ANY job runs, in this process or (via fresh job_records
    rows) another one, holds background_jobs' exclusive lock so no job
    starts meanwhile, and re-checks right before the folder swap.
  - A restore never takes sign-in state from the upload: the current
    users/permissions/audit tables, sources settings, backups/, browser
    profiles, source profiles and extension token are kept, and every
    session is revoked.
  - Writes are field-whitelisted: status and the organizational tags only,
    through db.update_drama / db.set_custom_tag.
  - Bulk calls return one result per requested id.
  - No result or error message carries a filesystem path. Finished export
    and backup files live in library-level folders excluded from backups;
    `latest_admin_artifact` returns name/size, and
    `admin_artifact_path` (server-side only) is what the download route
    streams.

No FastAPI import.
"""

import contextlib
import datetime
import io
import logging
import os
import re
import shutil
import tempfile
import time
import zipfile
import zlib
from typing import Optional

import background_jobs
import db
import storage
import subtitle_formats
import translate_engines
from core import Line, lines_to_bilingual_srt, lines_to_srt
from services import (drama_service, ownership_service, settings_service,
                      translate_service)
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

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
# Fixed prefixes: a file name never carries a user's name or email.
_ARTIFACT_PREFIX = {"backup": "baihe_library_backup", "export": "dramas_export",
                    "database": "library", "user_backup": "baihe_my_items_backup"}
_EXPORTABLE_STATUSES = ("translated", "dubbed", "exported")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def require_confirm(confirm, confirm_text, word: str, action: str):
    if confirm is not True or confirm_text != word:
        raise InvalidInputError(f"{action} needs confirm=true and confirm_text set to the "
                                f"word {word}, in capitals.")


def _check_ids(drama_ids) -> list:
    if not isinstance(drama_ids, (list, tuple)) or not drama_ids:
        raise InvalidInputError("Pick at least one drama.")
    if len(drama_ids) > MAX_BULK_IDS:
        raise InvalidInputError(f"At most {MAX_BULK_IDS} dramas per request.")
    out = []
    for did in drama_ids:
        if (isinstance(did, bool) or not isinstance(did, int)
                or not 1 <= did <= drama_service.MAX_ID):
            raise InvalidInputError("A drama id is a positive whole number.")
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
    if any(j.get("status") in ("running", "queued")
           for j in background_jobs.list_all_jobs().values()):
        return True
    cutoff = time.time() - drama_service.STALE_JOB_RECORD_SECONDS
    return any(r.get("status") in ("running", "queued") and (r.get("updated_at") or 0) >= cutoff
               for r in db.list_job_records())


def _job_id_running(job_id: str) -> bool:
    """This process's job, or a fresh running/queued job_records row."""
    job = background_jobs.get_status(job_id)
    if job and job.get("status") in ("running", "queued"):
        return True
    cutoff = time.time() - drama_service.STALE_JOB_RECORD_SECONDS
    rec = db.get_job_record(job_id)
    return bool(rec and rec.get("status") in ("running", "queued")
                and (rec.get("updated_at") or 0) >= cutoff)


@contextlib.contextmanager
def maintenance(action: str):
    """Held around bulk delete / storage cleanup: refuses (Conflict) while a
    restore holds the library, or a backup/export is reading it, and keeps
    a restore from starting until the operation ends."""
    if not background_jobs.enter_maintenance():
        raise ConflictError(f"A restore is in progress; {action} is not possible right now.")
    try:
        for job_id, label in ((BACKUP_JOB_ID, "backup"), (AUTO_BACKUP_JOB_ID, "backup"),
                              (USER_BACKUP_JOB_ID, "backup"),
                              (EXPORT_JOB_ID, "library export")):
            if _job_id_running(job_id):
                raise ConflictError(f"A {label} is running -- wait for it to finish before "
                                    f"{action}.")
        yield
    finally:
        background_jobs.exit_maintenance()


def refuse_during_maintenance(label: str):
    """A backup/export must not read the library while a bulk delete or
    storage cleanup is changing it (_maintenance refuses the reverse)."""
    if background_jobs.maintenance_active():
        raise ConflictError(f"Dramas are being deleted or storage cleaned; the {label} "
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
    return _bulk_apply(drama_ids, lambda did: db.update_drama(did, status=status))


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
    require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting dramas")
    with maintenance("deleting dramas"):
        results = []
        for did in ids:
            if db.get_drama(did) is None:
                results.append({"drama_id": did, "ok": False, "error": "not_found"})
                continue
            if drama_service.job_running_for_drama(did):
                results.append({"drama_id": did, "ok": False, "error": "job_running"})
                continue
            try:
                leftover = drama_service.hard_delete_drama(did)
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
    cancels a job the user started. A drama `principal` can't see (auth
    B2) is reported exactly like a missing one."""
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
            engines[did] = drama.get("translation_engine") or settings_service.get_default_engine()
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
        raise InvalidInputError("expected_engines maps drama ids to engine names.")
    out = {}
    for k, v in expected.items():
        try:
            did = int(k)
        except (TypeError, ValueError):
            raise InvalidInputError("expected_engines maps drama ids to engine names.") from None
        if not isinstance(v, str):
            raise InvalidInputError("expected_engines maps drama ids to engine names.")
        out[did] = v
    return out


def start_bulk_translate(drama_ids, default_locale: Optional[str] = None,
                         expected_engines=None, allow_paid_summary: bool = True,
                         principal=None, include_genre_notes: bool = True,
                         default_female_pronouns: bool = False) -> dict:
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
    if default_locale is None:
        default_locale = settings_service.get_preference("default_locale")
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
        raise InvalidInputError("None of the picked dramas are untranslated (status 'aligned').")
    api_keys = {}
    for engine in translate_engines.ENGINES:
        try:
            api_keys[engine] = translate_service.resolve_api_key(engine)
        except Exception:
            api_keys[engine] = None
    cap = settings_service.get_monthly_cap_usd()
    started = background_jobs.start_job(
        BULK_TRANSLATE_JOB_ID, wjs.run_bulk_series_translate_job,
        BULK_TRANSLATE_JOB_ID, queued, api_keys,
        default_locale=default_locale,
        ollama_base_url=settings_service.resolve_key("ollama_url") or None,
        gemini_free_tier=settings_service.get_gemini_free_tier(),
        models={}, monthly_cap=cap, expected_engines=expected_engines,
        allow_paid_summary=allow_paid_summary,
        include_genre_notes=include_genre_notes,
        default_female_pronouns=default_female_pronouns)
    if not started:
        raise ConflictError("A bulk translation is already running.")
    return {"job_id": BULK_TRANSLATE_JOB_ID, "queued": queued, "skipped": skipped}


# --------------------------------------------------------------------------
# export zip / backup (jobs)
# --------------------------------------------------------------------------

def _write_artifact(kind: str, suffix: str, fail_message: str, write) -> tuple:
    """Creates a hidden partial file in the kind's folder, calls
    write(tmp_path), then renames it into place. Returns (name, size).
    Any OSError, including creating the folder or removing the partial
    file, becomes a RuntimeError with fail_message (no path in it)."""
    tmp = None
    try:
        try:
            final_dir = _artifact_dir(kind, create=True)
            name = f"{_ARTIFACT_PREFIX[kind]}_{_timestamp()}{suffix}"
            fd, tmp = tempfile.mkstemp(prefix=".partial_", suffix=suffix, dir=final_dir)
            os.close(fd)
            write(tmp)
            final = os.path.join(final_dir, name)
            os.replace(tmp, final)
            tmp = None
            return name, os.path.getsize(final)
        finally:
            if tmp is not None and os.path.exists(tmp):
                os.remove(tmp)
    except OSError:
        raise RuntimeError(fail_message) from None


def _export_job(job_id, drama_ids):
    exported = 0

    def write(tmp):
        nonlocal exported
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for n, did in enumerate(drama_ids):
                background_jobs.update_progress(job_id, n / max(1, len(drama_ids)),
                                                f"Exporting {n + 1}/{len(drama_ids)}")
                d = db.get_drama(did)
                rows = db.load_lines(did) if d else []
                lns = [Line(idx=r["idx"], start=r["start"], end=r["end"], zh=r["zh"],
                            en=r["en"] or "", speaker=r.get("speaker"), sfx=bool(r.get("sfx")))
                       for r in rows]
                if not lns:
                    continue
                lns, _ = subtitle_formats.clamp_overlaps(lns)
                safe = re.sub(r"[^\w\- ]", "", d["title_en"] or d["title_zh"] or "") or str(did)
                folder = f"{did} {safe}"
                zf.writestr(f"{folder}/english.srt", lines_to_srt(lns, "en"))
                zf.writestr(f"{folder}/chinese.srt", lines_to_srt(lns, "zh"))
                zf.writestr(f"{folder}/bilingual.srt", lines_to_bilingual_srt(lns))
                dub_path = os.path.join(db.DRAMAS_DIR, str(did), "dub_track.wav")
                if os.path.isfile(dub_path) and not os.path.islink(dub_path):
                    zf.write(dub_path, f"{folder}/dub_track.wav")
                exported += 1

    name, _ = _write_artifact("export", ".zip", "The export could not be written.", write)
    background_jobs.set_result(job_id, {"exported": exported, "name": name})
    background_jobs.update_progress(job_id, 1.0, "Export ready.")


def start_export_zip(drama_ids=None) -> dict:
    """Job: zips english/chinese/bilingual SRT (+ dub track) for each
    translated/dubbed/exported drama -- the picked ones, or all of them.
    Returns {job_id, drama_ids}; with explicit ids also `results`, one per
    requested id ({drama_id, ok, error: not_found|not_translated})."""
    results = None
    if drama_ids is None:
        ids = [d["id"] for d in db.list_dramas() if d.get("status") in _EXPORTABLE_STATUSES]
    else:
        ids, results = [], []
        for did in _check_ids(drama_ids):
            drama = db.get_drama(did)
            if drama is None:
                results.append({"drama_id": did, "ok": False, "error": "not_found"})
            elif drama.get("status") not in _EXPORTABLE_STATUSES:
                results.append({"drama_id": did, "ok": False, "error": "not_translated"})
            else:
                ids.append(did)
                results.append({"drama_id": did, "ok": True})
    if not ids:
        raise InvalidInputError("No translated dramas to export.",
                                details={"results": results} if results is not None else None)
    refuse_during_maintenance("library export")
    _refuse_duplicate(EXPORT_JOB_ID, "library export")
    if not background_jobs.start_job(EXPORT_JOB_ID, _export_job, EXPORT_JOB_ID, ids,
                                     description="Library export"):
        raise ConflictError("A library export is already running.")
    out = {"job_id": EXPORT_JOB_ID, "drama_ids": ids}
    if results is not None:
        out["results"] = results
    return out


def _sanitized_snapshot(dest: str):
    """A consistent database snapshot with every auth session removed
    (secure_delete, so the session hashes aren't left in free pages) and
    folded out of WAL mode, so dest is one self-contained file."""
    import sqlite3
    db.snapshot_database(dest)
    try:
        conn = sqlite3.connect(dest, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                            "AND name = 'auth_sessions'").fetchone():
                conn.execute("DELETE FROM auth_sessions")
            conn.execute("PRAGMA journal_mode = DELETE")
        finally:
            conn.close()
    except sqlite3.Error:
        raise OSError("snapshot could not be prepared") from None


def _backup_excluded_top_level() -> tuple:
    """Top-level library entries a backup never contains: old backups and
    exports, saved site sign-ins, approved source profiles (a restore keeps
    the current ones either way), the browser-extension token and the
    sources raw-content cache (rebuildable: a missing file is a cache miss)."""
    from sources import store as src_store
    return wjs.restore_kept_names() + (os.path.basename(src_store.cache_dir()),)


def _backup_files(library_dir: str, skip: set, excluded: tuple) -> list:
    """(full path, archive name, size) for every media file a backup holds,
    in the order the zip is written."""
    found = []
    for root, dirs, files in os.walk(library_dir, topdown=True):
        if root == library_dir:
            dirs[:] = [d for d in dirs if d not in excluded]
            files = [f for f in files if f not in excluded]
        for fname in files:
            full = os.path.join(root, fname)
            if full in skip or fname == ".env" or os.path.islink(full):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            found.append((full, os.path.relpath(full, library_dir), size))
    return found


# Share of the progress bar the file copy uses; the rest is the database
# snapshot and manifest, which are written last.
_BACKUP_FILES_SHARE = 0.95
_BACKUP_PROGRESS_INTERVAL = 0.25


def write_backup_zip(dest: str, include_media: bool = True, manifest=None,
                     progress=None, should_cancel=None):
    """Writes a backup zip to `dest`: a sanitized library.db snapshot and,
    with include_media, every other library file except the excluded
    top-level entries (backups/, sign-ins, source profiles, extension
    token, source_cache/), the live database files, symlinks and any `.env`. `manifest`,
    if given, is called with the snapshot's path and returns bytes stored
    as manifest.json (so it describes exactly the database in the zip).
    `progress(fraction, message)`, if given, is called at most a few times a
    second while files are written. `should_cancel()`, if given, is checked
    between files; when true this raises background_jobs.JobCancelled (the
    caller removes the partial file)."""
    library_dir = db.LIBRARY_DIR
    skip = {db.DB_PATH, db.DB_PATH + "-wal", db.DB_PATH + "-shm"}
    excluded = _backup_excluded_top_level()

    def check_cancel():
        if should_cancel is not None and should_cancel():
            raise background_jobs.JobCancelled()

    with storage.job_workdir("snapshot") as snapdir:
        snap = os.path.join(snapdir, "library.db")
        check_cancel()
        _sanitized_snapshot(snap)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            if include_media:
                if progress is not None:
                    progress(0.0, "Counting files...")
                files = _backup_files(library_dir, skip, excluded)
                total_bytes = sum(size for _, _, size in files)
                done_bytes = 0
                last_report = 0.0
                for n, (full, arcname, size) in enumerate(files, 1):
                    check_cancel()
                    zf.write(full, arcname)
                    done_bytes += size
                    now = time.monotonic()
                    if progress is not None and now - last_report >= _BACKUP_PROGRESS_INTERVAL:
                        last_report = now
                        frac = done_bytes / total_bytes if total_bytes else n / len(files)
                        progress(frac * _BACKUP_FILES_SHARE,
                                 f"Backing up ({n} of {len(files)} files)")
            check_cancel()
            if progress is not None:
                progress(_BACKUP_FILES_SHARE, "Writing database snapshot...")
            zf.write(snap, "library.db")
            if manifest is not None:
                zf.writestr("manifest.json", manifest(snap))


def _backup_job(job_id):
    background_jobs.update_progress(job_id, 0.0, "Copying the database...")
    name, size = _write_artifact(
        "backup", ".zip", "The backup could not be written.",
        lambda dest: write_backup_zip(
            dest,
            progress=lambda frac, msg: background_jobs.update_progress(job_id, frac, msg),
            should_cancel=lambda: background_jobs.is_cancel_requested(job_id)))
    background_jobs.set_result(job_id, {"name": name, "size": size})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def start_backup() -> dict:
    """Job: full library backup zip (database snapshot + media)."""
    refuse_during_maintenance("backup")
    _refuse_duplicate(BACKUP_JOB_ID, "backup")
    if not background_jobs.start_job(BACKUP_JOB_ID, _backup_job, BACKUP_JOB_ID,
                                     description="Library backup"):
        raise ConflictError("A backup is already running.")
    return {"job_id": BACKUP_JOB_ID}


def _database_backup_job(job_id):
    name, size = _write_artifact("database", ".db", "The database backup could not be written.",
                                 _sanitized_snapshot)
    background_jobs.set_result(job_id, {"name": name, "size": size})
    background_jobs.update_progress(job_id, 1.0, "Database backup ready.")


def start_database_backup() -> dict:
    """Job: database-only backup (fast, small): one consistent library.db snapshot, auth sessions removed,
    no media. Fetch it with admin_artifact_path("database")."""
    refuse_during_maintenance("database backup")
    _refuse_duplicate(DATABASE_BACKUP_JOB_ID, "database backup")
    if not background_jobs.start_job(DATABASE_BACKUP_JOB_ID, _database_backup_job,
                                     DATABASE_BACKUP_JOB_ID, description="Database backup"):
        raise ConflictError("A database backup is already running.")
    return {"job_id": DATABASE_BACKUP_JOB_ID}


# --------------------------------------------------------------------------
# backup of one owner's items ("backup of just my stuff")
# --------------------------------------------------------------------------

# How the per-owner backup treats every library.db table. Default-deny: a
# table missing here is dropped from the copy at run time, and
# tests/test_user_backup.py fails until it is classified.
#   owned_dramas / owned_series: only the owner's rows (NULL owner = the PC
#       owner), owner column cleared (the target install's PC owner)
#   drama: rows of a kept drama only (a NULL drama_id is nobody's: dropped)
#   page: rows of a kept page only; series: rows of a kept series only
#   series_character: kept drama and kept series character only
#   user: rows of the owner only, user column cleared
#   profile_drama: rows of a kept drama on the default profile only
#   profiles: the default profile only (the one reader data uses)
#   style_scope: the learned style of a kept series only
#   keep: household-wide, nothing personal
#   empty: every row dropped
USER_BACKUP_TABLES = {
    "dramas": ("owned_dramas", "the owner's dramas"),
    "series": ("owned_series", "the owner's series"),
    "lines": ("drama", ""), "characters": ("drama", ""), "pages": ("drama", ""),
    "translation_notes": ("drama", ""), "line_emotions": ("drama", ""),
    "consistency_issues": ("drama", ""), "vocab_lookups": ("drama", ""),
    "usage_log": ("drama", ""), "line_history": ("drama", ""),
    "progress": ("profile_drama", "other household profiles' reading stays behind"),
    "personal_notes": ("profile_drama", "other household profiles' notes stay behind"),
    "reading_history": ("profile_drama", "other household profiles' history stays behind"),
    "translation_versions": ("drama", ""), "bug_reports": ("drama", ""),
    "wiki_entries": ("drama", ""), "edit_samples": ("drama", ""),
    "line_provenance": ("drama", ""), "metadata_research_results": ("drama", ""),
    "metadata_field_provenance": ("drama", ""),
    "voice_suggestion_dismissals": ("series_character", ""),
    "bubbles": ("page", ""),
    "glossary_terms": ("series", ""), "series_characters": ("series", ""),
    "translation_memory": ("series", ""),
    "translate_history": ("user", "the owner's standalone translations"),
    "profiles": ("profiles", "reader data is per library, on the default profile"),
    "style_profile": ("style_scope", "the global profile is learned from everyone's edits"),
    "known_titles": ("keep", "the household's title catalogue"),
    "presets": ("keep", "household workspace presets"),
    "users": ("empty", "auth"), "user_permissions": ("empty", "auth"),
    "auth_sessions": ("empty", "auth"), "audit_log": ("empty", "auth"),
    "bulk_jobs": ("empty", "provider batch ids of this PC's API accounts; a restored "
                           "in-flight batch could be polled again"),
    "bulk_job_lines": ("empty", "belongs to bulk_jobs"),
    "voice_bank": ("empty", "household clips; the clip files are not copied either"),
    "job_records": ("empty", "this PC's job history"),
    "speaker_merge_undos": ("empty", "short-lived undo records"),
    "job_checkpoints": ("empty", "this PC's job state"),
    "job_stage_timings": ("empty", "this PC's job history"),
    "gpu_lock": ("empty", "this PC's job state"),
    "result_cache": ("empty", "cached model output from every user's runs"),
    "metadata_research_cache": ("empty", "lookups from every user"),
    "app_settings": ("empty", "this PC's settings"),
    "assistant_backlog": ("empty", "this PC's maintenance notes"),
    "benchmark_cases": ("empty", "this PC's test set, may quote any drama"),
    "benchmark_runs": ("empty", "belongs to benchmark_cases"),
    "benchmark_sessions": ("empty", "this PC's benchmark runs"),
    "benchmark_results": ("empty", "belongs to benchmark_sessions"),
    "model_candidates": ("empty", "this PC's model choices"),
    "model_decisions": ("empty", "this PC's model choices"),
}
# Copies an old line-reference migration left behind (_backup_step2_<table>)
# hold rows of every drama; they are dropped from the copy.
_DROPPED_TABLE_PREFIX = "_backup_step2_"


def _user_backup_filter(path: str, owner_id) -> list:
    """Cuts the snapshot at `path` down to one owner's items (owner_id
    None: the PC owner, owner_user_id NULL), following
    USER_BACKUP_TABLES, and compacts it. Returns the kept drama ids.

    Series shared between users: a series goes with its owner, with its
    glossary, characters and translation memory. A drama is kept only
    when its owner is kept, so another user's drama in the owner's series
    stays behind; the owner's drama in someone else's series is kept
    without that series (series_id cleared, and links to that series'
    characters removed)."""
    import sqlite3
    try:
        conn = sqlite3.connect(path, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN")
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]

            def cols(t):
                return {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')}

            def keep_only(t, where, args=()):
                # IS NOT 1: a NULL (e.g. a NULL drama_id) counts as not kept.
                conn.execute(f'DELETE FROM "{t}" WHERE ({where}) IS NOT 1', args)

            for t in ("keep_dramas", "keep_series", "keep_pages", "keep_chars"):
                conn.execute(f"CREATE TEMP TABLE {t} (id INTEGER PRIMARY KEY)")
            conn.execute("INSERT INTO temp.keep_dramas SELECT id FROM dramas "
                         "WHERE owner_user_id IS ?", (owner_id,))
            conn.execute("INSERT INTO temp.keep_series SELECT id FROM series "
                         "WHERE owner_user_id IS ?", (owner_id,))
            default_profile = None
            if "profiles" in tables:
                default_profile = conn.execute("SELECT MIN(id) FROM profiles").fetchone()[0]
            in_dramas = "drama_id IN (SELECT id FROM temp.keep_dramas)"
            in_series = "series_id IN (SELECT id FROM temp.keep_series)"
            rules = {t: USER_BACKUP_TABLES.get(t, ("drop", ""))[0] for t in tables}
            for t in tables:
                if t.startswith(_DROPPED_TABLE_PREFIX) or rules[t] == "drop":
                    if not t.startswith(_DROPPED_TABLE_PREFIX):
                        log.warning("Per-user backup: unclassified table %s left out", t)
                    conn.execute(f'DROP TABLE "{t}"')
                    continue
                rule = rules[t]
                need = {"drama": "drama_id", "series_character": "drama_id", "page": "page_id",
                        "series": "series_id", "user": "user_id",
                        "profile_drama": "profile_id"}.get(rule)
                if rule == "empty" or (need and need not in cols(t)) or (
                        rule == "profile_drama" and "drama_id" not in cols(t)):
                    conn.execute(f'DELETE FROM "{t}"')
                elif rule == "profile_drama":
                    # A NULL profile_id means the default profile.
                    keep_only(t, in_dramas + " AND COALESCE(profile_id, ?) IS ?",
                              (default_profile, default_profile))
                elif rule in ("owned_dramas", "owned_series"):
                    keep = "keep_dramas" if rule == "owned_dramas" else "keep_series"
                    keep_only(t, f"id IN (SELECT id FROM temp.{keep})")
                    conn.execute(f'UPDATE "{t}" SET owner_user_id = NULL')
                elif rule in ("drama", "series"):
                    keep_only(t, in_dramas if rule == "drama" else in_series)
                elif rule == "user":
                    keep_only(t, "user_id IS ?", (owner_id,))
                    conn.execute(f'UPDATE "{t}" SET user_id = NULL')
            # Second pass: rules that depend on rows kept above.
            if "pages" in rules:
                conn.execute("INSERT INTO temp.keep_pages SELECT id FROM pages")
            if "series_characters" in rules:
                conn.execute("INSERT INTO temp.keep_chars SELECT id FROM series_characters")
            for t, rule in rules.items():
                if rule == "page" and "page_id" in cols(t):
                    keep_only(t, "page_id IN (SELECT id FROM temp.keep_pages)")
                elif rule == "series_character" and "drama_id" in cols(t):
                    keep_only(t, in_dramas + " AND series_character_id IN "
                                 "(SELECT id FROM temp.keep_chars)")
                elif rule == "style_scope":
                    keep_only(t, "scope IN (SELECT 'series:' || id FROM temp.keep_series)")
                elif rule == "profiles":
                    keep_only(t, "id IS ?", (default_profile,))
            if "series_id" in cols("dramas"):
                conn.execute(f"UPDATE dramas SET series_id = NULL WHERE series_id IS NOT NULL "
                             f"AND NOT {in_series}")
            if "series_character_id" in cols("characters"):
                conn.execute("UPDATE characters SET series_character_id = NULL "
                             "WHERE series_character_id IS NOT NULL AND series_character_id "
                             "NOT IN (SELECT id FROM temp.keep_chars)")
            # Each AUTOINCREMENT counter is reset to the highest kept id, so
            # it doesn't tell how many rows the whole household has.
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone():
                live = set(r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"))
                for (name,) in conn.execute("SELECT name FROM sqlite_sequence").fetchall():
                    top = (conn.execute(f'SELECT MAX(rowid) FROM "{name}"').fetchone()[0]
                           if name in live else None)
                    if top is None:
                        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (name,))
                    else:
                        conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                                     (top, name))
            for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                        "AND name LIKE 'sqlite_stat%'").fetchall():
                conn.execute(f'DROP TABLE "{name}"')
            kept = [r[0] for r in conn.execute("SELECT id FROM temp.keep_dramas ORDER BY id")]
            conn.execute("COMMIT")
            conn.execute("VACUUM")
            return kept
        finally:
            conn.close()
    except sqlite3.Error:
        raise OSError("snapshot could not be filtered") from None


def _drama_media_files(drama_id: int):
    """(full path, zip name) for every regular file in the drama's own
    folder. The folder must be a real directory directly under dramas/;
    symlinks (files or folders) and any `.env` are skipped."""
    base = db.DRAMAS_DIR
    folder = os.path.join(base, str(drama_id))
    if (os.path.islink(folder) or not os.path.isdir(folder)
            or os.path.dirname(os.path.realpath(folder)) != os.path.realpath(base)):
        return
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for fname in files:
            full = os.path.join(root, fname)
            if fname == ".env" or os.path.islink(full) or not os.path.isfile(full):
                continue
            yield full, os.path.relpath(full, db.LIBRARY_DIR).replace(os.sep, "/")


def write_user_backup_zip(dest: str, owner_id=None, cancelled=None):
    """A backup zip of one owner's items (owner_id None: the PC owner):
    the snapshot cut down by _user_backup_filter, plus only the kept
    dramas' own folders under dramas/. Nothing else from the library
    folder (voice bank, benchmark files, sources.db, other dramas'
    folders) is included. The live library is only read. `cancelled`, if
    given, is polled between files; when it returns True the job stops
    with background_jobs.JobCancelled (the caller removes the partial file)."""
    def check():
        if cancelled is not None and cancelled():
            raise background_jobs.JobCancelled()

    with storage.job_workdir("snapshot") as snapdir:
        snap = os.path.join(snapdir, "library.db")
        _sanitized_snapshot(snap)
        kept = _user_backup_filter(snap, owner_id)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            for did in kept:
                for full, arcname in _drama_media_files(did):
                    check()
                    zf.write(full, arcname)
            zf.write(snap, "library.db")
    check()


_USER_BACKUP_NAME = re.compile(r"baihe_my_items_backup_\d{8}_\d{6}\.zip")


def _remove_older_user_backups(keep: str):
    """Only the newest per-person backup is kept: each one holds a person's
    private items. Only regular files with the generated name are removed."""
    folder = _artifact_dir("user_backup", create=False)
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if (name == keep or not _USER_BACKUP_NAME.fullmatch(name) or os.path.islink(path)
                or not os.path.isfile(path)):
            continue
        try:
            os.remove(path)
        except OSError:
            log.warning("An older per-person backup could not be removed")


def _user_backup_job(job_id, owner_id):
    name, size = _write_artifact(
        "user_backup", ".zip", "The backup could not be written.",
        lambda dest: write_user_backup_zip(
            dest, owner_id, cancelled=lambda: background_jobs.is_cancel_requested(job_id)))
    _remove_older_user_backups(name)
    background_jobs.set_result(job_id, {"name": name, "size": size})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def start_user_backup(user_id=None) -> dict:
    """Job: backup zip of one owner's dramas and series, restorable into a
    fresh install with restore_backup. user_id None: the items owned at
    the PC (no owner). Unknown user: NotFoundError. Fetch the file with
    admin_artifact_path("user_backup")."""
    if user_id is not None:
        if (isinstance(user_id, bool) or not isinstance(user_id, int)
                or not 1 <= user_id <= drama_service.MAX_ID):
            raise InvalidInputError("A user id is a positive whole number.")
        if db.auth_get_user(user_id) is None:
            raise NotFoundError("No such user.")
    refuse_during_maintenance("backup")
    _refuse_duplicate(USER_BACKUP_JOB_ID, "backup")
    if not background_jobs.start_job(USER_BACKUP_JOB_ID, _user_backup_job, USER_BACKUP_JOB_ID,
                                     user_id, description="Backup of one person's items"):
        raise ConflictError("A backup is already running.")
    return {"job_id": USER_BACKUP_JOB_ID}


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------

_BAD_ZIP = "This file is not a valid Baihe library backup."
_BUSY = ("A background job is running -- wait for it to finish or cancel it before "
         "restoring.")

# Restore-specific limits for an uploaded (network) zip, tighter than
# workspace_job_service's generous guardrails: expanded total at
# most max(factor x upload size, 2 x current library size, 1 GiB), a
# member-count cap, and enough free disk for the expanded total plus a
# margin before anything is extracted.
RESTORE_MAX_MEMBERS = 100_000
_RESTORE_EXPANSION_FACTOR = 10
_RESTORE_MIN_TOTAL_BYTES = 1024 ** 3
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


def _unsafe_member(info: zipfile.ZipInfo) -> bool:
    name = info.filename
    norm = name.replace("\\", "/")
    if not norm or "\x00" in norm or norm.startswith("/") or re.match(r"^[A-Za-z]:", norm):
        return True
    parts = (norm[:-1] if info.is_dir() else norm).split("/")
    # "." / empty parts are dropped by zipfile (./backups -> backups), and a
    # part ending in a dot or space names the same file as without it on
    # Windows -- either could slip a member past the kept-name filter.
    if any(p in ("", ".", "..") or p.endswith((".", " ")) for p in parts):
        return True
    return (info.external_attr >> 16) & 0o170000 == 0o120000  # symlink entry


def _library_size() -> int:
    """Bytes under LIBRARY_DIR, not counting the entries a backup skips."""
    excluded = _backup_excluded_top_level()
    root_dir = db.LIBRARY_DIR
    total = 0
    for root, dirs, files in os.walk(root_dir, topdown=True):
        if root == root_dir:
            dirs[:] = [d for d in dirs if d not in excluded]
        for fname in files:
            full = os.path.join(root, fname)
            try:
                if not os.path.islink(full):
                    total += os.path.getsize(full)
            except OSError:
                continue
    return total


def _restore_total_cap(upload_size: int) -> int:
    return max(_RESTORE_EXPANSION_FACTOR * upload_size, 2 * _library_size(),
               _RESTORE_MIN_TOTAL_BYTES)


def validate_backup_zip(zip_bytes) -> None:
    """Every check restore runs before touching the library: a real zip,
    library.db present, no absolute/traversal/symlink/encrypted member,
    within the upload limits and the tighter restore limits above
    (member count, expanded total, free disk), no corrupt member.
    Fixed-text InvalidInputError on failure (no member names or paths
    echoed). The library.db itself is checked with SQLite after extraction
    (workspace_job_service.validate_staged_library_db)."""
    if not isinstance(zip_bytes, (bytes, bytearray)) or not zip_bytes:
        raise InvalidInputError("Upload a backup .zip file.")
    _validate_zip(io.BytesIO(zip_bytes), len(zip_bytes), check_disk=True)


def validate_backup_file(path: str, check_disk: bool = True, check_limits: bool = True) -> None:
    """validate_backup_zip for a zip on disk (read in place, never loaded
    whole into memory). check_disk=False skips the free-space check, for
    a backup that was just written rather than one about to be restored
    whole. check_limits=False skips the upload size/count caps (member
    count, per-member and expanded-total size) for the app's own snapshot,
    which can legitimately be larger than any upload; the structural
    checks (library.db present, no unsafe/symlink/encrypted member, CRCs)
    always run, and a caller extracting members must cap what it extracts."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            _validate_zip(fh, size, check_disk=check_disk, check_limits=check_limits)
    except OSError:
        raise InvalidInputError(_BAD_ZIP) from None


def _validate_zip(source, size: int, check_disk: bool, check_limits: bool = True) -> None:
    try:
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
            if check_limits and len(infos) > min(wjs.MAX_RESTORE_MEMBERS, RESTORE_MAX_MEMBERS):
                raise InvalidInputError("The backup has too many files; it looks corrupted "
                                        "or unsafe to extract.")
            if "library.db" not in zf.namelist():
                raise InvalidInputError(_BAD_ZIP + " (no library.db inside).")
            total_cap = (min(wjs.MAX_RESTORE_TOTAL_BYTES, _restore_total_cap(size))
                         if check_limits else None)
            total = 0
            for info in infos:
                if _unsafe_member(info):
                    raise InvalidInputError("The backup contains an unsafe file path.")
                if info.flag_bits & 0x1:
                    raise InvalidInputError("The backup contains an encrypted file.")
                if check_limits and info.file_size > wjs.MAX_RESTORE_MEMBER_BYTES:
                    raise InvalidInputError("The backup contains a file that is too large; it "
                                            "looks corrupted or unsafe to extract.")
                total += info.file_size
                if check_limits and total > total_cap:
                    raise InvalidInputError("The backup would expand too large; it looks "
                                            "corrupted or unsafe to extract.")
            if check_disk and not has_disk_room(os.path.dirname(os.path.abspath(db.LIBRARY_DIR)),
                                                 total):
                raise InvalidInputError("Not enough free disk space to restore this backup.")
            if zf.testzip() is not None:
                raise InvalidInputError("The backup zip is corrupted.")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError, NotImplementedError,
            RuntimeError, zlib.error):
        raise InvalidInputError(_BAD_ZIP) from None


def _count_sessions() -> int:
    return sum(len(db.auth_list_sessions(u["id"])) for u in db.auth_list_users())


def restore_backup(zip_bytes, confirm=False, confirm_text="", actor_id=None) -> dict:
    """Replaces the whole library with an uploaded backup. Order: confirm
    (confirm=True, confirm_text "RESTORE") -> take background_jobs'
    exclusive hold (refused if any job runs here; no job can start until
    the restore ends) -> refuse if another process has a fresh running
    job -> full validation -> workspace_job_service.restore_library_backup
    (staging extract, SQLite checks, schema migration of the staged
    library.db, current auth tables and sources settings kept, job and
    auth/settings-change re-check right before the rename,
    rename-aside, rename-in, roll back on failure; backups/ and the other
    kept entries carried across). Nothing is touched unless every check
    passes. Afterwards every sign-in session is revoked and an audit entry
    is written (attributed to actor_id, the signed-in user, if given).
    Returns {restored, sessions_revoked}."""
    from services import auth_service
    require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a backup")
    if not background_jobs.acquire_exclusive("Library restore"):
        raise ConflictError(_BUSY)
    try:
        if any_job_running():
            raise ConflictError(_BUSY)
        validate_backup_zip(zip_bytes)
        revoked = _count_sessions()

        def _recheck():
            if any_job_running():
                raise ConflictError(_BUSY)
            # A finished job's thread can still be writing to the database
            # that is about to be renamed aside.
            if not background_jobs.wait_for_job_threads(10.0):
                raise ConflictError("A background job is still finishing; try again in a moment.")

        try:
            wjs.restore_library_backup(bytes(zip_bytes), db.LIBRARY_DIR, before_swap=_recheck)
        except ServiceError:
            raise
        except (ValueError, zipfile.BadZipFile, RuntimeError, zlib.error) as e:
            msg = str(e) if str(e) in (wjs.BAD_LIBRARY_DB, wjs.BAD_SOURCES_DB) else _BAD_ZIP
            raise InvalidInputError(msg) from None
        except OSError as e:
            log.exception("Library restore failed")
            raise ServiceError("The restore could not be completed; the current library was "
                               "left in place.") from e
        auth_service.write_audit(actor_id, "library.restore",
                                 f"library restored from backup; sessions revoked: {revoked}")
    finally:
        background_jobs.release_exclusive()
    return {"restored": True, "sessions_revoked": revoked}


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
    scan = storage.scan_library_storage(db.LIBRARY_DIR, [d["id"] for d in db.list_dramas()])
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
            if drama_service.job_running_for_drama(did):
                results.append({"drama_id": did, "ok": False, "error": "job_running"})
                continue
            r = storage.clean_drama_storage(os.path.join(db.DRAMAS_DIR, str(did)), cats)
            freed += r["freed_bytes"]
            results.append({"drama_id": did, "ok": True, "freed_bytes": r["freed_bytes"]})
        return {"preset": preset, "freed_bytes": freed, "results": results}
