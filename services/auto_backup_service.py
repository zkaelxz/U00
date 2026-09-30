"""
services/auto_backup_service.py -- roadmap Step 43 as redefined by the user
(2026-09-29): an opt-in automatic backup that keeps ONE snapshot, plus
restoring a single drama from it. (The roadmap's original Step 43,
universal soft-delete, was replaced by this.)

- Settings (app_settings): enabled (off by default), frequency
  (daily/weekly/monthly, weekly by default), include_media (off: database
  only) and folder ("" = <library>/backups/auto, which every backup already
  skips because backups/ is an excluded top-level entry; a custom folder
  must be outside the library or inside its backups/ folder).
- The snapshot is one zip (library.db + manifest.json, plus media when
  include_media), written by library_admin_service.write_backup_zip -- the
  same writer as the manual backup -- into a hidden partial file next to
  it, validated (zip checks + SQLite quick_check + manifest), and only then
  renamed over the previous snapshot. A failed backup leaves the old
  snapshot untouched.
- The due-check (check_and_run) runs at API startup and hourly from the
  API's existing background poller (api/background.py). A scheduled run
  never starts while any job runs or a restore/maintenance holds the
  library; it is simply tried again at the next check.
- restore_drama copies one drama out of the snapshot (read from a
  read-only temp copy of its library.db) into the live library: the drama
  row and every child table it cascades to (see _CHILD_TABLES), plus its
  series when that series is gone, plus its folder when the snapshot has
  media. If the drama's id is still in use it comes back as a new drama
  (new id, title suffixed "(restored <date>)"); other dramas are never
  touched.

No result or error message carries a filesystem path, except the folder
setting the owner typed themselves (all routes are local_only).
No Streamlit or FastAPI import.
"""

import contextlib
import datetime
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
import zlib

import background_jobs
import db
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

SETTINGS_KEY = "auto_backup.settings"
STATE_KEY = "auto_backup.state"
FREQUENCIES = {"daily": 1, "weekly": 7, "monthly": 30}  # days between runs
DEFAULT_SETTINGS = {"enabled": False, "frequency": "weekly", "include_media": False,
                    "folder": ""}
DEFAULT_SUBDIR = ("backups", "auto")
SNAPSHOT_NAME = "baihe_snapshot.zip"
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
_BAD_SNAPSHOT = "The backup snapshot could not be read; it may be damaged."
_FAILED = "The backup could not be written; the previous snapshot was kept."

# Held while the snapshot file is replaced, deleted, or read for a restore,
# so none of those see a half-swapped file (and Windows never renames over
# a file another thread has open).
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
    return out


def _check_folder(value) -> str:
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
    if not os.path.isdir(real):
        raise InvalidInputError("That backup folder doesn't exist. Create it first.")
    return os.path.normpath(value)


def set_settings(enabled=None, frequency=None, include_media=None, folder=None) -> dict:
    """Updates the given fields (None = unchanged) and returns
    settings_overview()."""
    current = get_settings()
    if enabled is not None:
        if not isinstance(enabled, bool):
            raise InvalidInputError("enabled must be true or false.")
        current["enabled"] = enabled
    if frequency is not None:
        if frequency not in FREQUENCIES:
            raise InvalidInputError("Unknown frequency.", details={"allowed": list(FREQUENCIES)})
        current["frequency"] = frequency
    if include_media is not None:
        if not isinstance(include_media, bool):
            raise InvalidInputError("include_media must be true or false.")
        current["include_media"] = include_media
    if folder is not None:
        folder = _check_folder(folder)
        if folder != current["folder"]:
            _move_snapshot(current["folder"], folder)
        current["folder"] = folder
    db.set_app_setting(SETTINGS_KEY, current)
    return settings_overview()


def _folder_path(folder: str) -> str:
    return folder or os.path.join(db.LIBRARY_DIR, *DEFAULT_SUBDIR)


