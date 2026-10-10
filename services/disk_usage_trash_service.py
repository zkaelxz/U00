"""
services/disk_usage_trash_service.py -- the Trash list, Restore and the two
permanent deletes (purge one entry, empty the Trash) of the data-folder disk
usage feature. UI-free; raises services/service_errors.

The rename into Trash, the manifests and the entry measuring stay in
services/disk_usage_service.py, which the scan and clear also use; this module
only reads them and holds the library for the change. Purge and empty are the
ONLY permanent deletes in the feature: they touch only <data>/baihe_trash/<id>
(id matched by a strict pattern, real path checked, no link at the root or at
the entry), need the typed word DELETE, and walk the entry without following
links (_remove_tree).
"""

import os
import stat

from services.disk_usage_service import (TRASH_DELETE_FAILED, TRASH_MANIFEST, TRASH_PAYLOAD,
                                         TRASH_WORD, _Budget, _changing, _Ctx, _drop_entry_shell,
                                         _entry_path, _is_link_stat, _measure_payload, _norm,
                                         _protection, _read_manifest, _rename_no_overwrite,
                                         _require_confirm, _resolve, _same_volume, _too_deep,
                                         _trash_entries, _trash_root, _within, busy_reason)
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)


def _restore_target(manifest: dict, meas: dict, ctx: _Ctx):
    """(destination path, None) when the payload may go back where it came
    from, else (None, plain reason). The same checks clear makes, on the
    original place: every folder on the way re-resolved with no links, the
    place free, and not protected."""
    parts = manifest["parts"]
    if meas["kind"] is None:
        return None, "The trashed item is missing, so it can't be restored."
    if meas["kind"] != manifest["kind"]:
        return None, "The trashed item doesn't match its record, so it can't be restored."
    if meas["unreadable"] or meas["has_link"]:
        return None, "It holds a link or couldn't be read fully, so it can't be put back from here."
    try:
        parent = _resolve(parts[:-1])
    except NotFoundError:
        return None, "The folder it came from is gone. Create it again, or delete the item from Trash."
    except InvalidInputError:
        return None, "The folder it came from can't be used (it is, or sits behind, a link)."
    if not os.path.isdir(parent):
        return None, "The folder it came from is no longer a folder."
    dest = os.path.join(parent, parts[-1])
    if os.path.lexists(dest):
        return None, "Something with the same name is already in its old place."
    protected, reason = _protection(tuple(parts), dest, meas["flagged"], ctx)
    if protected:
        return None, reason or "Its old place is protected."
    return dest, None


def trash_list() -> dict:
    """Every entry in Trash with what it was, its measured size and whether
    Restore would work now. A read-only walk with its own budget; it does not
    take the scan's lock, so a running scan never hides the list. An entry the
    walk budget didn't reach has no size (None), never the manifest's."""
    trash = _trash_root()
    budget = _Budget()
    ctx = _Ctx()
    items = []
    total = 0
    entries = _trash_entries(trash) if trash else []
    for trash_id, entry in entries:
        manifest = _read_manifest(entry)
        size = files = None
        restorable = False
        kind = (manifest or {}).get("kind")
        if not budget.hit:
            meas = _measure_payload(entry, budget, manifest["parts"] if manifest else ())
            if not budget.hit:
                size, files = meas["size"], meas["files"]
                restorable = bool(manifest) and _restore_target(manifest, meas, ctx)[1] is None
                kind = kind or meas["kind"]
        total += size or 0
        items.append({
            "id": trash_id,
            "original_path_relative": "/".join(manifest["parts"]) if manifest else None,
            "kind": kind, "size_bytes": size, "file_count": files,
            "trashed_at": manifest["trashed_at"] if manifest else None,
            "restorable": restorable,
        })
    return {"items": items, "size_bytes": total, "item_count": len(items),
            "partial": bool(budget.hit), "busy_reason": busy_reason()}


def trash_restore(trash_id, confirm=False) -> dict:
    """Puts a Trash entry back at its original relative path with one atomic
    rename. 409 with a plain reason when the original folder is gone, the
    place is taken, it would be protected, or a job is running."""
    _require_confirm(confirm)
    with _changing("Disk usage restore"):
        trash = _trash_root()
        if trash is None:
            raise NotFoundError("That item is no longer in Trash.")
        entry = _entry_path(trash_id, trash)
        manifest = _read_manifest(entry)
        if manifest is None:
            raise ConflictError("This Trash item's record is missing or damaged, so it can't "
                                "be restored. You can still delete it.",
                                details={"reason": "cannot_restore"})
        budget = _Budget()
        meas = _measure_payload(entry, budget, manifest["parts"])
        if budget.hit:
            raise ConflictError("This is too large to check completely before restoring.",
                                details={"reason": "cannot_restore"})
        dest, reason = _restore_target(manifest, meas, _Ctx())
        if reason:
            raise ConflictError(reason, details={"reason": "cannot_restore"})
        payload = os.path.join(entry, TRASH_PAYLOAD)
        if (not _same_volume(payload, os.path.dirname(dest))
                or _too_deep(meas["deepest"], payload, dest)):
            raise ConflictError("It can't be put back there from here (another drive, or the "
                                "path would be too long).", details={"reason": "cannot_restore"})
        try:
            _rename_no_overwrite(payload, dest, manifest["kind"])
        except OSError:
            raise ConflictError("Couldn't put it back (it may be in use), so nothing was "
                                "changed.", details={"reason": "cannot_restore"}) from None
        _drop_entry_shell(entry)
        return {"name": manifest["parts"][-1], "kind": manifest["kind"],
                "size_bytes": meas["size"], "file_count": meas["files"]}


