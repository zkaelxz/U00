"""
services/library_admin_service.py -- the Library tab's destructive and
admin actions (migration E0 remainder): bulk status/tags/delete, bulk
translate, export-all zip, full backup, restore and storage cleanup.

Service half only; no router yet. Rules this module keeps:
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
    `admin_artifact_path` (server-side only) is what a future download
    route streams.

No Streamlit or FastAPI import.
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

BULK_TRANSLATE_JOB_ID = "bulk_series_translate"  # same id the Streamlit tab uses
EXPORT_JOB_ID = "library_export_zip"
BACKUP_JOB_ID = "library_backup"
DATABASE_BACKUP_JOB_ID = "library_db_backup"
AUTO_BACKUP_JOB_ID = "library_auto_backup"  # services/auto_backup_service.py

# Both under LIBRARY_DIR/backups, which a full backup already skips, so an
# export or old backup is never zipped into the next backup.
_ARTIFACT_SUBDIRS = {"backup": ("backups",), "export": ("backups", "exports"),
                     "database": ("backups", "database")}
_ARTIFACT_EXT = {"backup": ".zip", "export": ".zip", "database": ".db"}
_ARTIFACT_PREFIX = {"backup": "baihe_library_backup", "export": "dramas_export",
                    "database": "library"}
_EXPORTABLE_STATUSES = ("translated", "dubbed", "exported")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _require_confirm(confirm, confirm_text, word: str, action: str):
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


def _any_job_running() -> bool:
    """In-process running/queued jobs, plus fresh running/queued
    job_records rows from another process (same staleness rule as
    drama_service.job_running_for_drama)."""
    if any(j.get("status") in ("running", "queued")
           for j in background_jobs.list_all_jobs().values()):
        return True
    cutoff = time.time() - drama_service._STALE_JOB_RECORD_SECONDS
    return any(r.get("status") in ("running", "queued") and (r.get("updated_at") or 0) >= cutoff
               for r in db.list_job_records())


def _job_id_running(job_id: str) -> bool:
    """This process's job, or a fresh running/queued job_records row."""
    job = background_jobs.get_status(job_id)
    if job and job.get("status") in ("running", "queued"):
        return True
    cutoff = time.time() - drama_service._STALE_JOB_RECORD_SECONDS
    rec = db.get_job_record(job_id)
    return bool(rec and rec.get("status") in ("running", "queued")
                and (rec.get("updated_at") or 0) >= cutoff)


@contextlib.contextmanager
def _maintenance(action: str):
    """Held around bulk delete / storage cleanup: refuses (Conflict) while a
    restore holds the library, or a backup/export is reading it, and keeps
    a restore from starting until the operation ends."""
    if not background_jobs.enter_maintenance():
        raise ConflictError(f"A restore is in progress; {action} is not possible right now.")
    try:
        for job_id, label in ((BACKUP_JOB_ID, "backup"), (AUTO_BACKUP_JOB_ID, "backup"),
                              (EXPORT_JOB_ID, "library export")):
            if _job_id_running(job_id):
                raise ConflictError(f"A {label} is running -- wait for it to finish before "
                                    f"{action}.")
        yield
    finally:
        background_jobs.exit_maintenance()


def _refuse_during_maintenance(label: str):
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
    if kind not in _ARTIFACT_SUBDIRS:
        raise InvalidInputError("Unknown artifact kind.")
    path = os.path.join(db.LIBRARY_DIR, *_ARTIFACT_SUBDIRS[kind])
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
    _require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting dramas")
    with _maintenance("deleting dramas"):
        results = []
        for did in ids:
            if db.get_drama(did) is None:
                results.append({"drama_id": did, "ok": False, "error": "not_found"})
                continue
            if drama_service.job_running_for_drama(did):
                results.append({"drama_id": did, "ok": False, "error": "job_running"})
                continue
            try:
                leftover = drama_service._hard_delete_drama(did)
            except ServiceError as e:
                results.append({"drama_id": did, "ok": False, "error": "delete_failed",
                                "message": str(e)})
                continue
            entry = {"drama_id": did, "ok": True}
            if leftover:
                entry["warning"] = drama_service._LEFTOVER_FILES_MESSAGE
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
        if drama is None or not ownership_service.can_see_drama(principal, did):
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
                         principal=None) -> dict:
    """Starts the existing bulk-series translate job
    (workspace_job_service.run_bulk_series_translate_job) for the picked
    dramas whose status is "aligned" (the same filter the tab applies) and
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
        allow_paid_summary=allow_paid_summary)
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
    _refuse_during_maintenance("library export")
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
    the current ones either way) and the browser-extension token."""
    return wjs._restore_kept_names()


