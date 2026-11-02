"""
sources/adapters/newxtoon.py -- NewXToon (newxtoon4.com, Korean webtoons).

A server-rendered Laravel site, so plain GETs are enough and no JavaScript
runs. The layout below was reported from a browser on the owner's PC
(2026-11-01); this adapter was written against that description and
invented fixtures, not against a live fetch.

  search      GET /search?q=<query>                 links to /comics/<id>
  series      GET /comics/<id>                      og:title / h1, og:image,
                                                    chapter links
  chapters    <a href="/comics/<id>/chapters/<chapter id>"> on the series page
  pages       GET /comics/<id>/chapters/<chapter id>
                                                    <img class="reader-page">,
                                                    hosted on a rotating file host
                                                    (the first <img> on the page is
                                                    an ad and has another class)

Ad links (`/ads/`, the file host's `/ads/` images, the statistics script)
are never fetched or followed. Series and chapter ids are numeric ids the
site hands us; anything else is refused before it reaches a URL.
"""

import re
from urllib.parse import quote, urljoin, urlsplit

from ..base import SourceAdapter, layout_changed_class, soup as _soup
from ..domains import SiteDomains
from ..http_transport import header
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError)
from ..registry import register

BASE_URL = "https://newxtoon4.com"
MAX_LIST_PAGES = 30

_ID = re.compile(r"[0-9]{1,12}")
_SERIES_PATH = re.compile(r"^/comics/([0-9]{1,12})/?$")
_CHAPTER_PATH = re.compile(r"^/comics/([0-9]{1,12})/chapters/([0-9]{1,12})/?$")

LayoutChanged = layout_changed_class("NewXToon")


def _https(url) -> str:
    url = str(url or "")
    return url if urlsplit(url).scheme == "https" else ""


def _is_ad(url: str) -> bool:
    return "/ads/" in urlsplit(url).path


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


