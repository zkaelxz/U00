"""
services/disk_usage_service.py -- "what is taking up space" for the app's own
data folder, plus clearing items to the Recycle Bin and moving the few parts
Baihe can be told a new location for. UI-free; raises services/service_errors.

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
  saved site sign-ins, installer files, the program folder, and any folder
  that holds or contains one of those. Both refuse while any background job,
  restore or maintenance run holds the library.
- Clear needs confirm=true and the size and file count the user saw (409 when
  the item changed since), and sends the item to the Recycle Bin through one
  injectable function (send_to_recycle_bin). It refuses off Windows, when the
  process isn't an interactive user session (a Windows service has its own
  bin the owner never sees; recycle_unavailable_reason), when the data folder
  is a drive root or the user's home, for a folder it couldn't read fully or
  that holds a link, and whenever the bin's settings are unknown; it never
  deletes permanently. Inspect, re-resolve and recycle run under the library's
  exclusive hold.
- Move works only for a folder with an existing, safe way to repoint it
  without a restart: the automatic-backup folder (auto_backup_service
  .set_settings moves the copies and saves the setting together). Everything
  else reports why it can't be moved.
"""

import contextlib
import datetime
import os
import re
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

MAX_ENTRIES = 2_000_000
MAX_SECONDS = 30.0
_TIME_CHECK_EVERY = 256
_MAX_REL_LEN = 1024
_MAX_DEPTH = 64

BAD_PATH = "That path isn't inside the app's data folder."
BUSY = ("A job, restore or other library task is running. Wait for it to finish, then try "
        "again.")
NO_RECYCLE_BIN = "No Recycle Bin on this system, so nothing was removed."
NOT_INTERACTIVE = ("Clear needs Baihe to run as you, not as a Windows service. Its Recycle Bin "
                   "would be the service's, which you can't open.")
SESSION_UNKNOWN = ("Clear couldn't confirm that Baihe is running as you, so nothing was removed.")
ROOT_TOO_BROAD = ("Baihe's data folder is a whole drive or your user folder, so nothing in it "
                  "can be cleared or moved from here.")
LINK_INSIDE = ("This folder contains a link or junction, so it can't be cleared whole. Open it "
               "and clear items inside instead.")
UNREADABLE = ("Part of this folder couldn't be read, so it can't be checked for database or key "
              "files. Open it and clear items inside instead.")
SCAN_BUSY = "Another scan is already running. Wait for it to finish, then try again."
MAX_ITEMS = 2000

# Where a Recycle Bin silently deletes for good instead (a drive with no bin,
# "delete immediately", or an item bigger than the bin's limit): refuse first.
# Used when the drive has no explicit limit.
DEFAULT_BIN_FRACTION = 0.05

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
_CHECKOUT_DATA_NAMES = {"library", "model_cache"}

# library/ folders that are not drama media, with what losing them costs.
_LIBRARY_FOLDERS = {
    "tmp": ("Temporary job files", "Working files of finished or interrupted jobs."),
    "source_cache": ("Site fetch cache", "Downloaded pages; fetched again when needed."),
    "source_review_tmp": ("Import review scratch", "Images kept while an import review is open."),
    "updates": ("Downloaded updates", "Installers already downloaded; download again if needed."),
    "logs": ("Log files", "Diagnostic logs; new ones are written as the app runs."),
    "piper_voices": ("Downloaded voices", "Text-to-speech voices; downloaded again when used."),
}
_REPLACEABLE_FOLDERS = {
    "voice_bank": "Your saved voice samples.",
    "benchmark_cases": "Your saved benchmark cases.",
}
IRREPLACEABLE_NOTE = ("Source audio and your work for this title. It can't be recreated "
                      "from inside Baihe.")
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
    return os.path.realpath(portable._APP_DIR)


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


def _root_blocker(root: str):
    """Why nothing under this data folder may be cleared or moved: it is a
    drive root or the user's own folder (a mis-set data location)."""
    if os.path.dirname(root) == root or not os.path.splitdrive(root)[1].strip("\\/"):
        return ROOT_TOO_BROAD
    if any(_norm(root) == _norm(h) for h in _home_dirs()):
        return ROOT_TOO_BROAD
    return None


