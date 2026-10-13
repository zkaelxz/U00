"""
services/disk_usage_guard.py -- path confinement and protection policy of the
data-folder disk usage feature: which relative paths are accepted (no '..',
drive letters, links or names Windows would rewrite), which items clear and move
must never touch (live databases, key files, the program, the Trash folder,
anything Baihe did not create) and the per-call context those checks read.
UI-free; raises services/service_errors. Used by the scan (to mark items
protected) and by every change (clear, move, restore) to re-check them.
"""

import os
import re
import stat

import db
import portable
from services import auto_backup_service as abs_
from services.service_errors import InvalidInputError, NotFoundError

TRASH_DIRNAME = "baihe_trash"       # installer.service_layout.DATA_FOLDER_ENTRIES holds it too
_MAX_REL_LEN = 1024
_MAX_DEPTH = 64


BAD_PATH = "That path isn't inside the app's data folder."
ROOT_TOO_BROAD = ("Baihe's data folder is a whole drive or your user folder, so nothing in it "
                  "can be cleared or moved from here.")
NOT_BAIHE = "Not created by Baihe"
PREVIOUS_LIBRARY = ("Previous library kept after restore. Delete it under Previous library "
                    "below once you have checked the library.")
TRASH_PROTECTED = ("Baihe's Trash folder. Restore or delete things from it in the Trash list "
                   "below.")


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
SAVED_COMICS_DIRNAME = "saved_comics"       # sources_save_service.SAVE_DIRNAME


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
        from installer import service_layout as installer_service
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
        self.linked = None      # set by a scan only; clear and restore never measure link targets


def _previous_library(name: str) -> bool:
    from services import previous_library_service   # it measures with this module
    return previous_library_service.is_previous_library_name(name)


def _under_backups(parts) -> bool:
    return len(parts) >= 2 and parts[0].lower() == os.path.basename(db.LIBRARY_DIR).lower() \
        and parts[1].lower() == "backups"


def _flag_name(name: str, parts_of_parent) -> bool:
    """A file name that makes its folder unsafe to clear whole."""
    if _SECRET_NAME_RE.search(name):
        return True
    return bool(_DB_NAME_RE.search(name)) and not _under_backups(parts_of_parent)


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
    if len(low) == 1 and _previous_library(parts[0]):
        return True, PREVIOUS_LIBRARY
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