def _require_word(text) -> None:
    if not isinstance(text, str) or text != TRASH_WORD:
        raise InvalidInputError(f"Type {TRASH_WORD} in capital letters to confirm.")


def _plain_dir(path: str) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISDIR(st.st_mode) and not _is_link_stat(st)
            and _norm(os.path.realpath(path)) == _norm(path))


def _clear_readonly(path: str) -> None:
    """Drops the read-only bit (Windows refuses to delete such a file or
    folder) of something just seen as an ordinary file or folder, never of a link."""
    st = os.lstat(path)
    if _is_link_stat(st):
        raise OSError("link")
    mode = stat.S_IREAD | stat.S_IWRITE | (stat.S_IEXEC if stat.S_ISDIR(st.st_mode) else 0)
    if os.chmod in os.supports_follow_symlinks:
        os.chmod(path, mode, follow_symlinks=False)
    elif stat.S_ISREG(st.st_mode):
        os.chmod(path, mode)
    # else: without no-follow chmod only a plain file is changed, never a
    # folder that could have been swapped for a link since the lstat.


_FD_WALK_MAX_DEPTH = 200


_FD_WALK = (os.name == "posix" and hasattr(os, "O_DIRECTORY") and hasattr(os, "O_NOFOLLOW")
            and all(f in os.supports_dir_fd for f in (os.open, os.stat, os.unlink, os.rmdir)))


def _fd_walk_supported() -> bool:
    return _FD_WALK


def _remove_children_fd(dirfd: int, depth: int) -> None:
    """Deletes everything inside the folder open as `dirfd`. Every step is
    relative to an open folder, so a folder swapped for a link after it was
    listed can't redirect a delete: a link is unlinked itself and a folder is
    only entered by opening it without following links and checking it is the
    one that was seen."""
    if depth > _FD_WALK_MAX_DEPTH:
        raise OSError("too deep")
    try:
        names = os.listdir(dirfd)
    except PermissionError:
        os.fchmod(dirfd, stat.S_IRWXU)
        names = os.listdir(dirfd)
    here = os.fstat(dirfd).st_dev
    for name in names:
        if name in ("", ".", "..") or "/" in name:
            raise OSError("unexpected name")
        st = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
        if stat.S_ISDIR(st.st_mode):
            if st.st_dev != here:
                raise OSError("folder on another volume")
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dirfd)
            try:
                seen = os.fstat(fd)
                if (seen.st_dev, seen.st_ino) != (st.st_dev, st.st_ino):
                    raise OSError("folder changed")
                _remove_children_fd(fd, depth + 1)
            finally:
                os.close(fd)
            try:
                os.rmdir(name, dir_fd=dirfd)
            except PermissionError:
                os.fchmod(dirfd, stat.S_IRWXU)
                os.rmdir(name, dir_fd=dirfd)
        else:
            try:
                os.unlink(name, dir_fd=dirfd)
            except PermissionError:
                os.fchmod(dirfd, stat.S_IRWXU)
                os.unlink(name, dir_fd=dirfd)


