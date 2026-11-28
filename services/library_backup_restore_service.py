"""
services/library_backup_restore_service.py -- restore of a library backup zip:
the checks run before anything is touched (validate_backup_zip /
validate_backup_file) and restore_backup, which replaces the whole library.

Routes: api/routers/library_admin_routes.py. Rules this module keeps:
  - Restore refuses while ANY job runs, in this process or (via fresh
    job_records rows) another one, holds background_jobs' exclusive lock so no
    job starts meanwhile, and re-checks right before the folder swap.
  - A restore never takes sign-in state from the upload: the current
    users/permissions/audit tables, sources settings, backups/, browser
    profiles, source profiles and extension token are kept, and every
    session is revoked.

No FastAPI import.
"""

import io
import logging
import os
import zipfile
import zlib

from jobs import job_launch, job_runner
import db
from engine_backends import echo
from services import (assistant_settings_service, auth_service, confidence_backfill_service,
                      pc_profile_service)
from services import library_restore_service as lrs
from services.library_admin_service import (RESTORE_CONFIRM_TEXT, any_job_running, has_disk_room,
                                            require_confirm)
from services.library_backup_service import _backup_excluded_top_level
from services.library_restore_sql import _unsafe_member
from lib.errors import ConflictError, InvalidInputError, ServiceError

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# restore
# --------------------------------------------------------------------------

_BAD_ZIP = "This file is not a valid Baihe library backup."
_CRC_CHUNK = 1024 * 1024
_BUSY = ("A background job is running -- wait for it to finish or cancel it before "
         "restoring.")

# Restore-specific limits for an uploaded (network) zip, tighter than
# library_restore_service's generous guardrails: expanded total at
# most max(factor x upload size, 2 x current library size, 1 GiB), a
# member-count cap, and enough free disk for the expanded total plus a
# margin before anything is extracted.
RESTORE_MAX_MEMBERS = 100_000
_RESTORE_EXPANSION_FACTOR = 10
_RESTORE_MIN_TOTAL_BYTES = 1024 ** 3


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


# A restore holds the whole zip in memory (bytes, then BytesIO), so it is
# capped below the general upload limit, which can be set to 1 TB.
RESTORE_MAX_UPLOAD_BYTES = 2 * 1024 ** 3


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
    (library_restore_service.validate_staged_library_db)."""
    if not isinstance(zip_bytes, (bytes, bytearray)) or not zip_bytes:
        raise InvalidInputError("Upload a backup .zip file.")
    _validate_zip(io.BytesIO(zip_bytes), len(zip_bytes), check_disk=True)


def validate_backup_file(path: str, check_disk: bool = True, check_limits: bool = True,
                         on_read=None) -> None:
    """validate_backup_zip for a zip on disk (read in place, never loaded
    whole into memory). check_disk=False skips the free-space check, for
    a backup that was just written rather than one about to be restored
    whole. check_limits=False skips the upload size/count caps (member
    count, per-member and expanded-total size) for the app's own snapshot,
    which can legitimately be larger than any upload; the structural
    checks (library.db present, no unsafe/symlink/encrypted member, CRCs)
    always run, and a caller extracting members must cap what it extracts.
    `on_read(done_bytes, total_bytes)`, if given, is called as the CRC pass
    reads the members, and may raise to stop it (a cancelled job)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            _validate_zip(fh, size, check_disk=check_disk, check_limits=check_limits,
                          on_read=on_read)
    except OSError:
        raise InvalidInputError(_BAD_ZIP) from None


def _read_every_member(zf: zipfile.ZipFile, infos, on_read) -> None:
    """What zf.testzip() checks (each member's CRC, which a read verifies),
    reporting as it goes: a full backup can be many GB, and testzip can't be
    followed or stopped. Raises BadZipFile on a bad CRC."""
    total = sum(i.file_size for i in infos)
    done = 0
    for info in infos:
        on_read(done, total)
        with zf.open(info) as fh:
            while chunk := fh.read(_CRC_CHUNK):
                done += len(chunk)
                on_read(done, total)


