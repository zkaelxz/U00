"""
services/disk_usage_service.py -- "what is taking up space" for the app's own
data folder, plus moving items to Baihe's Trash folder (and restoring or
permanently deleting them from it) and moving the few parts Baihe can be told
a new location for. UI-free; raises lib/errors.

Scope is the data folder only (the parent of db.LIBRARY_DIR, which is
portable.data_dir() in the running app). Everything is addressed by a path
RELATIVE to it, and nothing returned names an absolute path or the data
folder itself.

Scan: the immediate children of one folder with recursive size and file
count, measured by a bounded walk (MAX_ENTRIES entries or MAX_SECONDS, then
`partial`). Symlinks, Windows junctions and folders on another volume are
counted as the link itself and never entered, so a folder's size is what lives
inside it. What they point at is measured apart (services/disk_usage_links.py,
same budget, shown as `linked_bytes`, never a path), so a folder reads the same
from its parent and when opened. Nothing is cached: every call walks the disk
again.

Clear and move are server-enforced, not only hidden in the UI:
- protected: the data folder itself, the live SQLite files (and -wal, -shm,
  -journal), .env and key/secret files, the INSTALLED/PORTABLE markers, the
  saved site sign-ins, installer files, the program folder, the Trash folder,
  and any folder that holds or contains one of those. Both refuse while any
  background job, restore or maintenance run holds the library, or while
  another process holds the GPU lock (all a run in another process leaves; see
  _busy_under_hold in services/disk_usage_hold.py).
- Clear needs confirm=true and the size and file count the user saw (409 when
  the item changed since) and MOVES the item into <data>/baihe_trash/<id>/
  with one same-volume rename plus a manifest.json (nothing is copied and
  nothing is deleted, so no space is freed). The Windows Recycle Bin is not
  used: the installed API runs as a service account whose bin the owner
  can't see. It refuses when the data folder is a drive root, the user's home
  or a folder holding it or a known shell folder, for a folder it couldn't
  read fully (including paths Windows can't open here) or that holds a link,
  when the item is on another volume than the Trash folder, and for top-level
  entries Baihe doesn't create (shown but protected). Inspect, re-resolve
  and rename run under the library's exclusive hold.
- Restore puts a trashed item back at its original relative path when the
  parent is still there, the destination is free and passes the same checks
  as clear.
- Purge and empty (services/disk_usage_trash_service.py, with the Trash list
  and Restore) are the ONLY permanent deletes in the feature, besides deleting
  the previous library a restore or reset kept beside the library folder
  (services/previous_library_service.py, typed DELETE); those folders show up
  here protected, with that reason.
- Unused voice clips: the files in a title's voice_refs/ that no speaker points
  to, offered by opaque id (no name or path in a response) and moved to Trash
  through the same rename as clear, each re-checked under the library hold.
- Move works only for a folder with an existing, safe way to repoint it
  without a restart: the automatic-backup folder (auto_backup_service
  .set_settings moves the copies and saves the setting together). Everything
  else reports why it can't be moved.

Where things live: path confinement and the protection rules in
disk_usage_guard.py; the walk and the public record of an item in
disk_usage_measure.py; the library hold in disk_usage_hold.py; the Trash
folder's layout and the rename into it in disk_usage_trash_store.py. This module
holds the scan, clear and move that sit on top of them.
"""

import os
import shutil
import stat
import threading
import time

from services import auto_backup_service as abs_
from services import disk_usage_links
from services import disk_usage_title_sizes
from services.disk_usage_guard import (TRASH_DIRNAME, _MAX_REL_LEN, _Ctx, _home_dirs,
                                       _is_link_stat, _program_dir, _resolve, _root, _within,
                                       split_rel)
from services.disk_usage_hold import _changing, _exclusive, _op_lock, _require_confirm, busy_reason
from services.disk_usage_measure import (MAX_ENTRIES, MAX_ITEMS, _TIME_CHECK_EVERY, _Budget,
                                         _describe, _measure)
from services.disk_usage_trash_store import CHANGED, _move_to_trash, _trash_totals
from lib.errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError, UnsupportedOperationError)

LINK_INSIDE = ("This folder contains a link or junction, so it can't be cleared whole. Open it "
               "and clear items inside instead.")
