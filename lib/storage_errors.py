"""
lib/storage_errors.py -- a full disk, a read-only or refused file, a file
another program holds open, or a damaged or busy SQLite database, told
apart from other failures and given a fixed sentence.

The exception's own text can carry the library path, so callers show the
sentence and keep the raw text for the log. The API maps these to HTTP
responses (`api/error_handlers.py`); the job runner stores the sentence as
a failed job's error. A foreign key refusal is here too: on a shared
server it is a write that lost a race with a delete.
"""

import builtins
import errno
import os
import re
import sqlite3
from typing import NamedTuple, Optional


class StorageProblem(NamedTuple):
    code: str
    status: int
    message: str


DISK_FULL = StorageProblem("disk_full", 507, "The disk is full.")
READ_ONLY = StorageProblem("library_read_only", 503, "The library folder is read-only.")
DB_DAMAGED = StorageProblem("database_corrupt", 500,
                            "The library database looks damaged; restore from a backup.")
# Windows' C runtime reports a file another program holds open as the same
# EACCES as a refused permission, so the sentence names both causes.
PERMISSION_DENIED = StorageProblem(
    "permission_denied", 503,
    "Permission denied: Baihe is not allowed to open a file it needs, or another program has it "
    "open. The app log names the file.")
# 503, not 409: the client's view of the record is not stale, the file is busy.
FILE_IN_USE = StorageProblem("file_in_use", 503,
                             "A file Baihe needs is open in another program. Close it and try again.")
DB_BUSY = StorageProblem("library_busy", 503, "The library database is busy. Try again in a moment.")
# On a shared server one member can delete a title (or a line, a series) while
# another member's save or job is writing to it: the write's checks passed
# before the delete, so only the foreign key catches it.
GONE_MEANWHILE = StorageProblem(
    "conflict", 409,
    "Something this change refers to no longer exists; it may have just been deleted. "
    "Reload and try again.")

# Windows reports a file another process holds open as a PermissionError
# (EACCES) too; only its winerror tells it apart from a real access refusal.
_WIN_SHARING_VIOLATIONS = {32, 33}


def _opened_folder_as_file(exc: OSError) -> bool:
    """Windows' C runtime gives EACCES, with no winerror, for open() on a
    folder: a bug in the caller, not a permission problem. A real refusal on
    a folder (Win32 calls carry winerror 5; POSIX has EISDIR for this) is
    still one."""
    if os.name != "nt" or getattr(exc, "winerror", None) is not None:
        return False
    try:
        return bool(exc.filename) and os.path.isdir(exc.filename)
    except (TypeError, ValueError, OSError):
        return False


def classify(exc: BaseException) -> Optional[StorageProblem]:
    """The problem `exc` is, else None. Only these causes are recognised, so
    a bug that happens to raise OSError is not dressed up as one."""
    if isinstance(exc, OSError) and not isinstance(exc, sqlite3.Error):
        if exc.errno == errno.ENOSPC:
            return DISK_FULL
        if exc.errno == errno.EROFS:
            return READ_ONLY
        if getattr(exc, "winerror", None) in _WIN_SHARING_VIOLATIONS:
            return FILE_IN_USE
        if exc.errno in (errno.EACCES, errno.EPERM) and not _opened_folder_as_file(exc):
            return PERMISSION_DENIED
    if isinstance(exc, sqlite3.Error):
        # Opening the library when no other connection has it open creates
        # its WAL index file; on a full disk that fails as a generic "disk
        # I/O error", so every request, reads included, would be a mystery
        # 500 rather than "the disk is full".
        if getattr(exc, "sqlite_errorname", None) == "SQLITE_IOERR_SHMSIZE":
            return DISK_FULL
        text = str(exc).lower()
        if "disk image is malformed" in text or "file is not a database" in text:
            return DB_DAMAGED
        if "readonly database" in text or "read-only" in text:
            return READ_ONLY
        if "database or disk is full" in text:
            return DISK_FULL
        if "database is locked" in text:
            return DB_BUSY
        if "foreign key constraint failed" in text:
            return GONE_MEANWHILE
    return None


# A worker process sends back only the exception's type name and text.
_CODE_IN_TEXT = re.compile(r"^\[(Errno|WinError) (\d+)\]")
_WIN_DISK_FULL = {39, 112}
_WIN_ACCESS_DENIED = 5
_SQLITE_TYPES = {"OperationalError", "DatabaseError", "IntegrityError"}
_OS_ERROR_TYPES = {name for name, obj in vars(builtins).items()
                   if isinstance(obj, type) and issubclass(obj, OSError)}


def classify_text(exc_type: str, message: str) -> Optional[StorageProblem]:
    """classify() for an exception known only by its type name and text."""
    message = message or ""
    if exc_type in _SQLITE_TYPES:
        return classify(sqlite3.OperationalError(message))
    m = _CODE_IN_TEXT.match(message) if exc_type in _OS_ERROR_TYPES else None
    if not m:
        return None
    kind, number = m.group(1), int(m.group(2))
    if kind == "Errno":
        return classify(OSError(number, message))
    if number in _WIN_SHARING_VIOLATIONS:
        return FILE_IN_USE
    if number == _WIN_ACCESS_DENIED:
        return PERMISSION_DENIED
    if number in _WIN_DISK_FULL:
        return DISK_FULL
    return None


def require_readable(path: str) -> None:
    """Open `path` once and close it, so a refused or locked file raises here
    and gets its error response. Starlette's FileResponse sends 200 and the
    headers before it opens the file, so a failure there is an empty body."""
    with open(path, "rb"):
        pass
