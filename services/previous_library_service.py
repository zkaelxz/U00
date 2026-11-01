"""
services/previous_library_service.py -- the copies of the library kept beside
the library folder: the previous library a restore left
(<library>.pre_restore_*: the media of titles added since a database-only
backup, and anything else it could not carry across), a restore's unfinished
staging folder (.restore_staging_*), and the copy kept before a Reset library
(<library>.pre_reset_*). Disk usage lists them as one entry and deletes them
together after a typed confirm.

Routes: api/routers/disk_usage_routes.py. Every response is sizes and counts,
never a path.
"""

import os
import re
import shutil
import sqlite3
import time

from jobs import job_launch
import db
from services import disk_usage_measure as measure
from services import disk_usage_trash_service as trash_svc
from lib.errors import ConflictError, InvalidInputError, ServiceError

LABEL = "Previous library kept after restore"
DELETE_WORD = "DELETE"
PRE_RESET_MARKER = ".pre_reset_"
STAGING_PREFIX = ".restore_staging_"
# tempfile.mkdtemp's random suffix: the restore names its staging folder that way.
_STAGING_RE = re.compile(re.escape(STAGING_PREFIX) + r"[a-z0-9_]+", re.ASCII)
# A restore writes into its staging folder throughout; one quiet this long died.
STAGING_STALE_SECONDS = 6 * 3600
BUSY = "A job, restore or other library task is running. Wait for it to finish, then try again."
NO_LIBRARY = ("The library is missing or fails a check, so the previous library may be the "
              "only good copy. Restart Baihe; if that doesn't bring the library back, close it "
              "and rename that folder to the library folder's name.")
CHANGED = "The previous library changed since you looked. Check again."
RESET_COPY_FAILED = ("A copy of the library could not be kept before the reset (the disk may be "
                     "full, or a file is in use); nothing was reset.")


def _parent_and_base():
    parent, base = os.path.split(os.path.abspath(db.LIBRARY_DIR))
    return parent, base


def is_previous_library_name(name: str) -> bool:
    _parent, base = _parent_and_base()
    return (db.is_kept_copy_name(base, name, db.PRE_RESTORE_MARKER)
            or db.is_kept_copy_name(base, name, PRE_RESET_MARKER) or _is_staging(name))


def _is_staging(name: str) -> bool:
    return _STAGING_RE.fullmatch(os.path.normcase(name)) is not None


def _folders() -> list:
    """(name, path) of each kept copy: ordinary folders only, never a link."""
    parent, _base = _parent_and_base()
    try:
        names = sorted(os.listdir(parent))
    except OSError:
        return []
    out = []
    for name in names:
        path = os.path.join(parent, name)
        if is_previous_library_name(name) and os.path.isdir(path) and not os.path.islink(path):
            out.append((name, path))
    return out


