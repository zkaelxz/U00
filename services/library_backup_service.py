"""
services/library_backup_service.py -- the export-all zip, the full library
backup and the database-only backup jobs, plus the sanitized database snapshot
they share.

Routes: api/routers/library_admin_routes.py. Finished files live in
library-level folders excluded from backups (see library_admin_service's
ARTIFACT_SUBDIRS), so an export or old backup is never zipped into the next
backup. A backup never takes sign-in state: every session and device token is
removed from the snapshot.

No FastAPI import.
"""

import os
import re
import tempfile
import time
import zipfile

import background_jobs
from jobs import job_launch
import db
import storage
import subtitle_formats
from core import Line, lines_to_bilingual_srt, lines_to_srt
from services import keep_original_service, vdb_names_service
from services import library_translated_guard
from services import library_restore_service as lrs
from services.library_admin_service import (BACKUP_JOB_ID, DATABASE_BACKUP_JOB_ID, EXPORT_JOB_ID,
                                            _artifact_dir, _check_ids, _refuse_duplicate,
                                            _timestamp, has_disk_room, refuse_during_maintenance)
from services.service_errors import ConflictError, InvalidInputError
from sources import store as src_store

# Fixed prefixes: a file name never carries a user's name or email.
_ARTIFACT_PREFIX = {"backup": "baihe_library_backup", "export": "dramas_export",
                    "database": "library", "user_backup": "baihe_my_items_backup"}
_EXPORTABLE_STATUSES = ("translated", "dubbed", "exported")


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
                keep_original_service.export_title_files(
                    zf, did, d, folder,
                    has_room=lambda need: has_disk_room(os.path.dirname(tmp), need),
                    should_cancel=lambda: background_jobs.is_cancel_requested(job_id))
                exported += 1

    name, _ = _write_artifact("export", ".zip", "The export could not be written.", write)
    background_jobs.set_result(job_id, {"exported": exported, "name": name})
    background_jobs.update_progress(job_id, 1.0, "Export ready.")


def start_export_zip(drama_ids=None) -> dict:
    """Job: zips english/chinese/bilingual SRT (+ dub track, + a pinned
    title's originals: keep_original_service) for each
    translated/dubbed/exported drama -- the picked ones, or all of them.
    Returns {job_id, drama_ids}; with explicit ids also `results`, one per
    requested id ({drama_id, ok, error: not_found|not_translated|no_translated_lines})."""
    ids, results = library_translated_guard.pick_exportable(drama_ids, _check_ids, _EXPORTABLE_STATUSES)
    if not ids:
        raise InvalidInputError("No translated titles to export.",
                                details={"results": results} if results is not None else None)
    refuse_during_maintenance("library export")
    _refuse_duplicate(EXPORT_JOB_ID, "library export")
    if not job_launch.start_job(EXPORT_JOB_ID, _export_job, EXPORT_JOB_ID, ids,
                                     description="Library export"):
        raise ConflictError("A library export is already running.")
    out = {"job_id": EXPORT_JOB_ID, "drama_ids": ids}
    if results is not None:
        out["results"] = results
    return out


def _sanitized_snapshot(dest: str):
    """A consistent database snapshot with every session and device token
    and the personal VTuber name list's prompt text removed (secure_delete, so
    neither is left in free pages) and
    folded out of WAL mode, so dest is one self-contained file."""
    import sqlite3
    db.snapshot_database(dest)
    try:
        conn = sqlite3.connect(dest, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            for table in ("auth_sessions", "extension_device_tokens"):
                if table in have:
                    conn.execute(f"DELETE FROM {table}")
            vdb_names_service.strip_from_snapshot(conn)
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute("VACUUM")   # free pages copied from the live file can hold deleted rows
        finally:
            conn.close()
    except sqlite3.Error:
        raise OSError("snapshot could not be prepared") from None


def _backup_excluded_top_level() -> tuple:
    """Top-level library entries a backup never contains: old backups and
    exports, saved site sign-ins, approved source profiles (a restore keeps
    the current ones either way), the browser-extension token and the
    sources raw-content cache (rebuildable: a missing file is a cache miss)."""
    return lrs.restore_kept_names() + (os.path.basename(src_store.cache_dir()),)


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
    """Writes a backup zip to `dest`: sanitized library.db and sources.db snapshots and,
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
    skip = {p + x for p in (db.DB_PATH, src_store.db_path()) for x in ("", "-wal", "-shm")}
    excluded = _backup_excluded_top_level()

    def check_cancel():
        if should_cancel is not None and should_cancel():
            raise background_jobs.JobCancelled()

    with storage.job_workdir("snapshot") as snapdir:
        snap = os.path.join(snapdir, "library.db")
        check_cancel()
        _sanitized_snapshot(snap)
        src_snap = src_store.backup_snapshot(os.path.join(snapdir, "sources.db"))
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
            if src_snap:
                zf.write(src_snap, "sources.db")
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
    if not job_launch.start_job(BACKUP_JOB_ID, _backup_job, BACKUP_JOB_ID,
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
    if not job_launch.start_job(DATABASE_BACKUP_JOB_ID, _database_backup_job,
                                     DATABASE_BACKUP_JOB_ID, description="Database backup"):
        raise ConflictError("A database backup is already running.")
    return {"job_id": DATABASE_BACKUP_JOB_ID}
