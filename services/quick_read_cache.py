"""Quick read's temporary results cache on disk: size- and age-limited, never a saved series or title."""

import functools
import hashlib
import json
import os
import threading
import time

import db
from lib import atomic_write

MAX_BYTES = 400 * 1024 * 1024
MAX_AGE_SECONDS = 14 * 24 * 3600
DIRNAME = "quick_read_cache"
_lock = threading.Lock()


def _dir() -> str:
    # Beside the library, not in its tmp folder: the temp sweep removes anything older than a day.
    path = os.path.join(db.LIBRARY_DIR, DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


# The code that decides what a cached page looks like: reading, translating, erasing, lettering.
# An entry made by older code of these is never served, so an upgrade (or an edit while developing)
# shows the new result instead of the old redraw.
_PIPELINE_FILES = ("ocr.py", "ocr_layout.py", "page_turns.py", "page_capture_checks.py", "en_cleanup.py",
                   "services/page_server.py", "services/extension_bridge_service.py",
                   "services/page_reread_service.py", "services/comic_glossary_service.py")
_PIPELINE_DIRS = ("scanlate",)


@functools.cache
def pipeline_version() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = [os.path.join(root, name) for name in _PIPELINE_FILES]
    for folder in _PIPELINE_DIRS:
        base = os.path.join(root, folder)
        paths += [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".py")] \
            if os.path.isdir(base) else []
    digest = hashlib.sha256()
    for path in paths:
        try:
            with open(path, "rb") as f:
                digest.update(os.path.relpath(path, root).encode() + b"\0" + f.read())
        except OSError:
            digest.update(b"missing\0")   # a changed file set still changes the version
    return digest.hexdigest()[:16]


def make_key(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, default=str, sort_keys=True).encode()).hexdigest()


def _path(key: str) -> str:
    return os.path.join(_dir(), key + ".json")


def get(key: str | None) -> dict | None:
    if not key:
        return None
    path = _path(key)
    try:
        if time.time() - os.path.getmtime(path) > MAX_AGE_SECONDS:
            return None
        with open(path, "r", encoding="utf-8") as f:
            result = json.load(f)
        os.utime(path)   # the sweep drops the least recently used first
        return result
    except (OSError, ValueError):
        return None


def put(key: str | None, result: dict) -> None:
    if not key:
        return
    path = _path(key)
    tmp = path + ".part"
    try:
        with _lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(result, f)
            atomic_write.replace_file(tmp, path)
            _sweep()
    except OSError:
        return   # a cache that cannot write only costs the next read its speed


def _sweep() -> None:
    folder = _dir()
    now = time.time()
    entries = []
    for name in os.listdir(folder):
        full = os.path.join(folder, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        if name.endswith(".part") or now - stat.st_mtime > MAX_AGE_SECONDS:
            try:
                os.remove(full)
            except OSError:
                pass
            continue
        entries.append((stat.st_mtime, stat.st_size, full))
    total = sum(size for _, size, _ in entries)
    for _, size, full in sorted(entries):
        if total <= MAX_BYTES:
            break
        try:
            os.remove(full)
            total -= size
        except OSError:
            pass
