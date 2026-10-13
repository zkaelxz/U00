"""
services/auto_backup_service.py -- an opt-in automatic backup that keeps a
few rotating copies. This module runs it: saving settings, the backup job,
the scheduled due-check, deleting copies, and listing the dramas in a copy.

- Each successful run writes a NEW copy (see auto_backup_files for how it is
  written, checked and placed) with library_admin_service.write_backup_zip --
  the same writer as the manual backup. Only after the copy is in place are
  old copies pruned (auto_backup_copies holds the ownership and retention
  rules). A failed backup or failed check never deletes anything; nor does a
  run skipped for want of free space (services/backup_space_check.py).
- The due-check (check_and_run) runs at API startup and hourly from the
  API's existing background poller (api/background.py). A scheduled run
  never starts while any job runs or a restore/maintenance holds the
  library; it is simply tried again at the next check.
- Restoring one drama from a copy is services/drama_restore_service.py,
  which imports this module (never the reverse).

No result or error message carries a filesystem path (copies are named by
file name only), except the folder setting the owner typed themselves (all
routes are local_only).
No FastAPI import.
"""

import contextlib
import logging
import os
import shutil
import tempfile
import threading
import time

import background_jobs
from jobs import job_launch
import db
from db import fsync_dir as _fsync_dir
from services import backup_space_check as space
from services import library_admin_service as las
from services import library_backup_service as lbs
from services.auto_backup_copies import (_MANAGED, _MAX_COPY_NAME_LEN, _NO_COPY, _NO_SNAPSHOT, _classify,
                                         _identity, _identity_info, _list_copies, _pick_copy,
                                         _prune, _record_copy, _snapshot_lock, snapshot_info)
from services.auto_backup_files import (_fsync_file, _manifest_bytes, _move_copies,
                                        _place_copy, _verify_snapshot)
from services.auto_backup_settings import (FREQUENCIES, SETTINGS_KEY, _check_folder, _get_state,
                                           _iso, _now, _parse, _target_dir, _update_state,
                                           folder_path, get_settings, is_due, next_run_at)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

log = logging.getLogger(__name__)

JOB_ID = las.AUTO_BACKUP_JOB_ID


CHECK_INTERVAL_SECONDS = 3600.0


RESTORE_CONFIRM_TEXT = "RESTORE"


DELETE_CONFIRM_TEXT = "DELETE"


_UNMANAGED = ("That copy isn't managed by this library (another library's, one made before "
              "copies were tagged, or one that can't be read). Confirm deleting it "
              "explicitly.")


_FAILED = "The backup could not be written; the existing copies were kept."


_CHECK_FAILED = "The automatic backup check failed, so no backup was started."


_last_check = None          # time.monotonic() of the last periodic check


_tick_lock = threading.Lock()


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


def job_running() -> bool:
    job = background_jobs.get_status(JOB_ID)
    return bool(job and job.get("status") in ("running", "queued"))


class _LowSpace(Exception):
    pass


def _backup_job(job_id, include_media: bool):
    now = _now()
    started = _iso(now)
    _update_state(last_attempt_at=started)
    tmp = None
    try:
        target = _target_dir(create=True)
        if not space.has_room(target, include_media, _get_state()):
            raise _LowSpace()
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
        lbs.write_backup_zip(tmp, include_media=include_media,
                             manifest=lambda snap: _manifest_bytes(snap, include_media, owner),
                             should_cancel=lambda: background_jobs.is_cancel_requested(job_id))
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
    except background_jobs.JobCancelled:
        # A cancel is not a failure: no error is recorded, and the finally
        # below removes the partial archive. The scheduled check backs off
        # as after a failure, or a cancelled long run would restart hourly.
        _update_state(last_cancelled_at=started)
        raise
    except Exception as exc:
        error = space.LOW_SPACE if isinstance(exc, _LowSpace) else _FAILED
        log.warning("Automatic backup failed: %s", type(exc).__name__)
        _update_state(last_error=error)
        raise RuntimeError(error) from None
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.remove(tmp)
    kind = space.copy_kind(include_media)
    _update_state(last_success_at=started, last_error=None, last_copy_size=size,
                  last_copy_kind=kind)
    background_jobs.set_result(job_id, {"size": size, "kind": kind})
    background_jobs.update_progress(job_id, 1.0, "Backup ready.")


def _start(include_media: bool) -> bool:
    # Under _snapshot_lock so a folder change can't slip between its
    # running-job check and its save (see set_settings).
    with _snapshot_lock:
        return job_launch.start_job(JOB_ID, _backup_job, JOB_ID, include_media,
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
    if job_launch.exclusive_active():
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
        if (job_launch.maintenance_active() or job_launch.exclusive_active()
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
    in the backup folder, partial manual backups and exports (".partial_*"
    in backups/ and its subfolders) and restore staging folders from older versions
    ("dramas/.restoring-*") left by a process that was killed mid-way,
    once older than a day, plus the leftovers of interrupted imports and
    restores (db.recover_media_imports: any age once journalled, but never
    one whose import holds its lock). Symlinks are left alone. Never
    raises."""
    now = time.time() if now is None else now
    removed = db.recover_media_imports(max_age, now)["settled"]
    targets = [(folder_path(get_settings()["folder"]), ".baihe_snapshot.partial-", False),
               (db.DRAMAS_DIR, ".restoring-", True)]
    targets += [(os.path.join(db.LIBRARY_DIR, *sub), ".partial_", False)
                for sub in las.ARTIFACT_SUBDIRS.values()]
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
