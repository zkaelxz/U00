"""
services/library_restore_service.py -- restoring a library backup zip:
validate it, build fresh databases from the app's own schema filled with
the upload's rows, and swap the result in for the library folder without
ever destroying the current library if anything fails
(services/library_backup_restore_service.restore_backup is the caller).
"""

import logging
import os
import time

import db
from lib.errors import NewerDatabaseError
from services.library_restore_sql import (_current_state_marker, _fsync_dir, _open_carry_conn,
                                          _ro_uri, _schema_sql_allowed, _table_names)
from services import auth_service, library_restore_sql


# Restore-a-backup guardrails -- generous, not tight, since a
# full library backup legitimately includes media (audio, video); they
# exist to catch a corrupted or accidentally-huge zip failing safely
# (before it fills the disk), not to defend against a malicious upload in
# this single-user app.
MAX_RESTORE_MEMBERS = 500_000
MAX_RESTORE_MEMBER_BYTES = 50 * 1024 ** 3  # 50 GiB, any one file
MAX_RESTORE_TOTAL_BYTES = 200 * 1024 ** 3  # 200 GiB, expanded total


# Top-level library entries a restore never takes from an upload and
# instead carries across from the current library: saved backups/exports,
# saved site sign-ins, approved source profiles and the browser-extension
# token, plus the temp folder of in-flight work. (Upload members under these names are skipped, so a planted
# backups/exports/x.zip can never become the "latest export".)
def restore_kept_names():
    import page_server
    import storage
    from sources import store as src_store
    return ("backups", src_store.BROWSER_PROFILES_DIRNAME, "source_profiles",
            page_server.TOKEN_FILENAME, storage.TEMP_DIRNAME)


# Who may sign in and what they may do: never from the upload (auth_service).
_RESTORE_KEPT_AUTH_TABLES = auth_service.RESTORE_KEPT_TABLES

# app_settings keys a restore takes from the current library, never from the
# upload: the automatic-backup identity (auto_backup_service.IDENTITY_KEY),
# settings and state. A backup from another library must not bring that
# library's id (its copies in a shared folder would then look like this
# one's and be rotated out), an older backup must not roll the copy sequence
# back, and an uploaded file must not choose where this library's copies
# are written.
_RESTORE_KEPT_APP_SETTINGS = ("auto_backup.identity", "auto_backup.settings",
                              "auto_backup.state")


# Media folders a restore keeps from the current library when the zip has
# none: a database-only backup still names these files.
_RESTORE_MEDIA_DIRS = ("dramas", "voice_bank", "benchmark_cases")

# Library folders the app rebuilds on its own: a zip without them doesn't
# keep the previous library just for them.
_REBUILDABLE_DIRS = ("logs", "source_cache", "source_review_tmp", "updates", "piper_voices")

# SQLite side files a restore never extracts: the validated library.db /
# sources.db image must be exactly what goes live.
_RESTORE_SKIPPED_SIDECARS = tuple(f"{base}{ext}" for base in ("library.db", "sources.db")
                                  for ext in ("-wal", "-shm", "-journal"))


BAD_LIBRARY_DB = ("The backup's library.db is not a readable Baihe library "
                   "database.")
BAD_SOURCES_DB = "The backup's sources.db is not a readable database."
SOURCES_DB_NOT_CARRIED = ("The current sources.db could not be copied into the restored "
                          "library (it may be damaged); nothing was changed. To restore "
                          "without it, move sources.db out of the library folder (tracked "
                          "series and source settings go with it) and try again.")
RESTORED_DB_FAILED = ("The restored library.db failed a check once in place; your previous "
                      "library was put back unchanged.")
RESTORE_NOT_PUT_BACK = ("The restore failed and the previous library could not be put back: it "
                        "is in the folder ending in .pre_restore_ beside the library folder. "
                        "Don't delete it; if a restart doesn't bring your library back, close "
                        "Baihe and rename that folder to the library folder's name.")
RESTORE_DISK_FULL = ("The disk ran out of space while the backup was being prepared; nothing "
                     "was changed. Free some space and try again.")
BACKUP_FROM_NEWER_APP = ("The backup was made by a newer version of Baihe than this one; "
                         "nothing was changed. Update Baihe, then restore it.")
