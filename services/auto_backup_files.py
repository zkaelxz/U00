"""
services/auto_backup_files.py -- the snapshot files themselves: building a
copy's manifest, checking a finished copy, putting it in place, reading the
database back out of one, and moving copies to a new folder.

A new copy is written by library_admin_service.write_backup_zip into a hidden
partial file, validated (zip checks + SQLite quick_check + manifest), flushed
to disk, and only then linked into place under a name no file has (never over
an existing file); the job in auto_backup_service drives those steps. No
FastAPI import.
"""

import contextlib
import datetime
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile

import storage
from db import fsync_dir as _fsync_dir
from lib.link_new import link_new
from services import library_backup_restore_service as lbr
from services import library_restore_service as lrs
from services.auto_backup_copies import (MANIFEST_FORMAT, _BAD_SNAPSHOT, _MANAGED, _classify,
                                         _identity_info, _read_copy, _read_manifest, _recorded)
from services.auto_backup_settings import _iso, _now, folder_path
from services.service_errors import ConflictError, InvalidInputError, ServiceError

log = logging.getLogger(__name__)

# What a single-drama restore extracts is capped per member (the whole-zip
# upload caps don't apply to the app's own snapshot; see _verify_snapshot).
_MAX_MEMBER_BYTES = lrs.MAX_RESTORE_MEMBER_BYTES


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


def _copy_name(at: datetime.datetime) -> str:
    return f"baihe_snapshot-{at.astimezone(datetime.timezone.utc):%Y%m%d-%H%M%S}.zip"


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
    lbr.validate_backup_file(path, check_disk=False, check_limits=False)
    with zipfile.ZipFile(path) as zf, storage.job_workdir() as tmp:
        manifest = _read_manifest(zf)
        dest = extract_db(zf, tmp)
        with contextlib.closing(sqlite3.connect(ro_uri(dest), uri=True)) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM dramas ORDER BY id")]
    if ids != [d.get("id") for d in manifest["dramas"]]:
        raise InvalidInputError(_BAD_SNAPSHOT)


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
                link_new(tmp, path)
                return path
            except FileExistsError:
                pass
        at += datetime.timedelta(seconds=1)
    raise OSError("no free backup copy name")
