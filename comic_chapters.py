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
     "chapters": [{"key", "id", "title", "url", "files": [filename, ...],
                   "sources": {source_key: filename}}],
     "hidden": [filename, ...]}

`url` is optional (the chapter's page on its source, display-safe); entries
written before it existed read as having none. `sources` (optional) maps each
source picture already imported into the chapter to the first page written
from it, so importing the chapter again adds only the pictures still missing.

Reading never raises: a missing, truncated or hand-edited file reads as "no
chapter data". Pages are the authority for what exists; entries naming a page
that is gone are ignored.
"""
import hashlib
import json
import os
import re
import threading
import time
import uuid

import core
import db
from engine_backends.shared import display_url, redact_for_storage

MANIFEST_NAME = "chapters.json"
# The last good copy, read when the main file is unreadable (a power cut
# mid-save, a bad sector). Written before the main file on every save, so at
# least one of the two always holds a complete manifest.
BACKUP_NAME = "chapters.json.bak"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# Below the read cap so the app can never write a file it then refuses to read.
MAX_WRITE_BYTES = 3 * 1024 * 1024
# Windows: os.replace fails with PermissionError while another handle (a
# backup, an antivirus scan, a reader) has the file open for a moment.
_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY = 0.05
MAX_TITLE = 200
MAX_URL = 2000
SOURCE_KEY_LEN = 16
UNKNOWN_ID = "unknown"
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
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
    return CONTROL_CHARS.sub(" ", str(value or "")).strip()[:limit]


def _title(value) -> str:
    """Titles come from the source site and are returned by the pages list, so
    they get the same treatment as other fetched text: secrets and URL queries
    removed, and filesystem paths too."""
    text = _WIN_PATH.sub("[path]", redact_for_storage(str(value or "")))
    return _text(text)


def _url(value) -> str:
    link = display_url(value)
    return link if len(link) <= MAX_URL else ""


def chapter_ref(chapter_id=None, title=None, source=None, url=None):
    """The label a page writer passes for the chapter it is adding, or None
    when it has neither an id nor a title (the pages then stay unlabelled).
    `url` is the chapter's page on its source; kept only in display-safe form.
    `scope` is a hash of the full link, never stored: chapters told apart only
    by query or fragment share a display link, and often a title-based key."""
    cid, name = _text(chapter_id, 100), _title(title)
    if not cid and not name:
        return None
    src = _text(source, 40)
    return {"key": f"{src}:{cid}" if cid else f"title:{name}",
            "id": cid, "title": name, "url": _url(url), "scope": _digest(url) if url else ""}


def _digest(text) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:SOURCE_KEY_LEN]


def source_key(ref, order, url):
    """Names one source picture of the chapter `ref` from the chapter's full
    link, the picture's position and its full address, so a picture of one
    chapter can never match another's. A link with a rotating token matches
    nothing and the picture is added again: a duplicate, never a lost page.
    None without a chapter link or picture address."""
    if not ref or not ref.get("scope") or not url:
        return None
    return _digest(f"{ref['scope']}|{order}|{url}")


def imported_sources(drama_id: int, ref) -> set:
    """The source keys of chapter `ref` whose page is still in it. A page a
    rolled-back import dropped is no longer listed, so its picture counts as missing."""
    if not ref:
        return set()
    entry = next((c for c in load(drama_id)["chapters"] if c["key"] == ref["key"]), None)
    if entry is None:
        return set()
    files = set(entry["files"])
    return {k for k, f in entry.get("sources", {}).items() if f in files}


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), MANIFEST_NAME)


def _backup_path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), BACKUP_NAME)


def _read(drama_id: int):
    """(manifest, readable). A missing file is readable and empty; one that
    exists but cannot be used falls back to the last good copy, and is empty
    and not readable when that cannot be used either."""
    manifest, readable = _read_file(_path(drama_id))
    if readable:
        return manifest, True
    backup, backup_readable = _read_file(_backup_path(drama_id))
    if backup_readable and os.path.exists(_backup_path(drama_id)):
        return backup, True
    return manifest, False


def _read_file(path: str):
    empty = {"version": 1, "chapters": [], "hidden": []}
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
    listed = raw.get("chapters")
    for c in listed if isinstance(listed, list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("files"), list):
            continue
        entry = {"key": _text(c.get("key"), 160), "id": _text(c.get("id"), 100),
                 "title": _text(c.get("title")), "url": _url(c.get("url")),
                 "files": [f for f in c["files"] if isinstance(f, str)]}
        sources = c.get("sources")
        if isinstance(sources, dict):
            entry["sources"] = {k: f for k, f in sources.items()
                                if isinstance(k, str) and len(k) == SOURCE_KEY_LEN
                                and isinstance(f, str)}
        chapters.append(entry)
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
            core.atomic_write(_backup_path(drama_id), data)
            core.atomic_write(_path(drama_id), data)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY * (attempt + 1))


def record_pages(drama_id: int, ref: dict, filenames) -> None:
    """Appends `filenames` (in page order) to the chapter `ref`, creating it
    on first sight. Called by the page writer after the rows exist. A
    `ref["source"]` (source_key) records the source picture they came from."""
    if not ref or not filenames:
        return
    with _lock(drama_id):
        manifest = _load_for_write(drama_id)
        entry = next((c for c in manifest["chapters"] if c["key"] == ref["key"]), None)
        if entry is None:
            entry = {**{k: ref[k] for k in ("key", "id", "title")}, "files": []}
            manifest["chapters"].append(entry)
        if ref.get("url") and not entry.get("url"):
            entry["url"] = ref["url"]
        entry["files"].extend(f for f in filenames if f not in entry["files"])
        if ref.get("source"):
            entry.setdefault("sources", {})[ref["source"]] = filenames[0]
        _save(drama_id, manifest)


def _drop_files(chapter: dict, gone) -> None:
    """Takes `gone` out of the chapter, its source record included: page names
    are reused, and a record left on a reused name would pass over a new picture."""
    chapter["files"] = [f for f in chapter["files"] if f not in gone]
    if "sources" in chapter:
        chapter["sources"] = {k: f for k, f in chapter["sources"].items() if f not in gone}


def start_chapter(drama_id: int, filenames, title: str) -> None:
    """Files `filenames` under a new chapter. They leave any chapter they were in, so
    cutting a long run in two never leaves a page in both."""
    moved = set(filenames)
    if not moved:
        return
    with _lock(drama_id):
        manifest = _load_for_write(drama_id)
        for c in manifest["chapters"]:
            _drop_files(c, moved)
        manifest["chapters"] = [c for c in manifest["chapters"] if c["files"]]
        manifest["chapters"].append({"key": f"split:{uuid.uuid4().hex[:8]}", "id": "",
                                     "title": _title(title), "url": "", "files": list(filenames)})
        _save(drama_id, manifest)


def forget_pages(drama_id: int, filenames) -> None:
    """Drops removed pages. A page index can be reused after a rolled-back
    import, and a stale entry would label the next page with the wrong chapter."""
    gone = set(filenames)
    with _lock(drama_id):
        manifest = _load_for_write(drama_id)
        for c in manifest["chapters"]:
            _drop_files(c, gone)
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


def load_checked(drama_id: int) -> tuple:
    """(manifest, readable), so a caller about to move pages can refuse up
    front instead of finding out halfway."""
    with _lock(drama_id):
        return _read(drama_id)


def move_labels(source_id: int, target_id: int, renames: dict, keep_both: bool) -> None:
    """Carries the labels and hidden flags of pages moved to another drama:
    `renames` maps each moved page's filename in the source to its filename
    in the target. The target is written first, so a failure in between
    leaves a label on both sides rather than on neither. With `keep_both`,
    a chapter the target already has stays a separate chapter there (a key
    of its own) instead of joining the existing one."""
    first, second = sorted((int(source_id), int(target_id)))
    with _lock(first), _lock(second):
        src = _load_for_write(source_id)
        dst = _load_for_write(target_id)
        had = {c["key"] for c in dst["chapters"]}
        keys = set(had)
        new_key = {}
        for c in src["chapters"]:
            moved = [renames[f] for f in c["files"] if f in renames]
            if not moved:
                continue
            key = c["key"]
            if keep_both and key in had:
                if key not in new_key:
                    n = 2
                    while f"{key}#{n}" in keys:
                        n += 1
                    new_key[key] = f"{key}#{n}"
                    keys.add(new_key[key])
                key = new_key[key]
            entry = next((e for e in dst["chapters"] if e["key"] == key), None)
            if entry is None:
                entry = {"key": key, "id": c["id"], "title": c["title"], "url": c.get("url", ""),
                         "files": []}
                dst["chapters"].append(entry)
            entry["files"].extend(f for f in moved if f not in entry["files"])
        dst["hidden"] = sorted(set(dst["hidden"]) | {renames[f] for f in src["hidden"]
                                                       if f in renames})
        _save(target_id, dst)
        for c in src["chapters"]:
            _drop_files(c, renames)
        src["chapters"] = [c for c in src["chapters"] if c["files"]]
        src["hidden"] = [f for f in src["hidden"] if f not in renames]
        _save(source_id, src)


def group_pages(pages, manifest: dict) -> list:
    """Reading-order chapter groups for `pages` (db.list_pages rows, in
    idx order). A group is a run of adjacent pages with the same chapter, so a
    chapter's `first_page` is always the ordinal of a real page. Pages the
    manifest does not name form an "unknown" group. Each group:
    {id, title, known, first_page (1-based ordinal), page_count,
    hidden_count, filenames, url}."""
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
                           "url": c.get("url", "") if c else "",
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
