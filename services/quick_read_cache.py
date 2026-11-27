"""Quick read's temporary results cache and per-title memory on disk: size- and age-limited,
never a saved series or title."""

import functools
import hashlib
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

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
                   "services/page_reread_service.py", "services/comic_glossary_service.py",
                   "services/comic_fixes_service.py", "translation_guide.py")
_PIPELINE_DIRS = ("scanlate", "engine_backends")


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
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        # Age counts from the first save: the touch below only orders the size sweep, so an entry
        # that keeps being read still expires and its page is read again with current code.
        if time.time() - float(stored["saved_at"]) > MAX_AGE_SECONDS:
            return None
        os.utime(path)   # the sweep drops the least recently used first
        return stored["result"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def put(key: str | None, result: dict) -> None:
    if not key:
        return
    path = _path(key)
    tmp = path + ".part"
    try:
        with _lock:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"saved_at": time.time(), "result": result}, f)
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


# A chapter slug such as "psycho-eyes-chapter-1", "ep12" or "第3话"; what precedes it names the title.
_CHAPTER_PART = re.compile(r"(?i)(?:^|[-_.])(?:chapter|chap|ch|episode|ep|capitulo|raw)[-_.]?\d.*$"
                           r"|^第?\d+(?:[.-]\d+)*(?:话|話|回|화|章)?$|^(?:chapter|ch|ep|episode)$")


@functools.lru_cache(maxsize=256)
def title_key(url: str) -> str:
    """The title a chapter link belongs to, so Quick read's memory of names, pronouns and
    language carries from one chapter to the next: the source adapter's series id when one
    serves the link, else the link with its chapter part cut. A link with no chapter part to
    cut stays as it is, so it is never merged with another title's."""
    url = str(url or "")
    try:
        from sources import registry
        cls = registry.adapter_class_for_url(url)
        # Parsing a link needs no client; a stand-in keeps the adapter from building one.
        parsed = cls(client=object(), allow_adult=False).parse_url(url) if cls else None
    except Exception:
        cls, parsed = None, None
    if cls is not None and parsed and parsed[0] == "series":
        return f"{cls.name}:{parsed[1]}"
    if cls is not None and parsed and parsed[0] == "chapter" and getattr(parsed[1], "series_id", None):
        return f"{cls.name}:{parsed[1].series_id}"
    split = urlsplit(url)
    parts = [p for p in split.path.split("/") if p]
    cut = _CHAPTER_PART.sub("", parts[-1]) if parts else ""
    if not parts or cut == parts[-1]:
        return url
    parts = [*parts[:-1], cut] if cut else parts[:-1]
    if not parts:
        return url
    return f"{(split.hostname or '').lower()}/{'/'.join(parts)}"


def source_language(url: str) -> str:
    """The comic language a link's source adapter serves ("" when none, or several)."""
    try:
        from sources import registry
        cls = registry.adapter_class_for_url(str(url or ""))
    except Exception:
        return ""
    languages = list(getattr(cls, "languages", None) or [])
    return languages[0] if len(languages) == 1 and languages[0] in ("zh", "ja", "ko") else ""


def _memory_key(title: str) -> str:
    return make_key("title-memory", title)


def title_memory(title: str) -> dict:
    """{"names", "cast", "language"} Quick read kept for a title, or {}."""
    hit = get(_memory_key(title)) if title else None
    return hit if isinstance(hit, dict) else {}


def remember_title(title: str, memory: dict) -> None:
    if title:
        put(_memory_key(title), memory)
