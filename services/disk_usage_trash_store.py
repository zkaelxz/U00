"""
services/disk_usage_trash_store.py -- the Trash folder's layout for the
data-folder disk usage feature: the one atomic rename into <data>/baihe_trash/
<id>/payload, the manifest beside it, reading and measuring entries, and the
renames that never overwrite. The Trash list, Restore and the permanent deletes
(services/disk_usage_trash_service.py) and the unused-clip move
(services/disk_usage_clips_service.py) build on it; the only deletion here is
_drop_entry_shell, for an entry this call just created.
"""

import contextlib
import datetime
import errno
import json
import os
import re
import secrets
import stat

from services.disk_usage_guard import (TRASH_DIRNAME, _is_link_stat, _norm, _resolve, _root,
                                       split_rel)
from services.disk_usage_hold import BUSY, _busy_under_hold
from services.disk_usage_measure import MAX_ITEMS, MAX_PATH, _Budget, _Measured, _measure
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

TRASH_MANIFEST = "manifest.json"
TRASH_PAYLOAD = "payload"           # the moved item; its own name is only in the manifest
_TRASH_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
TRASH_FAILED = ("Couldn't move it to Trash (it may be in use, or on another drive), so nothing "
                "was moved.")
TRASH_DELETE_FAILED = ("Couldn't delete everything in that Trash item (something may be in "
                       "use). What is left is still in Trash.")
CHANGED = "This item changed since you looked. Rescan and check again."


def _rename(src: str, dst: str) -> None:
    """The one atomic same-volume move. os.rename, not os.replace: on Windows
    it refuses to overwrite an existing destination. Never copies, so a path
    on another volume fails instead of being copied and deleted."""
    os.rename(src, dst)


def _same_volume(a: str, b: str) -> bool:
    try:
        return os.lstat(a).st_dev == os.lstat(b).st_dev
    except OSError:
        return False


def _too_deep(deepest: int, old_base: str, new_base: str) -> bool:
    """True when the longest path under `old_base` would reach MAX_PATH after
    the item is renamed to `new_base` (Windows opens nothing that long)."""
    return deepest - len(old_base) + len(new_base) >= MAX_PATH


def _move_to_trash(parts, real: str, item: dict, still_ok=None) -> str:
    """The rename into Trash, under the caller's exclusive hold. `still_ok`
    is asked right before the rename and again right after it: a False the
    second time puts the item back, so a database change that doesn't take
    the library hold (picking a clip for a speaker) can't be raced for more
    than the instant between the two checks. Returns the trash id."""
    # Last look before the rename: still the same plain item at the same
    # place, no link swapped in, and nothing started meanwhile.
    if _busy_under_hold():
        raise ConflictError(BUSY)
    again = _resolve(parts)
    try:
        swapped = _is_link_stat(os.lstat(again))
    except OSError:
        swapped = True
    if _norm(again) != _norm(real) or swapped:
        raise ConflictError(CHANGED, details={"reason": "changed"})
    trash = _trash_root(create=True)
    payload = os.path.join(trash, _new_trash_id(), TRASH_PAYLOAD)     # ids are fixed length
    if (not _same_volume(again, trash)
            or _too_deep(item["_deepest_path"], again, payload)):
        raise ServiceError(TRASH_FAILED)
    entry = None
    for _attempt in range(5):
        candidate = os.path.join(trash, _new_trash_id())
        try:
            os.mkdir(candidate)
        except FileExistsError:
            continue                    # someone else's entry: never touched
        except OSError:
            raise ServiceError(TRASH_FAILED) from None
        entry = candidate
        break
    if entry is None:
        raise ServiceError(TRASH_FAILED)
    trash_id = os.path.basename(entry)
    payload = os.path.join(entry, TRASH_PAYLOAD)
    try:
        if still_ok is not None and not still_ok():
            raise ConflictError(CHANGED, details={"reason": "changed"})
        _write_manifest(entry, parts, item)
        _rename(again, payload)
    except OSError:
        _drop_entry_shell(entry)        # this call created it
        raise ServiceError(TRASH_FAILED) from None
    except ConflictError:
        _drop_entry_shell(entry)
        raise
    if still_ok is not None and not still_ok():
        try:
            _rename_no_overwrite(payload, again, item["kind"])
        except FileExistsError:
            pass                        # something is already back at the place: leave it be
        except OSError:
            raise ServiceError("The file was needed again as it was moved, but couldn't be put "
                               "back. Restore it from the Trash list.") from None
        else:
            _drop_entry_shell(entry)
        raise ConflictError(CHANGED, details={"reason": "changed"})
    return trash_id