SCAN_BUSY = "Another scan is already running. Wait for it to finish, then try again."
_scan_lock = threading.Lock()


def scan(path="") -> dict:
    """The children of one folder (default: the data folder), biggest first.
    One bounded walk; `partial` says a limit cut it short, in which case sizes
    are lower bounds. One scan runs at a time (ConflictError otherwise)."""
    if not _scan_lock.acquire(blocking=False):
        raise ConflictError(SCAN_BUSY)
    try:
        return _scan(path)
    finally:
        _scan_lock.release()


def _scan(path) -> dict:
    parts = split_rel(path)
    folder = _resolve(parts)
    if not os.path.isdir(folder):
        raise InvalidInputError("That item is a file, not a folder.")
    budget = _Budget()
    ctx = _Ctx()
    ctx.linked = disk_usage_links.LinkedSizes(
        ctx.root, [ctx.program, *_home_dirs()], _within, _measure)
    trash_info, trash_measured = _trash_totals()
    items = []
    entries = []
    overflow = 0
    list_cut = None
    try:
        with os.scandir(folder) as it:
            # Cap before sorting: a folder of millions of names is never held
            # whole. Names past the cap are only counted, up to the entry
            # and time limits, without spending the walk's own budget.
            for entry in it:
                if len(entries) < MAX_ITEMS:
                    entries.append(entry)
                    continue
                overflow += 1
                if overflow >= MAX_ENTRIES:
                    list_cut = "entries"
                    break
                if overflow % _TIME_CHECK_EVERY == 0 and time.monotonic() > budget.deadline:
                    list_cut = "time"
                    break
    except OSError:
        raise ServiceError("That folder could not be read.") from None
    entries.sort(key=lambda e: e.name)
    listed = 0
    for entry in entries:
        if budget.hit:
            break
        listed += 1
        child_parts = tuple(parts) + (entry.name,)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        budget.tick()
        # The Trash folder was just walked for the summary; don't walk it twice.
        known = (trash_measured if not parts and entry.name == TRASH_DIRNAME
                 and not _is_link_stat(st) else None)
        items.append(_describe(child_parts, entry.path, st, budget, ctx, known))
    disk_usage_title_sizes.annotate(parts, folder, items)
    not_shown = len(entries) - listed + overflow
    total = sum(i["size_bytes"] for i in items)
    for i in items:
        i["percent_of_parent"] = round(100.0 * i["size_bytes"] / total, 1) if total else 0.0
    items.sort(key=lambda i: (-i["size_bytes"], i["name"].lower()))
    try:
        usage = shutil.disk_usage(ctx.root)
        disk = {"disk_total_bytes": usage.total, "disk_free_bytes": usage.free}
    except OSError:
        disk = {"disk_total_bytes": None, "disk_free_bytes": None}
    reason = budget.hit or list_cut or ("items" if not_shown else None)
    return {
        "path": "/".join(parts),
        "parent": "/".join(parts[:-1]) if parts else None,
        "total_bytes": total,
        "file_count": sum(i["file_count"] for i in items),
        "linked_bytes": sum(i["linked_bytes"] or 0 for i in items),
        "linked_files": sum(i["linked_files"] or 0 for i in items),
        "linked_complete": all(i["linked_complete"] is not False for i in items),
        "items": items,
        "not_shown": not_shown,
        "partial": bool(reason),
        "partial_reason": reason,
        "scanned_entries": budget.entries,
        "busy_reason": busy_reason(),
        "trash": trash_info,
        **disk,
    }


def _inspect_for_change(path, op: str):
    """Shared by clear and move, under the exclusive hold: resolve the path,
    describe it afresh with the full walk budget, refuse a protected or
    partly-measured item. Returns (parts, real, item)."""
    parts = split_rel(path)
    if not parts:
        raise InvalidInputError("The data folder itself can't be cleared or moved.")
    real = _resolve(parts)
    try:
        st = os.lstat(real)
    except FileNotFoundError:
        raise NotFoundError("That item is no longer there.") from None
    except OSError:
        raise ConflictError("This item changed since you looked. Rescan and check again.",
                            details={"reason": "changed"}) from None
    budget = _Budget()
    item = _describe(tuple(parts), real, st, budget, _Ctx())
    if item["protected"]:
        raise InvalidInputError(item["protected_reason"] or "That item is protected.")
    if budget.hit or not item["complete"]:
        raise ConflictError(f"This is too large to check completely before {op}. Open it and "
                            "work on the pieces inside instead.")
    return parts, real, item