def _move_snapshot(old_folder: str, new_folder: str):
    """Keeps the one-snapshot rule across a folder change: the existing
    snapshot moves to the new folder (replacing a stale file of the same
    name there). Refused while a backup runs; on failure nothing changes."""
    if _job_running():
        raise ConflictError("A backup is running -- change the folder when it finishes.")
    with _snapshot_lock:
        src = os.path.join(_folder_path(old_folder), SNAPSHOT_NAME)
        if os.path.islink(src) or not os.path.isfile(src):
            return
        dest_dir = _folder_path(new_folder)
        dest = os.path.join(dest_dir, SNAPSHOT_NAME)
        tmp = None
        try:
            os.makedirs(dest_dir, exist_ok=True)
            if os.path.islink(dest):
                raise OSError("snapshot path is a link")
            try:
                os.replace(src, dest)       # same drive: atomic
                return
            except OSError:
                pass
            fd, tmp = tempfile.mkstemp(prefix=".baihe_snapshot.partial-", suffix=".zip",
                                       dir=dest_dir)
            os.close(fd)
            shutil.copyfile(src, tmp)
            os.replace(tmp, dest)
            tmp = None
            os.remove(src)
        except OSError:
            raise ServiceError("The existing snapshot could not be moved to the new folder; "
                               "the folder was not changed.") from None
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    os.remove(tmp)


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
    if last is None:
        return _now()   # never run: due at the next check
    return last + datetime.timedelta(days=FREQUENCIES[settings["frequency"]])


def is_due(now=None, settings=None, state=None) -> bool:
    settings = settings or get_settings()
    if not settings["enabled"]:
        return False
    state = _get_state() if state is None else state
    now = now or _now()
    attempt = _parse(state.get("last_attempt_at"))
    if state.get("last_error") and attempt is not None and now - attempt < RETRY_AFTER_FAILURE:
        return False
    last = _parse(state.get("last_success_at"))
    if last is None:
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
            "running": _job_running()}


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


def _snapshot_path() -> str:
    return os.path.join(_target_dir(create=False), SNAPSHOT_NAME)


def _existing_snapshot():
    """The snapshot's path when it is a regular file (never a symlink)."""
    path = _snapshot_path()
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    return path


def _read_manifest(zf: zipfile.ZipFile) -> dict:
    try:
        info = zf.getinfo(MANIFEST_NAME)
    except KeyError:
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if info.file_size > _MANIFEST_MAX_BYTES:
        raise InvalidInputError(_BAD_SNAPSHOT)
    try:
        data = json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, zipfile.BadZipFile, RuntimeError, OSError,
            EOFError, zlib.error):
        raise InvalidInputError(_BAD_SNAPSHOT) from None
    if (not isinstance(data, dict) or data.get("format") != MANIFEST_FORMAT
            or data.get("kind") not in ("db-only", "full")
            or not isinstance(data.get("dramas"), list)):
        raise InvalidInputError(_BAD_SNAPSHOT)
    return data


def snapshot_info() -> dict:
    """{exists: False} or {exists: True, created_at, kind, size, app_version,
    drama_count}. A snapshot that can't be read reports readable=False."""
    with _snapshot_lock:
        path = _existing_snapshot()
        if path is None:
            return {"exists": False}
        try:
            size = os.path.getsize(path)
            with zipfile.ZipFile(path) as zf:
                manifest = _read_manifest(zf)
        except (OSError, zipfile.BadZipFile, InvalidInputError):
            return {"exists": True, "readable": False}
    return {"exists": True, "readable": True, "created_at": manifest.get("created_at"),
            "kind": manifest["kind"], "size": size,
            "app_version": manifest.get("app_version") or "unknown",
            "drama_count": len(manifest["dramas"])}


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


def _extract_db(zf: zipfile.ZipFile, dest_dir: str) -> str:
    """library.db from the snapshot into dest_dir, checked with SQLite
    (quick_check) before anything reads it."""
    dest = os.path.join(dest_dir, "library.db")
    if zf.getinfo("library.db").file_size > _MAX_MEMBER_BYTES:
        raise InvalidInputError(_BAD_SNAPSHOT)
    with zf.open("library.db") as src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, 1024 * 1024)
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


def _backup_job(job_id, include_media: bool):
    started = _iso(_now())
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
        final = os.path.join(target, SNAPSHOT_NAME)
        with _snapshot_lock:
            if os.path.islink(final):
                raise OSError("snapshot path is a link")
            os.replace(tmp, final)
        tmp = None
        size = os.path.getsize(final)
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
    return background_jobs.start_job(JOB_ID, _backup_job, JOB_ID, include_media,
                                     description="Automatic backup")