def _backup_folder_real():
    """Real path of the automatic-backup folder when it exists, else None."""
    try:
        current = abs_._folder_path(abs_.get_settings().get("folder", ""))
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


def _under_backups(parts) -> bool:
    return len(parts) >= 2 and parts[0].lower() == os.path.basename(db.LIBRARY_DIR).lower() \
        and parts[1].lower() == "backups"


def _flag_name(name: str, parts_of_parent) -> bool:
    """A file name that makes its folder unsafe to clear whole."""
    if _SECRET_NAME_RE.search(name):
        return True
    return bool(_DB_NAME_RE.search(name)) and not _under_backups(parts_of_parent)


class _Measured:
    __slots__ = ("size", "files", "flagged", "unreadable", "has_link")

    def __init__(self):
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


def _measure(path: str, parts: tuple, budget: _Budget) -> _Measured:
    """Size, file count and what the folder holds: a protected file name, a
    link, or a part that could not be read. Links are counted as themselves
    and never entered; an entry that vanished mid-walk is skipped, any other
    error marks the result unreadable (so clear refuses it)."""
    m = _Measured()
    stack = [(path, parts)]
    while stack and not budget.hit:
        cur, cur_parts = stack.pop()
        try:
            it = os.scandir(cur)
        except FileNotFoundError:
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
                try:
                    st = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError:
                    m.unreadable = True
                    continue
                link = _is_link_stat(st)
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
    (a title's own media, saved voice samples and cases, and every backup
    copy), or None."""
    low = tuple(p.lower() for p in parts)
    lib_name = os.path.basename(db.LIBRARY_DIR).lower()
    if len(low) >= 2 and low[0] == lib_name:
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


def _describe(parts: tuple, path: str, st, budget: _Budget, ctx: _Ctx) -> dict:
    """The public record of one item. `path` is the verified absolute path."""
    is_link = _is_link_stat(st)
    is_dir = stat.S_ISDIR(st.st_mode) and not is_link
    unreadable = has_link = flagged = False
    if is_dir:
        m = _measure(path, parts, budget)
        size, files, flagged = m.size, m.files, m.flagged
        unreadable, has_link = m.unreadable, m.has_link
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


def session_blocker(session_id, sid, username):
    """Why this process can't use the signed-in person's Recycle Bin, or None.
    Pure so it is tested anywhere. A Windows service (session 0, or a service,
    virtual or machine account) has a Recycle Bin of its own that the owner
    never sees; anything not known for sure is refused."""
    if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id < 0:
        return SESSION_UNKNOWN
    if session_id == 0:
        return NOT_INTERACTIVE
    if not isinstance(sid, str) or not sid:
        return SESSION_UNKNOWN
    # Only a local, domain or Entra ID user is a person. SYSTEM, LOCAL/NETWORK
    # SERVICE and NT SERVICE\* (S-1-5-80-...) are not.
    if not sid.startswith(("S-1-5-21-", "S-1-12-1-")):
        return NOT_INTERACTIVE
    if isinstance(username, str) and username.endswith("$"):
        return NOT_INTERACTIVE
    return None


def _is_windows() -> bool:
    return os.name == "nt"


def _windows_session_facts():
    """(session id, user SID string, user name) of this process. Raises on any
    failure; the caller treats that as 'unknown' and refuses."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.LocalFree.argtypes = [ctypes.c_void_p]
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    session = wintypes.DWORD()
    if not k32.ProcessIdToSessionId(k32.GetCurrentProcessId(), ctypes.byref(session)):
        raise OSError("ProcessIdToSessionId failed")
    token = wintypes.HANDLE()
    if not adv.OpenProcessToken(k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise OSError("OpenProcessToken failed")
    try:
        need = wintypes.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(need))     # TokenUser
        buf = ctypes.create_string_buffer(need.value)
        if not need.value or not adv.GetTokenInformation(token, 1, buf, need, ctypes.byref(need)):
            raise OSError("GetTokenInformation failed")
        psid = ctypes.c_void_p.from_buffer(buf).value      # TOKEN_USER starts with the SID pointer
        text = wintypes.LPWSTR()
        if not psid or not adv.ConvertSidToStringSidW(psid, ctypes.byref(text)):
            raise OSError("ConvertSidToStringSidW failed")
        sid = text.value
        k32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
    finally:
        k32.CloseHandle(token)
    return session.value, sid, os.environ.get("USERNAME", "")


