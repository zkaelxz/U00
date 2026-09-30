"""
sources/adapters/lightnovel_fun.py -- 轻之国度 (www.lightnovel.fun), zh light
novels, roadmap Step 115 (reduced by user decision 2026-09-30: lightnovel.fun
only, for personal zh->en reading of the public /book and /reader pages).

Technique read directly from the live site while building this adapter
(2026-09-30); no reference scraper's code was ported:

  search        GET /search?keyword=<q>&page=<n>   server-rendered Nuxt page
  series        GET /book/<bookId>                 title/author/cover/tags/summary
                                                    and the volume catalog, plus
                                                    the first public chapter's
                                                    reader page for the work's
                                                    own notice and any posted
                                                    download links
  chapters      the same /book page's catalog, plus /reader pages for any
                volume the book page leaves unloaded (see below)
  chapter body  GET /reader/<bookId>/<chapterId>   currentChapter.contentHtml
                                                    (refused without it: the
                                                    lock flag lives there)

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
docs/known-working-sources.md). A locked chapter raises ContentHidden. Such
links (and their 提取码) are listed in SeriesInfo.links for the person to open
themselves (user decision, 2026-09-30); nothing here ever requests them.

robots.txt (fetched directly 2026-09-30): `User-agent: *` disallows only
`/settings/` and `/publish_mgr/` -- neither path is used here. No
Crawl-delay, so the app's normal pacing applies.
"""

import logging
import re
from urllib.parse import quote, urljoin

from ..base import SourceAdapter
from ..models import (ChapterInfo, ContentAccess, ContentHidden, ContentType, FailureReason,
                      FetchFailed, SearchResult, SeriesInfo, SourceError)
from ..registry import register

log = logging.getLogger(__name__)

BASE_URL = "https://www.lightnovel.fun"

# The per-work notices uploaders put on their releases (the site-wide rules
# page couldn't be found). Shown with the series details and in the
# source's terms notes so the person sees them before importing.
SITE_NOTICE = ("轻之国度 works carry the uploaders' own notices: 仅供个人学习交流使用，禁作商业用途 "
               "(personal study only, no commercial use); 禁止转载 (no reposting); "
               "禁止二改二传 (no re-editing or re-uploading).")

# ASCII digits only: str.isdigit() and \d also accept e.g. Arabic-Indic digits.
_DIGITS = re.compile(r"[0-9]+")
# Lines of a work's own uploader notice (in its summary or first chapter).
# Phrases only, not bare words like 仅供 / 二改, so a story line isn't taken
# for a notice.
_NOTICE_RE = re.compile(r"仅供个人|禁作商业|禁做商业|禁止转载|请勿转载|转载请保留|转载时请保留|"
                        r"转发时请保留|禁止二改|禁止二传|24小时内删除")
# ASCII URL characters only, so text run straight on after a link (…/b0188mxnyb密码:be3j)
# isn't swallowed into it; trailing punctuation is stripped in _download_links.
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+")
# An extraction code of 3-8 characters, not a longer word cut short, and not
# an archive's 解压密码.
_CODE_RE = re.compile(r"(?<!解压)(?:提取码|密码|访问码)\s*[：:]\s*([A-Za-z0-9]{3,8})(?![A-Za-z0-9])")
_LANZOU_RE = re.compile(r"(?:[a-z0-9-]+\.)*lanzou[a-z]?\.com")
# File lockers uploaders post EPUBs to. Listed for the person, never fetched.
_LOCKERS = {"pan.baidu.com": "百度网盘 (Baidu Pan)", "123pan.com": "123云盘",
            "www.123pan.com": "123云盘", "www.aliyundrive.com": "阿里云盘",
            "www.alipan.com": "阿里云盘", "pan.quark.cn": "夸克网盘"}

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
                out = {}
                for j in range(0, len(pairs) - 1, 2):
                    key = hydrate(pairs[j]) if tag == "Map" else pairs[j]
                    if isinstance(key, (str, int, float)):  # never str() a shared subgraph
                        out[str(key)] = hydrate(pairs[j + 1])
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
    except (ValueError, TypeError, RecursionError):
        return {}
    data = (root or {}).get("data") if isinstance(root, dict) else None
    return data if isinstance(data, dict) else {}