def _validate_zip(source, size: int, check_disk: bool, check_limits: bool = True,
                  on_read=None) -> None:
    try:
        with zipfile.ZipFile(source) as zf:
            infos = zf.infolist()
            if check_limits and len(infos) > min(lrs.MAX_RESTORE_MEMBERS, RESTORE_MAX_MEMBERS):
                raise InvalidInputError("The backup has too many files; it looks corrupted "
                                        "or unsafe to extract.")
            if "library.db" not in zf.namelist():
                raise InvalidInputError(_BAD_ZIP + " (no library.db inside).")
            total_cap = (min(lrs.MAX_RESTORE_TOTAL_BYTES, _restore_total_cap(size))
                         if check_limits else None)
            total = 0
            for info in infos:
                if _unsafe_member(info):
                    raise InvalidInputError("The backup contains an unsafe file path.")
                if info.flag_bits & 0x1:
                    raise InvalidInputError("The backup contains an encrypted file.")
                if check_limits and info.file_size > lrs.MAX_RESTORE_MEMBER_BYTES:
                    raise InvalidInputError("The backup contains a file that is too large; it "
                                            "looks corrupted or unsafe to extract.")
                total += info.file_size
                if total_cap is not None and total > total_cap:
                    raise InvalidInputError("The backup would expand too large; it looks "
                                            "corrupted or unsafe to extract.")
            # The rebuild writes a scratch copy and a fresh file of each
            # database beside the extracted one.
            rebuild = 2 * sum(i.file_size for i in infos
                              if i.filename in ("library.db", "sources.db"))
            if check_disk and not has_disk_room(os.path.dirname(os.path.abspath(db.LIBRARY_DIR)),
                                                 total + rebuild):
                raise InvalidInputError("Not enough free disk space to restore this backup.")
            if on_read is not None:
                _read_every_member(zf, infos, on_read)
            elif zf.testzip() is not None:
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
    job -> full validation -> library_restore_service.restore_library_backup
    (staging extract, SQLite checks, schema migration of the staged
    library.db, current auth tables and sources settings kept, job and
    auth/settings-change re-check right before the rename,
    rename-aside, rename-in, roll back on failure; backups/ and the other
    kept entries carried across). Nothing is touched unless every check
    passes. Afterwards every sign-in session is revoked and an audit entry
    is written (attributed to actor_id, the signed-in user, if given).
    Returns {restored, sessions_revoked, media_kept, previous_library_kept}."""
    require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a backup")
    if not job_launch.acquire_exclusive("Library restore"):
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
            if not (job_runner.wait_for_job_threads(10.0) and confidence_backfill_service.wait(10.0)):
                raise ConflictError("A background job is still finishing; try again in a moment.")

        try:
            outcome = lrs.restore_library_backup(bytes(zip_bytes), db.LIBRARY_DIR,
                                                 before_swap=_recheck)
        except ServiceError:
            raise
        except (ValueError, zipfile.BadZipFile, RuntimeError, zlib.error) as e:
            msg = str(e) if str(e) in lrs.RESTORE_DB_ERRORS else _BAD_ZIP
            raise InvalidInputError(msg) from None
        except OSError as e:
            log.exception("Library restore failed")
            raise ServiceError("The restore could not be completed; the current library was "
                               "left in place.") from e
        # The restored database decides Developer Mode, which the echo engine follows.
        echo.set_enabled(assistant_settings_service.developer_mode_enabled())
        pc_profile_service.forget_cached_pin()
        auth_service.write_audit(actor_id, "library.restore",
                                 f"library restored from backup; sessions revoked: {revoked}; "
                                 + lrs.account_counts(outcome))
    finally:
        job_launch.release_exclusive()
    # An older backup has no rescoring marker; its lines would stay unscored until the next start.
    confidence_backfill_service.start_in_background()
    return {"restored": True, "sessions_revoked": revoked, **(outcome or {})}