def start_now(replace: bool = False, include_media=None) -> dict:
    """"Back up now": writes a new snapshot, replacing the existing one.
    When one exists, replace must be True (the UI shows its date first).
    include_media defaults to the setting."""
    if not isinstance(replace, bool):
        raise InvalidInputError("replace must be true or false.")
    if include_media is None:
        include_media = get_settings()["include_media"]
    elif not isinstance(include_media, bool):
        raise InvalidInputError("include_media must be true or false.")
    if _existing_snapshot() is not None and not replace:
        raise InvalidInputError("A backup snapshot already exists; backing up now replaces "
                                "it. Send replace=true to confirm.")
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


def delete_snapshot(confirm=False, confirm_text="") -> dict:
    las._require_confirm(confirm, confirm_text, DELETE_CONFIRM_TEXT, "Deleting the snapshot")
    if _job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with _snapshot_lock:
        path = _existing_snapshot()
        if path is None:
            raise NotFoundError(_NO_SNAPSHOT)
        try:
            os.remove(path)
        except OSError:
            raise ServiceError("The snapshot could not be deleted; is it open somewhere?") \
                from None
    return {"deleted": True}


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
                 "progress", "personal_notes", "reading_history")
_SKIPPED_TABLES = {
    "usage_log": "ON DELETE SET NULL: the spending rows survive a delete, so restoring them "
                 "would count the cost twice",
    "bulk_jobs": "provider batch jobs: a restored in-flight batch could be polled again and "
                 "write stale results over the restored lines",
}
_LINE_REF_TABLES = ("translation_notes", "line_emotions", "reading_history", "bug_reports")
_PROFILE_TABLES = ("progress", "personal_notes", "reading_history")
_LINE_JSON = {"translation_versions": "lines_json", "line_history": "snapshot_json"}
_SERIES_CHILDREN = ("glossary_terms", "series_characters", "translation_memory")


def list_snapshot_dramas() -> dict:
    """The dramas inside the snapshot, from its manifest, each with
    exists_now (its id is in use, so a restore makes a new drama)."""
    with _snapshot_lock:
        path = _existing_snapshot()
        if path is None:
            raise NotFoundError(_NO_SNAPSHOT)
        try:
            with zipfile.ZipFile(path) as zf:
                manifest = _read_manifest(zf)
        except (OSError, zipfile.BadZipFile):
            raise InvalidInputError(_BAD_SNAPSHOT) from None
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
    return {"created_at": manifest.get("created_at"), "kind": manifest["kind"], "dramas": out}


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


def _remap_json_lines(value, line_map):
    try:
        items = json.loads(value) if value else None
    except ValueError:
        return value
    if not isinstance(items, list):
        return value
    for item in items:
        if isinstance(item, dict) and "id" in item:
            item["id"] = line_map.get(item["id"])
    return json.dumps(items, ensure_ascii=False)


def _live_ids(dst, table: str) -> set:
    return {r[0] for r in dst.execute(f'SELECT id FROM "{table}"')}


def _free_series_name(dst, name) -> str:
    base = (name or "Series").strip() or "Series"
    candidate = f"{base} (restored {datetime.date.today().isoformat()})"
    n = 2
    while dst.execute("SELECT 1 FROM series WHERE name = ?", (candidate,)).fetchone():
        candidate = f"{base} (restored {datetime.date.today().isoformat()}, {n})"
        n += 1
    return candidate


def _resolve_series(src, dst, series_id, drama_owner, users, counts) -> tuple:
    """(live series id or None, {old series_character id: live id}).
    The live series is reused only when it is the SAME series (same id and
    name) and the private-series rule allows the drama in it (the
    db.assign_drama_series predicate: a private series takes only its
    owner's and the PC owner's dramas). A name match alone never links a
    drama to someone else's series; otherwise the series comes back from
    the snapshot as a new series (renamed "... (restored <date>)" if its
    name is taken) with its glossary, characters and memory."""
    if series_id is None:
        return None, {}
    srows = _rows(src, "series", "id = ?", (series_id,))
    if not srows:
        return None, {}
    series = srows[0]
    live = dst.execute("SELECT id, owner_user_id, COALESCE(is_private, 0) FROM series "
                       "WHERE id = ? AND name IS ?", (series_id, series.get("name"))).fetchone()
    if live is not None and not (live[2] and drama_owner is not None
                                 and drama_owner != live[1]):
        live_chars = {r[0] for r in dst.execute(
            "SELECT id FROM series_characters WHERE series_id = ?", (series_id,))}
        return series_id, {c: c for c in live_chars}
    row = {k: v for k, v in series.items() if k != "id"}
    if row.get("owner_user_id") not in users:
        row["owner_user_id"] = None
    if dst.execute("SELECT 1 FROM series WHERE name = ?", (row.get("name"),)).fetchone():
        row["name"] = _free_series_name(dst, row.get("name"))
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