RESTORE_DB_ERRORS = (BAD_LIBRARY_DB, BAD_SOURCES_DB, SOURCES_DB_NOT_CARRIED, RESTORED_DB_FAILED,
                     RESTORE_DISK_FULL, BACKUP_FROM_NEWER_APP)


def _ran_out_of_disk(exc) -> bool:
    """Whether a failed database rebuild was the disk filling up, which the
    rebuild's own errors report as a bad backup. Follows the chain that
    `raise ... from None` keeps in __context__."""
    import errno
    import sqlite3
    # Both the constant and sqlite_errorcode are Python 3.11+; 13 is SQLite's own code.
    full = getattr(sqlite3, "SQLITE_FULL", 13)
    while exc is not None:
        if isinstance(exc, sqlite3.Error) and getattr(exc, "sqlite_errorcode", None) == full:
            return True
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _check_uploaded_db(db_path: str, message: str, need_table: str = None) -> None:
    """Raises ValueError(message) unless the uploaded file is SQLite,
    passes quick_check and every sqlite_master row is a plain table or
    index (see _schema_sql_allowed; NULL sql only for sqlite_autoindex_*).

    Why this is safe to open: read-only + immutable, trusted_schema OFF,
    and the only statements run are PRAGMA quick_check and a SELECT on
    sqlite_master. Loading the schema parses CREATE text but executes
    nothing: a trigger only runs on a write (none here) and a virtual
    table's module is only connected when that table is queried (never
    here). A schema SQLite can't parse fails the open -> refused.
    (Python 3.11's sqlite3 has no Connection.setconfig for
    SQLITE_DBCONFIG_DEFENSIVE, so that option isn't used.)"""
    import sqlite3
    if not os.path.isfile(db_path) or os.path.islink(db_path):
        raise ValueError(message)
    conn = None
    try:
        conn = sqlite3.connect(_ro_uri(db_path) + "&immutable=1", uri=True)
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA ignore_check_constraints = ON")
        rows = conn.execute("SELECT type, name, sql FROM sqlite_master").fetchall()
        for _type, name, sql in rows:
            if sql is None:
                if not str(name).startswith("sqlite_autoindex_"):
                    raise ValueError(message)
            elif not _schema_sql_allowed(sql):
                raise ValueError(message)
        if [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()] != ["ok"]:
            raise ValueError(message)
        if need_table and not any(t == "table" and n == need_table for t, n, _ in rows):
            raise ValueError(message)
    except sqlite3.Error:
        raise ValueError(message) from None
    finally:
        if conn is not None:
            conn.close()


def validate_staged_library_db(db_path: str) -> None:
    """ValueError (fixed text, no path) unless an uploaded library.db is
    SQLite, passes quick_check, holds only plain tables/indexes and has a
    dramas table."""
    _check_uploaded_db(db_path, BAD_LIBRARY_DB, need_table="dramas")


def _rebuild_from_upload(fresh_path: str, upload_path: str, skip_tables=(),
                         live_path: str = None, live_tables=(), message: str = ""):
    """fresh_path already holds the app's own schema. Copies rows from the
    (validated) upload for every app table except skip_tables, and rows
    from the live database for live_tables, never taking a table
    definition from either. Raises ValueError(message) on any SQLite
    error."""
    import sqlite3
    try:
        conn = _open_carry_conn(fresh_path)
        try:
            conn.execute("ATTACH DATABASE ? AS up", (_ro_uri(upload_path) + "&immutable=1",))
            if live_path and os.path.isfile(live_path):
                conn.execute("ATTACH DATABASE ? AS cur", (_ro_uri(live_path),))
            else:
                live_path = None
            conn.execute("BEGIN")
            up_tables = set(_table_names(conn, "up"))
            cur_tables = set(_table_names(conn, "cur")) if live_path else set()
            if "bug_reports" in up_tables:
                db.ensure_legacy_bug_reports(conn)
            for t in _table_names(conn, "main"):
                if t in live_tables:
                    if t in cur_tables:
                        library_restore_sql.copy_rows(conn, "cur", t)
                elif t not in skip_tables and t in up_tables:
                    library_restore_sql.copy_rows(conn, "up", t)
            conn.execute("COMMIT")
        finally:
            conn.close()
    except sqlite3.Error:
        raise ValueError(message) from None


