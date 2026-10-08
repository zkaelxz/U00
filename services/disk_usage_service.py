"""
services/disk_usage_service.py -- "what is taking up space" for the app's own
data folder, plus moving items to Baihe's Trash folder (and restoring or
permanently deleting them from it) and moving the few parts Baihe can be told
a new location for. UI-free; raises services/service_errors.

Scope is the data folder only (the parent of db.LIBRARY_DIR, which is
portable.data_dir() in the running app). Everything is addressed by a path
RELATIVE to it, and nothing returned names an absolute path or the data
folder itself.

Scan: the immediate children of one folder with recursive size and file
count, measured by a bounded walk (MAX_ENTRIES entries or MAX_SECONDS, then
`partial`). Symlinks and Windows junctions are counted as the link itself and
never entered. Nothing is cached: every call walks the disk again.

Clear and move are server-enforced, not only hidden in the UI:
- protected: the data folder itself, the live SQLite files (and -wal, -shm,
  -journal), .env and key/secret files, the INSTALLED/PORTABLE markers, the
  saved site sign-ins, installer files, the program folder, the Trash folder,
  and any folder that holds or contains one of those. Both refuse while any
  background job, restore or maintenance run holds the library, or while
  another process holds the GPU lock (all a CLI run leaves; see
  _busy_under_hold).
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
- Purge and empty are the ONLY permanent deletes in this module. They touch
  only <data>/baihe_trash/<id> (id matched by a strict pattern, real path
  checked, no link at the root or at the entry), need the typed word DELETE,
  and walk the entry without following links (_remove_tree).
- Unused voice clips: the files in a title's voice_refs/ that no speaker points
  to, offered by opaque id (no name or path in a response) and moved to Trash
  through the same rename as clear, each re-checked under the library hold.
- Move works only for a folder with an existing, safe way to repoint it
  without a restart: the automatic-backup folder (auto_backup_service
  .set_settings moves the copies and saves the setting together). Everything
  else reports why it can't be moved.
"""

import contextlib
import datetime
import errno
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time

import background_jobs
import db
import portable
import storage
from services import auto_backup_service as abs_
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError, UnsupportedOperationError)

TRASH_DIRNAME = "baihe_trash"       # installer.service.DATA_FOLDER_ENTRIES holds it too
MAX_ENTRIES = 2_000_000
MAX_SECONDS = 30.0
_TIME_CHECK_EVERY = 256
_MAX_REL_LEN = 1024
_MAX_DEPTH = 64

BAD_PATH = "That path isn't inside the app's data folder."
BUSY = ("A job, restore or other library task is running. Wait for it to finish, then try "
        "again.")
ROOT_TOO_BROAD = ("Baihe's data folder is a whole drive or your user folder, so nothing in it "
                  "can be cleared or moved from here.")
LINK_INSIDE = ("This folder contains a link or junction, so it can't be cleared whole. Open it "
               "and clear items inside instead.")
UNREADABLE = ("Part of this folder couldn't be read, so it can't be checked for database or key "
              "files. Open it and clear items inside instead.")
NOT_BAIHE = "Not created by Baihe"
SCAN_BUSY = "Another scan is already running. Wait for it to finish, then try again."
MAX_ITEMS = 2000

TRASH_MANIFEST = "manifest.json"
TRASH_PAYLOAD = "payload"           # the moved item; its own name is only in the manifest
TRASH_WORD = "DELETE"
_TRASH_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{8}")
TRASH_PROTECTED = ("Baihe's Trash folder. Restore or delete things from it in the Trash list "
                   "below.")
TRASH_FAILED = ("Couldn't move it to Trash (it may be in use, or on another drive), so nothing "
                "was moved.")
TRASH_DELETE_FAILED = ("Couldn't delete everything in that Trash item (something may be in "
                       "use). What is left is still in Trash.")
CHANGED = "This item changed since you looked. Rescan and check again."

# What the recursive walk treats as a key/secret file at any depth. Narrow on
# purpose: a drama's file named "token.mp3" must not lock its whole folder.
_SECRET_NAME_RE = re.compile(r"(^\.env|\.(key|pem|p12|pfx)$|^(secrets?|credentials?)(\.|$))",
                             re.IGNORECASE)
