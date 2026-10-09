"""
services/auto_backup_service.py -- an opt-in automatic backup that keeps a
few rotating copies, plus restoring a single drama from any of them.

- Settings (app_settings): enabled (off by default), frequency
  (daily/weekly/monthly, daily by default), include_media (off: database
  only) and folder ("" = <library>/backups/auto, which every backup already
  skips because backups/ is an excluded top-level entry; a custom folder
  must be outside the library or inside its backups/ folder, and not
  inside another library's folder). A value the owner already saved is
  kept; the defaults only fill in what was never saved.
- Each successful run writes a NEW copy, baihe_snapshot-YYYYMMDD-HHMMSS.zip
  (UTC; library.db + manifest.json, plus media when include_media), with
  library_admin_service.write_backup_zip -- the same writer as the manual
  backup -- into a hidden partial file next to it, validated (zip checks +
  SQLite quick_check + manifest), flushed to disk, and only then linked
  into place under a name no file has (never over an existing file). Only
  after that are old copies pruned. A failed backup or failed check never
  deletes anything.
- Ownership: each library has a random id, made once and kept in
  app_settings (IDENTITY_KEY) with the time it was made, a sequence number
  that goes up by one for every copy it writes, and the (sequence, name)
  of each copy it wrote that is still in the folder (never taken from a
  restored backup, see workspace_job_service._RESTORE_KEPT_APP_SETTINGS).
  The id and sequence go into every copy's manifest. A backup folder may
  be shared (a synced folder used by two PCs, possibly two PCs running a
  hand-copied library with the same id), so neither a file's name nor the
  id alone proves who wrote it: the rotation, "delete all", and a folder
  move touch only MANAGED copies -- ones whose manifest carries this
  library's id and a sequence this library recorded writing under that
  name, plus copies written before copies carried an id, dated before the
  id was made, when they sit in this library's own default folder inside
  the library (adopted). Every other copy (another library's or a clone's,
  an id-less copy in a custom folder, one that can't be read) is listed
  but takes no slot and is only deleted when the owner names it or asks
  for include_unmanaged. When the folder holds a copy with this id
  numbered above the stored counter (a clone, or a library.db restored by
  hand), that run prunes nothing and logs a warning.
- Retention (deterministic, applied after each successful run, under
  _snapshot_lock): one copy per day for the last 2 days, plus the first
  copy of each of the last 2 weeks. Precisely, among the managed copies
  dated no later than the copy just written (and, for this library's own,
  with a lower sequence number): "daily" = the new copy, plus
  the newest copy of the most recent earlier UTC day that has one (so extra
  runs on one day replace that day's earlier copy, never yesterday's);
  "weekly" = among the remaining copies, the FIRST (oldest) copy of each ISO
  week (UTC), keeping the 2 most recent such weeks. Every other such copy
  is deleted, so at most 4 remain. The copy just written is always kept and
  is the anchor: a copy dated after it (a future-dated name, a legacy file
  with a future time, a clock set back) takes no slot and is never deleted.
  Two runs within one second get names one second apart. A copy's date is
  the one in its name; the legacy single snapshot "baihe_snapshot.zip" from
  before rotation counts as a copy dated by its file time and ages out
  under the same rule. A copy that can't be opened right now (an OS error:
  locked, no permission, drive hiccup) or is damaged (not a zip, bad
  manifest) proves no owner: it takes no slot and is never deleted by the
  rotation. Pruning only ever touches regular files in the backup folder
  whose name is exactly the copy pattern or the legacy name: never other
  files, never symlinks, never a name taken from a request.
- The default restore pick (no copy named) is this library's copy with the
  highest sequence number, never a guess from file or wall-clock times.
  When that can't be told for sure (none of this library's copies is
  readable, another copy with this id is numbered as high or higher, a
  lower-numbered copy claims to be more than _ORDER_TOLERANCE newer, an
  unmanaged copy claims to be newer, a damaged copy's name is newer, or a
  copy can't be opened right now) nothing is picked: a ConflictError
  with details {reason: "choose_copy", candidates} asks the owner to name
  one.
- The due-check (check_and_run) runs at API startup and hourly from the
  API's existing background poller (api/background.py). A scheduled run
  never starts while any job runs or a restore/maintenance holds the
  library; it is simply tried again at the next check.
- restore_drama copies one drama out of a copy (the default pick above, or
  one named by the caller and matched against the folder's listing, never
  used as a path), read from a read-only temp copy of its library.db, into
  the live library: the drama row and every child table it cascades to (see
  CHILD_TABLES), plus its series when that series is gone, plus its folder
  when the copy has media. If the drama's id is still in use it comes back
  as a new drama (new id, title suffixed "(restored <date>)"); other dramas
  are never touched. The result names the copy it came from.

No result or error message carries a filesystem path (copies are named by
file name only), except the folder setting the owner typed themselves (all
routes are local_only).
No FastAPI import.
"""

import contextlib
import datetime
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import threading
import time
import uuid
import zipfile
import storage
import zlib

import background_jobs
import core
import db
import sensitivity_preset
from db import fsync_dir as _fsync_dir
from services import delete_service
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

SETTINGS_KEY = "auto_backup.settings"
STATE_KEY = "auto_backup.state"
# {"library_id": 32 hex, "sequence": last copy number, "created_at": ISO
# time the id was made, "written": [[sequence, file name], ...] of this
# library's copies still in the folder}. A whole-library restore keeps the
# current value (workspace_job_service copies this key from the live
# library).
IDENTITY_KEY = "auto_backup.identity"
_LIBRARY_ID_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
# A bound on the written list; a copy that falls off it only becomes
# unmanaged (kept), never someone else's.
_MAX_WRITTEN = 200
# The sequence decides which copy is newest, even when a clock correction
# makes created_at disagree; but a lower-numbered copy claiming to be more
# than this much newer means the clock or the counter can't be trusted, and
# the owner chooses.
_ORDER_TOLERANCE = datetime.timedelta(days=1)
# What a copy's manifest says about who made it (see _classify).
_THIS, _LEGACY, _OTHER = "this", "legacy", "other"
_MANAGED = (_THIS, _LEGACY)
FREQUENCIES = {"daily": 1, "weekly": 7, "monthly": 30}  # days between runs
DEFAULT_SETTINGS = {"enabled": False, "frequency": "daily", "include_media": False,
                    "folder": ""}
DEFAULT_SUBDIR = ("backups", "auto")
# Copies are named after their UTC time; the single snapshot kept before
# rotation had the fixed legacy name and is still read (and aged out).
_COPY_NAME_RE = re.compile(r"baihe_snapshot-([0-9]{8})-([0-9]{6})\.zip", re.ASCII)
LEGACY_SNAPSHOT_NAME = "baihe_snapshot.zip"
KEEP_DAILY = 2
KEEP_WEEKLY = 2
_MAX_COPY_NAME_LEN = 64
MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = 1
_MANIFEST_MAX_BYTES = 8 * 1024 * 1024
_MAX_FOLDER_LEN = 1024
# What a single-drama restore extracts is capped per member (the whole-zip
# upload caps don't apply to the app's own snapshot; see _verify_snapshot).
_MAX_MEMBER_BYTES = wjs.MAX_RESTORE_MEMBER_BYTES
# After a failed run the scheduled check waits this long before trying the
# (possibly large) backup again.
RETRY_AFTER_FAILURE = datetime.timedelta(days=1)

JOB_ID = las.AUTO_BACKUP_JOB_ID
CHECK_INTERVAL_SECONDS = 3600.0

RESTORE_CONFIRM_TEXT = "RESTORE"
DELETE_CONFIRM_TEXT = "DELETE"

_NO_SNAPSHOT = "There is no backup snapshot yet."
_NO_COPY = "That backup copy doesn't exist (it may have been rotated out)."
_BAD_SNAPSHOT = "The backup snapshot could not be read; it may be damaged."
_CHOOSE = ("Choose which backup copy to use: the newest one can't be told for sure "
           "(copies from another library or from before this update, or dates that "
           "disagree).")
_UNMANAGED = ("That copy isn't managed by this library (another library's, one made before "
              "copies were tagged, or one that can't be read). Confirm deleting it "
              "explicitly.")
_FAILED = "The backup could not be written; the existing copies were kept."
_CHECK_FAILED = "The automatic backup check failed, so no backup was started."

