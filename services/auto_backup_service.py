"""
services/auto_backup_service.py -- roadmap Step 43 as redefined by the user
(2026-09-29): an opt-in automatic backup that keeps a few rotating copies,
plus restoring a single drama from any of them. (The roadmap's original
Step 43, universal soft-delete, was replaced by this.)

- Settings (app_settings): enabled (off by default), frequency
  (daily/weekly/monthly, daily by default), include_media (off: database
  only) and folder ("" = <library>/backups/auto, which every backup already
  skips because backups/ is an excluded top-level entry; a custom folder
  must be outside the library or inside its backups/ folder). A value the
  owner already saved is kept; the defaults only fill in what was never
  saved.
- Each successful run writes a NEW copy, baihe_snapshot-YYYYMMDD-HHMMSS.zip
  (UTC; library.db + manifest.json, plus media when include_media), with
  library_admin_service.write_backup_zip -- the same writer as the manual
  backup -- into a hidden partial file next to it, validated (zip checks +
  SQLite quick_check + manifest), flushed to disk, and only then renamed
  into place. Only after that are old copies pruned. A failed backup or
  failed check never deletes anything.
- Retention (deterministic, applied after each successful run, under
  _snapshot_lock): one copy per day for the last 2 days, plus the first
  copy of each of the last 2 weeks. Precisely, among the readable copies
  dated no later than the copy just written: "daily" = the new copy, plus
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
  locked, no permission, drive hiccup) takes no slot and is never deleted.
  A damaged copy (not a zip, bad manifest) takes no slot and is deleted
  only once it is older than every kept copy (a newer damaged file is left
  for the owner to look at). Pruning only ever touches regular files in
  the backup folder whose name is exactly the copy pattern or the legacy
  name: never other files, never symlinks, never a name taken from a
  request.
- The due-check (check_and_run) runs at API startup and hourly from the
  API's existing background poller (api/background.py). A scheduled run
  never starts while any job runs or a restore/maintenance holds the
  library; it is simply tried again at the next check.
- restore_drama copies one drama out of a copy (the newest readable one not
  dated after now, or
  one named by the caller and matched against the folder's listing, never
  used as a path), read from a read-only temp copy of its library.db, into
  the live library: the drama row and every child table it cascades to (see
  _CHILD_TABLES), plus its series when that series is gone, plus its folder
  when the copy has media. If the drama's id is still in use it comes back
  as a new drama (new id, title suffixed "(restored <date>)"); other dramas
  are never touched. The result names the copy it came from.

No result or error message carries a filesystem path (copies are named by
file name only), except the folder setting the owner typed themselves (all
routes are local_only).
No Streamlit or FastAPI import.
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
import zlib

import background_jobs
import db
from services import delete_service
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

SETTINGS_KEY = "auto_backup.settings"
STATE_KEY = "auto_backup.state"
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
_MAX_MEMBER_BYTES = wjs._MAX_RESTORE_MEMBER_BYTES
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
_FAILED = "The backup could not be written; the existing copies were kept."

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
    artifact_dirs = {os.path.join(lib, *sub) for sub in las._ARTIFACT_SUBDIRS.values()}
    if real in artifact_dirs:
        raise InvalidInputError("That folder holds the manual backups and exports; pick a "
                                "folder of its own (the default is fine).")
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
    # One hold for the read, the running-job check, a folder move and the
    # save: two concurrent saves can't drop each other's fields, and a
    # backup can't start in between and write to the old folder (_start
    # takes the same lock).
    with _snapshot_lock:
        current = get_settings()
        for key, value in (("enabled", enabled), ("frequency", frequency),
                           ("include_media", include_media)):
            if value is not None:
                current[key] = value
        if folder is not None and folder != current["folder"]:
            if _job_running():
                raise ConflictError("A backup is running -- change the folder when it "
                                    "finishes.")
            _move_copies(current["folder"], folder)
            current["folder"] = folder
        db.set_app_setting(SETTINGS_KEY, current)
    # Not under the lock: snapshot_info takes it and it isn't reentrant.
    return settings_overview()


def _folder_path(folder: str) -> str:
    return folder or os.path.join(db.LIBRARY_DIR, *DEFAULT_SUBDIR)


def _move_copies(old_folder: str, new_folder: str):
    """Every copy (and the legacy snapshot) moves to the new folder with the
    folder setting, replacing a stale file of the same name there. The
    caller holds _snapshot_lock and has checked no backup runs. On failure
    the copies already moved are moved back and the error is raised, so the
    setting is not changed and the copies stay together."""
    src_dir, dest_dir = _folder_path(old_folder), _folder_path(new_folder)
    copies = _list_copies(src_dir)
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


def _fsync_dir(folder: str):
    """Makes a rename into `folder` durable. POSIX only: Windows can't open
    a directory for this and commits the rename with the file."""
    if os.name != "posix":
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
    return dt.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")


def _parse(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=datetime.timezone.utc)


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
            "running": _job_running(),
            "copies": snapshot_info()["copies"]}


# --------------------------------------------------------------------------
# snapshot file
# --------------------------------------------------------------------------

def _target_dir(create: bool) -> str:
    path = _folder_path(get_settings()["folder"])
    if create:
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            raise ServiceError("The backup folder could not be created.") from None
    return path


def _copy_name(at: datetime.datetime) -> str:
    return f"baihe_snapshot-{at.astimezone(datetime.timezone.utc):%Y%m%d-%H%M%S}.zip"


def _list_copies(folder: str) -> list:
    """Every copy in `folder`, newest first: [{name, path, at, size}]. Only
    regular files (checked without following links) whose name is exactly
    the copy pattern or the legacy name; `at` is the UTC time in the name,
    or the legacy file's modification time."""
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
        try:
            if match is not None:
                at = datetime.datetime.strptime(match.group(1) + match.group(2),
                                                "%Y%m%d%H%M%S").replace(
                    tzinfo=datetime.timezone.utc)
            else:
                at = datetime.datetime.fromtimestamp(st.st_mtime, datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
        out.append({"name": name, "path": path, "at": at, "size": st.st_size})
    out.sort(key=lambda c: (c["at"], c["name"]), reverse=True)
    return out


# Runs within one second get names a second apart (_new_copy_path), so the
# newest real copy can be named slightly after now; only a copy dated
# further ahead than this counts as future-dated for the default pick.
_FUTURE_SLACK = datetime.timedelta(minutes=10)


def _newest_first(copies: list) -> list:
    """`copies` (newest first) in the order the default pick tries them:
    copies dated up to now (plus _FUTURE_SLACK) first, then any dated later
    (a clock that ran ahead), so a future-dated copy is never taken over the latest real one
    but is still used when nothing else can be read."""
    limit = _now() + _FUTURE_SLACK
    return ([c for c in copies if c["at"] <= limit]
            + [c for c in copies if c["at"] > limit])


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
    except (zipfile.BadZipFile, InvalidInputError, EOFError, ValueError, RuntimeError,
            zlib.error):
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


def _prune(folder: str, new_name: str) -> int:
    """Deletes the copies the rotation no longer keeps; returns how many.
    The caller holds _snapshot_lock and has just put the validated copy
    `new_name` in place; it is always kept and anchors the rule. A copy
    dated after it (a clock set back, a future-dated file) or one that
    can't be opened right now takes no slot and is never deleted; a damaged
    copy is deleted only when older than every kept copy. Nothing is
    deleted unless the new copy reads back."""
    copies = _list_copies(folder)
    new = next((c for c in copies if c["name"] == new_name), None)
    if new is None or _read_copy(new["path"])[0] != _OK:
        return 0
    older = [c for c in copies if c["name"] != new_name and c["at"] <= new["at"]]
    state = {c["name"]: _read_copy(c["path"])[0] for c in older}
    readable = [new] + [c for c in older if state[c["name"]] == _OK]
    kept = _retention(readable)
    oldest_kept = min(c["at"] for c in readable if c["name"] in kept)
    removed = 0
    for copy in older:
        if copy["name"] in kept or state[copy["name"]] == _UNREADABLE:
            continue
        if state[copy["name"]] == _DAMAGED and copy["at"] >= oldest_kept:
            continue
        try:
            os.remove(copy["path"])     # removes a name, never follows a link
            removed += 1
        except OSError:
            log.warning("Could not delete an old automatic backup copy")
    return removed


def _pick_copy(name=None) -> tuple:
    """(copy, manifest) for the copy called `name` -- matched against the
    backup folder's own listing, never used as a path -- or, with no name,
    the newest readable copy not dated after now (a future-dated one only
    when no other can be read). The caller holds _snapshot_lock."""
    copies = _list_copies(_target_dir(create=False))
    if name is None:
        if not copies:
            raise NotFoundError(_NO_SNAPSHOT)
        for copy in _newest_first(copies):
            state, manifest = _read_copy(copy["path"])
            if state == _OK:
                return copy, manifest
        raise InvalidInputError(_BAD_SNAPSHOT)
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
            EOFError, zlib.error):
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if (not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT
            or data.get("kind") not in ("db-only", "full")
            or not isinstance(data.get("dramas"), list)):
        raise InvalidInputError(_BAD_SNAPSHOT)
    return data