_DB_NAME_RE = re.compile(r"\.(db|sqlite3?)(-wal|-shm|-journal)?$|\.db$", re.IGNORECASE)
_MARKER_NAMES = {"installed", "portable"}
_INSTALLER_NAMES = {"launcher", "desktop.ini", "thumbs.db"}
_BAD_CHARS = set('<>"|?*')
# Folders of a source checkout's root that hold data; everything else there is
# the program itself.
_CHECKOUT_DATA_NAMES = {"library", "model_cache", TRASH_DIRNAME}

# library/ folders that are not drama media, with what losing them costs.
_LIBRARY_FOLDERS = {
    "tmp": ("Temporary job files", "Working files of finished or interrupted jobs."),
    "source_cache": ("Site fetch cache", "Downloaded pages; fetched again when needed."),
    "source_review_tmp": ("Import review scratch", "Images kept while an import review is open."),
    "updates": ("Downloaded updates", "Installers already downloaded; download again if needed."),
    "logs": ("Log files", "Diagnostic logs; new ones are written as the app runs."),
    "piper_voices": ("Old voices", "Voices of the removed Piper engine; unused."),
}
_REPLACEABLE_FOLDERS = {
    "voice_bank": "Your saved voice samples.",
    "benchmark_cases": "Your saved benchmark cases.",
}
IRREPLACEABLE_NOTE = ("Source audio and your work for this title. It can't be recreated "
                      "from inside Baihe.")
SOURCE_PROFILES_NOTE = ("Your saved per-site settings. Once cleared they can't be recreated from "
                        "inside Baihe.")
SAVED_COMICS_NOTE = ("Chapters you saved as CBZ files. Once cleared they can't be recreated "
                     "from inside Baihe.")
SAVED_COMICS_DIRNAME = "saved_comics"       # sources_save_service.SAVE_DIRNAME
MAX_PATH = 260              # Windows opens nothing this long without long-path support
BACKUPS_NOTE = "Your backups. Once cleared they can't be recreated from inside Baihe."

_op_lock = threading.Lock()
_scan_lock = threading.Lock()


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _root() -> str:
    """Real path of the data folder: the parent of the live library folder."""
    return os.path.realpath(os.path.dirname(os.path.abspath(db.LIBRARY_DIR)))


def _program_dir() -> str:
    return os.path.realpath(portable.APP_DIR)


def _within(path: str, folder: str) -> bool:
    """True if `path` is `folder` or below it (both real paths)."""
    p, f = _norm(path), _norm(folder)
    return p == f or p.startswith(f.rstrip(os.sep) + os.sep)


def _is_link_stat(st) -> bool:
    """A symlink or a Windows reparse point (junction, mount point)."""
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def split_rel(rel) -> list:
    """The parts of a relative path, or InvalidInputError. Refuses absolute
    paths, drive letters and stream names (':'), '..', empty parts, names
    Windows would rewrite (trailing dot/space) and wildcard characters."""
    if rel is None or rel == "":
        return []
    if not isinstance(rel, str) or len(rel) > _MAX_REL_LEN or "\x00" in rel:
        raise InvalidInputError(BAD_PATH)
    parts = re.split(r"[\\/]", rel)
    if parts and parts[-1] == "":
        parts.pop()
    if len(parts) > _MAX_DEPTH:
        raise InvalidInputError(BAD_PATH)
    for part in parts:
        if (part in ("", ".", "..") or ":" in part or part != part.strip()
                or part.endswith(".") or _BAD_CHARS & set(part) or any(ord(c) < 32 for c in part)):
            raise InvalidInputError(BAD_PATH)
    return parts


def _resolve(parts: list) -> str:
    """The absolute path of data-folder/<parts>, checked component by
    component: every part must exist and be an ordinary file or folder (a link
    anywhere on the way is refused, never followed) and the real path must be
    exactly where the parts say. NotFoundError if it isn't there."""
    root = _root()
    cur = root
    for part in parts:
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            raise NotFoundError("That item is no longer there.") from None
        except OSError:
            raise InvalidInputError(BAD_PATH) from None
        if _is_link_stat(st):
            raise InvalidInputError("Links and junctions aren't followed or changed here.")
    real = os.path.realpath(cur)
    if _norm(real) != _norm(os.path.join(root, *parts)) or not _within(real, root):
        raise InvalidInputError(BAD_PATH)
    return real