# Held while a copy is added, pruned, moved, deleted, or read for a restore,
# so none of those see a half-written folder (and Windows never deletes or
# renames a file another thread has open).
_snapshot_lock = threading.Lock()
_last_check = None          # time.monotonic() of the last periodic check
_tick_lock = threading.Lock()


# --------------------------------------------------------------------------
# settings and state
# --------------------------------------------------------------------------

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


def set_settings(enabled=None, frequency=None, include_media=None, folder=None) -> dict:
    """Updates the given fields (None = unchanged) and returns
    settings_overview()."""
    if enabled is not None and not isinstance(enabled, bool):
        raise InvalidInputError("enabled must be true or false.")
    if frequency is not None and frequency not in FREQUENCIES:
        raise InvalidInputError("Unknown frequency.", details={"allowed": list(FREQUENCIES)})
    if include_media is not None and not isinstance(include_media, bool):
        raise InvalidInputError("include_media must be true or false.")
    if folder is not None:
        folder = _check_folder(folder)
    moving = folder is not None and folder != get_settings()["folder"]
    guard = contextlib.nullcontext()
    if moving:
        # A folder move is refused while a whole-library restore, bulk
        # delete or storage cleanup holds the library, and holds off a
        # restore until it ends.
        las.refuse_during_maintenance("backup folder change")
        guard = las.maintenance("changing the backup folder")
    # One hold for the read, the running-job check, a folder move and the
    # save: two concurrent saves can't drop each other's fields, and a
    # backup can't start in between and write to the old folder (_start
    # takes the same lock).
    with guard, _snapshot_lock:
        current = get_settings()
        for key, value in (("enabled", enabled), ("frequency", frequency),
                           ("include_media", include_media)):
            if value is not None:
                current[key] = value
        if folder is not None and folder != current["folder"]:
            if not moving:
                raise ConflictError("The backup settings changed meanwhile; try again.")
            if job_running():
                raise ConflictError("A backup is running -- change the folder when it "
                                    "finishes.")
            _move_copies(current["folder"], folder)
            current["folder"] = folder
        db.set_app_setting(SETTINGS_KEY, current)
    # Not under the lock: snapshot_info takes it and it isn't reentrant.
    return settings_overview()


def folder_path(folder: str) -> str:
    return folder or os.path.join(db.LIBRARY_DIR, *DEFAULT_SUBDIR)


def _move_copies(old_folder: str, new_folder: str):
    """The managed copies (see _classify) move to the new folder with the
    folder setting; unmanaged ones (another library's in a shared folder)
    stay where they are. A file of the same name in the new folder is
    replaced only when it is this library's own copy; anything else there
    refuses the move. The caller holds _snapshot_lock and has checked no
    backup runs. On failure the copies already moved are moved back and the
    error is raised, so the setting is not changed and the copies stay
    together."""
    src_dir, dest_dir = folder_path(old_folder), folder_path(new_folder)
    ident = _identity_info()
    copies = [c for c in _classify(src_dir, ident) if c["owner"] in _MANAGED]
    if not copies:
        return
    moved = []
    try:
        os.makedirs(dest_dir, exist_ok=True)
        # The same folder under another path (a link, a mapped drive vs its
        # network path): nothing to move, and "moving" a copy onto itself
        # could remove it.
        if os.path.samefile(src_dir, dest_dir):
            return
        for copy in copies:
            dest = os.path.join(dest_dir, copy["name"])
            if os.path.lexists(dest) and not (
                    os.path.isfile(dest) and not os.path.islink(dest)
                    and _recorded(ident, _read_copy(dest)[1], copy["name"])):
                raise ConflictError(f"The new folder already has a file named {copy['name']} "
                                    "that this library didn't make; move or rename it first. "
                                    "The folder was not changed.")
        for copy in copies:
            _move_file(copy["path"], os.path.join(dest_dir, copy["name"]))
            moved.append(copy)
    except OSError:
        for copy in reversed(moved):
            try:
                _move_file(os.path.join(dest_dir, copy["name"]), copy["path"])
            except OSError:
                log.warning("Could not move a backup copy back after a failed folder change")
        raise ServiceError("The existing backup copies could not be moved to the new folder; "
                           "the folder was not changed.") from None


def _move_file(src: str, dest: str):
    """Moves one copy. Never writes through a link at dest. Across drives
    the copy is made under a temp name next to dest, flushed to disk, and
    renamed over dest; only then is the original removed (and if that
    fails, dest is removed again so the copy isn't in both folders). The
    original is never removed before its copy is in place, so no failure
    or crash at any step loses the only copy. Raises OSError."""
    if os.path.islink(dest):
        raise OSError("snapshot path is a link")
    try:
        os.replace(src, dest)       # same drive: atomic
        return
    except OSError:
        pass
    # dest is src under another path: the copy below would overwrite src
    # with itself and then remove it. It is already where it should be.
    if os.path.lexists(dest) and os.path.samefile(src, dest):
        return
    fd, tmp = tempfile.mkstemp(prefix=".baihe_snapshot.partial-", suffix=".zip",
                               dir=os.path.dirname(dest))
    try:
        os.close(fd)
        shutil.copyfile(src, tmp)
        # The legacy snapshot is dated by its file time: keep it.
        with contextlib.suppress(OSError):
            st = os.stat(src)
            os.utime(tmp, (st.st_atime, st.st_mtime))
        _fsync_file(tmp)
        os.replace(tmp, dest)
        tmp = None      # it is dest now: never removed below
    finally:
        # src is still there on every path that reaches this with a tmp.
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.remove(tmp)
    # The original goes only once the copy's rename is on disk; if that
    # can't be confirmed, dest goes instead (src is still there).
    try:
        _fsync_dir(os.path.dirname(dest))
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(dest)
        raise
    try:
        os.remove(src)
    except OSError:
        if not os.path.lexists(src):
            return          # it went after all: the move is done
        with contextlib.suppress(OSError):
            os.remove(dest)
        raise


def _fsync_file(path: str):
    # r+b: Windows can't flush a handle opened read-only.
    with open(path, "r+b") as fh:
        os.fsync(fh.fileno())


def _get_state() -> dict:
    stored = db.get_app_setting(STATE_KEY, None)
    return dict(stored) if isinstance(stored, dict) else {}


def _update_state(**fields):
    state = _get_state()
    state.update(fields)
    db.set_app_setting(STATE_KEY, state)


def _is_sequence(value) -> bool:
    return type(value) is int and value >= 1


def _normalise_identity(stored):
    """The stored identity with every field checked ({library_id, sequence,
    created_at (str or None), written}), or None when it has no valid id."""
    if not (isinstance(stored, dict) and isinstance(stored.get("library_id"), str)
            and _LIBRARY_ID_RE.fullmatch(stored["library_id"])):
        return None
    seq = stored.get("sequence")
    created = stored.get("created_at")
    written = stored.get("written")
    return {"library_id": stored["library_id"],
            "sequence": seq if _is_sequence(seq) else 0,
            "created_at": created if _parse(created) is not None else None,
            "written": [[w[0], w[1]] for w in written
                        if isinstance(w, list) and len(w) == 2 and _is_sequence(w[0])
                        and isinstance(w[1], str)][-_MAX_WRITTEN:]
            if isinstance(written, list) else []}