def write_backup_zip(dest: str, include_media: bool = True, manifest=None):
    """Writes a backup zip to `dest`: a sanitized library.db snapshot and,
    with include_media, every other library file except the excluded
    top-level entries (backups/, sign-ins, source profiles, extension
    token), the live database files, symlinks and any `.env`. `manifest`,
    if given, is called with the snapshot's path and returns bytes stored
    as manifest.json (so it describes exactly the database in the zip)."""
    library_dir = db.LIBRARY_DIR
    skip = {db.DB_PATH, db.DB_PATH + "-wal", db.DB_PATH + "-shm"}
    excluded = _backup_excluded_top_level()
    with tempfile.TemporaryDirectory() as snapdir:
        snap = os.path.join(snapdir, "library.db")
        _sanitized_snapshot(snap)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            if include_media:
                for root, dirs, files in os.walk(library_dir, topdown=True):
                    if root == library_dir:
                        dirs[:] = [d for d in dirs if d not in excluded]
                        files = [f for f in files if f not in excluded]
                    for fname in files:
                        full = os.path.join(root, fname)
                        if full in skip or fname == ".env" or os.path.islink(full):
                            continue
                        zf.write(full, os.path.relpath(full, library_dir))
            zf.write(snap, "library.db")
            if manifest is not None:
                zf.writestr("manifest.json", manifest(snap))


def _backup_job(job_id):
    name, size = _write_artifact("backup", ".zip", "The backup could not be written.",
                                 write_backup_zip)
    background_jobs.set_result(job_id, {"name": name, "size": size})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def start_backup() -> dict:
    """Job: full library backup zip (database snapshot + media)."""
    _refuse_during_maintenance("backup")
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
    """Job: database-only backup (the tab's "Database-only backup (fast,
    small)"): one consistent library.db snapshot, auth sessions removed,
    no media. Fetch it with admin_artifact_path("database")."""
    _refuse_during_maintenance("database backup")
    _refuse_duplicate(DATABASE_BACKUP_JOB_ID, "database backup")
    if not background_jobs.start_job(DATABASE_BACKUP_JOB_ID, _database_backup_job,
                                     DATABASE_BACKUP_JOB_ID, description="Database backup"):
        raise ConflictError("A database backup is already running.")
    return {"job_id": DATABASE_BACKUP_JOB_ID}


# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------

_BAD_ZIP = "This file is not a valid Baihe library backup."
_BUSY = ("A background job is running -- wait for it to finish or cancel it before "
         "restoring.")

# Restore-specific limits for an uploaded (network) zip, tighter than
# workspace_job_service's generous Step 25k guardrails: expanded total at
# most max(factor x upload size, 2 x current library size, 1 GiB), a
# member-count cap, and enough free disk for the expanded total plus a
# margin before anything is extracted.
_RESTORE_MAX_MEMBERS = 100_000
_RESTORE_EXPANSION_FACTOR = 10
_RESTORE_MIN_TOTAL_BYTES = 1024 ** 3
_RESTORE_DISK_MARGIN_BYTES = 256 * 1024 ** 2


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
    within the Step 52 limits and the tighter restore limits above
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
            if check_limits and len(infos) > min(wjs._MAX_RESTORE_MEMBERS, _RESTORE_MAX_MEMBERS):
                raise InvalidInputError("The backup has too many files; it looks corrupted "
                                        "or unsafe to extract.")
            if "library.db" not in zf.namelist():
                raise InvalidInputError(_BAD_ZIP + " (no library.db inside).")
            total_cap = (min(wjs._MAX_RESTORE_TOTAL_BYTES, _restore_total_cap(size))
                         if check_limits else None)
            total = 0
            for info in infos:
                if _unsafe_member(info):
                    raise InvalidInputError("The backup contains an unsafe file path.")
                if info.flag_bits & 0x1:
                    raise InvalidInputError("The backup contains an encrypted file.")
                if check_limits and info.file_size > wjs._MAX_RESTORE_MEMBER_BYTES:
                    raise InvalidInputError("The backup contains a file that is too large; it "
                                            "looks corrupted or unsafe to extract.")
                total += info.file_size
                if check_limits and total > total_cap:
                    raise InvalidInputError("The backup would expand too large; it looks "
                                            "corrupted or unsafe to extract.")
            if check_disk:
                try:
                    free = shutil.disk_usage(os.path.dirname(os.path.abspath(db.LIBRARY_DIR))).free
                except OSError:
                    free = None
                if free is not None and free < total + _RESTORE_DISK_MARGIN_BYTES:
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
    _require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a backup")
    if not background_jobs.acquire_exclusive("Library restore"):
        raise ConflictError(_BUSY)
    try:
        if _any_job_running():
            raise ConflictError(_BUSY)
        validate_backup_zip(zip_bytes)
        revoked = _count_sessions()

        def _recheck():
            if _any_job_running():
                raise ConflictError(_BUSY)

        try:
            wjs.restore_library_backup(bytes(zip_bytes), db.LIBRARY_DIR, before_swap=_recheck)
        except ServiceError:
            raise
        except (ValueError, zipfile.BadZipFile, RuntimeError, zlib.error) as e:
            msg = str(e) if str(e) in (wjs._BAD_LIBRARY_DB, wjs._BAD_SOURCES_DB) else _BAD_ZIP
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
    _require_confirm(confirm, confirm_text, CLEAN_CONFIRM_TEXT, "Cleaning storage")
    with _maintenance("cleaning storage"):
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
