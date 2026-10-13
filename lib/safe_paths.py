"""
lib/safe_paths.py -- the one check for a filename read back from the
database before it is joined onto a title's folder.

`plain_file_in(folder, name)` returns the joined path only when `name` is a
plain file name (no separator, no `..`, no drive or UNC form, no Windows
device name) whose real path is directly inside `folder`; anything else
gives None. A restored or imported row can hold any string, so a stored
name is never trusted to stay inside the folder on its own.

The path is not checked for existence: callers keep their own
exists/isfile tests and treat None as "no such file".
"""
import os
import re
from typing import Optional

# Windows opens these as devices whatever the extension ("nul.txt").
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])$", re.IGNORECASE)


def plain_file_in(folder: str, name) -> Optional[str]:
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    if any(c in name for c in ("/", "\\", ":", "\x00")) or name != os.path.basename(name):
        return None
    if _RESERVED.match(name.split(".")[0].rstrip(" ")):
        return None
    path = os.path.join(folder, name)
    # Catches a symlink or junction in the name that points out of the folder.
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(folder):
        return None
    return path
