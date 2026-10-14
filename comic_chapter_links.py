"""
comic_chapter_links.py -- the chapter links the browser extension saw on a
series' page, for titles whose site has no adapter.

Stored as `chapter_links.json` in the drama's folder beside `chapters.json`,
for the same reason: it travels with the title in backups, and a backup
without media simply drops it. Links are display-safe (scheme, host, path).
Reading never raises: a missing or damaged file reads as no links.

    {"version": 1, "links": [{"title", "url"}, ...]}
"""
import json
import os
import re
import threading

import core
import db
from translate_engines import display_url

FILE_NAME = "chapter_links.json"
MAX_LINKS = 500
MAX_TITLE = 120
MAX_FILE_BYTES = 512 * 1024
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_lock = threading.Lock()


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), FILE_NAME)


def clean(raw, host: str) -> list:
    """Display-safe {title, url} entries on `host` only, without repeats, in order."""
    from urllib.parse import urlsplit
    out, seen = [], set()
    for item in raw[:4 * MAX_LINKS] if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        url = display_url(item.get("url"))
        title = _CONTROL.sub(" ", str(item.get("title") or "")).strip()[:MAX_TITLE]
        if not url or url in seen or not title:
            continue
        try:
            same_host = (urlsplit(url).hostname or "").removeprefix("www.") == host
        except ValueError:
            same_host = False
        if same_host:
            seen.add(url)
            out.append({"title": title, "url": url})
        if len(out) >= MAX_LINKS:
            break
    return out


def load(drama_id: int) -> list:
    try:
        path = _path(drama_id)
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        links = data.get("links") if isinstance(data, dict) else None
        return [l for l in links if isinstance(l, dict) and l.get("url") and l.get("title")] \
            if isinstance(links, list) else []
    except (OSError, ValueError):
        return []


def remember(drama_id: int, links: list) -> None:
    """Keeps `links` (already `clean`ed). A page listing every stored link is the
    series' list and replaces it; any other page (a chapter page showing only
    previous/next) only adds links not yet known, so it cannot shrink the list."""
    if not links:
        return
    with _lock:
        stored = load(drama_id)
        if not {l["url"] for l in stored} <= {l["url"] for l in links}:
            known = {l["url"] for l in stored}
            links = (stored + [l for l in links if l["url"] not in known])[:MAX_LINKS]
        data = json.dumps({"version": 1, "links": links}, ensure_ascii=False, separators=(",", ":"))
        try:
            core.atomic_write(_path(drama_id), data)
        except OSError:
            pass