def snapshot_info() -> dict:
    """{exists, copies} plus, for the copy a restore picks by default (the
    newest readable one, see _pick_copy), readable,
    created_at, kind, size, app_version and drama_count. copies lists every
    copy newest first as {name, created_at, size, kind, drama_count,
    readable, kept_as}: names only, never paths. When copies exist but none
    can be read: {exists: True, readable: False, copies}."""
    with _snapshot_lock:
        described = [(copy, _read_copy(copy["path"])[1])
                     for copy in _list_copies(_target_dir(create=False))]
    # As the next run would see it: a copy dated after now takes no slot.
    now = _now()
    kept = _retention([c for c, m in described if m is not None and c["at"] <= now])
    copies, entries = [], {}
    for copy, manifest in described:
        created = manifest.get("created_at") if manifest else None
        entry = {"name": copy["name"],
                 "created_at": created if isinstance(created, str) else _iso(copy["at"]),
                 "size": copy["size"], "readable": manifest is not None,
                 "kind": manifest["kind"] if manifest else None,
                 "drama_count": len(manifest["dramas"]) if manifest else None,
                 "kept_as": kept.get(copy["name"])}
        copies.append(entry)
        entries[copy["name"]] = entry
    # The one a restore with no copy named would use (see _pick_copy).
    manifests = {c["name"]: m for c, m in described}
    newest = next(((entries[c["name"]], manifests[c["name"]])
                   for c in _newest_first([c for c, _ in described])
                   if manifests[c["name"]] is not None), None)
    if not copies:
        return {"exists": False, "copies": []}
    if newest is None:
        return {"exists": True, "readable": False, "copies": copies}
    entry, manifest = newest
    return {"exists": True, "readable": True, "created_at": entry["created_at"],
            "kind": entry["kind"], "size": entry["size"],
            "app_version": manifest.get("app_version") or "unknown",
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


def _manifest_bytes(snap_path: str, include_media: bool) -> bytes:
    with contextlib.closing(sqlite3.connect(_ro_uri(snap_path), uri=True)) as conn:
        rows = conn.execute("SELECT id, title_en, title_zh, media_type FROM dramas "
                            "ORDER BY id").fetchall()
        counts = dict(conn.execute("SELECT drama_id, COUNT(*) FROM lines "
                                   "GROUP BY drama_id").fetchall())
    dramas = [{"id": r[0], "title": (r[1] or r[2] or f"Drama {r[0]}"),
               "media_type": r[3] or "", "line_count": counts.get(r[0], 0)} for r in rows]
    manifest = {"format": MANIFEST_FORMAT, "created_at": _iso(_now()),
                "kind": "full" if include_media else "db-only",
                "app_version": _app_version(), "database_size": os.path.getsize(snap_path),
                "dramas": dramas}
    return json.dumps(manifest, ensure_ascii=False).encode("utf-8")


def _ro_uri(path: str) -> str:
    from urllib.parse import quote
    return f"file:{quote(os.path.abspath(path))}?mode=ro"


def _extract_db(zf: zipfile.ZipFile, dest_dir: str, max_bytes: int = None,
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
        with contextlib.closing(sqlite3.connect(_ro_uri(dest), uri=True)) as conn:
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
    with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
        manifest = _read_manifest(zf)
        dest = _extract_db(zf, tmp)
        with contextlib.closing(sqlite3.connect(_ro_uri(dest), uri=True)) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM dramas ORDER BY id")]
    if ids != [d.get("id") for d in manifest["dramas"]]:
        raise InvalidInputError(_BAD_SNAPSHOT)


# --------------------------------------------------------------------------
# backup job
# --------------------------------------------------------------------------

def _job_running() -> bool:
    job = background_jobs.get_status(JOB_ID)
    return bool(job and job.get("status") in ("running", "queued"))


def _new_copy_path(folder: str, now: datetime.datetime) -> str:
    """A free name for the new copy: its UTC time, moved on a second at a
    time while that name is taken (two backups within one second). Never
    replaces an existing file. The caller holds _snapshot_lock."""
    at = now.astimezone(datetime.timezone.utc).replace(microsecond=0)
    for _ in range(1000):
        path = os.path.join(folder, _copy_name(at))
        if not os.path.lexists(path):
            return path
        at += datetime.timedelta(seconds=1)
    raise OSError("no free backup copy name")


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
        background_jobs.update_progress(job_id, 0.1, "Writing the backup...")
        las.write_backup_zip(tmp, include_media=include_media,
                             manifest=lambda snap: _manifest_bytes(snap, include_media))
        background_jobs.update_progress(job_id, 0.8, "Checking the backup...")
        _verify_snapshot(tmp)
        # On disk before any old copy is deleted: a power cut must not
        # leave an empty new copy and no old ones.
        _fsync_file(tmp)
        with _snapshot_lock:
            final = _new_copy_path(target, now)
            os.replace(tmp, final)
            tmp = None
            size = os.path.getsize(final)
            # Only now, with the new copy validated and in place (a failed
            # directory flush skips the rotation, never the backup).
            try:
                _fsync_dir(target)
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
    las._refuse_during_maintenance("backup")
    if background_jobs.exclusive_active():
        raise ConflictError("A restore is in progress; the backup can start when it finishes.")
    if _job_running():
        raise ConflictError("A backup is already running.")
    if not _start(include_media):
        raise ConflictError("A backup is already running.")
    return {"job_id": JOB_ID}


def check_and_run(now=None) -> str:
    """The scheduled due-check. Returns "disabled", "not_due", "busy" (a job,
    restore or maintenance is in progress; tried again at the next check)
    or "started". Never raises."""
    try:
        settings = get_settings()
        if not settings["enabled"]:
            return "disabled"
        if not is_due(now=now, settings=settings):
            return "not_due"
        if (background_jobs.maintenance_active() or background_jobs.exclusive_active()
                or las._any_job_running()):
            return "busy"
        return "started" if _start(settings["include_media"]) else "busy"
    except Exception as exc:
        log.warning("Automatic backup check failed: %s", type(exc).__name__)
        return "busy"


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
    in the backup folder and single-drama restore staging folders
    ("dramas/.restoring-*") left by a process that was killed mid-way,
    once older than a day. Symlinks are left alone. Never raises."""
    now = time.time() if now is None else now
    removed = 0
    targets = [(_folder_path(get_settings()["folder"]), ".baihe_snapshot.partial-", False),
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


def delete_snapshot(confirm=False, confirm_text="", snapshot=None, all_copies=False) -> dict:
    """Deletes the copy named `snapshot` (matched against the folder's
    listing), or every copy with all_copies=True -- exactly one of the two,
    so a request that lost its name can't delete everything. Needs
    confirm=True and confirm_text "DELETE". Returns {deleted, count}."""
    las._require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting the snapshot")
    if not isinstance(all_copies, bool):
        raise InvalidInputError("all must be true or false.")
    if (snapshot is None) == (not all_copies):
        raise InvalidInputError("Name one backup copy to delete, or ask for all of them.")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot
                                 or len(snapshot) > _MAX_COPY_NAME_LEN):
        raise InvalidInputError("A backup copy is named by its file name.")
    if _job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with _snapshot_lock:
        copies = _list_copies(_target_dir(create=False))
        if snapshot is not None:
            copies = [c for c in copies if c["name"] == snapshot]
            if not copies:
                raise NotFoundError(_NO_COPY)
        elif not copies:
            raise NotFoundError(_NO_SNAPSHOT)
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
    return {"deleted": True, "count": count}


# --------------------------------------------------------------------------
# restore one drama
# --------------------------------------------------------------------------

# Every table with a drama_id foreign key to dramas(id), in insert order,
# except the two in _SKIPPED_TABLES. tests/test_auto_backup_service.py
# checks this against the live schema, so a new child table fails a test
# until it is listed in one or the other.
_CHILD_TABLES = ("lines", "pages", "characters", "translation_notes", "line_emotions",
                 "consistency_issues", "vocab_lookups", "line_history", "translation_versions",
                 "bug_reports", "wiki_entries", "edit_samples", "voice_suggestion_dismissals",
                 "progress", "personal_notes", "reading_history", "metadata_field_provenance")
_SKIPPED_TABLES = {
    "usage_log": "ON DELETE SET NULL: the spending rows survive a delete, so restoring them "
                 "would count the cost twice",
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
_SERIES_CHILDREN = ("glossary_terms", "series_characters", "translation_memory")
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
    return {"name": copy["name"], "created_at": manifest.get("created_at"),
            "kind": manifest["kind"], "dramas": out}


def _columns(conn, table: str) -> list:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _has_table(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (table,)).fetchone() is not None


def _insert(dst, table: str, row: dict, live_cols) -> int:
    cols = [c for c in row if c in live_cols]
    names = ", ".join(f'"{c}"' for c in cols)
    marks = ", ".join("?" for _ in cols)
    cur = dst.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                      [row[c] for c in cols])
    return cur.lastrowid


def _rows(src, table: str, where: str, args) -> list:
    if not _has_table(src, table):
        return []
    cur = src.execute(f'SELECT * FROM "{table}" WHERE {where}', args)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _import_file_ref(folder, value, subdir):
    """`value` (normalised to "/") when it is a plain file name, or
    "<subdir>/<plain name>", that stays inside `folder`; None otherwise,
    and always None when `folder` is None (the drama's files weren't
    imported)."""
    if folder is None or not isinstance(value, str) or "\x00" in value or ":" in value:
        return None
    parts = value.replace("\\", "/").split("/")
    if len(parts) == 1:
        base, name = folder, parts[0]
    elif len(parts) == 2 and subdir is not None and parts[0] == subdir:
        base, name = os.path.join(folder, subdir), parts[1]
    else:
        return None
    if delete_service._file_in_folder(base, name) is None:
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
    srows = _rows(src, "series", "id = ?", (series_id,))
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
    live_id = _insert(dst, "series", row, _columns(dst, "series"))
    counts["series"] = 1
    char_map = {}
    for table in _SERIES_CHILDREN:
        live_cols = _columns(dst, table)
        n = 0
        for child in _rows(src, table, "series_id = ?", (series_id,)):
            old = child.pop("id", None)
            child["series_id"] = live_id
            new = _insert(dst, table, child, live_cols)
            if table == "series_characters":
                char_map[old] = new
            n += 1
        counts[table] = n
    return live_id, char_map


def _copy_drama(src, dst, old_id: int, new_id, title_suffix, import_as=None) -> tuple:
    """Inserts the drama and its children into dst (inside the caller's
    transaction). new_id None = a fresh id. Returns (live id, counts,
    series outcome -- see _resolve_series).
    import_as (a backup from another library, see backup_import_service) =
    {"owner_user_id", "is_private", "series": {}, "media_dir"}: the owner
    and privacy come from it and never from the file, the series is always
    new, the Notion page link is dropped, per-profile tables (profile ids
    mean something else in this library) are not copied, and each file
    reference (IMPORT_FILE_COLUMNS) is kept only when it names a file inside
    media_dir (the drama's imported files; None = none imported, so every
    reference is cleared)."""
    drama = _rows(src, "dramas", "id = ?", (old_id,))
    if not drama:
        raise NotFoundError("That drama isn't in the snapshot.")
    drama = drama[0]
    counts = {}
    users = {r[0] for r in dst.execute("SELECT id FROM users")}
    profiles = _live_ids(dst, "profiles")
    row = dict(drama)
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
    live_id = _insert(dst, "dramas", row, _columns(dst, "dramas"))
    counts["dramas"] = 1

    line_map, page_map = {}, {}
    for table in _CHILD_TABLES:
        if not _has_table(dst, table):
            continue
        if import_as is not None and table in _PROFILE_TABLES:
            counts[table] = 0
            continue
        live_cols = _columns(dst, table)
        n = 0
        for child in _rows(src, table, "drama_id = ?", (old_id,)):
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
            new = _insert(dst, table, child, live_cols)
            if table == "lines":
                line_map[old] = new
            elif table == "pages":
                page_map[old] = new
            n += 1
        counts[table] = n
    if page_map and _has_table(src, "bubbles"):
        live_cols = _columns(dst, "bubbles")
        n = 0
        for old_page, new_page in page_map.items():
            for bubble in _rows(src, "bubbles", "page_id = ?", (old_page,)):
                bubble.pop("id", None)
                bubble["page_id"] = new_page
                _insert(dst, "bubbles", bubble, live_cols)
                n += 1
        counts["bubbles"] = n
    return live_id, counts, series_outcome


def _stage_media(zf: zipfile.ZipFile, old_id: int):
    """Extracts dramas/<old_id>/... into a hidden staging folder inside
    DRAMAS_DIR; returns its path, or None when the snapshot has no files
    for this drama. Member names were already checked by
    validate_backup_file; each target is re-checked to stay inside."""
    prefix = f"dramas/{old_id}/"
    members = [i for i in zf.infolist()
               if i.filename.replace("\\", "/").startswith(prefix) and not i.is_dir()]
    if not members:
        return None
    if len(members) > las._RESTORE_MAX_MEMBERS or any(
            i.file_size > _MAX_MEMBER_BYTES for i in members):
        raise InvalidInputError("The drama's files in the snapshot look corrupted or unsafe "
                                "to extract.")
    need = sum(i.file_size for i in members)
    try:
        free = shutil.disk_usage(db.DRAMAS_DIR).free
    except OSError:
        free = None
    if free is not None and free < need + las._RESTORE_DISK_MARGIN_BYTES:
        raise InvalidInputError("Not enough free disk space to restore this drama's files.")
    os.makedirs(db.DRAMAS_DIR, exist_ok=True)
    staging = os.path.join(db.DRAMAS_DIR, f".restoring-{uuid.uuid4().hex[:8]}")
    os.makedirs(staging)
    base = os.path.realpath(staging)
    try:
        for info in members:
            rel = info.filename.replace("\\", "/")[len(prefix):]
            dest = os.path.realpath(os.path.join(staging, *rel.split("/")))
            if not dest.startswith(base + os.sep):
                raise InvalidInputError("The backup contains an unsafe file path.")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return staging


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
    las._require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a drama")
    if _job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with las._maintenance("restoring a drama"), tempfile.TemporaryDirectory() as tmp:
        staging = None
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
                    snap_db = _extract_db(zf, tmp)
                    if manifest["kind"] == "full":
                        staging = _stage_media(zf, drama_id)
            except (OSError, zipfile.BadZipFile):
                raise InvalidInputError(_BAD_SNAPSHOT) from None
        try:
            return _restore_from(snap_db, drama_id, staging, manifest, actor_id, copy["name"])
        finally:
            if staging is not None and os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)


def _restore_from(snap_db, drama_id, staging, manifest, actor_id, copy_name) -> dict:
    folder_free = not os.path.lexists(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    keep_id = db.get_drama(drama_id) is None and folder_free
    suffix = None if keep_id else f"(restored {datetime.date.today().isoformat()})"
    moved_to = None
    with contextlib.closing(sqlite3.connect(_ro_uri(snap_db), uri=True)) as src, \
            contextlib.closing(db.get_conn()) as dst:
        src.execute("PRAGMA trusted_schema = OFF")
        try:
            dst.execute("BEGIN IMMEDIATE")
            live_id, counts, series_outcome = _copy_drama(src, dst, drama_id, drama_id if keep_id else None,
                                          suffix)
            if staging is not None:
                final = os.path.join(db.DRAMAS_DIR, str(live_id))
                if os.path.lexists(final):
                    raise ConflictError("A folder for the restored drama already exists; "
                                        "nothing was restored.")
                os.rename(staging, final)
                moved_to = final
            dst.commit()
        except BaseException as exc:
            dst.rollback()
            if moved_to is not None:
                with contextlib.suppress(OSError):
                    os.rename(moved_to, staging)
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
            "media_restored": moved_to is not None, "snapshot": copy_name,
            "snapshot_kind": manifest["kind"],
            "series": series_outcome,
            "counts": counts, "skipped_tables": sorted(_SKIPPED_TABLES)}
