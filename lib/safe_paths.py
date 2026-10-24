"""
lib/safe_paths.py -- the one check for a filename read back from the
database before it is joined onto a title's folder.

`plain_file_in(folder, name)` returns the joined path only when `name` is a
plain file name (no separator, no `..`, no drive or UNC form, no Windows
device name) whose real path is directly inside `folder`; anything else
gives None. A restored or imported row can hold any string, so a stored
name is never trusted to stay inside the folder on its own.

`contained(root, path)` is the plain "does this real path stay inside that
root" test for paths a service builds itself.

`download_name(name)` is the ASCII name put in a Content-Disposition header.

The path is not checked for existence: callers keep their own
exists/isfile tests and treat None as "no such file".
"""
import os
import re
from typing import Optional

# Windows opens these as devices whatever the extension ("nul.txt"), and
# also with a superscript digit (COM\u00b9).
WINDOWS_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{base}{d}" for base in ("COM", "LPT") for d in "0123456789\u00b9\u00b2\u00b3"])


def is_device_name(name: str) -> bool:
    """True when Windows would open `name` as a device: the part before the
    first dot, trailing spaces dropped, is a device name."""
    return name.split(".")[0].rstrip(" ").upper() in WINDOWS_DEVICE_NAMES


def plain_file_in(folder: str, name) -> Optional[str]:
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    if any(c in name for c in ("/", "\\", ":", "\x00")) or name != os.path.basename(name):
        return None
    if is_device_name(name):
        return None
    path = os.path.join(folder, name)
    # Catches a symlink or junction in the name that points out of the folder.
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(folder):
        return None
    return path


def contained(root: str, path: str) -> bool:
    """True when `path` resolves (symlinks followed) to `root` or something
    below it."""
    real_root = os.path.realpath(root)
    try:
        return os.path.commonpath([real_root, os.path.realpath(path)]) == real_root
    except ValueError:  # different drives on Windows
        return False


def download_name(name: str) -> str:
    """Only [A-Za-z0-9._-], and no leading dot so a browser never saves a
    hidden file."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "download"