def _change_identity(change=None) -> dict:
    """The identity after one write transaction (so two processes never
    hand out the same number): made at random the first time (a stored
    value without a valid id is replaced, which only ever makes old copies
    unmanaged, never someone else's managed), created_at filled in when
    missing, then change(identity) applied in place."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?",
                               (IDENTITY_KEY,)).fetchone()
            try:
                stored = json.loads(row[0]) if row else None
            except ValueError:
                stored = None
            ident = _normalise_identity(stored) or {
                "library_id": uuid.uuid4().hex, "sequence": 0, "created_at": None,
                "written": []}
            if ident["created_at"] is None:
                ident["created_at"] = _iso(_now())
            if change is not None:
                change(ident)
            if ident != stored:
                conn.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                             (IDENTITY_KEY, json.dumps(ident)))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return ident


def _identity_info() -> dict:
    """The identity (see IDENTITY_KEY), made or completed when needed."""
    try:
        ident = _normalise_identity(db.get_app_setting(IDENTITY_KEY, None))
    except ValueError:
        ident = None
    if ident is None or ident["created_at"] is None:
        ident = _change_identity()
    return ident


def _identity(bump_from=None) -> tuple:
    """(library id, sequence). With bump_from, the sequence becomes
    max(stored, bump_from) + 1 and that number is returned."""
    if bump_from is None:
        ident = _identity_info()
    else:
        def bump(i):
            i["sequence"] = max(i["sequence"], bump_from) + 1
        ident = _change_identity(bump)
    return ident["library_id"], ident["sequence"]


def _record_copy(sequence: int, name: str, present: set):
    """Notes that this library wrote copy `name` numbered `sequence`, and
    drops the notes for copies no longer in the folder (`present`: the
    names there now)."""
    def record(ident):
        ident["written"] = [w for w in ident["written"]
                            if w[1] in present and w[1] != name][-(_MAX_WRITTEN - 1):]
        ident["written"].append([sequence, name])
    _change_identity(record)


def _recorded(ident: dict, manifest, name: str) -> bool:
    """The copy called `name` with this manifest is one this library wrote:
    its id, and its sequence recorded under that name."""
    if not isinstance(manifest, dict) or manifest.get("library_id") != ident["library_id"]:
        return False
    seq = manifest.get("sequence")
    return _is_sequence(seq) and [seq, name] in ident["written"]


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
    if (state.get("last_error") and attempt is not None and attempt <= now
            and now - attempt < RETRY_AFTER_FAILURE):
        return False
    last = _parse(state.get("last_success_at"))
    if last is None or last > now:     # a future date: the clock was wrong then
        return True
    return now - last >= datetime.timedelta(days=FREQUENCIES[settings["frequency"]])


def settings_overview() -> dict:
    settings = get_settings()
    state = _get_state()
    nxt = next_run_at(settings, state)
    return {**settings,
            "frequencies": list(FREQUENCIES),
            "last_run_at": state.get("last_success_at"),
            "last_attempt_at": state.get("last_attempt_at"),
            "last_error": state.get("last_error"),
            "next_run_at": _iso(nxt) if nxt else None,
            "running": job_running(),
            "copies": snapshot_info()["copies"]}


# --------------------------------------------------------------------------
# snapshot file
# --------------------------------------------------------------------------

def _target_dir(create: bool) -> str:
    path = folder_path(get_settings()["folder"])
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            raise ServiceError("The backup folder could not be created.") from None
    return path


def _copy_name(at: datetime.datetime) -> str:
    return f"baihe_snapshot-{at.astimezone(datetime.timezone.utc):%Y%m%d-%H%M%S}.zip"


def _list_copies(folder: str) -> list:
    """Every copy in `folder`, newest first: [{name, path, at, size,
    dated}]. Only regular files (checked without following links) whose
    name is exactly the copy pattern or the legacy name; `at` is the UTC
    time in the name, or the legacy file's modification time. A name
    whose date isn't a real date (or a legacy file with an impossible
    time) is still listed, dated=False, so it can be seen and deleted by
    name; it is never managed."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = []
    for name in names:
        match = _COPY_NAME_RE.fullmatch(name)
        if match is None and name != LEGACY_SNAPSHOT_NAME:
            continue
        path = os.path.join(folder, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        dated = True
        try:
            if match is not None:
                at = datetime.datetime.strptime(match.group(1) + match.group(2),
                                                "%Y%m%d%H%M%S").replace(
                    tzinfo=datetime.timezone.utc)
            else:
                at = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            dated = False
            at = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
        out.append({"name": name, "path": path, "at": at, "size": st.st_size,
                    "dated": dated})
    out.sort(key=lambda c: (c["at"], c["name"]), reverse=True)
    return out


def _adopts_legacy(folder: str) -> bool:
    """Copies without a library id (written before copies carried one) count
    as this library's only in its own default folder, and only while that
    folder really is inside the library (not a link out to a shared
    drive): nothing else writes there."""
    try:
        lib = os.path.realpath(db.LIBRARY_DIR)
        default = os.path.realpath(folder_path(""))
        return default.startswith(lib + os.sep) and os.path.realpath(folder) == default
    except (OSError, ValueError):
        return False


def _classify(folder: str, ident: dict = None) -> list:
    """_list_copies(folder) (newest first by name date), each copy with what
    its manifest says: state (see _read_copy), manifest, sequence (int or
    None), created (the manifest's created_at, else the name's date),
    same_id (the manifest carries this library's id and a sequence, whoever
    wrote it) and owner:
      _THIS   -- same_id, and this library recorded writing that sequence
                 under this name (see IDENTITY_KEY);
      _LEGACY -- a readable copy with neither id nor sequence, dated (its
                 manifest's created_at) before this library's id was made,
                 in the folder _adopts_legacy accepts;
      _OTHER  -- anything else: another library's id, a clone's or a
                 hand-restored library's copy with this id, no id
                 elsewhere, an id without a sequence, a copy that can't be
                 read, a name that isn't a real date.
    Only _THIS and _LEGACY (_MANAGED) take rotation slots or are deleted
    without the owner naming them. The caller holds _snapshot_lock."""
    ident = ident or _identity_info()
    adopt = _adopts_legacy(folder)
    id_made = _parse(ident["created_at"])
    out = []
    for copy in _list_copies(folder):
        state, manifest = _read_copy(copy["path"])
        owner, sequence, created, same_id = _OTHER, None, copy["at"], False
        if state == _OK:
            written_at = _parse(manifest.get("created_at"))
            created = written_at or copy["at"]
            seq = manifest.get("sequence")
            sequence = seq if _is_sequence(seq) else None
            same_id = manifest.get("library_id") == ident["library_id"] and sequence is not None
            if not copy["dated"]:
                pass
            elif same_id and _recorded(ident, manifest, copy["name"]):
                owner = _THIS
            elif (adopt and "library_id" not in manifest and "sequence" not in manifest
                  and written_at is not None and id_made is not None
                  and written_at < id_made):
                owner = _LEGACY
        out.append({**copy, "state": state, "manifest": manifest, "sequence": sequence,
                    "created": created, "same_id": same_id, "owner": owner})
    return out


def _default_pick(entries: list) -> tuple:
    """(entry, False) for the copy a restore uses when none is named: this
    library's copy with the highest sequence. (None, True) when that can't
    be told for sure (see the module docstring); (None, False) when no copy
    can be read at all."""
    readable = [e for e in entries if e["state"] == _OK]
    if not readable:
        return None, False
    own = [e for e in readable if e["owner"] == _THIS]
    if not own or any(e["state"] == _UNREADABLE for e in entries):
        return None, True
    top = max(own, key=lambda e: e["sequence"])
    for e in entries:
        if e is top:
            continue
        if e["state"] != _OK:
            # A damaged copy named after the pick may have been the newest.
            if _later(e["at"], top["at"]):
                return None, True
        elif e["same_id"] and e["sequence"] >= top["sequence"]:
            # Two with one number, or one numbered above this library's own
            # (a clone of this library, or a library.db restored by hand).
            return None, True
        elif e["owner"] == _THIS:
            if _later(e["created"], top["created"], _ORDER_TOLERANCE):
                return None, True
        elif _later(e["created"], top["created"]):
            return None, True
    return top, False


def _candidates(entries: list) -> list:
    """The copies a choose_copy answer offers: every readable one, newest
    first by name date. Names only, never paths."""
    return [{"name": e["name"], "created_at": _iso(e["created"]), "sequence": e["sequence"],
             "size": e["size"], "managed": e["owner"] in _MANAGED}
            for e in entries if e["state"] == _OK]


# What _read_copy found: a readable copy; one the OS wouldn't let us read
# right now (locked, no permission, a drive hiccup -- maybe fine later, so
# never deleted); or a damaged one (not a zip, no valid manifest).
_OK, _UNREADABLE, _DAMAGED = "ok", "unreadable", "damaged"


def _read_copy(path: str) -> tuple:
    """(_OK, manifest), (_UNREADABLE, None) or (_DAMAGED, None)."""
    try:
        with zipfile.ZipFile(path) as zf:
            return _OK, _read_manifest(zf)
    except OSError:
        return _UNREADABLE, None
    except Exception:
        # Anything else a foreign or broken file can make zipfile or the
        # manifest check raise (BadZipFile, NotImplementedError for an
        # unknown compression, struct/zlib errors, ...) is damage.
        return _DAMAGED, None


def _retention(readable: list) -> dict:
    """{name: "daily" | "weekly"} for the copies the rotation keeps (see the
    module docstring), from readable copies ordered newest first. The first
    is the anchor (the copy just written) and is always kept; the caller
    leaves out copies dated after it."""
    if not readable:
        return {}
    kept = {readable[0]["name"]: "daily"}
    days = {readable[0]["at"].date()}
    rest = []
    for copy in readable[1:]:       # newest first: the first seen of a day is its newest
        day = copy["at"].date()
        if day not in days and len(days) < KEEP_DAILY:
            kept[copy["name"]] = "daily"
            days.add(day)
        else:
            rest.append(copy)
    first_of_week = {}
    for copy in rest:               # newest first: the last one seen is the oldest
        first_of_week[copy["at"].isocalendar()[:2]] = copy
    for week in sorted(first_of_week, reverse=True)[:KEEP_WEEKLY]:
        kept[first_of_week[week]["name"]] = "weekly"
    return kept


def _rotation_pool(entries: list, anchor: dict) -> list:
    """The copies the rotation weighs against `anchor` (one of this
    library's), newest first: the anchor, then every managed copy dated no
    later than it and, if it is this library's, numbered lower. A copy
    dated after the anchor (a clock set back, a future-dated file) or
    numbered higher is never in the pool, so never deleted."""
    return [anchor] + [
        e for e in entries
        if e is not anchor and e["owner"] in _MANAGED and e["at"] <= anchor["at"]
        and (e["owner"] == _LEGACY or e["sequence"] < anchor["sequence"])]


def _prune(folder: str, new_name: str) -> int:
    """Deletes the copies the rotation no longer keeps; returns how many.
    The caller holds _snapshot_lock and has just put the validated copy
    `new_name` in place (and recorded it); it is always kept and anchors
    the rule. Only managed copies in _rotation_pool are ever deleted: never
    another library's or a clone's, never one that can't be read or proves
    no owner. Nothing is deleted unless the new copy reads back as this
    library's."""
    entries = _classify(folder)
    new = next((e for e in entries if e["name"] == new_name), None)
    if new is None or new["owner"] != _THIS:
        return 0
    pool = _rotation_pool(entries, new)
    kept = _retention(pool)
    removed = 0
    for copy in pool:
        if copy["name"] in kept:
            continue
        try:
            os.remove(copy["path"])     # removes a name, never follows a link
            removed += 1
        except OSError:
            log.warning("Could not delete an old automatic backup copy")
    return removed


def _pick_copy(name=None) -> tuple:
    """(copy, manifest) for the copy called `name` -- matched against the
    backup folder's own listing, never used as a path; any listed copy,
    managed or not -- or, with no name, _default_pick's copy (a
    ConflictError with reason "choose_copy" and the candidates when it
    can't tell). The caller holds _snapshot_lock."""
    folder = _target_dir(create=False)
    if name is None:
        entries = _classify(folder)
        if not entries:
            raise NotFoundError(_NO_SNAPSHOT)
        pick, ambiguous = _default_pick(entries)
        if ambiguous:
            raise ConflictError(_CHOOSE, details={"reason": "choose_copy",
                                                  "candidates": _candidates(entries)})
        if pick is None:
            raise InvalidInputError(_BAD_SNAPSHOT)
        return pick, pick["manifest"]
    copies = _list_copies(folder)
    if not isinstance(name, str) or not name or len(name) > _MAX_COPY_NAME_LEN:
        raise InvalidInputError("A backup copy is named by its file name.")
    for copy in copies:
        if copy["name"] == name:
            state, manifest = _read_copy(copy["path"])
            if state != _OK:
                raise InvalidInputError(_BAD_SNAPSHOT)
            return copy, manifest
    raise NotFoundError(_NO_COPY)


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        info = zf.getinfo(MANIFEST_NAME)
    except KeyError:
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if info.file_size > _MANIFEST_MAX_BYTES:
        raise InvalidInputError(_BAD_SNAPSHOT)
    # An OSError (the file couldn't be read) is not damage: it propagates,
    # so _read_copy can tell the two apart.
    try:
        data = json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError,
            EOFError, zlib.error, NotImplementedError):
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if (not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT
            or data.get("kind") not in ("db-only", "full")
            or not isinstance(data.get("dramas"), list)):
        raise InvalidInputError(_BAD_SNAPSHOT)
    return data