def _prepare_scratch(path: str):
    """Drops sqlite_stat* tables (query-planner statistics the app never
    writes) from the scratch copy before anything is run on it."""
    conn = _open_carry_conn(path)
    try:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                    "AND name LIKE 'sqlite_stat%'").fetchall():
            conn.execute(f'DROP TABLE "{name}"')
    finally:
        conn.close()


def _checkpoint(path: str):
    conn = _open_carry_conn(path)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()


def _carry_app_settings(conn, live_path: str):
    """Replaces the staged library's _RESTORE_KEPT_APP_SETTINGS rows with the
    live library's (none there = none in the restored one, so a new
    identity is made on first use)."""
    marks = ", ".join("?" for _ in _RESTORE_KEPT_APP_SETTINGS)
    conn.execute(f"DELETE FROM main.app_settings WHERE key IN ({marks})",
                 _RESTORE_KEPT_APP_SETTINGS)
    if not os.path.isfile(live_path):
        return
    conn.execute("ATTACH DATABASE ? AS cur", (_ro_uri(live_path),))
    try:
        if "app_settings" in _table_names(conn, "cur"):
            conn.execute(f"INSERT INTO main.app_settings (key, value) SELECT key, value "
                         f"FROM cur.app_settings WHERE key IN ({marks})",
                         _RESTORE_KEPT_APP_SETTINGS)
    finally:
        conn.execute("DETACH DATABASE cur")


def _build_staged_databases(staging_dir: str, library_dir: str) -> tuple:
    """Replaces the uploaded library.db / sources.db in staging with fresh
    files built from the app's own schema (db.migrate_database_file /
    sources.store.migrate_file) and filled with the upload's rows -- except
    the auth tables (current rows, sessions emptied: every session is
    revoked; a live library without accounts takes the upload's accounts)
    and the sources settings table (current rows). When the live accounts
    are kept, the restored rows' owners, the upload's tracked-series links
    and extension-only marks included, are matched to them by email
    (library_restore_sql.owner_map) and the upload's followed channels
    pass to the PC owner with automatic checks off. Returns (accounts
    taken from the upload, titles that changed owner, whether the upload
    held a readable account). An upload
    without sources.db gets a snapshot of the live one instead, migrated
    the same way; either sources.db must pass quick_check. Running and
    queued job_records rows are marked cancelled, gpu_lock is cleared and
    the job queue's start time is dropped (a backup can only hold stale
    ones). The uploaded files never become a live schema: they are only
    ATTACHed read-only after _check_uploaded_db."""
    accounts_restored, titles_reassigned, backup_has_accounts, from_upload, owners = \
        _stage_library_db(staging_dir, library_dir)
    _stage_sources_db(staging_dir, library_dir, from_upload, owners)
    return accounts_restored, titles_reassigned, backup_has_accounts