def _remove_tree_fd(top: str) -> None:
    top = os.path.abspath(top)
    parent_fd = os.open(os.path.dirname(top), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        name = os.path.basename(top)
        st = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(st.st_mode):
            raise OSError("not an ordinary folder")
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            seen = os.fstat(fd)
            if (seen.st_dev, seen.st_ino) != (st.st_dev, st.st_ino):
                raise OSError("folder changed")
            _remove_children_fd(fd, 0)
        finally:
            os.close(fd)
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _remove_tree(top: str) -> None:
    """PERMANENT DELETE: removes `top` and everything in it, never following a
    link. On POSIX the walk is relative to open folders (_remove_tree_fd). On
    Windows it goes path by path with a check before each step, so a folder
    swapped for a junction between the check and the delete is a residual
    window; closing it needs write access to the Trash folder, i.e. the
    owner's own account or an equivalent one. Links are unlinked themselves (a directory link or junction with
    rmdir, never entered); folders are listed only after being seen as
    ordinary folders with no link on the way; files lose a read-only bit when
    Windows needs that; folders are removed bottom-up. Raises OSError at the
    first thing that can't be removed (what is left stays)."""
    if _fd_walk_supported():
        _remove_tree_fd(top)
        return
    top = os.path.abspath(top)
    folders = []
    stack = [top]
    while stack:
        cur = stack.pop()
        if not _plain_dir(cur) or not _within(cur, top):
            raise OSError("not an ordinary folder")
        folders.append(cur)
        with os.scandir(cur) as it:
            children = [(e.path, e.name) for e in it]
        for path, name in children:
            if os.path.dirname(path) != cur or name in ("", ".", ".."):
                raise OSError("unexpected name")
            st = os.lstat(path)
            if _is_link_stat(st):
                try:
                    os.unlink(path)
                except OSError:
                    os.rmdir(path)
            elif stat.S_ISDIR(st.st_mode):
                stack.append(path)
            else:
                try:
                    os.unlink(path)
                except PermissionError:
                    _clear_readonly(path)
                    os.unlink(path)
    for folder in reversed(folders):
        try:
            os.rmdir(folder)
        except PermissionError:
            _clear_readonly(folder)
            os.rmdir(folder)


def _delete_entry(entry: str) -> None:
    """PERMANENT DELETE of one validated Trash entry. The manifest goes first,
    so an entry that fails halfway can't be restored from a partial payload."""
    manifest = os.path.join(entry, TRASH_MANIFEST)
    try:
        os.unlink(manifest)
    except FileNotFoundError:
        pass
    except PermissionError:
        _clear_readonly(manifest)
        os.unlink(manifest)
    _remove_tree(entry)
    if os.path.lexists(entry):
        raise OSError("still there")


def trash_purge(trash_id, confirm_text=None, expected_size_bytes=None) -> dict:
    """PERMANENT DELETE of one Trash entry. Needs the typed word and the size
    the person saw (409 when it changed); frees the space."""
    _require_word(confirm_text)
    unknown = expected_size_bytes is None       # the list showed "size unknown"
    if not unknown and (not isinstance(expected_size_bytes, int)
                        or isinstance(expected_size_bytes, bool)):
        raise InvalidInputError("Send the size you were shown.")
    with _changing("Disk usage delete from Trash"):
        trash = _trash_root()
        if trash is None:
            raise NotFoundError("That item is no longer in Trash.")
        entry = _entry_path(trash_id, trash)
        budget = _Budget()
        meas = _measure_payload(entry, budget)
        if budget.hit and not unknown:
            raise ConflictError("This is too large to check completely before deleting.",
                                details={"reason": "changed"})
        if not unknown and meas["size"] != expected_size_bytes:
            raise ConflictError("This Trash item changed since you looked. Reload the list "
                                "and check again.",
                                details={"reason": "changed", "size_bytes": meas["size"],
                                         "file_count": meas["files"]})
        try:
            _delete_entry(entry)
        except OSError:
            raise ServiceError(TRASH_DELETE_FAILED) from None
        return {"freed_bytes": meas["size"], "file_count": meas["files"]}


def trash_empty(confirm_text=None, expected_item_count=None, expected_size_bytes=None) -> dict:
    """PERMANENT DELETE of every entry in Trash (typed word needed). The count
    and total size the person saw must still match (409 `changed`), and a
    Trash too large to measure completely can't be emptied this way. Entries
    that can't be removed stay; `failed` counts them. Anything in the Trash
    folder that isn't one of Baihe's entries is left alone."""
    _require_word(confirm_text)
    for v in (expected_item_count, expected_size_bytes):
        if not isinstance(v, int) or isinstance(v, bool) or v < 0:
            raise InvalidInputError("Send the item count and size you were shown.")
    with _changing("Disk usage empty Trash"):
        trash = _trash_root()
        entries = _trash_entries(trash) if trash else []
        budget = _Budget()
        sizes = {}
        for trash_id, entry in entries:
            sizes[trash_id] = _measure_payload(entry, budget)["size"]
            if budget.hit:
                raise ConflictError("The Trash is too large to check completely. Delete its "
                                    "items one at a time.", details={"reason": "changed"})
        if len(entries) != expected_item_count or sum(sizes.values()) != expected_size_bytes:
            raise ConflictError("The Trash changed since you looked. Reload the list and check "
                                "again.", details={"reason": "changed", "item_count": len(entries),
                                                   "size_bytes": sum(sizes.values())})
        freed = removed = failed = 0
        for trash_id, _path in entries:
            try:
                entry = _entry_path(trash_id, trash)
                _delete_entry(entry)
            except (ServiceError, InvalidInputError, NotFoundError, OSError):
                failed += 1
                continue
            freed += sizes[trash_id]
            removed += 1
        return {"freed_bytes": freed, "removed": removed, "failed": failed}