def snapshot_info() -> dict:
    """{exists, copies} plus, for the copy a restore picks by default (see
    _default_pick), readable, default_copy (its name), choose_copy (False),
    created_at, kind, size, app_version and drama_count. When no copy can
    be picked safely: {exists, readable: True, choose_copy: True,
    default_copy: None, copies}. copies lists every copy newest first (by
    name date) as {name, created_at, size, kind, drama_count, readable,
    kept_as, managed, sequence}: names only, never paths. When copies exist
    but none can be read: {exists: True, readable: False, copies}."""
    with _snapshot_lock:
        entries = _classify(_target_dir(create=False))
    if not entries:
        return {"exists": False, "copies": []}
    # As the next run would see it: anchored on this library's newest copy.
    own = [e for e in entries if e["owner"] == _THIS]
    anchor = max(own, key=lambda e: e["sequence"]) if own else None
    kept = _retention(_rotation_pool(entries, anchor)) if anchor else {}
    copies = []
    for e in entries:
        manifest = e["manifest"]
        copies.append({"name": e["name"],
                       "created_at": _iso(e["created"]),
                       "size": e["size"], "readable": manifest is not None,
                       "kind": manifest["kind"] if manifest else None,
                       "drama_count": len(manifest["dramas"]) if manifest else None,
                       "kept_as": kept.get(e["name"]),
                       "managed": e["owner"] in _MANAGED, "sequence": e["sequence"]})
    pick, ambiguous = _default_pick(entries)
    if ambiguous:
        return {"exists": True, "readable": True, "choose_copy": True, "default_copy": None,
                "copies": copies}
    if pick is None:
        return {"exists": True, "readable": False, "copies": copies}
    entry = next(c for c in copies if c["name"] == pick["name"])
    return {"exists": True, "readable": True, "choose_copy": False,
            "default_copy": pick["name"], "created_at": entry["created_at"],
            "kind": entry["kind"], "size": entry["size"],
            "app_version": pick["manifest"].get("app_version") or "unknown",
            "drama_count": entry["drama_count"], "copies": copies}


def _app_version() -> str:
    """The checked-out commit (short hash) when the app runs from a git
    checkout, else "unknown". Read from .git without running git."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    git = os.path.join(root, ".git")
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as fh:
            head = fh.read(200).strip()
        if head.startswith("ref: "):
            ref = head[5:]
            ref_file = os.path.join(git, *ref.split("/"))
            if os.path.isfile(ref_file):
                with open(ref_file, encoding="utf-8") as fh:
                    head = fh.read(100).strip()
            else:
                head = ""
                with open(os.path.join(git, "packed-refs"), encoding="utf-8") as fh:
                    for line in fh:
                        parts = line.split()
                        if len(parts) == 2 and parts[1] == ref:
                            head = parts[0]
        return head[:12] if len(head) >= 12 and all(c in "0123456789abcdef" for c in head) \
            else "unknown"
    except OSError:
        return "unknown"


def _manifest_bytes(snap_path: str, include_media: bool, owner: tuple) -> bytes:
    """owner = (library id, sequence) from _identity: what makes the copy
    this library's (see _classify)."""
    with contextlib.closing(sqlite3.connect(ro_uri(snap_path), uri=True)) as conn:
        rows = conn.execute("SELECT id, title_en, title_zh, media_type FROM dramas "
                            "ORDER BY id").fetchall()
        counts = dict(conn.execute("SELECT drama_id, COUNT(*) FROM lines "
                                   "GROUP BY drama_id").fetchall())
    dramas = [{"id": r[0], "title": (r[1] or r[2] or f"Drama {r[0]}"),
               "media_type": r[3] or "", "line_count": counts.get(r[0], 0)} for r in rows]
    manifest = {"format": MANIFEST_FORMAT, "created_at": _iso(_now()),
                "kind": "full" if include_media else "db-only",
                "app_version": _app_version(), "database_size": os.path.getsize(snap_path),
                "library_id": owner[0], "sequence": owner[1], "dramas": dramas}
    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def ro_uri(path: str) -> str:
    from urllib.parse import quote
    return f"file:{quote(os.path.abspath(path))}?mode=ro"