def _text(value) -> str:
    """A payload string field, or "" for anything else. Fields are read
    through this (and _id) rather than str(): the decoded graph can share
    nodes, and str() of a hostile shared subgraph grows exponentially."""
    return value.strip() if isinstance(value, str) else ""


def _id(value) -> str:
    """A numeric payload id as a string, or "" if it isn't one."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value)
    return value if _DIGITS.fullmatch(value) else ""


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _entry(data: dict, name: str):
    """The payload entry keyed `name` (optionally with a suffix such as
    "-public") -- never a longer id that merely starts with the same digits."""
    for key, value in data.items():
        if (key == name or key.startswith(name + "-")) and isinstance(value, dict):
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
    return [c for c in _list(volume.get("chapters")) if isinstance(c, dict) and _id(c.get("id"))]


def _split(url: str):
    """urlsplit, or None: it raises ValueError on some malformed hosts."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(url)
        parts.hostname, parts.port  # noqa: B018 -- both can raise too
    except ValueError:
        return None
    return parts


def _locker_label(url: str) -> str:
    parts = _split(url)
    host = ((parts.hostname if parts else "") or "").lower()
    if host in _LOCKERS:
        return _LOCKERS[host]
    if _LANZOU_RE.fullmatch(host):
        return "蓝奏云 (Lanzou)"
    return ""


def _download_links(lines) -> list:
    """File-locker links in `lines` with their extraction code: the URL's own
    pwd= value, else a 提取码/密码 on the same line or the next two."""
    from urllib.parse import parse_qs
    lines = list(lines)
    out, seen = [], {}
    for i, line in enumerate(lines):
        for m in _URL_RE.finditer(line):
            url = m.group(0).rstrip(".,;:!?")
            label = _locker_label(url)
            parts = _split(url)
            if not label or parts is None:
                continue
            password = (parse_qs(parts.query).get("pwd") or [""])[0]
            if not password:
                for later in [line[m.start() + len(url):]] + lines[i + 1:i + 3]:
                    code = _CODE_RE.search(later)
                    if code:
                        password = code.group(1)
                        break
                    if _URL_RE.search(later):
                        break
            # One row per link as shown (the service drops the query string).
            key = f"{parts.scheme}://{(parts.hostname or '').lower()}{parts.path}"
            if key in seen:
                if password and not seen[key]["password"]:
                    seen[key]["password"] = password
                continue
            seen[key] = {"label": label, "url": url, "password": password}
            out.append(seen[key])
    return out[:10]


