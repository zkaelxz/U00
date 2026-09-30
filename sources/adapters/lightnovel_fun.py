"""
sources/adapters/lightnovel_fun.py -- 轻之国度 (www.lightnovel.fun), zh light
novels, roadmap Step 115 (reduced by user decision 2026-09-30: lightnovel.fun
only, for personal zh->en reading of the public /book and /reader pages).

Technique read directly from the live site while building this adapter
(2026-09-30); no reference scraper's code was ported:

  search        GET /search?keyword=<q>&page=<n>   server-rendered Nuxt page
  series        GET /book/<bookId>                 title/author/cover/tags/summary
                                                    and the volume catalog
  chapters      the same /book page's catalog, plus /reader pages for any
                volume the book page leaves unloaded (see below)
  chapter body  GET /reader/<bookId>/<chapterId>   currentChapter.contentHtml,
                                                    falling back to the
                                                    rendered div.reader-text

Every page is server-rendered by Nuxt: the visible HTML is there, and so is
the same data as a `<script id="__NUXT_DATA__">` JSON payload (Nuxt's
"devalue" format -- a flat array whose objects point at other entries by
index). The payload is read rather than the HTML because the HTML only
renders part of it (the book page's chapter grid shows 8 chapters behind an
"展开目录" button while the payload carries all 10). Reading it needs no
JavaScript and decodes nothing the site protects -- it is the page's own
state, sent to every visitor.

A book page loads only its first volume's chapter list; the other volumes
come back with `chaptersLoaded: false` and no chapters. A /reader page loads
the volume its own chapter belongs to and gives `prevChapterId` /
`nextChapterId`, so get_chapters() walks across volume boundaries: the
last chapter of a loaded volume -> its nextChapterId -> that chapter's
reader page, which carries the next volume's list. Two page loads per extra
volume, paced like every other request, and only ever the public pages.

Never touched, by user decision (2026-09-30): the login API
(`/api/pc-proxy/api/bff/auth-password-login-v1`), the paid 轻币 chapter
unlock (`.../new-content-read/unlock-chapter`), and any EPUB / pan.baidu.com
/ lanzou file-locker link a work's text may carry (the dl-raw.si precedent in
docs/known-working-sources.md). A locked chapter raises ContentHidden.

robots.txt (fetched directly 2026-09-30): `User-agent: *` disallows only
`/settings/` and `/publish_mgr/` -- neither path is used here. No
Crawl-delay, so the app's normal pacing applies.
"""

import logging
import re
from urllib.parse import quote, urljoin

from ..base import SourceAdapter
from ..models import (ChapterInfo, ContentAccess, ContentHidden, ContentType, FailureReason,
                      SearchResult, SeriesInfo, SourceError)
from ..registry import register

log = logging.getLogger(__name__)

BASE_URL = "https://www.lightnovel.fun"

# The per-work notices uploaders put on their releases (the site-wide rules
# page couldn't be found). Shown with the series details and in the
# source's terms notes so the person sees them before importing.
SITE_NOTICE = ("轻之国度 works carry the uploaders' own notices: 仅供个人学习交流使用，禁作商业用途 "
               "(personal study only, no commercial use); 禁止转载 (no reposting); "
               "禁止二改二传 (no re-editing or re-uploading).")

# devalue's tagged entries: ["Tag", ...] in place of a plain value.
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "NuxtError", "Island"}


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"轻之国度's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.UNKNOWN)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _devalue(arr):
    """Rebuilds the object graph from Nuxt's devalue array. Negative
    indices are devalue's constants (undefined, NaN, ...) -> None."""
    memo = {}

    def hydrate(i):
        if not isinstance(i, int) or i < 0 or i >= len(arr):
            return None
        if i in memo:
            return memo[i]
        memo[i] = None  # a cycle resolves to None instead of recursing forever
        v = arr[i]
        if isinstance(v, list) and v and isinstance(v[0], str):
            tag = v[0]
            if tag in _WRAPPERS:
                out = hydrate(v[1]) if len(v) > 1 else None
            elif tag in ("Date", "BigInt", "RegExp", "Object"):
                out = v[1] if len(v) > 1 else None
            elif tag == "Set":
                out = [hydrate(x) for x in v[1:]]
            elif tag in ("Map", "null"):
                pairs = v[1:]
                out = {str(hydrate(pairs[j]) if tag == "Map" else pairs[j]): hydrate(pairs[j + 1])
                       for j in range(0, len(pairs) - 1, 2)}
            else:
                out = None
        elif isinstance(v, list):
            out = [hydrate(x) for x in v]
        elif isinstance(v, dict):
            out = {k: hydrate(x) for k, x in v.items()}
        else:
            out = v
        memo[i] = out
        return out

    return hydrate(0)