def extract_db(zf: zipfile.ZipFile, dest_dir: str, max_bytes: int = None,
                check: bool = True) -> str:
    """library.db from the snapshot into dest_dir, checked with SQLite
    (quick_check) before anything reads it. check=False copies it without
    running any SQL: the caller checks it on its own guarded connection."""
    dest = os.path.join(dest_dir, "library.db")
    if zf.getinfo("library.db").file_size > (max_bytes or _MAX_MEMBER_BYTES):
        raise InvalidInputError(_BAD_SNAPSHOT)
    with zf.open("library.db") as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)
    if not check:
        return dest
    try:
        with contextlib.closing(sqlite3.connect(ro_uri(dest), uri=True)) as conn:
            conn.execute("PRAGMA trusted_schema = OFF")
            ok = conn.execute("PRAGMA quick_check").fetchone()
            if not ok or ok[0] != "ok":
                raise InvalidInputError(_BAD_SNAPSHOT)
            conn.execute("SELECT id FROM dramas LIMIT 1").fetchall()
    except sqlite3.Error:
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    return dest


def _verify_snapshot(path: str):
    """Everything a finished snapshot must pass before it replaces the old
    one: the restore zip checks, a readable manifest, a sound library.db
    whose dramas match the manifest."""
    las.validate_backup_file(path, check_disk=False, check_limits=False)
    with zipfile.ZipFile(path) as zf, storage.job_workdir() as tmp:
        manifest = _read_manifest(zf)
        dest = extract_db(zf, tmp)
        with contextlib.closing(sqlite3.connect(ro_uri(dest), uri=True)) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM dramas ORDER BY id")]
    if ids != [d.get("id") for d in manifest["dramas"]]:
        raise InvalidInputError(_BAD_SNAPSHOT)


# --------------------------------------------------------------------------
# backup job
# --------------------------------------------------------------------------

def job_running() -> bool:
    job = background_jobs.get_status(JOB_ID)
    return bool(job and job.get("status") in ("running", "queued"))


def _place_copy(tmp: str, folder: str, now: datetime.datetime) -> str:
    """Puts the finished partial file `tmp` in place as the new copy and
    returns its path: named after its UTC time, moved on a second at a time
    while that name is taken (two backups within one second, or a file that
    appears there meanwhile -- another PC writing into a shared folder).
    Never replaces an existing file. The caller holds _snapshot_lock."""
    at = now.astimezone(datetime.timezone.utc).replace(microsecond=0)
    for _ in range(1000):
        path = os.path.join(folder, _copy_name(at))
        if not os.path.lexists(path):
            try:
                _link_new(tmp, path)
                return path
            except FileExistsError:
                pass
        at += datetime.timedelta(seconds=1)
    raise OSError("no free backup copy name")


def _link_new(src: str, dest: str):
    """Gives `src` the name `dest` without ever replacing a file there
    (FileExistsError when one is there): a hard link, then src's name is
    removed. A file system without hard links (FAT/exFAT drives, some
    network shares) gets dest created exclusively first and src renamed
    over that empty placeholder, which only this call can have made."""
    try:
        os.link(src, dest)
    except FileExistsError:
        raise
    except (OSError, AttributeError, NotImplementedError):
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        os.close(fd)
        try:
            os.replace(src, dest)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(dest)
            raise
        return
    # The copy is in place under both names; a leftover partial name is
    # swept by cleanup_stale_leftovers.
    with contextlib.suppress(OSError):
        os.remove(src)


def _backup_job(job_id, include_media: bool):
    now = _now()
    started = _iso(now)
    _update_state(last_attempt_at=started)
    tmp = None
    try:
        target = _target_dir(create=True)
        fd, tmp = tempfile.mkstemp(prefix=".baihe_snapshot.partial-", suffix=".zip",
                                   dir=target)
        os.close(fd)
        # The next number above both the stored one and every copy with
        # this library's id already in the folder, so a sequence rolled
        # back by a hand-restored library.db can't reuse a number. A copy
        # numbered above the stored counter means someone else writes with
        # this id (a hand-copied library on another PC) or the counter was
        # rolled back: this run prunes nothing (see below).
        with _snapshot_lock:
            ident = _identity_info()
            floor = max((e["sequence"] for e in _classify(target, ident) if e["same_id"]),
                        default=0)
            foreign_above = floor > ident["sequence"]
            owner = _identity(bump_from=floor)
        background_jobs.update_progress(job_id, 0.1, "Writing the backup...")
        las.write_backup_zip(tmp, include_media=include_media,
                             manifest=lambda snap: _manifest_bytes(snap, include_media, owner))
        background_jobs.update_progress(job_id, 0.8, "Checking the backup...")
        _verify_snapshot(tmp)
        # On disk before any old copy is deleted: a power cut must not
        # leave an empty new copy and no old ones.
        _fsync_file(tmp)
        with _snapshot_lock:
            final = _place_copy(tmp, target, now)
            tmp = None
            size = os.path.getsize(final)
            # Only now, with the new copy validated and in place (a failed
            # directory flush or record skips the rotation, never the
            # backup; an unrecorded copy is only unmanaged).
            try:
                _record_copy(owner[1], os.path.basename(final),
                             {c["name"] for c in _list_copies(target)})
                _fsync_dir(target)
                if foreign_above:
                    log.warning("Automatic backup: the backup folder has a copy with this "
                                "library's id (%s) numbered above its own counter (another "
                                "PC using a copy of this library, or a library.db restored "
                                "by hand); no old copies were deleted this time", owner[0])
                else:
                    _prune(target, os.path.basename(final))
            except Exception as exc:
                log.warning("Could not rotate the automatic backup copies: %s",
                            type(exc).__name__)
    except Exception as exc:
        log.warning("Automatic backup failed: %s", type(exc).__name__)
        _update_state(last_error=_FAILED)
        raise RuntimeError(_FAILED) from None
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.remove(tmp)
    _update_state(last_success_at=started, last_error=None)
    background_jobs.set_result(job_id, {"size": size,
                                        "kind": "full" if include_media else "db-only"})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def _start(include_media: bool) -> bool:
    # Under _snapshot_lock so a folder change can't slip between its
    # running-job check and its save (see set_settings).
    with _snapshot_lock:
        return background_jobs.start_job(JOB_ID, _backup_job, JOB_ID, include_media,
                                         description="Automatic backup")


def start_now(replace: bool = False, include_media=None) -> dict:
    """"Back up now": writes a new copy, then rotates the old ones.
    include_media defaults to the setting. replace is still accepted from
    older clients and ignored: a new copy never replaces an existing one."""
    if not isinstance(replace, bool):
        raise InvalidInputError("replace must be true or false.")
    if include_media is None:
        include_media = get_settings()["include_media"]
    elif not isinstance(include_media, bool):
        raise InvalidInputError("include_media must be true or false.")
    las.refuse_during_maintenance("backup")
    if background_jobs.exclusive_active():
        raise ConflictError("A restore is in progress; the backup can start when it finishes.")
    if job_running():
        raise ConflictError("A backup is already running.")
    if not _start(include_media):
        raise ConflictError("A backup is already running.")
    return {"job_id": JOB_ID}


def check_and_run(now=None) -> str:
    """The scheduled due-check. Returns "disabled", "not_due", "busy" (a job,
    restore or maintenance is in progress; tried again at the next check),
    "started" or "error" (the check itself failed; recorded as last_error
    so the settings page shows it). Never raises."""
    try:
        settings = get_settings()
        if not settings["enabled"]:
            return "disabled"
        if not is_due(now=now, settings=settings):
            return "not_due"
        if (background_jobs.maintenance_active() or background_jobs.exclusive_active()
                or las.any_job_running()):
            return "busy"
        return "started" if _start(settings["include_media"]) else "busy"
    except Exception as exc:
        log.warning("Automatic backup check failed: %s", type(exc).__name__)
        with contextlib.suppress(Exception):
            _update_state(last_error=_CHECK_FAILED)
        return "error"


