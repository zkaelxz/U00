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
from urllib.parse import urlparse

import core
import db

MANIFEST_NAME = "chapters.json"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_TITLE = 200
UNKNOWN_ID = "unknown"
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_locks = {}
_locks_guard = threading.Lock()


def _lock(drama_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(int(drama_id), threading.Lock())


def _text(value, limit=MAX_TITLE) -> str:
    return _CONTROL.sub(" ", str(value or "")).strip()[:limit]


def _host(url) -> str:
    """Host only: a chapter URL can carry a token or a path to a private page."""
    try:
        return (urlparse(str(url or "")).hostname or "")[:100]
    except ValueError:
        return ""


def chapter_ref(chapter_id=None, title=None, url=None, source=None):
    """The label a page writer passes for the chapter it is adding, or None
    when it has neither an id nor a title (the pages then stay unlabelled).
    `url` is reduced to its host. Browser-extension captures send the same
    three fields."""
    cid, name = _text(chapter_id, 100), _text(title)
    if not cid and not name:
        return None
    src = _text(source, 40)
    return {"key": f"{src}:{cid}" if cid else f"title:{name}",
            "id": cid, "title": name, "host": _host(url)}


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), MANIFEST_NAME)


def load(drama_id: int) -> dict:
    empty = {"version": 1, "chapters": [], "hidden": []}
    try:
        path = _path(drama_id)
        if os.path.getsize(path) > MAX_MANIFEST_BYTES:
            return empty
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return empty
    if not isinstance(raw, dict):
        return empty
    chapters = []
    for c in raw.get("chapters") if isinstance(raw.get("chapters"), list) else []:
        if not isinstance(c, dict) or not isinstance(c.get("files"), list):
            continue
        chapters.append({"key": _text(c.get("key"), 160), "id": _text(c.get("id"), 100),
                         "title": _text(c.get("title")), "host": _text(c.get("host"), 100),
                         "files": [f for f in c["files"] if isinstance(f, str)]})
    hidden = raw.get("hidden")
    return {"version": 1, "chapters": chapters,
            "hidden": [f for f in hidden if isinstance(f, str)] if isinstance(hidden, list) else []}


def _save(drama_id: int, manifest: dict) -> None:
    core.atomic_write(_path(drama_id), json.dumps(manifest, ensure_ascii=False))


def record_pages(drama_id: int, ref: dict, filenames) -> None:
    """Appends `filenames` (in page order) to the chapter `ref`, creating it
    on first sight. Called by the page writer after the rows exist."""
    if not ref or not filenames:
        return
    with _lock(drama_id):
        manifest = load(drama_id)
        entry = next((c for c in manifest["chapters"] if c["key"] == ref["key"]), None)
        if entry is None:
            entry = {**{k: ref[k] for k in ("key", "id", "title", "host")}, "files": []}
            manifest["chapters"].append(entry)
        entry["files"].extend(f for f in filenames if f not in entry["files"])
        _save(drama_id, manifest)


def forget_pages(drama_id: int, filenames) -> None:
    """Drops removed pages. A page index can be reused after a rolled-back
    import, and a stale entry would label the next page with the wrong chapter."""
    gone = set(filenames)
    with _lock(drama_id):
        manifest = load(drama_id)
        for c in manifest["chapters"]:
            c["files"] = [f for f in c["files"] if f not in gone]
        manifest["chapters"] = [c for c in manifest["chapters"] if c["files"]]
        manifest["hidden"] = [f for f in manifest["hidden"] if f not in gone]
        _save(drama_id, manifest)


def set_hidden(drama_id: int, filenames, hidden: bool) -> None:
    with _lock(drama_id):
        manifest = load(drama_id)
        current = set(manifest["hidden"])
        current = current | set(filenames) if hidden else current - set(filenames)
        manifest["hidden"] = sorted(current)
        _save(drama_id, manifest)


def group_pages(pages, manifest: dict) -> list:
    """Reading-order chapter groups for `pages` (db.list_pages rows, in
    idx order). A group is a run of adjacent pages with the same chapter, so a
    chapter's `first_page` is always the ordinal of a real page. Pages the
    manifest does not name form an "unknown" group. Each group:
    {id, title, known, host, first_page (1-based ordinal), page_count,
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
                           "host": c["host"] if c else "", "first_page": ordinal,
                           "page_count": 0, "hidden_count": 0, "filenames": []})
        g = groups[-1]
        g["page_count"] += 1
        g["filenames"].append(page.get("filename"))
        if page.get("filename") in hidden:
            g["hidden_count"] += 1
    for g in groups:
        del g["_key"]
    return groups
