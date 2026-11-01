"""The library folder on disk: durable renames, and putting back a library
an interrupted restore left renamed aside."""

import contextlib
import os
import re
import shutil

import db
from lib.errors import UnfinishedRestoreError

# A restore renames the live library to <library><marker><time> before
# moving the restored one in, and deletes it only once that one checks out.
PRE_RESTORE_MARKER = ".pre_restore_"


def is_kept_copy_name(base: str, name: str, marker: str) -> bool:
    """Whether `name` is <base><marker><digits>, the time-stamped name Baihe
    gives a library it sets aside. A folder someone else named
    <base><marker>... is not one: it must never be renamed into the library
    or deleted with Baihe's own copies. Case follows the file system."""
    prefix = os.path.normcase(base + marker)
    name = os.path.normcase(name)
    return name.startswith(prefix) and re.fullmatch(r"[0-9]+", name[len(prefix):], re.ASCII) is not None


def fsync_dir(path: str):
    """Makes a rename into `path` durable; raises OSError. POSIX only:
    Windows can't open a directory and commits the rename with the file."""
    if os.name != "posix":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_path(library_dir: str) -> str:
    return os.path.abspath(library_dir) + ".restore-lock"


def _still_named(fd: int, path: str) -> bool:
    """Whether `path` still names the file open as `fd` (a holder that just
    finished may have removed it after this process opened it)."""
    try:
        a, b = os.fstat(fd), os.stat(path)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def hold_restore_lock(library_dir: str):
    """Held by a restore across its renames, so another process's start-up
    check can't take a moment between them for a crash. The OS lets go when
    the holder dies, so a crashed restore never leaves it held. A holder for
    release_restore_lock, or None when another process holds it."""
    path = _lock_path(library_dir)
    for _attempt in range(2):
        fd = db._lock_file(path, create=True)
        if fd is None:
            return None
        # A lock on a file the last holder already removed guards nothing:
        # a third process would lock a new file at the same name.
        if os.name != "posix" or _still_named(fd, path):
            return [fd, path]
        db._release_lock([fd])
    return None


def release_restore_lock(holder) -> None:
    """Lets go and removes the lock file when no other process has it, so
    one stays only while a restore or recovery may be running (or after a
    crash, where the next holder removes it)."""
    if not holder:
        return
    path = holder[1] if len(holder) > 1 else None
    if path and os.name == "posix" and holder[0] is not None and _still_named(holder[0], path):
        # Removed while still locked: a process that opened it meanwhile
        # sees the name gone (_still_named) and doesn't count its lock.
        with contextlib.suppress(OSError):
            os.remove(path)
    db._release_lock(holder)
    if path and os.name != "posix":
        # Windows refuses while another process has it open, which keeps it.
        with contextlib.suppress(OSError):
            os.remove(path)


def _set_aside(library_dir: str) -> list:
    parent, base = os.path.split(os.path.abspath(library_dir))
    try:
        return sorted(n for n in os.listdir(parent)
                      if db.is_kept_copy_name(base, n, db.PRE_RESTORE_MARKER))
    except FileNotFoundError:
        return []


def _is_missing(library_dir: str) -> bool:
    """No library: no folder, or one without library.db holding only what
    a start writes before its first database call (logs/, empty folders)."""
    if not os.path.isdir(library_dir) or os.path.islink(library_dir):
        return not os.path.lexists(library_dir)
    if os.path.lexists(os.path.join(library_dir, "library.db")):
        return False
    # Closed before returning: Windows can't remove a folder held open.
    with os.scandir(library_dir) as entries:
        return all(e.name == "logs" or (e.is_dir(follow_symlinks=False) and not os.listdir(e.path))
                   for e in entries)


def recover_interrupted_restore(library_dir: str):
    """A crash between a restore's renames leaves no library and the
    previous one aside: renames that one back (returns its name) rather
    than starting on a new, empty library. Several aside: refuses, since
    which one is current can't be told from here."""
    if not _is_missing(library_dir):
        return None
    aside = _set_aside(library_dir)
    base = os.path.basename(os.path.abspath(library_dir))
    if len(aside) > 1:
        raise UnfinishedRestoreError(
            f"The library folder '{base}' is missing and {len(aside)} folders left by "
            f"unfinished restores sit beside it ({', '.join(aside)}). Rename the one to "
            f"keep to '{base}' and start Baihe again.")
    if not aside:
        return None
    holder = db.hold_restore_lock(library_dir)
    if holder is None:
        return None   # a restore in another process is between its renames
    try:
        if os.path.lexists(library_dir):
            shutil.rmtree(library_dir)   # only logs and empty folders (_is_missing)
        os.rename(os.path.join(os.path.dirname(os.path.abspath(library_dir)), aside[0]),
                  library_dir)
    finally:
        db.release_restore_lock(holder)
    return aside[0]


def library_startup_notes(library_dir: str) -> list:
    """Runs the recovery; console lines for the owner. Call before anything
    writes into the library folder (the log lives there). Raises
    UnfinishedRestoreError."""
    notes = []
    back = db.recover_interrupted_restore(library_dir)
    if back:
        notes.append(f"Put back the library an interrupted restore had set aside in '{back}'.")
    holder = db.hold_restore_lock(library_dir)
    if holder is None:
        return notes   # a restore is running: its folder set aside is expected
    db.release_restore_lock(holder)
    left = _set_aside(library_dir)
    if left:
        notes.append(f"{', '.join(repr(n) for n in left)} beside the library folder holds the "
                     "previous library a restore set aside: its database and possibly media, "
                     "saved backups, site sign-ins and the browser extension token. Don't delete "
                     "it until you have checked the library; move back what you need first.")
    return notes