def _payload(html: str) -> dict:
    """The page's `data` map from __NUXT_DATA__, or {} if it isn't there."""
    import json
    tag = _soup(html).find("script", id="__NUXT_DATA__")
    if tag is None or not tag.string:
        return {}
    try:
        root = _devalue(json.loads(tag.string))
    except (ValueError, TypeError):
        return {}
    data = (root or {}).get("data") if isinstance(root, dict) else None
    return data if isinstance(data, dict) else {}


def _entry(data: dict, prefix: str):
    for key, value in data.items():
        if key.startswith(prefix) and isinstance(value, dict):
            return value
    return None


def _html_text(fragment: str) -> str:
    soup = _soup(fragment)
    for tag in soup.find_all(["rt", "rp"]):  # ruby readings: keep the base text only
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    names = ["p", "h1", "h2", "h3", "h4", "li"]
    blocks = [b for b in soup.find_all(names) if not b.find_parent(names)]
    text = "\n".join(b.get_text("") for b in blocks) if blocks else soup.get_text("\n")
    return "\n".join(line.strip() for line in text.split("\n") if line.strip())


def _chapter_rows(volume: dict) -> list:
    """A catalog volume's chapter entries that carry a real numeric id."""
    return [c for c in (volume.get("chapters") or [])
            if isinstance(c, dict) and str(c.get("id") or "").isdigit()]


def _locked(chapter: dict) -> bool:
    if chapter.get("locked"):
        return True
    return chapter.get("accessType") not in (None, "public") and not chapter.get("unlocked")