def _stage_library_db(staging_dir: str, library_dir: str) -> tuple:
    """The library.db half of _build_staged_databases. Returns (accounts taken from the upload,
    titles that changed owner, whether the upload held a readable account, whether the upload's
    accounts were taken, the email owner map for the sources step)."""
    import shutil
    import sqlite3
    from jobs import gpu_slots
    from services import queue_service
    staged = os.path.join(staging_dir, "library.db")
    upload = os.path.join(staging_dir, ".uploaded_library.db")
    os.replace(staged, upload)
    # The app's data migrations (line refs -> line ids, profiles)
    # run on a scratch COPY of the validated upload -- safe because the
    # allowlist leaves only plain tables/indexes; its DDL is discarded, only
    # its rows are copied into the fresh file below.
    scratch = os.path.join(staging_dir, ".migrated_upload.db")
    try:
        shutil.copyfile(upload, scratch)
        try:
            _prepare_scratch(scratch)
            db.migrate_database_file(scratch)
            _checkpoint(scratch)
            db.migrate_database_file(staged)
        except NewerDatabaseError:
            raise ValueError(BACKUP_FROM_NEWER_APP) from None
        except Exception:
            logging.getLogger(__name__).warning("Restore: staged database migration failed",
                                                exc_info=True)
            raise ValueError(BAD_LIBRARY_DB) from None
        live_lib = os.path.join(library_dir, "library.db")
        from_upload = not library_restore_sql.has_accounts(live_lib)
        taken = auth_service.RESTORE_ACCOUNT_TABLES if from_upload else ()
        accounts_restored = titles_reassigned = 0
        backup_has_accounts = True
        owners = {}
        _rebuild_from_upload(staged, scratch,
                             skip_tables=[t for t in _RESTORE_KEPT_AUTH_TABLES if t not in taken],
                             live_path=live_lib,
                             live_tables=[t for t in auth_service.RESTORE_LIVE_TABLES
                                          if t not in taken],
                             message=BAD_LIBRARY_DB)
        try:
            conn = _open_carry_conn(staged)
            try:
                conn.execute("UPDATE job_records SET status = 'cancelled', finished_at = ?, "
                             "cancel_requested = 0 WHERE status IN ('queued', 'running')",
                             (time.time(),))
                gpu_slots.clear(conn)
                queue_service.clear_schedule(conn)
                _carry_app_settings(conn, live_lib)
                library_restore_sql.settle_accounts(conn, live_lib)
                if from_upload:
                    accounts_restored = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                else:
                    owners = library_restore_sql.owner_map(conn, scratch)
                    backup_has_accounts = library_restore_sql.backup_has_usable_accounts(scratch)
                    titles_reassigned = library_restore_sql.match_owners(conn, owners)
                conn.execute("PRAGMA journal_mode = DELETE")
            finally:
                conn.close()
        except sqlite3.Error:
            raise ValueError(BAD_LIBRARY_DB) from None
    finally:
        for path in (upload, scratch, scratch + "-wal", scratch + "-shm"):
            if os.path.lexists(path):
                os.remove(path)
    return accounts_restored, titles_reassigned, backup_has_accounts, from_upload, owners


def _stage_sources_db(staging_dir: str, library_dir: str, from_upload: bool, owners: dict):
    """The sources.db half of _build_staged_databases."""
    import sqlite3
    from sources import store as src_store
    staged_src = os.path.join(staging_dir, "sources.db")
    live_src = os.path.join(library_dir, "sources.db")
    if os.path.lexists(staged_src):
        message = BAD_SOURCES_DB
        upload_src = os.path.join(staging_dir, ".uploaded_sources.db")
        os.replace(staged_src, upload_src)
        try:
            try:
                src_store.migrate_file(staged_src)
            except sqlite3.Error:
                raise ValueError(message) from None
            _rebuild_from_upload(staged_src, upload_src, skip_tables=("settings",),
                                 live_path=live_src, live_tables=("settings",), message=message)
            if not from_upload:
                try:
                    library_restore_sql.match_source_owners(staged_src, owners)
                    library_restore_sql.hand_follows_to_pc_owner(staged_src)
                except sqlite3.Error:
                    raise ValueError(message) from None
        finally:
            os.remove(upload_src)
    else:
        # A backup without sources.db (database-only, an older backup) must
        # not delete the tracked series and source settings: the restored
        # library keeps the live ones.
        message = SOURCES_DB_NOT_CARRIED
        try:
            if not src_store.snapshot_to(staged_src, source=live_src):
                return
            src_store.migrate_file(staged_src)
        except sqlite3.Error:
            raise ValueError(message) from None
    _require_quick_check(staged_src, message)


def account_counts(outcome) -> str:
    """The restore's account counts in words, for its audit entry."""
    outcome = outcome or {}
    return (f"accounts restored from the backup: {outcome.get('accounts_restored', 0)}; "
            f"titles reassigned to a live account or the PC owner: "
            f"{outcome.get('titles_reassigned', 0)}"
            f"{'' if outcome.get('backup_has_accounts', True) else ' (the backup holds no readable account)'}")


def _require_quick_check(path: str, message: str) -> None:
    import sqlite3
    try:
        conn = _open_carry_conn(path)
        try:
            ok = [r[0] for r in conn.execute("PRAGMA quick_check").fetchall()] == ["ok"]
        finally:
            conn.close()
    except sqlite3.Error:
        ok = False
    if not ok:
        raise ValueError(message)


def _put_back(old_dir, library_dir: str, failed_dir: str = None) -> None:
    from services.service_errors import ServiceError
    try:
        if failed_dir:
            os.rename(library_dir, failed_dir)
        if old_dir:
            os.rename(old_dir, library_dir)
    except OSError as e:
        logging.getLogger(__name__).exception("Restore: the previous library was not put back")
        raise ServiceError(RESTORE_NOT_PUT_BACK) from e


