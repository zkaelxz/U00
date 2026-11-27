"""Finds the self-contained HTML pages the owner drops in the data folder's private-pages folder."""

import os
import re

import portable
from lib.errors import NotFoundError

FOLDER_NAME = "private-pages"
# One path segment of plain characters: a name that can't hold a separator, a
# drive, a dot-dot or a stream suffix never needs resolving to be safe.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\.html")
# CON.html and friends name devices on Windows, which isfile() may not answer quickly.
_RESERVED = re.compile(r"(con|prn|aux|nul|com[0-9]|lpt[0-9])\.html", re.IGNORECASE)
_NOT_FOUND = "Not found."


def _folder() -> str:
    return os.path.join(portable.data_dir(), FOLDER_NAME)


def page_path(name: str) -> str:
    """The file for `name`, or NotFoundError for a bad name, a missing file or
    anything that is not a plain file directly inside the folder (a link out
    of it included)."""
    if not _NAME.fullmatch(name or "") or _RESERVED.fullmatch(name):
        raise NotFoundError(_NOT_FOUND)
    folder = os.path.realpath(_folder())
    path = os.path.join(folder, name)
    if os.path.islink(path) or not os.path.isfile(path) or os.path.realpath(path) != path:
        raise NotFoundError(_NOT_FOUND)
    return path
