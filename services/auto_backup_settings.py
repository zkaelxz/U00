"""
services/auto_backup_settings.py -- the automatic backup's saved settings, its
run bookkeeping and when the next run is due.

- Settings (app_settings): enabled (off by default), frequency
  (daily/weekly/monthly, daily by default), include_media (off: database
  only) and folder ("" = <library>/backups/auto, which every backup already
  skips because backups/ is an excluded top-level entry; a custom folder
  must be outside the library or inside its backups/ folder, and not
  inside another library's folder). A value the owner already saved is
  kept; the defaults only fill in what was never saved.
- State (STATE_KEY): when the last run was tried and succeeded and its
  last error or cancel. The due-check reads it; the backup job writes it
  (auto_backup_service).

Saving settings (and moving the copies with the folder) is
auto_backup_service.set_settings. No FastAPI import.
"""

import datetime
import os

import db
from services import library_admin_service as las
from services.service_errors import InvalidInputError, ServiceError

SETTINGS_KEY = "auto_backup.settings"


STATE_KEY = "auto_backup.state"


FREQUENCIES = {"daily": 1, "weekly": 7, "monthly": 30}  # days between runs


DEFAULT_SETTINGS = {"enabled": False, "frequency": "daily", "include_media": False,
                    "folder": ""}


DEFAULT_SUBDIR = ("backups", "auto")


_MAX_FOLDER_LEN = 1024


# After a failed run the scheduled check waits this long before trying the
# (possibly large) backup again.
RETRY_AFTER_FAILURE = datetime.timedelta(days=1)


def get_settings() -> dict:
    stored = db.get_app_setting(SETTINGS_KEY, None)
    out = dict(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        for key, default in DEFAULT_SETTINGS.items():
            if isinstance(stored.get(key), type(default)):
                out[key] = stored[key]
    if out["frequency"] not in FREQUENCIES:
        out["frequency"] = DEFAULT_SETTINGS["frequency"]
    if out["folder"]:
        # Re-checked on every read: a whole-library restore can bring in a
        # folder setting saved on another PC. A folder that breaks the
        # rules falls back to the default; one that is merely missing (a
        # drive not plugged in) is kept, so its backup fails visibly
        # rather than landing somewhere else.
        try:
            out["folder"] = _check_folder(out["folder"], require_exists=False)
        except InvalidInputError:
            out["folder"] = ""
    return out


def _check_folder(value, require_exists: bool = True) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > _MAX_FOLDER_LEN or "\x00" in value:
        raise InvalidInputError("The backup folder must be a full folder path.")
    value = value.strip()
    if not os.path.isabs(value):
        raise InvalidInputError("The backup folder must be a full folder path, starting "
                                "with the drive letter.")
    real = os.path.realpath(value)
    lib = os.path.realpath(db.LIBRARY_DIR)
    backups = os.path.join(lib, "backups")
    if (real == lib or real.startswith(lib + os.sep)) and not (
            real == backups or real.startswith(backups + os.sep)):
        raise InvalidInputError("The backup folder can't be inside the library (it would be "
                                "copied into its own backups). Pick a folder outside it.")
    artifact_dirs = {os.path.join(lib, *sub) for sub in las.ARTIFACT_SUBDIRS.values()}
    if real in artifact_dirs:
        raise InvalidInputError("That folder holds the manual backups and exports; pick a "
                                "folder of its own (the default is fine).")
    # Another library's folder (or its backups/auto): its copies would
    # share the folder with ours, and its default folder adopts untagged
    # copies. Detected by a library.db in the folder or above it.
    path = real
    while path != lib:
        if os.path.isfile(os.path.join(path, "library.db")):
            raise InvalidInputError("That folder is inside another library's folder. Pick a "
                                    "folder of its own.")
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    if require_exists and not os.path.isdir(real):
        raise InvalidInputError("That backup folder doesn't exist. Create it first.")
    return os.path.normpath(value)


def folder_path(folder: str) -> str:
    return folder or os.path.join(db.LIBRARY_DIR, *DEFAULT_SUBDIR)


def _get_state() -> dict:
    stored = db.get_app_setting(STATE_KEY, None)
    return dict(stored) if isinstance(stored, dict) else {}


def _update_state(**fields):
    state = _get_state()
    state.update(fields)
    db.set_app_setting(STATE_KEY, state)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    try:
        return dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, ValueError):     # a date at the very edge of the range
        return dt.isoformat(timespec="seconds")


def _parse(value):
    """A UTC datetime from an ISO string, or None -- also for one that
    can't be moved to UTC (an offset at the edge of the date range)."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError):
        return None


def _later(a: datetime.datetime, b: datetime.datetime, slack=datetime.timedelta(0)) -> bool:
    """a is more than `slack` after b; never raises at the edge of the date
    range (nothing is later than the latest date)."""
    try:
        return a > b + slack
    except OverflowError:
        return False


def next_run_at(settings=None, state=None):
    """When the next automatic backup is due (UTC datetime), or None when
    automatic backups are off. Never run yet = due now."""
    settings = settings or get_settings()
    if not settings["enabled"]:
        return None
    state = _get_state() if state is None else state
    last = _parse(state.get("last_success_at"))
    now = _now()
    # Never run, or a last run dated in the future (the clock ran ahead and
    # was corrected): due at the next check.
    if last is None or last > now:
        return now
    return last + datetime.timedelta(days=FREQUENCIES[settings["frequency"]])


def is_due(now=None, settings=None, state=None) -> bool:
    settings = settings or get_settings()
    if not settings["enabled"]:
        return False
    state = _get_state() if state is None else state
    now = now or _now()
    attempt = _parse(state.get("last_attempt_at"))
    cancelled = state.get("last_cancelled_at")
    stopped = state.get("last_error") or (cancelled is not None
                                          and cancelled == state.get("last_attempt_at"))
    if (stopped and attempt is not None and attempt <= now
            and now - attempt < RETRY_AFTER_FAILURE):
        return False
    last = _parse(state.get("last_success_at"))
    if last is None or last > now:     # a future date: the clock was wrong then
        return True
    return now - last >= datetime.timedelta(days=FREQUENCIES[settings["frequency"]])


def _target_dir(create: bool) -> str:
    path = folder_path(get_settings()["folder"])
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            raise ServiceError("The backup folder could not be created.") from None
    return path