def _remove_entry(path: str):
    import shutil
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def restore_library_backup(zip_bytes: bytes, library_dir: str, before_swap=None) -> dict:
    """Validate an uploaded backup zip and swap it in for
    library_dir, without ever destroying the existing library if the
    upload turns out to be invalid.

    Order, all before library_dir is touched: size/member checks; extract
    to a staging directory, skipping kept names and SQLite side files,
    then deleting anything the filesystem resolves to a kept name (case,
    trailing dots); check the uploaded library.db (and sources.db) with
    SQLite (quick_check, only plain tables/indexes, dramas table); build
    FRESH databases from the app's own schema and copy the upload's rows
    into them (_build_staged_databases -- the upload never becomes a live
    schema, and auth rows and sources settings come from the current
    library), so an unusable upload is refused here rather than after the
    swap; then before_swap() and a check that the live
    auth/settings state didn't change meanwhile. Only then is library_dir
    renamed aside and the staging directory renamed in (the original is
    renamed back on failure). The current kept entries (backups/, browser
    profiles, source profiles, extension token) are then moved into the
    restored library; if one can't be moved, the previous library is left
    beside the restored one instead of being deleted. Raises (ValueError,
    zipfile.BadZipFile, OSError, ServiceError, ...) with nothing changed
    if any check fails.

    before_swap: optional callable run right before the rename; if it
    raises, nothing is changed and the exception propagates.

    Returns {media_kept: the zip held no media and the current media was
    kept, previous_library_kept: it was left beside the restored one,
    accounts_restored: how many accounts came from the backup (only into a
    library that had none), titles_reassigned: how many titles' owners
    became a live account of the same email or the PC owner (only when the
    live accounts were kept), backup_has_accounts: False when the backup
    holds no readable account (always True when its accounts were taken),
    folders_left_behind: names of the previous library's other
    folders the zip lacks, which stay in the previous library}.
    """
    import io
    import shutil
    import tempfile
    import zipfile

    from services.service_errors import ConflictError

    kept = restore_kept_names()
    staging_dir = None
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            infos = _checked_members(zf)
            parent_dir = os.path.dirname(os.path.abspath(library_dir)) or "."
            staging_dir = tempfile.mkdtemp(prefix=".restore_staging_", dir=parent_dir)
            zipped = _extract_to_staging(zf, infos, staging_dir, kept)
        validate_staged_library_db(os.path.join(staging_dir, "library.db"))
        staged_src = os.path.join(staging_dir, "sources.db")
        if os.path.lexists(staged_src):
            _check_uploaded_db(staged_src, BAD_SOURCES_DB)
        marker = _current_state_marker(library_dir)
        try:
            accounts_restored, titles_reassigned, backup_has_accounts = \
                _build_staged_databases(staging_dir, library_dir)
        except (ValueError, OSError) as exc:
            if _ran_out_of_disk(exc):
                raise ValueError(RESTORE_DISK_FULL) from None
            raise
        if before_swap is not None:
            before_swap()
        if _current_state_marker(library_dir) != marker:
            raise ConflictError("Sign-in or source settings changed while the restore was "
                                "being prepared; nothing was changed -- try again.")

        _fsync_staged(staging_dir)
        # Held until the swap is done: another process's start-up check
        # must not take the moment between the renames for a crash.
        holder = db.hold_restore_lock(library_dir)
        if holder is None:
            raise ConflictError("Baihe is starting in another window; try again in a moment.")
        try:
            old_dir = None
            if os.path.exists(library_dir):
                old_dir = f"{library_dir}{db.PRE_RESTORE_MARKER}{int(time.time())}"
                os.rename(library_dir, old_dir)
            try:
                os.rename(staging_dir, library_dir)
            except Exception:
                _put_back(old_dir, library_dir)
                raise
            failed_dir, staging_dir = staging_dir, None
            _fsync_dir(parent_dir)
            try:
                _require_quick_check(os.path.join(library_dir, "library.db"), RESTORED_DB_FAILED)
            except ValueError:
                _put_back(old_dir, library_dir, failed_dir)
                staging_dir = failed_dir
                raise
            library_restore_sql.flush_tree(library_dir)
            media_kept, carried, left = _carry_from_previous(old_dir, library_dir, zipped, kept)
            return {"media_kept": media_kept,
                    "previous_library_kept": old_dir is not None and not carried,
                    "accounts_restored": accounts_restored,
                    "titles_reassigned": titles_reassigned,
                    "backup_has_accounts": backup_has_accounts,
                    "folders_left_behind": left}
        finally:
            db.release_restore_lock(holder)
    finally:
        if staging_dir is not None:
            shutil.rmtree(staging_dir, ignore_errors=True)


