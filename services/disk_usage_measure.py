"""
services/disk_usage_measure.py -- the bounded, read-only walk of the
data-folder disk usage feature and the public record of one item: size and file
count (links counted as themselves and never entered, folders on another volume
treated as links), what the folder holds (protected names, links, unreadable
parts), whether Baihe can rebuild it, whether losing it is irreplaceable and
whether it can be moved. UI-free; the protection rules themselves live in
services/disk_usage_guard.py.
"""

import datetime
import os
import stat
import time

import db
import storage
from services import disk_usage_links
from services.disk_usage_guard import (SAVED_COMICS_DIRNAME, _Ctx, _flag_name, _is_link_stat,
                                       _norm, _protection)

MAX_ENTRIES = 2_000_000
MAX_SECONDS = 30.0
_TIME_CHECK_EVERY = 256
UNREADABLE = ("Part of this folder couldn't be read, so it can't be checked for database or key "
              "files. Open it and clear items inside instead.")
MAX_ITEMS = 2000


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
MAX_PATH = 260              # Windows opens nothing this long without long-path support
BACKUPS_NOTE = "Your backups. Once cleared they can't be recreated from inside Baihe."


class _Measured:
    __slots__ = ("size", "files", "flagged", "unreadable", "has_link", "deepest", "links", "links_cut")

    def __init__(self, base_len: int = 0):
        self.deepest = base_len
        self.size = self.files = 0
        self.flagged = self.unreadable = self.has_link = False
        self.links = []
        self.links_cut = False


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


def _device_differs(st, ref_dev) -> bool:
    """True only when both device numbers are known and differ. On Windows
    os.DirEntry.stat() always reports st_dev 0 (only os.stat/os.lstat fill it),
    so 0 means "unknown", not "another volume"; reading it as a different
    device would turn every Windows folder into a link. Windows junctions and
    mounted volumes are reparse points, which _is_link_stat already catches."""
    return bool(ref_dev) and bool(st.st_dev) and st.st_dev != ref_dev


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
                link = _is_link_stat(st) or (stat.S_ISDIR(st.st_mode) and _device_differs(st, root_dev))
                if link:
                    m.has_link = True
                    if len(m.links) < disk_usage_links.MAX_LINK_TARGETS:
                        m.links.append(entry.path)
                    else:
                        m.links_cut = True
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


def _on_another_volume(path: str, st) -> bool:
    """A real folder on a different volume than the folder holding it (a POSIX
    mount point). _measure counts one as a link from its parent's side, so it
    has to be one from its own side too, or the two views disagree."""
    if not stat.S_ISDIR(st.st_mode):
        return False
    try:
        return _device_differs(st, os.lstat(os.path.dirname(path)).st_dev)
    except OSError:
        return False


def _iso(ts: float):
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _describe(parts: tuple, path: str, st, budget: _Budget, ctx: _Ctx, measured=None) -> dict:
    """The public record of one item. `path` is the verified absolute path.
    `measured` is a _Measured already taken for this folder (not walked again)."""
    is_link = _is_link_stat(st) or _on_another_volume(path, st)
    is_dir = stat.S_ISDIR(st.st_mode) and not is_link
    unreadable = has_link = flagged = False
    deepest = len(path)
    link_paths, links_cut = ([path] if is_link else []), False
    if is_dir:
        m = measured or _measure(path, parts, budget)
        size, files, flagged = m.size, m.files, m.flagged
        unreadable, has_link, deepest = m.unreadable, m.has_link, m.deepest
        link_paths, links_cut = m.links, m.links_cut
    else:
        size, files = st.st_size, 1
        flagged = _flag_name(parts[-1], parts[:-1]) if parts else False
    linked = ctx.linked.of(link_paths, budget, links_cut) if ctx.linked else None
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
        "linked_bytes": linked[0] if linked else None,
        "linked_files": linked[1] if linked else None,
        "linked_complete": linked[2] if linked else None,
        "complete": not budget.hit and not unreadable,
        "protected": protected,
        "protected_reason": reason,
        "regenerable": regen,
        "irreplaceable": bool(note and not protected),
        "irreplaceable_note": note if note and not protected else None,
        "movable": _movable(parts, real, protected, ctx),
        "title_sizes": None,
        "_deepest_path": deepest,
    }