def _title_count(db_path: str):
    """Titles in a library database, read-only; None when it can't be read."""
    from services.library_restore_sql import _ro_uri
    if not os.path.isfile(db_path) or os.path.islink(db_path):
        return None
    try:
        conn = sqlite3.connect(_ro_uri(db_path), uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM dramas").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def _library_checks_out() -> bool:
    """The live library.db is there and passes quick_check, and is not an
    empty library standing where a restore set a library with titles aside.
    A restore whose put-back failed leaves a failed one in place; a start
    during a restore can create a fresh, empty one (the real library then
    sits in .pre_restore_*, and deleting it would lose it). An empty library
    beside only a reset copy is the normal state after a reset."""
    from services import library_restore_service
    if not os.path.isfile(db.DB_PATH) or os.path.islink(db.DB_PATH):
        return False    # checked first: the check below would create an empty one
    try:
        library_restore_service._require_quick_check(db.DB_PATH, NO_LIBRARY)
    except ValueError:
        return False
    if _title_count(db.DB_PATH):
        return True
    _parent, base = _parent_and_base()
    for name, path in _folders():
        if db.is_kept_copy_name(base, name, db.PRE_RESTORE_MARKER):
            if _title_count(os.path.join(path, "library.db")) != 0:
                return False    # titles there, or unreadable: assume they matter
    return True


def _stale(name: str, path: str) -> bool:
    """A staging folder nothing has written to for STAGING_STALE_SECONDS: no
    restore is still preparing it (one in another process holds no lock
    while it extracts). Every other kept copy always counts."""
    if not _is_staging(name):
        return True
    newest = 0.0
    for root, dirs, files in os.walk(path):
        for entry in (root, *(os.path.join(root, n) for n in dirs + files)):
            try:
                newest = max(newest, os.lstat(entry).st_mtime)
            except OSError:
                return False
    return time.time() - newest > STAGING_STALE_SECONDS


def summary() -> dict:
    """{present, size_bytes, file_count, folder_count, partial, label}."""
    size = files = 0
    budget = measure._Budget()
    folders = _folders()
    for name, path in folders:
        m = measure._measure(path, (name,), budget)
        size += m.size
        files += m.files
    return {"present": bool(folders), "label": LABEL, "size_bytes": size, "file_count": files,
            "folder_count": len(folders), "partial": bool(budget.hit)}


def delete(confirm_text=None, expected_size_bytes=None) -> dict:
    """PERMANENT DELETE of every kept copy, except a staging folder another
    process's restore may still be writing (_stale). Needs the typed word and
    the size the owner saw; refused while anything holds the library, while a
    restore may be between its renames, and while the live library is
    missing, damaged or an empty one standing in for a set-aside library
    (_library_checks_out)."""
    if confirm_text != DELETE_WORD:
        raise InvalidInputError(f'Type {DELETE_WORD} to delete the previous library.')
    if not job_launch.acquire_exclusive("Previous library delete"):
        raise ConflictError(BUSY)
    try:
        holder = db.hold_restore_lock(db.LIBRARY_DIR)
        if holder is None:
            raise ConflictError(BUSY)
        try:
            if not _library_checks_out():
                raise ConflictError(NO_LIBRARY)
            seen = summary()
            if expected_size_bytes is not None and seen["size_bytes"] != expected_size_bytes:
                raise ConflictError(CHANGED)
            removed = freed = 0
            budget = measure._Budget()
            for name, path in _folders():
                if not _stale(name, path):
                    continue
                size = measure._measure(path, (name,), budget).size
                try:
                    trash_svc._remove_tree(path)
                except OSError:
                    raise ServiceError("Part of the previous library could not be deleted "
                                       "(something may be in use). What is left is still "
                                       "there.") from None
                removed += 1
                freed += size
        finally:
            db.release_restore_lock(holder)
    finally:
        job_launch.release_exclusive()
    return {"deleted": removed, "freed_bytes": freed}


def keep_before_reset() -> str:
    """Call under the library's exclusive hold, right before db.reset_library:
    keeps the database (a consistent snapshot) and the titles' media in
    <library>.pre_reset_<time>, so one Reset can be undone by hand. Returns
    that folder for after_reset. Raises ServiceError with nothing changed
    when the copy can't be made."""
    parent, base = _parent_and_base()
    dest = os.path.join(parent, f"{base}{PRE_RESET_MARKER}{int(time.time() * 1000)}")
    try:
        os.mkdir(dest)
        if os.path.isfile(db.DB_PATH):
            db.snapshot_database(os.path.join(dest, "library.db"))
        if os.path.isdir(db.DRAMAS_DIR):
            # Same volume (a sibling folder): one rename, nothing copied.
            os.rename(db.DRAMAS_DIR, os.path.join(dest, "dramas"))
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise ServiceError(RESET_COPY_FAILED) from None
    return dest


def after_reset(dest: str, ok: bool) -> None:
    """ok: the reset went through, so older copies go (only the last is
    kept). Otherwise, while the live database still holds every title the
    copy does (the reset failed before deleting it), the media goes back and
    this copy is dropped; if it was already deleted or emptied, the copy is
    the only whole one and stays as it is."""
    _parent, base = _parent_and_base()
    if not ok:
        live, kept = _title_count(db.DB_PATH), _title_count(os.path.join(dest, "library.db"))
        if live is None or (kept or 0) > live:
            return
        moved = os.path.join(dest, "dramas")
        if os.path.isdir(moved) and not os.path.lexists(db.DRAMAS_DIR):
            os.rename(moved, db.DRAMAS_DIR)
        if not os.path.lexists(moved):
            shutil.rmtree(dest, ignore_errors=True)
        return
    for name, path in _folders():
        if db.is_kept_copy_name(base, name, PRE_RESET_MARKER) and path != dest:
            shutil.rmtree(path, ignore_errors=True)