def recycle_unavailable_reason():
    """Why Clear can't work here, or None. The one check for the Recycle Bin
    being the signed-in person's own: Windows only, in an interactive session,
    not as a service account; undeterminable means no. Tests replace this."""
    if not _is_windows():
        return NO_RECYCLE_BIN
    try:
        facts = _windows_session_facts()
    except Exception:
        return SESSION_UNKNOWN
    return session_blocker(*facts)


def recycle_available() -> bool:
    return recycle_unavailable_reason() is None


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
    items = []
    try:
        with os.scandir(folder) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        raise ServiceError("That folder could not be read.") from None
    listed = 0
    for entry in entries:
        if budget.hit or listed >= MAX_ITEMS:
            break
        listed += 1
        child_parts = tuple(parts) + (entry.name,)
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        budget.tick()
        items.append(_describe(child_parts, entry.path, st, budget, ctx))
    not_shown = len(entries) - listed
    total = sum(i["size_bytes"] for i in items)
    for i in items:
        i["percent_of_parent"] = round(100.0 * i["size_bytes"] / total, 1) if total else 0.0
    items.sort(key=lambda i: (-i["size_bytes"], i["name"].lower()))
    try:
        usage = shutil.disk_usage(ctx.root)
        disk = {"disk_total_bytes": usage.total, "disk_free_bytes": usage.free}
    except OSError:
        disk = {"disk_total_bytes": None, "disk_free_bytes": None}
    recycle_reason = recycle_unavailable_reason()
    reason = budget.hit or ("items" if not_shown else None)
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
        "recycle_available": recycle_reason is None,
        "recycle_reason": recycle_reason,
        **disk,
    }


# --------------------------------------------------------------------------
# Clear: Recycle Bin
# --------------------------------------------------------------------------