def _notice_lines(lines) -> list:
    out = []
    for line in lines:
        line = line.strip()
        if _NOTICE_RE.search(line) and line not in out and len(line) <= 200:
            out.append(line)
    return out[:6]


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
    url_patterns = [r"lightnovel\.fun/(?:book|reader)/[0-9]+"]
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL

    def _get(self, path: str, action: str, use_cache: bool = True) -> str:
        return self.client.get(urljoin(self.base_url, path), action=action,
                               use_cache=use_cache).text

    @staticmethod
    def _check_id(value, what: str) -> str:
        value = str(value or "")
        if not _DIGITS.fullmatch(value):
            raise SourceError(f"{value!r} isn't a 轻之国度 {what} id (they are all digits).",
                              FailureReason.UNKNOWN)
        return value

    def _book(self, series_id: str) -> dict:
        series_id = self._check_id(series_id, "book")
        html = self._get(f"/book/{series_id}", f"Loading series {series_id}", use_cache=False)
        detail = _entry(_payload(html), f"pc-book-detail-{series_id}")
        if not detail or not isinstance(detail.get("book"), dict):
            raise LayoutChanged("the book details")
        return detail

    def _reader(self, series_id: str, chapter_id: str, action: str, use_cache: bool = True):
        """The page's reader-bootstrap payload, or None."""
        series_id = self._check_id(series_id, "book")
        chapter_id = self._check_id(chapter_id, "chapter")
        html = self._get(f"/reader/{series_id}/{chapter_id}", action, use_cache=use_cache)
        return _entry(_payload(html), f"reader-bootstrap-{series_id}-{chapter_id}")

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
            book_id = _id(item.get("bookId")) or _id(item.get("id"))
            title = _text(item.get("title"))
            if not book_id or not title:
                continue
            extra = {k: item[k] for k in ("author", "status", "chapterCount")
                     if isinstance(item.get(k), (str, int)) and not isinstance(item.get(k), bool)
                     and item.get(k)}
            out.append(SearchResult(self.name, book_id, title, urljoin(self.base_url, f"/book/{book_id}"),
                                    _text(item.get("cover")), extra=extra))
        return out

    # -- series ------------------------------------------------------------------
    def get_series(self, series_id: str):
        detail = self._book(series_id)
        book = detail["book"]
        title = _text(book.get("title"))
        if not title:
            raise LayoutChanged("the series title")
        authors = [a for a in (_text(book.get("author")), _text(book.get("illustrator"))) if a]
        summary = _text(book.get("summary"))
        status = _text(book.get("status"))
        # The work's own first chapter (制作信息, or an EPUB work's resource
        # post) usually carries the uploader's notice and any download links.
        lines = summary.split("\n") + self._first_chapter_lines(series_id, detail)
        notice = _notice_lines(lines)
        notice_text = ("Uploader's notice: " + " / ".join(notice)) if notice else SITE_NOTICE
        return SeriesInfo(
            self.name, series_id, title, urljoin(self.base_url, f"/book/{series_id}"),
            _text(book.get("cover")), authors=authors,
            description=notice_text + ("\n\n" + summary if summary else ""),
            links=_download_links(lines),
            genres=[t for t in _list(book.get("tags")) if isinstance(t, str)],
            status="completed" if "完结" in status else "ongoing" if "连载" in status else "unknown",
            content_type=ContentType.NOVEL.value, language="zh")

    def _first_chapter_lines(self, series_id: str, detail: dict) -> list:
        """Text lines of the book's first public chapter, [] if it is locked,
        missing or can't be fetched. One extra page per series view."""
        for vol in _list(detail.get("catalog")):
            rows = _chapter_rows(vol) if isinstance(vol, dict) and vol.get("chaptersLoaded") else []
            if not rows:
                continue
            if _locked(rows[0]):
                return []
            try:
                boot = self._reader(series_id, _id(rows[0]["id"]),
                                       f"Loading the notes of book {series_id}")
            except FetchFailed as e:
                log.warning("lightnovel_fun: first chapter of book %s unavailable: %s", series_id, e)
                return []
            current = _dict(_dict(boot).get("currentChapter"))
            content = current.get("contentHtml")
            if _locked(current) or not isinstance(content, str):
                return []
            return _html_text(content).split("\n")
        return []

    # -- chapters ----------------------------------------------------------------
    def get_chapters(self, series_id: str):
        detail = self._book(series_id)
        catalog = [v for v in _list(detail.get("catalog")) if isinstance(v, dict) and _id(v.get("id"))]
        if not catalog:
            raise LayoutChanged("the chapter list")
        volumes = {_id(v.get("id")): v for v in catalog}
        loaded = {vid: _chapter_rows(v) for vid, v in volumes.items() if v.get("chaptersLoaded")}
        loaded = {vid: rows for vid, rows in loaded.items() if rows}
        order = list(volumes)

        # Walk into each unloaded volume from a loaded neighbour. Every step
        # fills a volume or gives up on one, so this ends after at most one
        # pass per volume. Any volume a walked reader page happens to load is
        # kept too: if a volume has no public chapter, the link skips over it
        # into the next one, and the walk carries on from there.
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
                    seen = self._walk(series_id, loaded[before][-1], "nextChapterId")
                elif after in loaded:
                    seen = self._walk(series_id, loaded[after][0], "prevChapterId")
                else:
                    continue
                for other, rows in seen.items():
                    if other in volumes and other not in loaded:
                        loaded[other] = rows
                if vid not in loaded:
                    failed.add(vid)
                    log.warning("lightnovel_fun: couldn't load volume %s of book %s",
                                _text(volumes[vid].get("title")), series_id)
                progress = True

        chapters = []
        for vid in order:
            group = _text(volumes[vid].get("title"))
            for ch in loaded.get(vid, []):
                cid = _id(ch["id"])
                chapters.append(ChapterInfo(
                    self.name, series_id, cid, _text(ch.get("title")) or cid,
                    urljoin(self.base_url, f"/reader/{series_id}/{cid}"), group=group))
        if not chapters:
            raise LayoutChanged("any chapter in the chapter list")
        return chapters

    def _walk(self, series_id: str, neighbour: dict, link: str) -> dict:
        """{volume id: chapters} for every volume loaded on the reader page
        that `neighbour`'s prev/next link leads to. {} if there is no link,
        or a page in the chain can't be fetched (a deleted chapter): that
        volume is then left out rather than failing the whole list."""
        action = f"Loading the chapter list of book {series_id}"
        try:
            boot = self._reader(series_id, _id(neighbour["id"]), action, use_cache=False)
            target = _id(_dict(_dict(boot).get("currentChapter")).get(link))
            if not target:
                return {}
            boot = self._reader(series_id, target, action, use_cache=False)
        except FetchFailed as e:
            log.warning("lightnovel_fun: volume walk for book %s stopped: %s", series_id, e)
            return {}
        out = {}
        for vol in _list(_dict(boot).get("catalog")):
            if isinstance(vol, dict) and vol.get("chaptersLoaded") and _id(vol.get("id")):
                rows = _chapter_rows(vol)
                if rows:
                    out[_id(vol.get("id"))] = rows
        return out

    # -- chapter text ------------------------------------------------------------
    def get_chapter_text(self, chapter) -> str:
        boot = self._reader(chapter.series_id, chapter.chapter_id, f"Loading chapter {chapter.title}")
        current = (boot or {}).get("currentChapter") if boot else None
        if isinstance(current, dict):
            if _locked(current):
                price = current.get("coinPrice")
                cost = f" ({price} 轻币)" if isinstance(price, int) and not isinstance(price, bool) \
                    and price > 0 else ""
                raise ContentHidden(
                    f"\"{chapter.title}\" is a locked chapter on 轻之国度{cost}. The app never "
                    "signs in or spends 轻币 to unlock it -- read it on the site instead.",
                    FailureReason.PURCHASE_REQUIRED)
            content = current.get("contentHtml")
            if isinstance(content, str):
                # An illustrations-only chapter (彩页) has images and no text.
                return _html_text(content)
        # Without the page's own data there is no lock flag to check, and a
        # locked chapter's page still renders its teaser -- so refuse rather
        # than import a teaser as the chapter.
        raise LayoutChanged("the chapter data (and with it, whether the chapter is locked)")

    # -- urls / capabilities -----------------------------------------------------
    def parse_url(self, url: str):
        m = re.search(r"lightnovel\.fun/reader/([0-9]+)/([0-9]+)", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2),
                                           urljoin(self.base_url, f"/reader/{m.group(1)}/{m.group(2)}")))
        m = re.search(r"lightnovel\.fun/book/([0-9]+)", url or "")
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
            "never_used": "The login API and the paid 轻币 chapter unlock. Locked chapters "
                          "are reported, not unlocked.",
            "download_links": "EPUB / pan.baidu.com / lanzou file-locker links a work posts are "
                              "listed for the person to open, never downloaded or followed.",
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