@register
class LightnovelFunSource(SourceAdapter):
    name = "lightnovel_fun"
    display_name = "轻之国度 (lightnovel.fun)"
    content_types = [ContentType.NOVEL.value]
    languages = ["zh"]
    url_patterns = [r"lightnovel\.fun/(?:book|reader)/\d+"]
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL

    def _get(self, path: str, action: str, use_cache: bool = True) -> str:
        return self.client.get(urljoin(self.base_url, path), action=action,
                               use_cache=use_cache).text

    def _book(self, series_id: str) -> dict:
        html = self._get(f"/book/{series_id}", f"Loading series {series_id}", use_cache=False)
        detail = _entry(_payload(html), f"pc-book-detail-{series_id}")
        if not detail or not isinstance(detail.get("book"), dict):
            raise LayoutChanged("the book details")
        return detail

    def _reader(self, series_id: str, chapter_id: str, action: str, use_cache: bool = True):
        """(reader-bootstrap payload or None, raw html)."""
        html = self._get(f"/reader/{series_id}/{chapter_id}", action, use_cache=use_cache)
        return _entry(_payload(html), f"reader-bootstrap-{series_id}-{chapter_id}"), html

    # -- search ------------------------------------------------------------------
    def search(self, query: str, page: int = 1):
        html = self._get(f"/search?keyword={quote(query)}&page={int(page)}",
                         f"Searching 轻之国度 for {query!r}", use_cache=False)
        data = _payload(html)
        results = None
        for key, value in data.items():
            if '"keyword"' in key and isinstance(value, dict) and isinstance(value.get("items"), list):
                results = value["items"]
                break
        if results is None:
            raise LayoutChanged("the search results")
        out = []
        for item in results:
            if not isinstance(item, dict) or item.get("targetType", "book") != "book":
                continue
            book_id = str(item.get("bookId") or item.get("id") or "")
            title = (item.get("title") or "").strip()
            if not book_id.isdigit() or not title:
                continue
            extra = {k: item[k] for k in ("author", "status", "chapterCount") if item.get(k)}
            out.append(SearchResult(self.name, book_id, title, urljoin(self.base_url, f"/book/{book_id}"),
                                    item.get("cover") or "", extra=extra))
        return out

    # -- series ------------------------------------------------------------------
    def get_series(self, series_id: str):
        book = self._book(series_id)["book"]
        title = (book.get("title") or "").strip()
        if not title:
            raise LayoutChanged("the series title")
        authors = [a for a in (book.get("author"), book.get("illustrator")) if a]
        summary = (book.get("summary") or "").strip()
        status = book.get("status") or ""
        return SeriesInfo(
            self.name, series_id, title, urljoin(self.base_url, f"/book/{series_id}"),
            book.get("cover") or "", authors=authors,
            description=(summary + "\n\n" if summary else "") + SITE_NOTICE,
            genres=[t for t in (book.get("tags") or []) if isinstance(t, str)],
            status="completed" if "完结" in status else "ongoing" if "连载" in status else "unknown",
            content_type=ContentType.NOVEL.value, language="zh")

    # -- chapters ----------------------------------------------------------------
    def get_chapters(self, series_id: str):
        detail = self._book(series_id)
        catalog = [v for v in (detail.get("catalog") or []) if isinstance(v, dict)]
        if not catalog:
            raise LayoutChanged("the chapter list")
        volumes = {str(v.get("id")): v for v in catalog}
        loaded = {vid: _chapter_rows(v) for vid, v in volumes.items() if v.get("chaptersLoaded")}
        loaded = {vid: rows for vid, rows in loaded.items() if rows}
        order = [str(v.get("id")) for v in catalog]

        # Walk into each unloaded volume from a loaded neighbour. Every step
        # fills a volume or gives up on one, so this ends after at most one
        # pass per volume.
        failed = set()
        progress = True
        while progress:
            progress = False
            for i, vid in enumerate(order):
                if vid in loaded or vid in failed:
                    continue
                before = order[i - 1] if i > 0 else None
                after = order[i + 1] if i + 1 < len(order) else None
                if before in loaded:
                    got = self._load_volume(series_id, vid, loaded[before][-1], "nextChapterId")
                elif after in loaded:
                    got = self._load_volume(series_id, vid, loaded[after][0], "prevChapterId")
                else:
                    continue
                if got:
                    loaded[vid] = got
                else:
                    failed.add(vid)
                    log.warning("lightnovel_fun: couldn't load volume %s of book %s",
                                volumes[vid].get("title"), series_id)
                progress = True

        chapters = []
        for vid in order:
            group = (volumes[vid].get("title") or "").strip()
            for ch in loaded.get(vid, []):
                cid = str(ch["id"])
                chapters.append(ChapterInfo(
                    self.name, series_id, cid, (ch.get("title") or "").strip() or cid,
                    urljoin(self.base_url, f"/reader/{series_id}/{cid}"), group=group))
        if not chapters:
            raise LayoutChanged("any chapter in the chapter list")
        return chapters

    def _load_volume(self, series_id: str, volume_id: str, neighbour: dict, link: str):
        """The chapters of `volume_id`, reached from `neighbour` (the chapter
        next to it in an already-loaded volume) via its reader page's
        prev/next link. [] if the chain doesn't lead there."""
        nid = str(neighbour["id"])
        boot, _ = self._reader(series_id, nid, f"Loading the chapter list of book {series_id}",
                               use_cache=False)
        target = str(((boot or {}).get("currentChapter") or {}).get(link) or "")
        if not target.isdigit():
            return []
        boot, _ = self._reader(series_id, target, f"Loading the chapter list of book {series_id}",
                               use_cache=False)
        for vol in (boot or {}).get("catalog") or []:
            if isinstance(vol, dict) and str(vol.get("id")) == volume_id and vol.get("chaptersLoaded"):
                return _chapter_rows(vol)
        return []

    # -- chapter text ------------------------------------------------------------
    def get_chapter_text(self, chapter) -> str:
        boot, html = self._reader(chapter.series_id, chapter.chapter_id,
                                  f"Loading chapter {chapter.title}")
        current = (boot or {}).get("currentChapter") if boot else None
        if isinstance(current, dict):
            if _locked(current):
                price = current.get("coinPrice")
                cost = f" ({price} 轻币)" if price else ""
                raise ContentHidden(
                    f"\"{chapter.title}\" is a locked chapter on 轻之国度{cost}. The app never "
                    "signs in or spends 轻币 to unlock it -- read it on the site instead.",
                    FailureReason.PURCHASE_REQUIRED)
            content = current.get("contentHtml")
            if isinstance(content, str):
                # An illustrations-only chapter (彩页) has images and no text.
                return _html_text(content)
        container = _soup(html).select_one("div.reader-text")
        if container is None:
            raise LayoutChanged("the chapter text")
        return _html_text(str(container))

    # -- urls / capabilities -----------------------------------------------------
    def parse_url(self, url: str):
        m = re.search(r"lightnovel\.fun/reader/(\d+)/(\d+)", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2),
                                           urljoin(self.base_url, f"/reader/{m.group(1)}/{m.group(2)}")))
        m = re.search(r"lightnovel\.fun/book/(\d+)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered Nuxt pages; reads the page's own "
                                 "__NUXT_DATA__ JSON (no JavaScript run, nothing decrypted)",
            "browser_required": False,
            "volumes": "A book page loads only its first volume's chapter list; the others "
                       "are reached through the public /reader pages' prev/next links "
                       "(two page loads per extra volume).",
            "never_used": "The login API, the paid 轻币 chapter unlock, and EPUB / "
                          "pan.baidu.com / lanzou file-locker links. Locked chapters are "
                          "reported, not unlocked.",
            "challenge": "No Cloudflare challenge seen on lightnovel.fun (2026-09-30). One was "
                         "reported on lightnovel.us in 2023; if one appears it is a wall, "
                         "handed to the person, never solved.",
        }
        caps.terms = {
            "robots_txt": "User-agent: * disallows only /settings/ and /publish_mgr/ (fetched "
                          "directly 2026-09-30). AhrefsBot, DotBot, MJ12bot and SemrushBot "
                          "are disallowed entirely.",
            "tos": "The site rules page (LK站规) couldn't be found: its footer links have no "
                   "target and /site_rule returns 404. Automation permission stays UNKNOWN.",
            "notices": SITE_NOTICE,
            "tos_prohibited": False,
        }
        return caps