def periodic_tick(interval: float = None) -> bool:
    """Called by the API's background poller; runs check_and_run at most
    once per `interval` seconds (hourly). Returns True when it checked."""
    global _last_check
    interval = CHECK_INTERVAL_SECONDS if interval is None else interval
    with _tick_lock:
        now = time.monotonic()
        if _last_check is not None and now - _last_check < interval:
            return False
        _last_check = now
    check_and_run()
    return True


STALE_LEFTOVER_SECONDS = 24 * 3600


def cleanup_stale_leftovers(max_age: float = STALE_LEFTOVER_SECONDS, now: float = None) -> int:
    """Startup sweep: partial snapshot files (".baihe_snapshot.partial-*")
    in the backup folder and restore staging folders from older versions
    ("dramas/.restoring-*") left by a process that was killed mid-way,
    once older than a day, plus the leftovers of interrupted imports and
    restores (db.recover_media_imports: any age once journalled, but never
    one whose import holds its lock). Symlinks are left alone. Never
    raises."""
    now = time.time() if now is None else now
    removed = db.recover_media_imports(max_age, now)["settled"]
    targets = [(folder_path(get_settings()["folder"]), ".baihe_snapshot.partial-", False),
               (db.DRAMAS_DIR, ".restoring-", True)]
    for folder, prefix, is_dir in targets:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            if not name.startswith(prefix):
                continue
            path = os.path.join(folder, name)
            try:
                if os.path.islink(path) or (os.path.isdir(path) != is_dir):
                    continue
                if now - os.path.getmtime(path) < max_age:
                    continue
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed += 1
            except OSError:
                log.warning("Could not remove a leftover backup/restore file")
    return removed


def delete_snapshot(confirm=False, confirm_text="", snapshot=None, all_copies=False,
                    include_unmanaged=False) -> dict:
    """Deletes the copy named `snapshot` (matched against the folder's
    listing), or every managed copy with all_copies=True -- exactly one of
    the two, so a request that lost its name can't delete everything. An
    unmanaged copy (see _classify) is deleted only with
    include_unmanaged=True: by name, or with all_copies as well. Needs
    confirm=True and confirm_text "DELETE". Returns {deleted, count,
    kept_unmanaged} (the unmanaged copies left in place)."""
    las.require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting the snapshot")
    if not isinstance(all_copies, bool):
        raise InvalidInputError("all must be true or false.")
    if not isinstance(include_unmanaged, bool):
        raise InvalidInputError("include_unmanaged must be true or false.")
    if (snapshot is None) == (not all_copies):
        raise InvalidInputError("Name one backup copy to delete, or ask for all of them.")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot
                                 or len(snapshot) > _MAX_COPY_NAME_LEN):
        raise InvalidInputError("A backup copy is named by its file name.")
    if job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    # Refused while a whole-library restore, bulk delete or storage cleanup
    # holds the library, and holds off a restore until it ends.
    las.refuse_during_maintenance("deletion of backup copies")
    with las.maintenance("deleting backup copies"), _snapshot_lock:
        entries = _classify(_target_dir(create=False))
        left = 0
        if snapshot is not None:
            copies = [c for c in entries if c["name"] == snapshot]
            if not copies:
                raise NotFoundError(_NO_COPY)
            if copies[0]["owner"] not in _MANAGED and not include_unmanaged:
                raise ConflictError(_UNMANAGED, details={"reason": "unmanaged"})
        elif not entries:
            raise NotFoundError(_NO_SNAPSHOT)
        else:
            copies = [c for c in entries if include_unmanaged or c["owner"] in _MANAGED]
            left = len(entries) - len(copies)
            if not copies:
                raise NotFoundError("None of the copies in the backup folder are managed by "
                                    "this library; nothing was deleted.")
        count, failed = 0, 0
        for copy in copies:
            try:
                os.remove(copy["path"])
                count += 1
            except OSError:
                failed += 1
        if failed:
            raise ServiceError("A backup copy could not be deleted; is it open somewhere?",
                               details={"deleted": count, "not_deleted": failed})
    return {"deleted": True, "count": count, "kept_unmanaged": left}


# --------------------------------------------------------------------------
# restore one drama
# --------------------------------------------------------------------------

# Every table with a drama_id foreign key to dramas(id), in insert order,
# except those in _SKIPPED_TABLES. tests/test_auto_backup_service.py
# checks this against the live schema, so a new child table fails a test
# until it is listed in one or the other.
CHILD_TABLES = ("lines", "pages", "characters", "translation_notes", "line_emotions",
                 "consistency_issues", "vocab_lookups", "line_history", "translation_versions",
                 "bug_reports", "wiki_entries", "edit_samples", "voice_suggestion_dismissals",
                 "progress", "personal_notes", "reading_history", "metadata_field_provenance")
_SKIPPED_TABLES = {
    "usage_log": "ON DELETE SET NULL: the spending rows survive a delete, so restoring them "
                 "would count the cost twice",
    "speaker_merge_undos": "short-lived, single-use undo records; a restored one would "
                           "describe lines and rows that no longer match",
    "bulk_jobs": "provider batch jobs: a restored in-flight batch could be polled again and "
                 "write stale results over the restored lines",
    "metadata_research_results": "a short-lived research cache pruned by age, keyed by a "
                                 "research id a restored copy would collide with; the applied "
                                 "values and their sources (metadata_field_provenance) are "
                                 "restored",
}
_LINE_REF_TABLES = ("translation_notes", "line_emotions", "reading_history", "bug_reports")
_PROFILE_TABLES = ("progress", "personal_notes", "reading_history")
_LINE_JSON = {"translation_versions": "lines_json", "line_history": "snapshot_json"}
SERIES_CHILDREN = ("glossary_terms", "series_characters", "translation_memory",
                   "glossary_dismissals")
# Columns naming a file in the drama folder, with the one subfolder the app
# writes that file in (None: the folder itself). Readers join these onto the
# drama folder, so a backup from another library keeps one only when it is a
# name inside the new drama's own folder (see _import_file_ref).
IMPORT_FILE_COLUMNS = {
    "dramas": {"audio_filename": None, "source_video_filename": None,
               "novel_reference_filename": None, "cover_art_filename": None},
    "lines": {"dub_filename": "dub_clips"},
    "characters": {"ref_audio_filename": "voice_refs"},
    "pages": {"filename": "pages", "rendered_filename": "pages"},
}


def list_snapshot_dramas(snapshot=None) -> dict:
    """The dramas inside a copy (the one named `snapshot`, else the newest
    readable one), from its manifest, each with exists_now (its id is in
    use, so a restore makes a new drama)."""
    with _snapshot_lock:
        copy, manifest = _pick_copy(snapshot)
    live = {d["id"] for d in db.list_dramas()}
    out = []
    for d in manifest["dramas"]:
        if not isinstance(d, dict) or not isinstance(d.get("id"), int):
            continue
        out.append({"id": d["id"], "title": str(d.get("title") or "")[:300],
                    "media_type": str(d.get("media_type") or "")[:40],
                    "line_count": d.get("line_count") if isinstance(d.get("line_count"), int)
                    else 0,
                    "exists_now": d["id"] in live})
    created = _parse(manifest.get("created_at")) or copy["at"]
    return {"name": copy["name"], "created_at": _iso(created),
            "kind": manifest["kind"], "dramas": out}


def columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def has_table(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,)).fetchone() is not None


def _storable_words(value) -> bool:
    """A line's word timings come in only within the size a transcription
    stores (a file may be hand-made); their content is checked again on use."""
    return isinstance(value, str) and len(value) <= core.MAX_STORED_WORD_BYTES


def _insert(dst, table: str, row: dict, live_cols) -> int:
    cols = [c for c in row if c in live_cols]
    names = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" for _ in cols)
    cur = dst.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                      [row[c] for c in cols])
    return cur.lastrowid


