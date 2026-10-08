"""
comic_chapters.py -- which chapter each comic page belongs to, and which
pages the reader has marked "not part of the story".

Stored as `chapters.json` in the drama's own folder, keyed by the page's
stored filename (e.g. "pages/page_0007.png"), not by page id. Why a file and
not a table: the frozen db.py cannot grow, and the drama folder already
travels with the title in media backups, restores and the disk-usage scan,
while a filename survives a restore that renumbers page ids. A backup without
media drops the file; those pages then read as "Chapter unknown", which is
also what every title imported before this file existed shows.

    {"version": 1,
     "chapters": [{"key", "id", "title", "host", "files": [filename, ...]}],
     "hidden": [filename, ...]}

Reading never raises: a missing, truncated or hand-edited file reads as "no
chapter data". Pages are the authority for what exists; entries naming a page
that is gone are ignored.
"""
import json
import os
import re
import threading
import time

import core
import db
from translate_engines import redact_for_storage

MANIFEST_NAME = "chapters.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# Below the read cap so the app can never write a file it then refuses to read.
MAX_WRITE_BYTES = 3 * 1024 * 1024
# Windows: os.replace fails with PermissionError while another handle (a
# backup, an antivirus scan, a reader) has the file open for a moment.
_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY = 0.05
MAX_TITLE = 200
UNKNOWN_ID = "unknown"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WIN_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>]*|\\\\[^\s\"'<>]+")
_locks = {}
_locks_guard = threading.Lock()


class ManifestUnreadable(OSError):
    """The manifest exists but could not be read, or cannot be written within
    its size cap. An OSError so page writers that already treat a label write
    as best-effort keep the pages and carry on."""


def _lock(drama_id: int) -> threading.RLock:
    # Reentrant: the writers hold it across their own load().
    with _locks_guard:
        return _locks.setdefault(int(drama_id), threading.RLock())


def _text(value, limit=MAX_TITLE) -> str:
    return _CONTROL.sub(" ", str(value or "")).strip()[:limit]


def _title(value) -> str:
    """Titles come from the source site and are returned by the pages list, so
    they get the same treatment as other fetched text: secrets and URL queries
    removed, and filesystem paths too."""
    text = _WIN_PATH.sub("[path]", redact_for_storage(str(value or "")))
    return _text(text)


def chapter_ref(chapter_id=None, title=None, source=None):
    """The label a page writer passes for the chapter it is adding, or None
    when it has neither an id nor a title (the pages then stay unlabelled)."""
    cid, name = _text(chapter_id, 100), _title(title)
    if not cid and not name:
        return None
    src = _text(source, 40)
    return {"key": f"{src}:{cid}" if cid else f"title:{name}",
            "id": cid, "title": name}


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), MANIFEST_NAME)


def _read(drama_id: int):
    """(manifest, readable). A missing file is readable and empty; one that
    exists but cannot be used is empty and not readable."""
    empty = {"version": 1, "chapters": [], "hidden": []}
    path = _path(drama_id)
    if not os.path.exists(path):
        return empty, True
    try:
        if os.path.getsize(path) > MAX_MANIFEST_BYTES:
            return empty, False
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return empty, False
    if not isinstance(raw, dict):
        return empty, False
    chapters = []
    for c in raw.get("chapters") if isinstance(raw.get("chapters"), list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("files"), list):
            continue
        chapters.append({"key": _text(c.get("key"), 160), "id": _text(c.get("id"), 100),
                         "title": _text(c.get("title")),
                         "files": [f for f in c["files"] if isinstance(f, str)]})
    hidden = raw.get("hidden")
    return {"version": 1, "chapters": chapters,
            "hidden": [f for f in hidden if isinstance(f, str)] if isinstance(hidden, list) else []}, True


def load(drama_id: int) -> dict:
    # Under the lock: on Windows a read that overlaps a writer's os.replace
    # makes the replace fail.
    with _lock(drama_id):
        return _read(drama_id)[0]


def _load_for_write(drama_id: int) -> dict:
    manifest, readable = _read(drama_id)
    if not readable:
        raise ManifestUnreadable("The chapter data file could not be read; it was left as it is.")
    return manifest


