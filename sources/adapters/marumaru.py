"""
sources/adapters/marumaru.py -- Marumaru (마루마루, Korean webtoons and manhwa).

Marumaru is a JavaScript app, but its data comes from public JSON endpoints
that need no login, so plain GETs are enough. Checked against the live site
(and its page loads in a real browser) on 2026-11-01:

  search      GET /api/search?q=<query>               JSON `items` (WEBTOON,
                                                      MANHWA, NOVEL, ANIME)
  series      GET /api/works/<id>                     JSON `work`, `synopsis`
  chapters    GET /api/works/<id>/episodes?page=<n>&size=100
                                                      JSON `items`, `total`
                                                      (size is capped at 100)
  pages       GET api.storagewebtoon.store/api/v1/reader/A2/episodes/<id>
                                                      JSON item.body[] with
                                                      kind=image urls
  images      GET peer.storagewebtoon.store/...webp   answers without a Referer;
                                                      one is sent anyway

The site moves between domains and keeps a landing page
(마루마루주소.com) that always links the current one. The domain list
(sources/domains.py) holds the current address; when every listed domain
fails, the landing page is read and any address on it that serves this site
becomes a proposal for the owner to confirm.

Series and chapter ids are numeric ids the site hands us; anything else is
refused before it reaches a URL.
"""

import json
import re
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter, layout_changed_class
from ..domains import SiteDomains, hostport
from ..http_transport import header
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError, SourceUnavailable)
from ..pacing import PaceLevel, PacingProfile
from ..registry import register
from .. import store

BASE_URL = "https://marumaru108.com"
LANDING_URL = "https://xn--2s2ba48db081g83a126ab6u.com/"
READER_URL = "https://api.storagewebtoon.store/api/v1/reader/A2/episodes/"
PAGE_SIZE = 100
MAX_PAGES = 50

_ID = re.compile(r"[0-9]{1,12}")
_HREF = re.compile(r"href=\"(https://[^\"/?#]+)/?\"")
_TYPES = {"WEBTOON": ContentType.MANHWA.value, "MANHWA": ContentType.MANHWA.value}

LayoutChanged = layout_changed_class("Marumaru", "API response")


def _safe_id(value, what: str) -> str:
    text = str(value if value is not None else "")
    if not _ID.fullmatch(text):
        raise LayoutChanged(f"a usable {what}")
    return text


def _https(url) -> str:
    url = str(url or "")
    return url if urlsplit(url).scheme == "https" else ""


def _json(resp, what: str):
    try:
        data = json.loads(resp.content)
    except ValueError:
        raise LayoutChanged(f"readable data for {what}") from None
    if not isinstance(data, dict):
        raise LayoutChanged(f"readable data for {what}")
    return data