def table_rows(src, table: str, where: str, args) -> list:
    if not has_table(src, table):
        return []
    cur = src.execute(f'SELECT * FROM "{table}" WHERE {where}', args)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _import_file_ref(folder, value, subdir):
    """`value` (normalised to "/") when it is a plain file name, or
    "<subdir>/<plain name>", that stays inside `folder`; None otherwise,
    and always None when `folder` is None (the drama's files weren't
    imported). A name that is a folder there (e.g. "pages") is None: a
    file column naming it would let a media replace move the folder."""
    if folder is None or not isinstance(value, str) or "\x00" in value or ":" in value:
        return None
    parts = value.replace("\\", "/").split("/")
    if len(parts) == 1:
        base, name = folder, parts[0]
    elif len(parts) == 2 and subdir is not None and parts[0] == subdir:
        base, name = os.path.join(folder, subdir), parts[1]
    else:
        return None
    path = delete_service.file_in_folder(base, name)
    if path is None or os.path.isdir(path):
        return None
    return name if len(parts) == 1 else f"{subdir}/{name}"


def _sanitise_file_refs(table: str, row: dict, import_as):
    if import_as is None:
        return
    for col, subdir in IMPORT_FILE_COLUMNS.get(table, {}).items():
        if col in row:
            row[col] = _import_file_ref(import_as.get("media_dir"), row[col], subdir)


def _remap_json_lines(value, line_map, import_as=None):
    """Points the saved lines' ids at the new lines. import_as (see
    _copy_drama): each saved dub_filename is sanitised like lines'
    (restoring the version writes it back into lines), and a value that
    isn't a list of lines becomes an empty list."""
    try:
        items = json.loads(value) if value else None
    except (ValueError, RecursionError):
        return value if import_as is None else "[]"
    if not isinstance(items, list):
        return value if import_as is None else "[]"
    for item in items:
        if isinstance(item, dict) and "id" in item:
            item["id"] = line_map.get(item["id"])
        if import_as is not None and isinstance(item, dict) and "dub_filename" in item:
            item["dub_filename"] = _import_file_ref(import_as.get("media_dir"),
                                                    item["dub_filename"], "dub_clips")
    return json.dumps(items, ensure_ascii=False)


def _live_ids(dst, table: str) -> set:
    return {r[0] for r in dst.execute(f'SELECT id FROM "{table}"')}