def _copy_drama(src, dst, old_id: int, new_id, title_suffix) -> tuple:
    """Inserts the drama and its children into dst (inside the caller's
    transaction). new_id None = a fresh id. Returns (live id, counts)."""
    drama = _rows(src, "dramas", "id = ?", (old_id,))
    if not drama:
        raise NotFoundError("That drama isn't in the snapshot.")
    drama = drama[0]
    counts = {}
    users = {r[0] for r in dst.execute("SELECT id FROM users")}
    profiles = _live_ids(dst, "profiles")
    row = dict(drama)
    if row.get("owner_user_id") not in users:
        row["owner_user_id"] = None
    series_id, char_map = _resolve_series(src, dst, drama.get("series_id"),
                                          row["owner_user_id"], users, counts)
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
        live_cols = _columns(dst, table)
        n = 0
        for child in _rows(src, table, "drama_id = ?", (old_id,)):
            old = child.pop("id", None)
            child["drama_id"] = live_id
            if table in _PROFILE_TABLES and child.get("profile_id") not in profiles:
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
                child[col] = _remap_json_lines(child.get(col), line_map)
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
    return live_id, counts


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


def restore_drama(drama_id, confirm=False, confirm_text="", actor_id=None) -> dict:
    """Restores one drama from the snapshot. Needs confirm=True and
    confirm_text "RESTORE". Refused while a restore, bulk delete, storage
    cleanup, backup or export holds the library. The drama keeps its id
    when that id (and its folder) are free, else it becomes a new drama
    titled "... (restored <date>)". Returns {drama_id, restored_as_new,
    title, media_restored, counts, skipped_tables}."""
    if isinstance(drama_id, bool) or not isinstance(drama_id, int) or drama_id < 1:
        raise InvalidInputError("A drama id is a positive whole number.")
    las._require_confirm(confirm, confirm_text, RESTORE_CONFIRM_TEXT, "Restoring a drama")
    if _job_running():
        raise ConflictError("A backup is running -- wait for it to finish.")
    with las._maintenance("restoring a drama"), tempfile.TemporaryDirectory() as tmp:
        staging = None
        with _snapshot_lock:
            path = _existing_snapshot()
            if path is None:
                raise NotFoundError(_NO_SNAPSHOT)
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
            return _restore_from(snap_db, drama_id, staging, manifest, actor_id)
        finally:
            if staging is not None and os.path.isdir(staging):
                shutil.rmtree(staging, ignore_errors=True)


def _restore_from(snap_db, drama_id, staging, manifest, actor_id) -> dict:
    folder_free = not os.path.lexists(os.path.join(db.DRAMAS_DIR, str(drama_id)))
    keep_id = db.get_drama(drama_id) is None and folder_free
    suffix = None if keep_id else f"(restored {datetime.date.today().isoformat()})"
    moved_to = None
    with contextlib.closing(sqlite3.connect(_ro_uri(snap_db), uri=True)) as src, \
            contextlib.closing(db.get_conn()) as dst:
        src.execute("PRAGMA trusted_schema = OFF")
        try:
            dst.execute("BEGIN IMMEDIATE")
            live_id, counts = _copy_drama(src, dst, drama_id, drama_id if keep_id else None,
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
                                 f"drama {drama_id} restored from the backup snapshot "
                                 f"as drama {live_id}")
    except Exception:
        log.warning("Could not write the audit entry for a drama restore")
    return {"drama_id": live_id, "restored_as_new": not keep_id, "title": title or "",
            "media_restored": moved_to is not None, "snapshot_kind": manifest["kind"],
            "counts": counts, "skipped_tables": sorted(_SKIPPED_TABLES)}