def _checked_members(zf) -> list:
    """The zip's members, refused (ValueError) unless it holds library.db, stays within the member
    count and size limits and reads back without a CRC error."""
    if "library.db" not in zf.namelist():
        raise ValueError("This doesn't look like a Baihe library "
                          "backup (no library.db found inside the zip).")
    infos = zf.infolist()
    if len(infos) > MAX_RESTORE_MEMBERS:
        raise ValueError(
            f"Backup zip contains {len(infos):,} files, more than the "
            f"{MAX_RESTORE_MEMBERS:,}-file limit -- this looks corrupted "
            "or unsafe to extract.")
    total_size = 0
    for info in infos:
        if info.file_size > MAX_RESTORE_MEMBER_BYTES:
            raise ValueError(
                f"Backup zip contains a file ({info.filename}) that would "
                f"expand to {info.file_size / 1024 ** 3:.1f} GiB, more than "
                f"the {MAX_RESTORE_MEMBER_BYTES / 1024 ** 3:.0f} GiB "
                "per-file limit -- this looks corrupted or unsafe to extract.")
        total_size += info.file_size
        if total_size > MAX_RESTORE_TOTAL_BYTES:
            raise ValueError(
                f"Backup zip would expand to more than "
                f"{MAX_RESTORE_TOTAL_BYTES / 1024 ** 3:.0f} GiB total -- "
                "this looks corrupted or unsafe to extract.")
    if zf.testzip() is not None:
        raise ValueError("Backup zip is corrupted.")
    return infos


def _extract_to_staging(zf, infos, staging_dir: str, kept) -> set:
    """Extracts every member but the kept names and SQLite side files; returns the zip's
    top-level names."""
    zipped = set()
    for info in infos:
        norm = info.filename.replace("\\", "/")
        top = norm.lstrip("/").split("/", 1)[0]
        zipped.add(top)
        if top in kept or norm in _RESTORE_SKIPPED_SIDECARS:
            continue
        zf.extract(info, staging_dir)
    # Whatever the filesystem maps to a kept name (./backups, BACKUPS,
    # "extension_token.txt." on Windows) is removed from staging.
    for name in kept + _RESTORE_SKIPPED_SIDECARS:
        path = os.path.join(staging_dir, name)
        if os.path.lexists(path):
            _remove_entry(path)
    return zipped


def _fsync_staged(staging_dir: str):
    # The renames can reach disk before the staged data does: a power
    # loss after the old library is deleted must not leave empty files.
    for name in ("library.db", "sources.db"):
        if os.path.isfile(os.path.join(staging_dir, name)):
            with open(os.path.join(staging_dir, name), "r+b") as fh:
                os.fsync(fh.fileno())
    _fsync_dir(staging_dir)


def _carry_from_previous(old_dir, library_dir: str, zipped: set, kept) -> tuple:
    """Moves the kept entries (and the media a backup without media left out) from the previous
    library into the restored one, and deletes the previous library once nothing is left in it.
    Returns (media kept, everything carried, folders left behind)."""
    import shutil
    if old_dir is None:
        return False, False, []
    # A backup without media must not delete the current media.
    media = tuple(n for n in _RESTORE_MEDIA_DIRS if n not in zipped)
    media_kept, carried = library_restore_sql.carry_across(old_dir, library_dir, kept + media, media)
    left = library_restore_sql.folders_left_behind(
        old_dir, zipped | set(_RESTORE_MEDIA_DIRS + _REBUILDABLE_DIRS))
    carried = carried and not left
    if carried:
        shutil.rmtree(old_dir, ignore_errors=True)
    return media_kept, carried, left
