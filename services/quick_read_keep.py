"""Debug copies of Quick read pages (original picture, read-step regions, final regions) kept for reproducing a bad page."""

import hashlib
import json
import os
import queue
import threading
import time

import db
import stored_settings
from applog import get_logger

MAX_PAGES = 300
MAX_AGE_SECONDS = 3 * 24 * 3600
DIRNAME = "quick_read_keep"
_ENABLED_CACHE_SECONDS = 30

_local = threading.local()
_writes: "queue.Queue[tuple]" = queue.Queue(maxsize=50)
_writer_lock = threading.Lock()
_writer_started = False
_enabled_at = 0.0
_enabled_value = False


def enabled() -> bool:
    """Developer Mode and the keep switch, both off by default. The answer is remembered for a few
    seconds so a page pays a clock read, not two database reads."""
    global _enabled_at, _enabled_value
    now = time.monotonic()
    if now - _enabled_at < _ENABLED_CACHE_SECONDS:
        return _enabled_value
    from services import assistant_settings_service
    try:
        _enabled_value = bool(assistant_settings_service.developer_mode_enabled()
                              and stored_settings.read("quick_read_keep_pages"))
    except Exception:
        _enabled_value = False
    _enabled_at = now
    return _enabled_value


def _plain(value):
    """What the read step produced as JSON, whatever the regions carry (arrays, tuples)."""
    return json.loads(json.dumps(value, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))


def note_read(bubbles) -> None:
    """Remembers the regions as the read step left them, before translation changes them; picked up
    by `take` on the same thread."""
    try:
        _local.read = _plain(bubbles)
    except Exception:
        _local.read = None


def take():
    read = getattr(_local, "read", None)
    _local.read = None
    return read


def _folder() -> str:
    path = os.path.join(db.LIBRARY_DIR, DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def save(page_key: str, original: bytes, content_type: str, read_regions, result: dict) -> None:
    """Queues the write on a background thread; a full queue drops the copy rather than slow a page."""
    global _writer_started
    entry = (page_key, original, content_type, read_regions,
             {k: result.get(k) for k in ("width", "height", "regions", "notes", "seconds", "untranslated_lines")})
    with _writer_lock:
        if not _writer_started:
            threading.Thread(target=_write_loop, daemon=True, name="quick-read-keep").start()
            _writer_started = True
    try:
        _writes.put_nowait(entry)
    except queue.Full:
        pass


def _write_loop() -> None:
    while True:
        entry = _writes.get()
        try:
            _write(*entry)
            _sweep()
        except Exception:
            get_logger().warning("Could not keep a Quick read page copy", exc_info=True)


def _write(page_key, original, content_type, read_regions, final) -> None:
    name = hashlib.sha256(f"{page_key}".encode()).hexdigest()[:24]
    folder = _folder()
    ext = {"image/png": "png", "image/webp": "webp"}.get(content_type, "jpg")
    with open(os.path.join(folder, f"{name}.{ext}"), "wb") as f:
        f.write(original)
    with open(os.path.join(folder, f"{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"page_key": page_key, "image": f"{name}.{ext}", "content_type": content_type,
                   "saved_at": time.time(), "read_regions": read_regions, **final}, f)


def _sweep() -> None:
    folder = _folder()
    now = time.time()
    pages = []
    for name in os.listdir(folder):
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder, name)
        pages.append((os.path.getmtime(path), name[:-5]))
    pages.sort(reverse=True)
    for index, (mtime, stem) in enumerate(pages):
        if index < MAX_PAGES and now - mtime <= MAX_AGE_SECONDS:
            continue
        for leftover in os.listdir(folder):
            if leftover.startswith(stem + "."):
                try:
                    os.remove(os.path.join(folder, leftover))
                except OSError:
                    pass