def _free_series_name(dst, name, label="restored") -> str:
    base = (str(name) if name else "Series").strip() or "Series"
    today = datetime.date.today().isoformat()
    candidate = f"{base} ({label} {today})"
    n = 2
    while dst.execute("SELECT 1 FROM series WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base} ({label} {today}, {n})"
        n += 1
    return candidate


def _resolve_series(src, dst, series_id, drama_owner, users, counts, import_as=None) -> tuple:
    """(live series id or None, {old series_character id: live id},
    outcome "none" | "linked" | "recreated" | "dropped_private").
    A live series with the snapshot's id that is now someone else's
    private series (the db.assign_drama_series predicate: a private series
    takes only its owner's and the PC owner's dramas) means the drama comes
    back with no series ("dropped_private") and nothing of it is copied --
    checked on the id alone, so a series its owner has since made private
    is never re-shared. Otherwise the drama is linked only when the live
    series has the same id AND name (after a whole-library restore of an
    older library a new series can reuse an id the snapshot used for a
    different one), and a name match alone never links. In every other case
    the series comes back from the snapshot as a new series (renamed
    "... (restored <date>)" if its name is taken) with its glossary,
    characters and memory.
    import_as (see _copy_drama) skips all of that: the series is always
    created new, owned by the importing user, once per source series."""
    if series_id is None:
        return None, {}, "none"
    srows = table_rows(src, "series", "id = ?", (series_id,))
    if not srows:
        return None, {}, "none"
    series = srows[0]
    if import_as is not None:
        if series_id not in import_as["series"]:
            row = {k: v for k, v in series.items() if k != "id"}
            row["owner_user_id"] = import_as["owner_user_id"]
            row["is_private"] = import_as["is_private"]
            row["name"] = _free_series_name(dst, row.get("name"), "imported")
            import_as["series"][series_id] = _insert_series(src, dst, series_id, row, counts)
        live_id, char_map = import_as["series"][series_id]
        return live_id, char_map, "recreated"
    live = dst.execute("SELECT id, owner_user_id, COALESCE(is_private, 0), name FROM series "
                       "WHERE id = ?", (series_id,)).fetchone()
    if live is not None and live[2] and drama_owner is not None and drama_owner != live[1]:
        # Checked on the id alone, whatever the name: never copy or re-share
        # a series that is now someone else's private series.
        return None, {}, "dropped_private"
    if live is not None and live[3] == series.get("name"):
        live_chars = {r[0] for r in dst.execute(
            "SELECT id FROM series_characters WHERE series_id = ?", (series_id,))}
        return series_id, {c: c for c in live_chars}, "linked"
    row = {k: v for k, v in series.items() if k != "id"}
    if row.get("owner_user_id") not in users:
        row["owner_user_id"] = None
    if dst.execute("SELECT 1 FROM series WHERE name = ?", (row.get("name"),)).fetchone():
        row["name"] = _free_series_name(dst, row.get("name"))
    live_id, char_map = _insert_series(src, dst, series_id, row, counts)
    return live_id, char_map, "recreated"


def _insert_series(src, dst, series_id, row, counts) -> tuple:
    """Inserts the series row and its glossary, characters and memory;
    (live series id, {old series_character id: live id})."""
    live_id = _insert(dst, "series", row, columns(dst, "series"))
    counts["series"] = 1
    char_map = {}
    for table in SERIES_CHILDREN:
        live_cols = columns(dst, table)
        n = 0
        for child in table_rows(src, table, "series_id = ?", (series_id,)):
            old = child.pop("id", None)
            child["series_id"] = live_id
            new = _insert(dst, table, child, live_cols)
            if table == "series_characters":
                char_map[old] = new
            n += 1
        counts[table] = n
    return live_id, char_map


def copy_drama(src, dst, old_id: int, new_id, title_suffix, import_as=None) -> tuple:
    """Inserts the drama and its children into dst (inside the caller's
    transaction). new_id None = a fresh id. Returns (live id, counts,
    series outcome -- see _resolve_series).
    import_as (a backup from another library, see backup_import_service) =
    {"owner_user_id", "is_private", "series": {}, "media_dir"}: the owner
    and privacy come from it and never from the file, the series is always
    new, old `notion_page_id` is dropped, per-profile tables (profile ids
    mean something else here) are not copied, and each file
    reference (IMPORT_FILE_COLUMNS) is kept only when it names a file inside
    media_dir (None = no files imported, so all are cleared)."""
    drama = table_rows(src, "dramas", "id = ?", (old_id,))
    if not drama:
        raise NotFoundError("That drama isn't in the snapshot.")
    drama = drama[0]
    counts = {}
    users = {r[0] for r in dst.execute("SELECT id FROM users")}
    profiles = _live_ids(dst, "profiles")
    row = sensitivity_preset.older_row(drama)
    if import_as is not None:
        row["owner_user_id"] = import_as["owner_user_id"]
        row["is_private"] = import_as["is_private"]
        row["notion_page_id"] = None
        _sanitise_file_refs("dramas", row, import_as)
    elif row.get("owner_user_id") not in users:
        row["owner_user_id"] = None
    series_id, char_map, series_outcome = _resolve_series(
        src, dst, drama.get("series_id"), row["owner_user_id"], users, counts, import_as)
    row["series_id"] = series_id
    if series_id is not None:
        row["is_private"] = 0   # a drama in a series follows the series (decision 4)
    if new_id is None:
        row.pop("id", None)
    else:
        row["id"] = new_id
    if title_suffix:
        key = "title_en" if (row.get("title_en") or "").strip() else "title_zh"
        row[key] = f"{(row.get(key) or '').strip()} {title_suffix}".strip()
    row["updated_at"] = datetime.datetime.utcnow().isoformat()
    live_id = _insert(dst, "dramas", row, columns(dst, "dramas"))
    counts["dramas"] = 1

    line_map, page_map = {}, {}
    for table in CHILD_TABLES:
        if not has_table(dst, table):
            continue
        if import_as is not None and table in _PROFILE_TABLES:
            counts[table] = 0
            continue
        live_cols = columns(dst, table)
        n = 0
        for child in table_rows(src, table, "drama_id = ?", (old_id,)):
            old = child.pop("id", None)
            child["drama_id"] = live_id
            _sanitise_file_refs(table, child, import_as)
            if (table in _PROFILE_TABLES and child.get("profile_id") is not None
                    and child["profile_id"] not in profiles):
                continue
            if table in _LINE_REF_TABLES and child.get("line_id") is not None:
                child["line_id"] = line_map.get(child["line_id"])
                if child["line_id"] is None and table == "line_emotions":
                    continue
            if "series_character_id" in child and child["series_character_id"] is not None:
                child["series_character_id"] = char_map.get(child["series_character_id"])
                if child["series_character_id"] is None and table == "voice_suggestion_dismissals":
                    continue
            if table in _LINE_JSON:
                col = _LINE_JSON[table]
                child[col] = _remap_json_lines(child.get(col), line_map, import_as)
            if table == "lines" and not _storable_words(child.get("word_timings")):
                child.pop("word_timings", None)
            new = _insert(dst, table, child, live_cols)
            if table == "lines":
                line_map[old] = new
            elif table == "pages":
                page_map[old] = new
            n += 1
        counts[table] = n
    if page_map and has_table(src, "bubbles"):
        live_cols = columns(dst, "bubbles")
        n = 0
        for old_page, new_page in page_map.items():
            for bubble in table_rows(src, "bubbles", "page_id = ?", (old_page,)):
                bubble.pop("id", None)
                bubble["page_id"] = new_page
                _insert(dst, "bubbles", bubble, live_cols)
                n += 1
        counts["bubbles"] = n
    return live_id, counts, series_outcome


def stage_media(zf: zipfile.ZipFile, old_id: int, staging: str):
    """Extracts dramas/<old_id>/... into a new folder inside `staging` (an
    import's db.new_media_staging folder); returns its path, or None when
    the snapshot has no files for this drama. Member names were already
    checked by validate_backup_file; each target is re-checked to stay
    inside."""
    prefix = f"dramas/{old_id}/"
    members = [i for i in zf.infolist()
               if i.filename.replace("\\", "/").startswith(prefix) and not i.is_dir()]
    if not members:
        return None
    if len(members) > las.RESTORE_MAX_MEMBERS or any(
            i.file_size > _MAX_MEMBER_BYTES for i in members):
        raise InvalidInputError("The drama's files in the snapshot look corrupted or unsafe "
                                "to extract.")
    if not las.has_disk_room(db.DRAMAS_DIR, sum(i.file_size for i in members)):
        raise InvalidInputError("Not enough free disk space to restore this drama's files.")
    folder = os.path.join(staging, f"drama-{int(old_id)}")
    os.makedirs(folder)
    base = os.path.realpath(folder)
    try:
        for info in members:
            rel = info.filename.replace("\\", "/")[len(prefix):]
            dest = os.path.realpath(os.path.join(folder, *rel.split("/")))
            if not dest.startswith(base + os.sep):
                raise InvalidInputError("The backup contains an unsafe file path.")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder


def claim_folder(drama_id: int, conflict: str):
    """dramas/<drama_id> must not exist for a drama being added: a leftover
    of an import that never committed is cleared, anything else is renamed
    aside (db.claim_new_drama_folder); 409 when that fails."""
    try:
        db.claim_new_drama_folder(drama_id)
    except db.DramaFolderConflict:
        raise ConflictError(conflict) from None


def move_media_in(staging: str, folders: dict, conflict: str):
    """Call inside the transaction, with the new drama rows inserted:
    {new drama id: staged folder}. Refuses before moving anything when a
    new id's folder already exists, records the journal (db's media
    journal), then renames each staged folder to dramas/<id>. The caller
    commits next; on any failure it calls _end_media_staging."""
    for did in folders:
        claim_folder(did, conflict)
    db.write_media_journal(staging, folders)
    for did, path in folders.items():
        if not db.move_staged_folder(staging, did, path):
            raise ConflictError(conflict)
    _fsync_dir(db.DRAMAS_DIR)


def end_media_staging(staging: str) -> bool:
    """Ends an import's staging (see db.finish_media_staging): after a
    commit only the markers, journal and staging folder go; otherwise the
    folders it moved into place go too. False when something was left in
    place (logged by id; the journal stays, so the next start retries)."""
    try:
        return db.finish_media_staging(staging)
    except (OSError, sqlite3.Error):
        log.warning("An import's staging folder could not be removed")
        return False


def restore_drama(drama_id, confirm=False, confirm_text="", actor_id=None,
                  snapshot=None) -> dict:
    """Restores one drama from a copy: the one named `snapshot` (matched
    against the backup folder's listing), else the newest readable one.
    Needs confirm=True and
    confirm_text "RESTORE". Refused while a restore, bulk delete, storage
    cleanup, backup or export holds the library. The drama keeps its id
    when that id (and its folder) are free, else it becomes a new drama
    titled "... (restored <date>)". Returns {drama_id, restored_as_new,
    title, media_restored, snapshot (the copy's name), snapshot_kind,
    series, counts, skipped_tables}."""
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("A drama id is a positive whole number.")
    las.require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a drama")
    if job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with las.maintenance("restoring a drama"), storage.job_workdir() as tmp:
        db.recover_media_imports()
        staging = staged = None
        with _snapshot_lock:
            copy = _pick_copy(snapshot)[0]
            path = copy["path"]
            las.validate_backup_file(path, check_disk=False, check_limits=False)
            try:
                with zipfile.ZipFile(path) as zf:
                    manifest = _read_manifest(zf)
                    if drama_id not in [d.get("id") for d in manifest["dramas"]
                                        if isinstance(d, dict)]:
                        raise NotFoundError("That drama isn't in the snapshot.")
                    snap_db = extract_db(zf, tmp)
                    if manifest["kind"] == "full":
                        staging = db.new_media_staging()
                        staged = stage_media(zf, drama_id, staging)
            except (OSError, zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error):
                if staging is not None:
                    end_media_staging(staging)
                raise InvalidInputError(_BAD_SNAPSHOT) from None
            except BaseException:
                if staging is not None:
                    end_media_staging(staging)
                raise
        try:
            return _restore_from(snap_db, drama_id, staging, staged, manifest, actor_id,
                                 copy["name"])
        finally:
            if staging is not None:
                end_media_staging(staging)


def _restore_from(snap_db, drama_id, staging, staged, manifest, actor_id, copy_name) -> dict:
    folder_free = not os.path.lexists(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    keep_id = db.get_drama(drama_id) is None and folder_free
    suffix = None if keep_id else f"(restored {datetime.date.today().isoformat()})"
    conflict = ("A folder for the restored drama is already in the library's dramas folder "
                "and could not be moved aside; nothing was restored.")
    with contextlib.closing(sqlite3.connect(ro_uri(snap_db), uri=True)) as src, \
            contextlib.closing(db.get_conn()) as dst:
        src.execute("PRAGMA trusted_schema = OFF")
        try:
            dst.execute("BEGIN IMMEDIATE")
            live_id, counts, series_outcome = copy_drama(src, dst, drama_id, drama_id if keep_id else None,
                                          suffix)
            claim_folder(live_id, conflict)   # never inherit a stray dramas/<new id>
            if staged is not None:
                move_media_in(staging, {live_id: staged}, conflict)
            dst.commit()
        except BaseException as exc:
            dst.rollback()
            if staging is not None and not end_media_staging(staging):
                raise ServiceError("The drama was not restored, but some of its files could not "
                                   "be cleaned up and are still in the library's dramas folder; "
                                   "the app tries again at the next start.") from None
            if isinstance(exc, (ServiceError, KeyboardInterrupt, SystemExit)):
                raise
            log.warning("Single-drama restore failed: %s", type(exc).__name__)
            raise ServiceError("The drama could not be restored; nothing was changed.") \
                from None
        title = dst.execute("SELECT COALESCE(NULLIF(title_en, ''), title_zh) FROM dramas "
                            "WHERE id = ?", (live_id,)).fetchone()[0]
    try:
        from services import auth_service
        auth_service.write_audit(actor_id, "library.restore_drama",
                                 f"drama {drama_id} restored from backup copy {copy_name} "
                                 f"as drama {live_id}")
    except Exception:
        log.warning("Could not write the audit entry for a drama restore")
    return {"drama_id": live_id, "restored_as_new": not keep_id, "title": title or "",
            "media_restored": staged is not None, "snapshot": copy_name,
            "snapshot_kind": manifest["kind"],
            "series": series_outcome,
            "counts": counts, "skipped_tables": sorted(_SKIPPED_TABLES)}