@register
class MarumaruSource(SourceAdapter):
    name = "marumaru"
    display_name = "Marumaru (마루마루)"
    content_types = [ContentType.MANHWA.value]
    languages = ["ko"]
    url_patterns = [r"marumaru\d*\.[a-z]+/(?:webtoon|manhwa)/\d+"]
    base_urls = [BASE_URL]
    host_min_interval = {"marumaru108.com": 2.5, "api.storagewebtoon.store": 2.5}
    pacing_profile = PacingProfile(
        fast=PaceLevel(min_delay=1.5, max_delay=2.5, max_concurrent=1),
        evidence="robots.txt fetched 2026-11-01: only /search, /account, /bookmarks, /favorites "
                 "and /recent disallowed, no Crawl-delay; no terms page found.",
        fast_allowed=True)
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str | None = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.site = SiteDomains(self, base_url)

    @classmethod
    def verify_site(cls, text: str) -> bool:
        """The home page or any page of the site: the app shell's title carries 마루마루."""
        m = re.search(r"<title>(.*?)</title>", text or "", re.S)
        return bool(m and "마루마루" in m.group(1))

    def _api(self, path: str, action: str) -> dict:
        try:
            resp = self.site.get(path, action=action, use_cache=False)
        except SourceUnavailable as e:
            if e.reason is FailureReason.ALL_DOMAINS_UNREACHABLE and not self.site.fixed:
                self._propose_from_landing()
            raise
        return _json(resp, action.lower())

    def _propose_from_landing(self):
        """Reads the landing page and proposes each https address on it that serves this site.
        Never changes the list itself; the owner confirms on the PC."""
        try:
            landing = self.client.get(LANDING_URL, action="Looking for Marumaru's new address",
                                      use_cache=False)
        except Exception:
            return
        listed = {hostport(o) for o in self.site.origins()}
        for origin in dict.fromkeys(_HREF.findall(landing.text)[:10]):
            host = hostport(origin)
            if not host or host in listed or "marumaru" not in host:
                continue
            try:
                home = self.client.get(origin + "/", action="Checking a possible Marumaru address",
                                       use_cache=False)
                if hostport(home.url or origin) == host and self.verify_site(home.text):
                    store.propose_domain(self.name, host)
            except Exception:
                continue

    def search(self, query: str, page: int = 1):
        if page > 1 or not query.strip():
            return []
        data = self._api(f"/api/search?q={quote(query.strip())}", f"Searching Marumaru for {query!r}")
        items = data.get("items")
        if not isinstance(items, list):
            raise LayoutChanged("the search results")
        out = []
        for item in items:
            if not isinstance(item, dict) or item.get("mediaType") not in _TYPES \
                    or not _ID.fullmatch(str(item.get("id"))) or not item.get("title"):
                continue
            wid = str(item["id"])
            out.append(SearchResult(self.name, wid, item["title"], f"{self.site.base}/webtoon/{wid}",
                                    _https(item.get("coverUrl")),
                                    extra={"author": str(item.get("author") or ""),
                                           "adult": bool(item.get("adult")),
                                           "chapters": item.get("episodeCount")}))
        return out

    def get_series(self, series_id: str):
        wid = _safe_id(series_id, "series id")
        data = self._api(f"/api/works/{wid}", f"Loading series {wid}")
        work = data.get("work")
        if not isinstance(work, dict) or not work.get("title"):
            raise LayoutChanged("this series's details")
        status = str(work.get("status") or "").upper()
        return SeriesInfo(
            self.name, wid, work["title"], f"{self.site.base}/webtoon/{wid}",
            _https(work.get("coverUrl")),
            authors=[a.strip() for a in str(work.get("author") or "").split("/") if a.strip()],
            description=str(data.get("synopsis") or ""),
            genres=[g.strip() for g in str(work.get("genres") or "").split(",") if g.strip()],
            status="completed" if status == "COMPLETED" else "ongoing" if status == "SERIALIZING"
            else "unknown",
            content_type=ContentType.MANHWA.value, language="ko")

    def get_chapters(self, series_id: str):
        wid = _safe_id(series_id, "series id")
        rows, total = [], None
        for page in range(MAX_PAGES):
            data = self._api(f"/api/works/{wid}/episodes?page={page}&size={PAGE_SIZE}",
                             f"Loading chapters of {wid}")
            items = data.get("items")
            if not isinstance(items, list):
                raise LayoutChanged("the chapter list")
            rows.extend(i for i in items if isinstance(i, dict))
            total = data.get("total") if isinstance(data.get("total"), int) else len(rows)
            if not items or len(rows) >= total:
                break
        if not rows:
            raise LayoutChanged("any chapters in this series")
        rows.sort(key=lambda r: r.get("number") if isinstance(r.get("number"), (int, float)) else 0)
        return [ChapterInfo(self.name, wid, _safe_id(r.get("id"), "chapter id"),
                            str(r.get("title") or f"{r.get('number')}"),
                            f"{self.site.base}/webtoon/{wid}/{r['id']}")
                for r in rows]

    def get_pages(self, chapter):
        eid = _safe_id(chapter.chapter_id, "chapter id")
        data = _json(self.client.get(READER_URL + eid, action=f"Loading chapter {chapter.title}",
                                     use_cache=False), "this chapter")
        body = (data.get("item") or {}).get("body")
        if not isinstance(body, list):
            raise LayoutChanged("this chapter's images")
        urls = [_https(b.get("url")) for b in body if isinstance(b, dict) and b.get("kind") == "image"]
        urls = [u for u in urls if u]
        if not urls:
            raise LayoutChanged("any page images in this chapter")
        return [PageRef(self.name, eid, i, u, headers={"Referer": self.site.base + "/"})
                for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        ctype = header(resp.headers, "content-type").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/"):
            raise SourceError(f"Page {page.index + 1} didn't come back as an image.",
                              FailureReason.HTTP_ERROR)
        name = urlsplit(page.url).path.rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".webp"
        return resp.content, ext

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        parts = [p for p in urlsplit(url).path.split("/") if p]
        if len(parts) < 2 or parts[0] not in ("webtoon", "manhwa") or not _ID.fullmatch(parts[1]):
            return None
        if len(parts) == 2:
            return ("series", parts[1])
        if len(parts) == 3 and _ID.fullmatch(parts[2]):
            return ("chapter", ChapterInfo(self.name, parts[1], parts[2], parts[2],
                                           f"{self.site.base}/webtoon/{parts[1]}/{parts[2]}"))
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "the site's public JSON: marumaru /api/search and /api/works, "
                                 "and the reader API on api.storagewebtoon.store for a chapter's "
                                 "image list; no JavaScript runs",
            "browser_required": False,
            "images": "webp on peer.storagewebtoon.store, served without a Referer too",
            "domains": "the site moves; the landing page 마루마루주소.com links the current "
                       "address and is read only when every listed domain fails",
        }
        caps.terms = {
            "robots_txt": "marumaru108.com/robots.txt (fetched 2026-11-01): Disallow /search, "
                          "/account, /bookmarks, /favorites, /recent; no Crawl-delay. The "
                          "adapter uses /api/search, which that file does not list.",
            "read": "No terms page was found on the site (checked 2026-11-01).",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
