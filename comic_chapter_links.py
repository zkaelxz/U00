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
import threading

import core
import db
from comic_chapters import MAX_URL, _title, _url

FILE_NAME = "chapter_links.json"
MAX_LINKS = 500
MAX_TITLE = 120
MAX_FILE_BYTES = 512 * 1024
# Reentrant: remember() holds it across its own load().
_lock = threading.RLock()


def _path(drama_id: int) -> str:
    return os.path.join(db.drama_dir(drama_id), FILE_NAME)


def clean(raw, host=None) -> list:
    """Display-safe {title, url} entries, without repeats, in order. Titles get the
    treatment of other fetched text, and a link over MAX_URL is dropped. With `host`,
    only links on that site are kept (a link elsewhere is not a chapter of it)."""
    from urllib.parse import urlsplit
    out, seen = [], set()
    for item in raw[:4 * MAX_LINKS] if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        link = item.get("url")
        url = _url(link) if isinstance(link, str) and len(link) <= MAX_URL else ""
        # display_url cuts a path that looks like a token down to the bare host, which is no chapter.
        if urlsplit(url).path in ("", "/"):
            continue
        title = _title(item.get("title"))[:MAX_TITLE]
        if not url or url in seen or not title:
            continue
        if host is not None:
            try:
                if (urlsplit(url).hostname or "").removeprefix("www.") != host:
                    continue
            except ValueError:
                continue
        seen.add(url)
        out.append({"title": title, "url": url})
        if len(out) >= MAX_LINKS:
            break
    return out


def _dumps(links: list) -> str:
    return json.dumps({"version": 1, "links": links}, ensure_ascii=False, separators=(",", ":"))


def load(drama_id: int) -> list:
    """The stored links, cleaned again on the way out: the file is plain text in the
    drama's folder, so what it holds is not trusted to be what `clean` wrote."""
    with _lock:
        try:
            path = _path(drama_id)
            if os.path.getsize(path) > MAX_FILE_BYTES:
                return []
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
    return clean(data.get("links") if isinstance(data, dict) else None)


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
        # Never write a file load() would refuse to read back: drop the tail instead.
        while links and len(_dumps(links).encode("utf-8")) > MAX_FILE_BYTES:
            links = links[:len(links) // 2 or 1] if len(links) > 1 else []
        if not links:
            return
        try:
            core.atomic_write(_path(drama_id), _dumps(links))
        except OSError:
            pass