def _serialise(manifest: dict) -> str:
    """Compact JSON within MAX_WRITE_BYTES. When a long series outgrows it the
    oldest chapters' labels are dropped (their pages read as "Chapter
    unknown"); hidden flags are kept, and a manifest that still does not fit
    is refused rather than written past what load() will read."""
    def dumps(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    data = dumps(manifest)
    excess = len(data.encode("utf-8")) - MAX_WRITE_BYTES
    if excess > 0:
        chapters, drop = manifest["chapters"], 0
        while excess > 0 and drop < len(chapters):
            excess -= len(dumps(chapters[drop]).encode("utf-8")) + 1
            drop += 1
        data = dumps({**manifest, "chapters": chapters[drop:]})
        if len(data.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ManifestUnreadable("The chapter data is too large to store.")
    return data


def _save(drama_id: int, manifest: dict) -> None:
    data = _serialise(manifest)
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            core.atomic_write(_path(drama_id), data)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY * (attempt + 1))


def record_pages(drama_id: int, ref: dict, filenames) -> None:
    """Appends `filenames` (in page order) to the chapter `ref`, creating it
    on first sight. Called by the page writer after the rows exist."""
    if not ref or not filenames:
        return
    with _lock(drama_id):
        manifest = _load_for_write(drama_id)
        entry = next((c for c in manifest["chapters"] if c["key"] == ref["key"]), None)
        if entry is None:
            entry = {**{k: ref[k] for k in ("key", "id", "title")}, "files": []}
            manifest["chapters"].append(entry)
        entry["files"].extend(f for f in filenames if f not in entry["files"])
        _save(drama_id, manifest)


def forget_pages(drama_id: int, filenames) -> None:
    """Drops removed pages. A page index can be reused after a rolled-back
    import, and a stale entry would label the next page with the wrong chapter."""
    gone = set(filenames)
    with _lock(drama_id):
        manifest = _load_for_write(drama_id)
        for c in manifest["chapters"]:
            c["files"] = [f for f in c["files"] if f not in gone]
        manifest["chapters"] = [c for c in manifest["chapters"] if c["files"]]
        manifest["hidden"] = [f for f in manifest["hidden"] if f not in gone]
        _save(drama_id, manifest)


def set_hidden(drama_id: int, filenames, hidden: bool) -> None:
    with _lock(drama_id):
        manifest = _load_for_write(drama_id)
        current = set(manifest["hidden"])
        current = current | set(filenames) if hidden else current - set(filenames)
        manifest["hidden"] = sorted(current)
        _save(drama_id, manifest)


def group_pages(pages, manifest: dict) -> list:
    """Reading-order chapter groups for `pages` (db.list_pages rows, in
    idx order). A group is a run of adjacent pages with the same chapter, so a
    chapter's `first_page` is always the ordinal of a real page. Pages the
    manifest does not name form an "unknown" group. Each group:
    {id, title, known, first_page (1-based ordinal), page_count,
    hidden_count, filenames}."""
    owner = {f: c for c in manifest["chapters"] for f in c["files"]}
    hidden = set(manifest["hidden"])
    groups, seen = [], {}
    for ordinal, page in enumerate(pages, start=1):
        c = owner.get(page.get("filename"))
        key = c["key"] if c else UNKNOWN_ID
        if not groups or groups[-1]["_key"] != key:
            seen[key] = seen.get(key, 0) + 1
            gid = key if seen[key] == 1 else f"{key}~{seen[key]}"
            groups.append({"_key": key, "id": gid, "known": c is not None,
                           "title": c["title"] if c and c["title"] else "",
                           "chapter_id": c["id"] if c else "",
                           "first_page": ordinal,
                           "page_count": 0, "hidden_count": 0, "filenames": []})
        g = groups[-1]
        g["page_count"] += 1
        g["filenames"].append(page.get("filename"))
        if page.get("filename") in hidden:
            g["hidden_count"] += 1
    for g in groups:
        del g["_key"]
    return groups