def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def recycle_blocker(size_bytes: int, drive_type, nuke_on_delete, max_capacity_mb, volume_bytes,
                    no_recycle_policy=0, policy_size_percent=None):
    """Why the Recycle Bin would NOT keep this item (so asking it to would
    delete for good with confirmations off), or None. Pure so it is tested
    anywhere; the Windows readers below feed it. drive_type 3 = fixed disk.
    Anything unknown or malformed refuses: nuke_on_delete must be 0,
    max_capacity_mb is an integer or None (no explicit limit), and
    no_recycle_policy (NoRecycleFiles) must be 0."""
    if drive_type != 3:
        return "This drive has no Recycle Bin (removable or network), so nothing was removed."
    if no_recycle_policy != 0:
        return ("A Windows policy turns the Recycle Bin off, or it couldn't be checked, so "
                "nothing was removed.")
    if _int_or_none(nuke_on_delete) is None or nuke_on_delete not in (0, 1):
        return "The drive's Recycle Bin setting couldn't be read, so nothing was removed."
    if nuke_on_delete:
        return ("This drive is set to delete immediately instead of using the Recycle Bin, so "
                "nothing was removed.")
    if max_capacity_mb is not None and _int_or_none(max_capacity_mb) is None:
        return "The Recycle Bin size for this drive couldn't be read, so nothing was removed."
    volume = _int_or_none(volume_bytes)
    limits = []
    if max_capacity_mb:
        limits.append(max_capacity_mb * 1024 * 1024)
    elif volume:
        limits.append(int(volume * DEFAULT_BIN_FRACTION))
    if policy_size_percent is not None:
        pct = _int_or_none(policy_size_percent)
        if pct is None or pct > 100 or not volume:
            return "The Recycle Bin size policy couldn't be checked, so nothing was removed."
        limits.append(volume * pct // 100)
    if not limits:
        return "The Recycle Bin size for this drive couldn't be checked, so nothing was removed."
    if size_bytes > min(limits):
        return ("This is bigger than the drive's Recycle Bin limit, so Windows would delete it "
                "for good. Raise the Recycle Bin size or clear smaller pieces.")
    return None


_UNREADABLE_VALUE = "unreadable"


def _read_reg(winreg, hive, sub, name):
    """(found key, value): value is None when the value is absent and
    _UNREADABLE_VALUE when the registry refused. Key absent -> (False, None)."""
    try:
        key = winreg.OpenKey(hive, sub)
    except FileNotFoundError:
        return False, None
    except OSError:
        return False, _UNREADABLE_VALUE
    with key:
        try:
            return True, winreg.QueryValueEx(key, name)[0]
        except FileNotFoundError:
            return True, None
        except OSError:
            return True, _UNREADABLE_VALUE


def _windows_bin_facts(path: str):
    """(drive_type, NukeOnDelete, MaxCapacity in MB, volume bytes,
    NoRecycleFiles policy, RecycleBinSize policy percent) from Windows.
    Anything that can't be read comes back as a value recycle_blocker refuses
    (None for the drive facts, _UNREADABLE_VALUE for the policy)."""
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    drive = os.path.splitdrive(path)[0]
    if not re.fullmatch(r"[A-Za-z]:", drive):
        return None, None, None, None, _UNREADABLE_VALUE, None
    root = drive + "\\"
    drive_type = kernel32.GetDriveTypeW(root)
    nuke = max_mb = None
    no_recycle = 0
    size_pct = None
    try:
        import winreg
    except ImportError:
        return drive_type, None, None, None, _UNREADABLE_VALUE, None
    buf = ctypes.create_unicode_buffer(64)
    found = (re.search(r"Volume(\{[0-9A-Fa-f-]+\})", buf.value)
             if kernel32.GetVolumeNameForVolumeMountPointW(root, buf, 64) else None)
    if found:
        sub = (r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
               "\\" + found.group(1))
        has_key, nuke = _read_reg(winreg, winreg.HKEY_CURRENT_USER, sub, "NukeOnDelete")
        if has_key:
            # Absent MaxCapacity means no explicit limit; unreadable is not
            # an integer, so the blocker refuses it.
            max_mb = _read_reg(winreg, winreg.HKEY_CURRENT_USER, sub, "MaxCapacity")[1]
        else:
            nuke = None                 # no Volume key: unknown, refuse
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        value = _read_reg(winreg, hive,
                          r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer",
                          "NoRecycleFiles")[1]
        if value is not None and value != 0:
            no_recycle = 1              # set, or unreadable
        pct = _read_reg(winreg, hive, r"Software\Policies\Microsoft\Windows\Explorer",
                        "RecycleBinSize")[1]
        if pct is not None:
            both = isinstance(pct, int) and isinstance(size_pct, (int, type(None)))
            size_pct = (pct if size_pct is None else min(size_pct, pct)) if both else _UNREADABLE_VALUE
    try:
        volume = shutil.disk_usage(root).total
    except OSError:
        volume = None
    return drive_type, nuke, max_mb, volume, no_recycle, size_pct


def send_to_recycle_bin(path: str, size_bytes: int = 0) -> None:
    """Moves one file or folder to the Windows Recycle Bin (undoable). Raises
    UnsupportedOperationError when this isn't the signed-in person's Windows
    session and OSError/ServiceError when Windows refuses. The single
    platform call: tests replace this function."""
    reason = recycle_unavailable_reason()
    if reason:
        raise UnsupportedOperationError(reason)
    import ctypes
    from ctypes import wintypes
    if len(path) > 259 or _BAD_CHARS & set(path[2:]):
        raise ServiceError("This path is too long for the Recycle Bin, so nothing was removed.")
    blocker = recycle_blocker(size_bytes, *_windows_bin_facts(path))
    if blocker:
        raise ServiceError(blocker)

    FO_DELETE = 0x0003
    FOF_SILENT, FOF_NOCONFIRMATION = 0x0004, 0x0010
    FOF_ALLOWUNDO, FOF_NOERRORUI = 0x0040, 0x0400
    FOF_NO_CONNECTED_ELEMENTS = 0x2000      # foo.html must not drag in foo_files

    class SHFILEOPSTRUCTW(ctypes.Structure):
        # The shell declares this packed on 32-bit Windows, naturally aligned
        # on 64-bit.
        _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    # pFrom is a list of names ending with two NULs: one path, then the
    # buffer's own terminator.
    buf = ctypes.create_unicode_buffer(os.path.normpath(path) + "\0")
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p).value
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
                 | FOF_NO_CONNECTED_ELEMENTS)
    code = shell32.SHFileOperationW(ctypes.byref(op))
    if code != 0 or op.fAnyOperationsAborted:
        raise ServiceError("Windows couldn't move it to the Recycle Bin (it may be in use or "
                           "protected), so nothing was removed.")