@register
class NewXToonSource(SourceAdapter):
    name = "newxtoon"
    display_name = "NewXToon"
    content_types = [ContentType.MANHWA.value]
    languages = ["ko"]
    url_patterns = [r"(?:newxtoon\d*\.com|xn--vg1b002axlc8ts\.com)/comics/\d+"]
    base_urls = [BASE_URL, "https://xn--vg1b002axlc8ts.com"]
    # Fast stays off: no terms page to vet, and Fast is limited to vetted sites.
    host_min_interval = {"newxtoon4.com": 2.5, "xn--vg1b002axlc8ts.com": 2.5}

    def __init__(self, client=None, base_url: str | None = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.site = SiteDomains(self, base_url)

    @property
    def base_url(self) -> str:
        return self.site.base

    @classmethod
    def verify_site(cls, text: str) -> bool:
        """A page of this site: it links to /comics/<id> series at least three times."""
        ids = set(re.findall(r'href="(?:https?://[^"/]+)?/comics/(\d{1,12})/?"', text or ""))
        return len(ids) >= 3

    def _page(self, path: str, action: str) -> str:
        return self.site.get(path, action=action, use_cache=False).text

    def search(self, query: str, page: int = 1):
        if page > 1 or not query.strip():
            return []
        html = self._page(f"/search?q={quote(query.strip())}", f"Searching NewXToon for {query!r}")
        out, seen = [], set()
        for a in _soup(html).find_all("a", href=True):
            m = _SERIES_PATH.match(urlsplit(str(a["href"])).path)
            if not m or m.group(1) in seen:
                continue
            img = a.find("img")
            title = _clean(a.get("title") or a.get_text() or (img.get("alt") if img else ""))
            if not title:
                continue
            seen.add(m.group(1))
            cover = urljoin(self.base_url + "/", str(img["src"])) if img and img.get("src") else ""
            out.append(SearchResult(self.name, m.group(1), title,
                                    f"{self.base_url}/comics/{m.group(1)}",
                                    "" if _is_ad(cover) else _https(cover)))
        return out

    def _series_id(self, value) -> str:
        sid = str(value or "")
        if not _ID.fullmatch(sid):
            raise LayoutChanged("a usable series id")
        return sid

    def _series_soup(self, series_id: str, page: int = 1):
        suffix = f"?page={page}" if page > 1 else ""
        return _soup(self._page(f"/comics/{series_id}{suffix}", f"Loading series {series_id}"))

    def get_series(self, series_id: str):
        sid = self._series_id(series_id)
        soup = self._series_soup(sid)
        og = soup.find("meta", attrs={"property": "og:title"})
        h1 = soup.find("h1")
        title = _clean((og.get("content") if og else "") or (h1.get_text() if h1 else ""))
        if not title:
            raise LayoutChanged("this series's title")
        cover = soup.find("meta", attrs={"property": "og:image"})
        desc = soup.find("meta", attrs={"property": "og:description"}) \
            or soup.find("meta", attrs={"name": "description"})
        cover_url = _https(cover.get("content")) if cover else ""
        return SeriesInfo(self.name, sid, title, f"{self.base_url}/comics/{sid}",
                          "" if _is_ad(cover_url) else cover_url,
                          description=_clean(desc.get("content")) if desc else "",
                          content_type=ContentType.MANHWA.value, language="ko")

    def get_chapters(self, series_id: str):
        sid = self._series_id(series_id)
        rows, seen = [], set()
        for page in range(1, MAX_LIST_PAGES + 1):
            soup = self._series_soup(sid, page)
            fresh = 0
            for a in soup.find_all("a", href=True):
                m = _CHAPTER_PATH.match(urlsplit(str(a["href"])).path)
                if not m or m.group(1) != sid or m.group(2) in seen:
                    continue
                seen.add(m.group(2))
                fresh += 1
                rows.append((int(m.group(2)), m.group(2), _clean(a.get_text()) or f"Chapter {m.group(2)}"))
            if not fresh:
                break
        if not rows:
            raise LayoutChanged("any chapters in this series")
        # Chapter ids grow with upload order, which is reading order on this site.
        rows.sort()
        return [ChapterInfo(self.name, sid, cid, title,
                            f"{self.base_url}/comics/{sid}/chapters/{cid}") for _, cid, title in rows]

    def get_pages(self, chapter):
        sid = self._series_id(chapter.series_id)
        cid = str(chapter.chapter_id or "")
        if not _ID.fullmatch(cid):
            raise LayoutChanged("a usable chapter id")
        html = self._page(f"/comics/{sid}/chapters/{cid}", f"Loading chapter {chapter.title}")
        urls = []
        for img in _soup(html).find_all("img", class_="reader-page"):
            url = _https(img.get("data-src") or img.get("src"))
            if url and not _is_ad(url) and url not in urls:
                urls.append(url)
        if not urls:
            raise LayoutChanged("any page images in this chapter")
        return [PageRef(self.name, cid, i, u) for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        ctype = header(resp.headers, "content-type").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/"):
            raise SourceError(f"Page {page.index + 1} didn't come back as an image.",
                              FailureReason.HTTP_ERROR)
        name = urlsplit(page.url).path.rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        path = urlsplit(url).path
        m = _CHAPTER_PATH.match(path)
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2),
                                           f"{self.base_url}/comics/{m.group(1)}/chapters/{m.group(2)}"))
        m = _SERIES_PATH.match(path)
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML (server-rendered Laravel); page images are "
                                 "img.reader-page tags; no JavaScript runs",
            "browser_required": False,
            "images": "served by a rotating file host; no Referer needed. Ad images and ad "
                      "links (/ads/) are never fetched",
        }
        caps.terms = {
            "robots_txt": "Not read: the site answered 403 to this project's cloud container, so "
                          "it was checked only from the owner's PC (2026-11-01).",
            "read": "No terms page was reviewed.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
