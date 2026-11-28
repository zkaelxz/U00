"""services/log_copy_service.py -- the weekly scrubbed log copy: the Send logs zip, saved by itself into a folder the PC's admin picked."""

import contextlib
import logging
import os
import time

import db
from lib.errors import ConflictError, InvalidInputError
from services import diagnostics_gaps_service as gaps

log = logging.getLogger(__name__)

FOLDER_KEY = "logs.weekly_copy_folder"
LAST_KEY = "logs.weekly_copy_last"
TRIED_KEY = "logs.weekly_copy_tried"
RETRY_SECONDS = 3600
INTERVAL_SECONDS = 7 * 24 * 3600
KEEP = 4
# Not the Send logs name, so a folder that also holds hand-saved zips never has those rotated away.
_PREFIX, _SUFFIX = "baihe-weekly-logs-", ".zip"
_MAX_FOLDER_LEN = 1024


def get_folder() -> str:
    value = db.get_app_setting(FOLDER_KEY, "")
    return value if isinstance(value, str) else ""


def overview() -> dict:
    last = db.get_app_setting(LAST_KEY)
    # Only the last folder name, like the watch folder: a path is never sent back.
    return {"enabled": bool(get_folder()), "folder_name": os.path.basename(get_folder().rstrip("\\/")) or get_folder(),
            "keep": KEEP,
            "last_copy_at": last if isinstance(last, (int, float)) else None}


def _check_folder(value) -> str:
    if not isinstance(value, str) or len(value) > _MAX_FOLDER_LEN or "\x00" in value:
        raise InvalidInputError("The log folder must be a full folder path.")
    value = value.strip()
    if not os.path.isabs(value):
        raise InvalidInputError("The log folder must be a full folder path, starting with the drive letter.")
    real = os.path.realpath(value)
    for prefix in ("\\\\?\\UNC\\", "\\\\?\\"):   # \\?\ forms would slip past the comparisons below
        if real.startswith(prefix):
            real = ("\\\\" if "UNC" in prefix else "") + real[len(prefix):]
            break
    if not os.path.isdir(real):
        raise InvalidInputError("That folder does not exist.")
    # Logs of a library or of the app itself must not land inside what a backup or an update replaces.
    lib = os.path.realpath(db.LIBRARY_DIR)
    app_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    for protected in (lib, app_dir):
        if real == protected or real.startswith(protected + os.sep):
            raise InvalidInputError("Pick a folder outside the library and the program files.")
    probe = os.path.join(real, f".baihe-write-test-{os.getpid()}")
    try:
        with open(probe, "wb"):
            pass
        os.remove(probe)
    except OSError:
        raise InvalidInputError("Baihe cannot write to that folder.") from None
    return real


def set_folder(value) -> dict:
    """Turns the weekly copy on for a folder, or off for an empty value. Existing copies stay."""
    folder = _check_folder(value) if (not isinstance(value, str) or value.strip()) else ""
    db.set_app_setting(FOLDER_KEY, folder)
    if folder:
        db.set_app_setting(LAST_KEY, None)   # the first copy comes with the next tick
    return overview()


def _copies(folder: str) -> list:
    try:
        names = [n for n in os.listdir(folder) if n.startswith(_PREFIX) and n.endswith(_SUFFIX)]
    except OSError:
        return []
    return sorted(names)   # the file name carries the time, so name order is age order


def write_copy(now: float | None = None) -> dict:
    """Writes one zip and drops the oldest beyond KEEP. Counts only, never a path, in the log."""
    folder = get_folder()
    if not folder:
        raise ConflictError("The weekly log copy is off.")
    if not os.path.isdir(folder):
        raise OSError("log folder unreachable")   # before the costly build
    data = gaps.build_support_bundle()
    name = gaps.support_bundle_filename(now).replace("baihe-logs-", _PREFIX, 1)
    target = os.path.join(folder, name)
    tmp = target + ".part"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)
    removed = 0
    for old in _copies(folder)[:-KEEP]:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(folder, old))
            removed += 1
    db.set_app_setting(LAST_KEY, time.time() if now is None else now)
    kept = len(_copies(folder))
    log.info("Weekly log copy written: %d KB, %d kept, %d removed", len(data) // 1024, kept, removed)
    return {"bytes": len(data), "kept": kept, "removed": removed}


def periodic_tick(now: float | None = None) -> bool:
    """From the API's background poller: writes a copy once a week while a folder is set. Never raises."""
    try:
        if not get_folder():
            return False
        now = time.time() if now is None else now
        last = db.get_app_setting(LAST_KEY)
        if isinstance(last, (int, float)) and now - last < INTERVAL_SECONDS:
            return False
        tried = db.get_app_setting(TRIED_KEY)
        if isinstance(tried, (int, float)) and now - tried < RETRY_SECONDS:
            return False   # a folder that fails (unplugged drive) is retried hourly, not every poll
        db.set_app_setting(TRIED_KEY, now)
        write_copy(now)
        return True
    except ConflictError:
        return False   # another logs zip is being made; the next tick tries again
    except Exception as exc:
        log.warning("Weekly log copy failed: %s", type(exc).__name__)
        return False