# --------------------------------------------------------------------------
# Trash: list, restore, permanent delete
# --------------------------------------------------------------------------

def _trash_root(create: bool = False):
    """<data>/baihe_trash when it is an ordinary folder, None when it doesn't
    exist (and `create` is false). A link, a file or an odd real path there
    is a ServiceError: nothing is read or changed through it."""
    path = os.path.join(_root(), TRASH_DIRNAME)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(path)
            st = os.lstat(path)
        except OSError:
            raise ServiceError(TRASH_FAILED) from None
    except OSError:
        raise ServiceError("The Trash folder couldn't be read.") from None
    if (_is_link_stat(st) or not stat.S_ISDIR(st.st_mode)
            or _norm(os.path.realpath(path)) != _norm(path)):
        raise ServiceError("The Trash folder isn't an ordinary folder, so nothing was changed.")
    return path


def _new_trash_id() -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(4)}"


def _entry_path(trash_id, trash: str) -> str:
    """The folder of one Trash entry. The id must match the strict pattern (so
    no separator, '..' or drive letter can get through), the entry must be a
    real folder directly inside the Trash folder and not a link."""
    if not isinstance(trash_id, str) or not _TRASH_ID_RE.fullmatch(trash_id):
        raise InvalidInputError("That isn't an item in Trash.")
    path = os.path.join(trash, trash_id)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise NotFoundError("That item is no longer in Trash.") from None
    except OSError:
        raise ServiceError("That Trash item couldn't be read.") from None
    if (_is_link_stat(st) or not stat.S_ISDIR(st.st_mode)
            or _norm(os.path.realpath(path)) != _norm(path)
            or _norm(os.path.dirname(path)) != _norm(trash)):
        raise InvalidInputError("That Trash item isn't an ordinary folder, so it isn't touched "
                                "here.")
    return path


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _write_manifest(entry: str, parts, item: dict) -> None:
    data = {"version": 1, "original_path": "/".join(parts), "kind": item["kind"],
            "size_bytes": item["size_bytes"], "file_count": item["file_count"],
            "trashed_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with open(os.path.join(entry, TRASH_MANIFEST), "x", encoding="utf-8") as fh:
        json.dump(data, fh)


def _read_manifest(entry: str):
    """The checked manifest of one entry (original parts, kind, size, count,
    time) or None when it is missing or not valid: what it says is only used
    after every check clear makes again."""
    path = os.path.join(entry, TRASH_MANIFEST)
    try:
        st = os.lstat(path)
        if _is_link_stat(st) or not stat.S_ISREG(st.st_mode) or st.st_size > 16384:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        parts = split_rel(data.get("original_path"))
    except InvalidInputError:
        return None
    size, files, kind = _count(data.get("size_bytes")), _count(data.get("file_count")), data.get("kind")
    if not parts or kind not in ("file", "folder") or size is None or files is None:
        return None
    at = data.get("trashed_at")
    return {"parts": parts, "kind": kind, "size_bytes": size, "file_count": files,
            "trashed_at": at if isinstance(at, str) else None}


def _measure_payload(entry: str, budget: _Budget, parts=()) -> dict:
    """What is in an entry's payload: kind (None when missing), size, file
    count and what _measure reports. A link is its own file and never entered.
    `parts` is the item's ORIGINAL relative path (from the manifest): files are
    judged as they would be in their old place, so a trashed backups folder
    that holds .db copies is not taken for the live database."""
    payload = os.path.join(entry, TRASH_PAYLOAD)
    out = {"kind": None, "size": 0, "files": 0, "flagged": False, "unreadable": False,
           "has_link": False, "deepest": len(payload)}
    try:
        st = os.lstat(payload)
    except FileNotFoundError:
        return out
    except OSError:
        out["unreadable"] = True
        return out
    if stat.S_ISDIR(st.st_mode) and not _is_link_stat(st):
        m = _measure(payload, tuple(parts), budget)
        out.update(kind="folder", size=m.size, files=m.files, flagged=m.flagged,
                   unreadable=m.unreadable, has_link=m.has_link, deepest=m.deepest)
    else:
        out.update(kind="file", size=st.st_size, files=1, has_link=_is_link_stat(st))
    return out


def _trash_entries(trash: str):
    """(id, entry path) of the ordinary folders directly in the Trash folder
    whose names are Baihe's ids, newest first. Each one is checked with
    _entry_path (no link, real path where it says), so nothing is read through
    a link. Anything else there is ignored."""
    try:
        with os.scandir(trash) as it:
            names = [e.name for e in it if _TRASH_ID_RE.fullmatch(e.name)]
    except OSError:
        raise ServiceError("The Trash folder couldn't be read.") from None
    names.sort(reverse=True)
    found = []
    for name in names:
        try:
            found.append((name, _entry_path(name, trash)))
        except (ServiceError, InvalidInputError, NotFoundError):
            continue
        if len(found) >= MAX_ITEMS:
            break
    return found


def _trash_totals():
    """(summary, measured): {size_bytes, item_count, partial} of the Trash
    folder and a _Measured of the same walk (payload sizes and file counts)
    that the scan reuses for the baihe_trash item."""
    m = _Measured()
    try:
        trash = _trash_root()
        if trash is None:
            return {"size_bytes": 0, "item_count": 0, "partial": False}, m
        budget = _Budget()
        entries = _trash_entries(trash)
        for _id, entry in entries:
            if budget.hit:
                break
            meas = _measure_payload(entry, budget)
            m.size += meas["size"]
            m.files += meas["files"]
            m.has_link = m.has_link or meas["has_link"]
            m.unreadable = m.unreadable or meas["unreadable"]
        m.unreadable = m.unreadable or bool(budget.hit)
        return {"size_bytes": m.size, "item_count": len(entries), "partial": bool(budget.hit)}, m
    except ServiceError:
        m.unreadable = True
        return {"size_bytes": 0, "item_count": 0, "partial": True}, m


def _drop_entry_shell(entry: str) -> None:
    """PERMANENT DELETE (own files only): removes the manifest.json and the
    then-empty folder of an entry that holds no payload. Never recursive; an
    entry that still holds anything else is left alone."""
    try:
        os.remove(os.path.join(entry, TRASH_MANIFEST))
    except OSError:
        pass
    try:
        os.rmdir(entry)
    except OSError:
        pass


def _rename_no_overwrite(src: str, dst: str, kind: str) -> None:
    """Moves src to dst and fails (OSError) if dst exists or appears meanwhile.
    Windows' rename already refuses. On POSIX rename would replace a file or an
    empty folder, so a file is hard-linked (fails when dst exists) and then
    unlinked from the source, and a folder goes onto a freshly made empty
    folder of ours (a rename onto a non-empty one fails). Where hard links are
    not available the last look is a plain existence check right before the
    rename: a residual window remains there that needs write access to the
    old place."""
    if os.name == "nt":
        _rename(src, dst)
        return
    if kind == "folder":
        os.mkdir(dst)
        try:
            _rename(src, dst)
        except OSError:
            with contextlib.suppress(OSError):
                os.rmdir(dst)
            raise
        return
    try:
        os.link(src, dst, follow_symlinks=False)
    except FileExistsError:
        raise
    except (OSError, NotImplementedError) as exc:
        if isinstance(exc, OSError) and exc.errno not in (
                errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EMLINK, errno.EXDEV):
            raise
        if os.path.lexists(dst):
            raise FileExistsError(dst) from None
        _rename(src, dst)
        return
    os.unlink(src)
