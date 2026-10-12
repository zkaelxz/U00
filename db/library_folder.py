"""The library folder on disk: durable renames, and putting back a library
an interrupted restore left renamed aside."""

import os

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


def recover_interrupted_restore(library_dir: str):
    """A crash between a restore's renames leaves no library folder and the
    previous library aside: renames that one back (returns its name) rather
    than starting on a new, empty library. Several aside: refuses, since
    which one is current can't be told from here."""
    if os.path.lexists(library_dir):
        return None
    parent, base = os.path.split(os.path.abspath(library_dir))
    try:
        aside = sorted(n for n in os.listdir(parent) if n.startswith(base + db.PRE_RESTORE_MARKER))
    except FileNotFoundError:
        return None
    if len(aside) > 1:
        raise UnfinishedRestoreError(
            f"The library folder '{base}' is missing and {len(aside)} folders left by "
            f"unfinished restores sit beside it ({', '.join(aside)}). Rename the one to "
            f"keep to '{base}' and start Baihe again.")
    if aside:
        os.rename(os.path.join(parent, aside[0]), library_dir)
        return aside[0]
    return None