def _require_confirm(confirm):
    if confirm is not True:
        raise InvalidInputError("Confirm to continue (confirm must be true).")


def _busy_under_hold() -> bool:
    """Busy checks that stay valid while this module holds the library: jobs
    in this process, maintenance, and fresh running job_records rows from
    another process (the CLI)."""
    from services import library_admin_service
    return bool(background_jobs.active_job_ids() or background_jobs.maintenance_active()
                or library_admin_service._any_job_running())


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
    st = os.lstat(real)
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
    """Sends one item to the Recycle Bin. `expected_*` are the size and file
    count the person saw: the item is measured again and a difference is a 409.
    Anything that can't be recreated (a title's media, backups) also needs
    confirm_irreplaceable."""
    _require_confirm(confirm)
    if expected_size_bytes is None or expected_file_count is None:
        raise InvalidInputError("Send the size and file count you were shown.")
    reason = recycle_unavailable_reason()
    if reason:
        raise UnsupportedOperationError(reason)
    if not _op_lock.acquire(blocking=False):
        raise ConflictError("Another clear or move is in progress. Try again in a moment.")
    try:
        with _exclusive("Disk usage clear"):
            parts, real, item = _inspect_for_change(path, "clearing")
            if item["contains_link"]:
                raise InvalidInputError(LINK_INSIDE)
            if (item["size_bytes"] != expected_size_bytes
                    or item["file_count"] != expected_file_count):
                raise ConflictError("This item changed since you looked. Rescan and check again.",
                                    details={"reason": "changed", "size_bytes": item["size_bytes"],
                                             "file_count": item["file_count"]})
            if item["irreplaceable"] and confirm_irreplaceable is not True:
                raise ConflictError("This can't be recreated from inside Baihe. Confirm that "
                                    "too to continue.",
                                    details={"reason": "needs_irreplaceable_confirm"})
            # Last look before the platform call: still the same plain item at
            # the same place, no link swapped in, and nothing started meanwhile.
            if _busy_under_hold():
                raise ConflictError(BUSY)
            again = _resolve(parts)
            if _norm(again) != _norm(real) or _is_link_stat(os.lstat(again)):
                raise ConflictError("This item changed since you looked. Rescan and check again.",
                                    details={"reason": "changed"})
            try:
                send_to_recycle_bin(again, item["size_bytes"])
            except ServiceError:
                raise
            except OSError:
                raise ServiceError("Windows couldn't move it to the Recycle Bin, so nothing was "
                                   "removed.") from None
            if os.path.lexists(again):
                raise ServiceError("It is still there after the Recycle Bin call, so nothing was "
                                   "freed. It may be in use.")
            return {"freed_bytes": item["size_bytes"], "file_count": item["file_count"],
                    "kind": item["kind"], "name": item["name"]}
    finally:
        _op_lock.release()


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