def _protected_paths() -> list:
    """Real paths that clear and move must never touch, nor anything holding
    them (the live databases and key files)."""
    lib = os.path.dirname(os.path.abspath(db.DB_PATH))
    out = [os.path.abspath(db.DB_PATH) + s for s in ("", "-wal", "-shm", "-journal")]
    try:
        from sources import store as src_store
        out += [src_store.db_path() + s for s in ("", "-wal", "-shm", "-journal")]
    except Exception:       # the sources package is optional here
        pass
    try:
        import page_server
        out.append(page_server.token_path())
    except Exception:
        pass
    out.append(os.path.join(os.path.dirname(lib), ".env"))
    return [os.path.realpath(p) for p in out]


def _home_dirs() -> list:
    """Real paths of the signed-in user's home/profile folder."""
    out = []
    for raw in (os.path.expanduser("~"), os.environ.get("USERPROFILE", "")):
        if raw and raw != "~":
            out.append(os.path.realpath(raw))
    return out


_SHELL_FOLDER_NAMES = ("Documents", "Desktop", "Downloads", "Pictures", "Music", "Videos")


def _root_blocker(root: str):
    """Why nothing under this data folder may be cleared or moved: it is a
    drive root, the user's own folder, a folder that holds the user's folder
    (C:\\Users) or is or holds one of its shell folders (Documents, Desktop,
    ...): a mis-set data location."""
    if os.path.dirname(root) == root or not os.path.splitdrive(root)[1].strip("\\/"):
        return ROOT_TOO_BROAD
    for home in _home_dirs():
        if _within(home, root):
            return ROOT_TOO_BROAD
        for name in _SHELL_FOLDER_NAMES:
            if _within(os.path.join(home, name), root):
                return ROOT_TOO_BROAD
    return None


def _baihe_top_level_names() -> frozenset:
    """Lower-case names Baihe itself creates at the top of the data folder:
    the installer's own list plus the save folder. If that list can't be
    loaded only the library qualifies, so everything else is protected."""
    try:
        from installer import service as installer_service
        names = set(installer_service.DATA_FOLDER_ENTRIES)
    except Exception:
        names = set()
    names |= {SAVED_COMICS_DIRNAME, TRASH_DIRNAME, os.path.basename(db.LIBRARY_DIR), "library"}
    return frozenset(n.lower() for n in names)


def _backup_folder_real():
    """Real path of the automatic-backup folder when it exists, else None."""
    try:
        current = abs_.folder_path(abs_.get_settings().get("folder", ""))
        if os.path.exists(current):
            return os.path.realpath(current)
    except Exception:
        pass
    return None


class _Ctx:
    """What does not change during one scan or one clear, read once so a
    folder of thousands of items does not repeat it per item."""

    def __init__(self):
        self.root = _root()
        self.program = _program_dir()
        self.protected = _protected_paths()
        self.root_reason = _root_blocker(self.root)
        self.backup_real = _backup_folder_real()
        self.top_level = _baihe_top_level_names()


def _under_backups(parts) -> bool:
    return len(parts) >= 2 and parts[0].lower() == os.path.basename(db.LIBRARY_DIR).lower() \
        and parts[1].lower() == "backups"


def _flag_name(name: str, parts_of_parent) -> bool:
    """A file name that makes its folder unsafe to clear whole."""
    if _SECRET_NAME_RE.search(name):
        return True
    return bool(_DB_NAME_RE.search(name)) and not _under_backups(parts_of_parent)


class _Measured:
    __slots__ = ("size", "files", "flagged", "unreadable", "has_link", "deepest")

    def __init__(self, base_len: int = 0):
        self.deepest = base_len
        self.size = self.files = 0
        self.flagged = self.unreadable = self.has_link = False


# --------------------------------------------------------------------------
# Walking
# --------------------------------------------------------------------------

