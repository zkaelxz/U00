"""The library folder on disk: durable renames, and putting back a library
an interrupted restore left renamed aside."""

import os
import shutil

import db
from lib.errors import UnfinishedRestoreError

# A restore renames the live library to <library><marker><time> before
# moving the restored one in, and deletes it only once that one checks out.
PRE_RESTORE_MARKER = ".pre_restore_"


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


def _set_aside(library_dir: str) -> list:
    parent, base = os.path.split(os.path.abspath(library_dir))
    try:
        return sorted(n for n in os.listdir(parent) if n.startswith(base + db.PRE_RESTORE_MARKER))
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
    if os.path.lexists(library_dir):
        shutil.rmtree(library_dir)   # only logs and empty folders (_is_missing)
    os.rename(os.path.join(os.path.dirname(os.path.abspath(library_dir)), aside[0]), library_dir)
    return aside[0]


def library_startup_notes(library_dir: str) -> list:
    """Runs the recovery; console lines for the owner. Call before anything
    writes into the library folder (the log lives there). Raises
    UnfinishedRestoreError."""
    notes = []
    back = db.recover_interrupted_restore(library_dir)
    if back:
        notes.append(f"Put back the library an interrupted restore had set aside in '{back}'.")
    left = _set_aside(library_dir)
    if left:
        notes.append(f"{', '.join(repr(n) for n in left)} beside the library folder was left by "
                     "a restore and may still hold saved backups, site sign-ins or the browser "
                     "extension token: move back what you need, then delete it.")
    return notes
