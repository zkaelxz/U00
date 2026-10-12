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
import shutil
import time

import background_jobs
import db
from services import disk_usage_service as du
from services import disk_usage_trash_service as trash_svc
from services.service_errors import ConflictError, InvalidInputError, ServiceError

LABEL = "Previous library kept after restore"
DELETE_WORD = "DELETE"
PRE_RESET_MARKER = ".pre_reset_"
STAGING_PREFIX = ".restore_staging_"
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
    low, base = name.lower(), base.lower()
    return (low.startswith(base + db.PRE_RESTORE_MARKER) or low.startswith(base + PRE_RESET_MARKER)
            or low.startswith(STAGING_PREFIX))


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


def _library_checks_out() -> bool:
    """The live library.db is there and passes quick_check. A restore whose
    put-back failed leaves a failed one in place and the real library aside."""
    from services import library_restore_service
    if not os.path.isfile(db.DB_PATH) or os.path.islink(db.DB_PATH):
        return False    # checked first: the check below would create an empty one
    try:
        library_restore_service._require_quick_check(db.DB_PATH, NO_LIBRARY)
    except ValueError:
        return False
    return True


def summary() -> dict:
    """{present, size_bytes, file_count, folder_count, partial, label}."""
    size = files = 0
    budget = du._Budget()
    folders = _folders()
    for name, path in folders:
        m = du._measure(path, (name,), budget)
        size += m.size
        files += m.files
    return {"present": bool(folders), "label": LABEL, "size_bytes": size, "file_count": files,
            "folder_count": len(folders), "partial": bool(budget.hit)}


def delete(confirm_text=None, expected_size_bytes=None) -> dict:
    """PERMANENT DELETE of every kept copy. Needs the typed word and the size
    the owner saw; refused while anything holds the library, while a restore
    may be between its renames, and when the library folder is missing (the
    start-up recovery puts a previous library back from there)."""
    if confirm_text != DELETE_WORD:
        raise InvalidInputError(f'Type {DELETE_WORD} to delete the previous library.')
    if not background_jobs.acquire_exclusive("Previous library delete"):
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
            removed = 0
            for _name, path in _folders():
                try:
                    trash_svc._remove_tree(path)
                except OSError:
                    raise ServiceError("Part of the previous library could not be deleted "
                                       "(something may be in use). What is left is still "
                                       "there.") from None
                removed += 1
        finally:
            db.release_restore_lock(holder)
    finally:
        background_jobs.release_exclusive()
    return {"deleted": removed, "freed_bytes": seen["size_bytes"]}


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
    kept). Otherwise the library still holds its rows: the media goes back
    and this copy is dropped."""
    _parent, base = _parent_and_base()
    if not ok:
        moved = os.path.join(dest, "dramas")
        if os.path.isdir(moved) and not os.path.lexists(db.DRAMAS_DIR):
            os.rename(moved, db.DRAMAS_DIR)
        if not os.path.lexists(moved):
            shutil.rmtree(dest, ignore_errors=True)
        return
    for name, path in _folders():
        if name.lower().startswith((base + PRE_RESET_MARKER).lower()) and path != dest:
            shutil.rmtree(path, ignore_errors=True)