class _Budget:
    def __init__(self, max_entries=None, max_seconds=None):
        self.max_entries = MAX_ENTRIES if max_entries is None else max_entries
        self.deadline = time.monotonic() + (MAX_SECONDS if max_seconds is None else max_seconds)
        self.entries = 0
        self.hit = None

    def tick(self) -> bool:
        """Counts one entry; False once a limit is hit."""
        if self.hit:
            return False
        self.entries += 1
        if self.entries > self.max_entries:
            self.hit = "entries"
        elif self.entries % _TIME_CHECK_EVERY == 0 and time.monotonic() > self.deadline:
            self.hit = "time"
        return not self.hit


def _confirmed_gone(path: str) -> bool:
    """True only when `path` is certainly no longer there: it can't be
    looked up and its parent's listing no longer holds the name. Windows
    reports over-long and oddly named paths as 'not found' too, so a plain
    FileNotFoundError proves nothing."""
    try:
        if os.path.lexists(path):
            return False
        parent, name = os.path.split(path)
        return name not in os.listdir(parent)
    except OSError:
        return False


def _unopenable_name(path: str, name: str) -> bool:
    """A path Windows may not open as written: MAX_PATH or longer (without
    long-path support), or a name ending in a dot or space (Windows rewrites
    it to another name)."""
    return len(path) >= MAX_PATH or name.endswith((".", " "))


def _measure(path: str, parts: tuple, budget: _Budget) -> _Measured:
    """Size, file count and what the folder holds: a protected file name, a
    link, or a part that could not be read. Links are counted as themselves
    and never entered; only an entry confirmed gone mid-walk is skipped, any
    other error (and any path Windows may not open) marks the result
    unreadable (so clear refuses it)."""
    m = _Measured(len(path))
    stack = [(path, parts)]
    try:
        root_dev = os.lstat(path).st_dev
    except FileNotFoundError:
        root_dev = None
    except OSError:
        m.unreadable = True
        return m
    while stack and not budget.hit:
        cur, cur_parts = stack.pop()
        try:
            it = os.scandir(cur)
        except FileNotFoundError:
            if not _confirmed_gone(cur):
                m.unreadable = True
            continue
        except OSError:
            m.unreadable = True
            continue
        with it:
            while True:
                try:
                    entry = next(it)
                except StopIteration:
                    break
                except OSError:
                    m.unreadable = True
                    break
                if not budget.tick():
                    break
                m.deepest = max(m.deepest, len(entry.path))
                if _unopenable_name(entry.path, entry.name):
                    m.unreadable = True
                try:
                    st = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    if not _confirmed_gone(entry.path):
                        m.unreadable = True
                    continue
                except OSError:
                    m.unreadable = True
                    continue
                # A folder on another volume (a POSIX mount point) is refused
                # like a Windows junction: Clear must never move or delete
                # what lives on a different disk.
                link = _is_link_stat(st) or (stat.S_ISDIR(st.st_mode) and root_dev is not None and st.st_dev != root_dev)
                if link:
                    m.has_link = True
                if stat.S_ISDIR(st.st_mode) and not link:
                    stack.append((entry.path, cur_parts + (entry.name,)))
                    continue
                m.size += st.st_size
                m.files += 1
                if not m.flagged and _flag_name(entry.name, cur_parts):
                    m.flagged = True
    return m


# --------------------------------------------------------------------------
# Describing an item
# --------------------------------------------------------------------------

def _regenerable(parts: tuple, is_dir: bool):
    """{label, note} when storage.py's categories (or a known cache folder)
    say the app can rebuild this item, else None."""
    low = tuple(p.lower() for p in parts)
    lib_name = os.path.basename(db.LIBRARY_DIR).lower()
    if low and low[0] == "model_cache":
        return {"label": "Downloaded models", "note": "Downloaded again when a feature needs them."}
    if len(low) >= 2 and low[0] == lib_name:
        if len(low) == 2 and low[1] in _LIBRARY_FOLDERS:
            label, note = _LIBRARY_FOLDERS[low[1]]
            return {"label": label, "note": note}
        if low[1] == "backups" and len(low) >= 3 and low[2] == "exports":
            return {"label": "Exports", "note": "Export files; make them again from the Library."}
        if low[1] in ("tmp", "source_cache", "source_review_tmp"):
            return {"label": _LIBRARY_FOLDERS[low[1]][0], "note": _LIBRARY_FOLDERS[low[1]][1]}
    name = low[-1] if low else ""
    in_drama = len(low) >= 3 and low[0] == lib_name and low[1] == "dramas"
    for cfg in storage.CLEANABLE_CATEGORIES.values():
        if not cfg.get("regenerable"):
            continue
        meta = {"label": cfg["label"], "note": cfg["note"]}
        if is_dir:
            if in_drama and len(low) == 4 and name in cfg.get("pattern_dirs", []):
                return meta
            continue
        subdir = cfg.get("pattern_subdir", "")
        parent_ok = (len(low) == 4 and not subdir) or (len(low) == 5 and subdir and low[3] == subdir)
        if in_drama and parent_ok and (
                any(name.startswith(p) for p in cfg.get("pattern_prefixes", []))
                or name in cfg.get("pattern_names", [])
                or any(name.endswith(s) for s in cfg.get("pattern_suffixes", []))):
            return meta
        if any(name.endswith(s) for s in cfg.get("pattern_suffixes", [])):
            return meta
    return None


