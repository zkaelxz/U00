"""
services/backup_space_check.py -- the free-space check the automatic backup
makes before it writes a copy, so a nearly full drive skips the run (recorded
like a failed one) instead of filling up with a partial copy.

The expected size is the last successful copy's size when that copy was of
the same kind (database only, or with media), else the library's own size
(the database file, or every file a backup with media takes). A drive that
doesn't report its free space never blocks a backup.
"""

import os
import shutil

import db
from services import library_admin_service as las

SPACE_FACTOR = 1.2
LOW_SPACE = ("The automatic backup was skipped: the backup drive doesn't have enough free "
             "space for a new copy. No copies were deleted. Free some space or choose another "
             "backup folder.")


def copy_kind(include_media: bool) -> str:
    return "full" if include_media else "db-only"


def expected_size(include_media: bool, state: dict) -> int:
    last = state.get("last_copy_size")
    if type(last) is int and last > 0 and state.get("last_copy_kind") == copy_kind(include_media):
        return last
    if include_media:
        return las._library_size()
    try:
        return os.path.getsize(db.DB_PATH)
    except OSError:
        return 0


def has_room(folder: str, include_media: bool, state: dict) -> bool:
    try:
        free = shutil.disk_usage(folder).free
    except OSError:
        return True
    return free >= SPACE_FACTOR * expected_size(include_media, state)