def clear(path, confirm=False, expected_size_bytes=None, expected_file_count=None,
          confirm_irreplaceable=False) -> dict:
    """Moves one item into the Trash folder (nothing is deleted or freed).
    `expected_*` are the size and file count the person saw: the item is
    measured again and a difference is a 409. Anything that can't be recreated
    (a title's media, backups) also needs confirm_irreplaceable."""
    _require_confirm(confirm)
    if expected_size_bytes is None or expected_file_count is None:
        raise InvalidInputError("Send the size and file count you were shown.")
    with _changing("Disk usage clear"):
        parts, real, item = _inspect_for_change(path, "clearing")
        if item["contains_link"]:
            raise InvalidInputError(LINK_INSIDE)
        if (item["size_bytes"] != expected_size_bytes
                or item["file_count"] != expected_file_count):
            raise ConflictError(CHANGED, details={"reason": "changed",
                                                  "size_bytes": item["size_bytes"],
                                                  "file_count": item["file_count"]})
        if item["irreplaceable"] and confirm_irreplaceable is not True:
            raise ConflictError("This can't be recreated from inside Baihe. Confirm that "
                                "too to continue.",
                                details={"reason": "needs_irreplaceable_confirm"})
        trash_id = _move_to_trash(parts, real, item)
        return {"moved_bytes": item["size_bytes"], "file_count": item["file_count"],
                "kind": item["kind"], "name": item["name"], "trash_id": trash_id}


# --------------------------------------------------------------------------
# Move
# --------------------------------------------------------------------------

def _check_destination(destination, item_real: str) -> str:
    if (not isinstance(destination, str) or not destination.strip() or "\x00" in destination
            or len(destination) > _MAX_REL_LEN or not os.path.isabs(destination.strip())):
        raise InvalidInputError("Pick a full folder path, starting with the drive letter.")
    dest = os.path.normpath(destination.strip())
    try:
        st = os.lstat(dest)
    except OSError:
        raise InvalidInputError("That destination folder doesn't exist. Create it first.") from None
    if _is_link_stat(st) or not stat.S_ISDIR(st.st_mode):
        raise InvalidInputError("Pick a real folder (not a link or a file) as the destination.")
    real = os.path.realpath(dest)
    root = _root()
    if _within(real, item_real) or _within(item_real, real):
        raise InvalidInputError("The destination can't be inside the item being moved, or hold it.")
    if _within(real, _program_dir()) or _within(_program_dir(), real):
        raise InvalidInputError("Pick a folder outside the program folder.")
    if _within(real, root) or _within(root, real):
        raise InvalidInputError("Pick a folder outside Baihe's data folder.")
    return dest


def move(path, destination, confirm=False) -> dict:
    """Moves a movable item to another folder and repoints Baihe at it. Only
    the automatic-backup folder qualifies (see module docstring); its copies
    move and the setting is saved together by auto_backup_service, and on any
    failure the copies stay where they were and the setting is unchanged."""
    _require_confirm(confirm)
    if not _op_lock.acquire(blocking=False):
        raise ConflictError("Another clear or move is in progress. Try again in a moment.")
    try:
        # set_settings takes the maintenance hold itself and that refuses
        # while an exclusive hold exists, so the exclusive hold covers the
        # inspection and destination checks and is released before it; the
        # settings call re-checks running backups and takes its own hold.
        with _exclusive("Disk usage move"):
            parts, real, item = _inspect_for_change(path, "moving")
            if not item["movable"]["supported"]:
                raise UnsupportedOperationError(item["movable"]["reason"])
            dest = _check_destination(destination, real)
        abs_.set_settings(folder=dest)
        remaining = 0
        try:
            remaining = _measure(real, tuple(parts), _Budget()).size if os.path.isdir(real) else 0
        except OSError:
            pass
        return {"moved_bytes": max(item["size_bytes"] - remaining, 0),
                "remaining_bytes": remaining, "what": item["movable"]["what"],
                "name": item["name"]}
    finally:
        _op_lock.release()