def _irreplaceable_note(parts: tuple, regenerable):
    """The warning for an item that can't be recreated from inside Baihe
    (a title's own media, saved voice samples and cases, per-site settings,
    saved comics and every backup copy), or None."""
    low = tuple(p.lower() for p in parts)
    lib_name = os.path.basename(db.LIBRARY_DIR).lower()
    if low and low[0] == SAVED_COMICS_DIRNAME:
        return SAVED_COMICS_NOTE
    if len(low) >= 2 and low[0] == lib_name:
        if low[1] == "source_profiles":
            return SOURCE_PROFILES_NOTE
        if low[1] == "dramas" and regenerable is None:
            return IRREPLACEABLE_NOTE
        if low[1] in _REPLACEABLE_FOLDERS:
            return IRREPLACEABLE_NOTE
        if low[1] == "backups" and regenerable is None:
            return BACKUPS_NOTE
    return None


def _protection(parts: tuple, real: str, flagged: bool, ctx: _Ctx):
    """(protected, reason) for one item; reasons never name a path."""
    low = tuple(p.lower() for p in parts)
    program, root = ctx.program, ctx.root
    if not parts:
        return True, "The data folder itself can't be cleared or moved."
    if ctx.root_reason:
        return True, ctx.root_reason
    if _norm(root) == _norm(program):
        if low[0] not in _CHECKOUT_DATA_NAMES and low[0] != os.path.basename(db.LIBRARY_DIR).lower():
            return True, "Part of the program, not your data."
    elif _within(program, real):
        return True, "Holds the program files."
    name = low[-1]
    if low[0] == "launcher" or (len(low) == 1 and (name in _MARKER_NAMES or name in _INSTALLER_NAMES)):
        return True, "Install or installer file."
    if low[0] == TRASH_DIRNAME.lower():
        return True, TRASH_PROTECTED
    if low[0] not in ctx.top_level:
        return True, NOT_BAIHE
    if _SECRET_NAME_RE.search(name):
        return True, "Holds API keys or other secrets."
    if len(low) >= 2 and low[0] == os.path.basename(db.LIBRARY_DIR).lower() and low[1] == "profiles":
        return True, "Saved site sign-ins."
    if _DB_NAME_RE.search(name) and not _under_backups(parts):
        return True, "The Baihe database."
    for prot in ctx.protected:
        if _within(prot, real):
            return True, ("The database or key files live here. Open it and clear items inside "
                          "instead.")
    if flagged:
        return True, ("Holds database or key files. Open it and clear items inside instead.")
    return False, None


def _movable(parts: tuple, real: str, protected: bool, ctx: _Ctx):
    """{supported, reason, what}: only a folder Baihe can be repointed away
    from without a restart."""
    if protected:
        return {"supported": False, "reason": "Protected items can't be moved.", "what": None}
    if ctx.backup_real and _norm(ctx.backup_real) == _norm(real):
        return {"supported": True, "reason": None, "what": "backups"}
    low = tuple(p.lower() for p in parts)
    if low and low[0] == "model_cache":
        reason = ("Model locations are read once when Baihe starts, so moving them needs a "
                  "restart and Baihe has no setting for it.")
    elif len(low) >= 2 and low[1] == "dramas":
        reason = "Baihe keeps the library's media in one fixed place and has no setting to move it."
    elif len(low) >= 3 and low[1] == "backups":
        reason = "Only the automatic-backup folder can be moved. Its folder is set in Settings."
    else:
        reason = "Baihe has no setting for this location, so it can't be moved safely."
    return {"supported": False, "reason": reason, "what": None}


def _iso(ts: float):
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _describe(parts: tuple, path: str, st, budget: _Budget, ctx: _Ctx, measured=None) -> dict:
    """The public record of one item. `path` is the verified absolute path.
    `measured` is a _Measured already taken for this folder (not walked again)."""
    is_link = _is_link_stat(st)
    is_dir = stat.S_ISDIR(st.st_mode) and not is_link
    unreadable = has_link = flagged = False
    deepest = len(path)
    if is_dir:
        m = measured or _measure(path, parts, budget)
        size, files, flagged = m.size, m.files, m.flagged
        unreadable, has_link, deepest = m.unreadable, m.has_link, m.deepest
    else:
        size, files = st.st_size, 1
        flagged = _flag_name(parts[-1], parts[:-1]) if parts else False
    real = os.path.realpath(path)
    protected, reason = _protection(parts, real, flagged, ctx)
    if is_link:
        protected, reason = True, "A link. Baihe never follows or changes links here."
    elif unreadable and not protected:
        protected, reason = True, UNREADABLE
    regen = _regenerable(parts, is_dir)
    note = _irreplaceable_note(parts, regen)
    return {
        "name": parts[-1] if parts else "",
        "path": "/".join(parts),
        "kind": "folder" if is_dir else "file",
        "size_bytes": size,
        "file_count": files,
        "percent_of_parent": None,
        "modified_at": _iso(st.st_mtime),
        "is_link": is_link,
        "contains_link": has_link,
        "complete": not budget.hit and not unreadable,
        "protected": protected,
        "protected_reason": reason,
        "regenerable": regen,
        "irreplaceable": bool(note and not protected),
        "irreplaceable_note": note if note and not protected else None,
        "movable": _movable(parts, real, protected, ctx),
        "_deepest_path": deepest,
    }


# --------------------------------------------------------------------------
# Public: scan
# --------------------------------------------------------------------------

def busy_reason():
    """Why clear and move are refused right now, or None."""
    if (background_jobs.active_job_ids() or background_jobs.maintenance_active()
            or background_jobs.exclusive_active()):
        return BUSY
    return None


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
        "items": items,
        "not_shown": not_shown,
        "partial": bool(reason),
        "partial_reason": reason,
        "scanned_entries": budget.entries,
        "busy_reason": busy_reason(),
        "trash": trash_info,
        **disk,
    }


# --------------------------------------------------------------------------
# Clear into the Trash folder
# --------------------------------------------------------------------------

def _require_confirm(confirm):
    if confirm is not True:
        raise InvalidInputError("Confirm to continue (confirm must be true).")


def _busy_under_hold() -> bool:
    """Busy checks that stay valid while this module holds the library: jobs
    in this process, maintenance, fresh running job_records rows written by
    another app process, and a live holder of the cross-process GPU lock. The
    CLI writes no job_records, so a CLI run is seen only while it holds the
    GPU lock (its GPU steps); a CLI run of non-GPU steps is not detected.
    Fails closed: an unreadable lock table counts as busy."""
    from services import library_admin_service
    if (background_jobs.active_job_ids() or background_jobs.maintenance_active()
            or library_admin_service.any_job_running()):
        return True
    try:
        return db.gpu_lock_status()[0] is not None
    except Exception:
        return True


@contextlib.contextmanager
def _exclusive(label: str):
    """The exclusive library hold around inspect -> re-resolve -> change, so no
    job starts in between. ConflictError when it can't be taken."""
    if not background_jobs.acquire_exclusive(label):
        raise ConflictError(BUSY)
    try:
        if _busy_under_hold():
            raise ConflictError(BUSY)
        yield
    finally:
        background_jobs.release_exclusive()


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


@contextlib.contextmanager
def _changing(label: str):
    """One change at a time, under the library's exclusive hold."""
    if not _op_lock.acquire(blocking=False):
        raise ConflictError("Another clear or move is in progress. Try again in a moment.")
    try:
        with _exclusive(label):
            yield
    finally:
        _op_lock.release()


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
