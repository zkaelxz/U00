"""
sources/adapters/wfwf.py -- a Korean webtoon aggregator whose domain number rotates (wfwf546.com).

Server-rendered pages in EUC-KR, so plain GETs are enough and no JavaScript runs
(read live 2026-11-26).

  search      GET /sh?q=<query, EUC-KR percent-encoded>   cards: a.t-card -> /list?toon=<id>
  series      GET /list?toon=<id>                         h1.w-title, cover in .thumb-wrap,
                                                          #summary, a.ep-item per episode
  chapters    a.ep-item href="/view?toon=<id>&num=<n>", 100 a page: GET /list?toon=<id>&s=o&pg=<p>
              (oldest first) until a page has no new episode
  pages       GET /view?toon=<id>&num=<n>                 <img data-src> inside #vimg-area, on an
                                                          image host that answers 403 without a
                                                          Referer from the site (plain hotlink check)

The site wraps its pages in gambling and betting ads; only images inside the reader area are read,
and no ad link is ever followed. Series ids and episode numbers are digits only; anything else is
refused before it reaches a URL.
"""

import re
from urllib.parse import quote, urlsplit

from ..base import PlainImageDownload, SourceAdapter, layout_changed_class, soup as _soup, https_only
from ..domains import SiteDomains
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, PageRef,
                      SearchResult, SeriesInfo)
from ..registry import register

BASE_URL = "https://wfwf546.com"
MAX_LIST_PAGES = 40

_ID = re.compile(r"[0-9]{1,12}")
_LIST_HREF = re.compile(r"^/list\?toon=([0-9]{1,12})$")
_VIEW_HREF = re.compile(r"^/view\?toon=([0-9]{1,12})&num=([0-9]{1,12})$")

LayoutChanged = layout_changed_class("wfwf")


def _clean(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


@register
class WfwfSource(PlainImageDownload, SourceAdapter):
    name = "wfwf"
    display_name = "wfwf (Korean webtoons)"
    homepage = BASE_URL
    content_types = [ContentType.MANHWA.value]
    languages = ["ko"]
    url_patterns = [r"wfwf\d*\.com/(?:list|view)\?toon=\d+"]
    base_urls = [BASE_URL]
    host_min_interval = {"wfwf546.com": 2.5}
    images_only = True

    def __init__(self, client=None, base_url: str | None = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.site = SiteDomains(self, base_url)

    @property
    def base_url(self) -> str:
        return self.site.base

    @classmethod
    def verify_site(cls, text: str) -> bool:
        """A page of this site: it links to /list?toon=<id> series at least three times."""
        return len(set(re.findall(r'href="/list\?toon=(\d{1,12})', text or ""))) >= 3

    def _page(self, path: str, action: str) -> str:
        return self.site.get(path, action=action, use_cache=False).text

    def search(self, query: str, page: int = 1):
        if page > 1 or not query.strip():
            return []
        # The site decodes the query as EUC-KR, so a UTF-8 escape would not match anything.
        encoded = quote(query.strip(), encoding="cp949", errors="replace")
        html = self._page(f"/sh?q={encoded}", f"Searching wfwf for {query!r}")
        out, seen = [], set()
        for a in _soup(html).select("a.t-card[href]"):
            m = _LIST_HREF.match(str(a["href"]))
            title_el = a.select_one(".t-title")
            title = _clean(title_el.get_text(" ") if title_el else "")
            if not m or m.group(1) in seen or not title:
                continue
            seen.add(m.group(1))
            img = a.find("img")
            cover = https_only(img.get("src")) if img else ""
            out.append(SearchResult(self.name, m.group(1), title,
                                    f"{self.base_url}/list?toon={m.group(1)}", cover))
        return out

    def _series_id(self, value) -> str:
        sid = str(value or "")
        if not _ID.fullmatch(sid):
            raise LayoutChanged("a usable series id")
        return sid

    def get_series(self, series_id: str):
        sid = self._series_id(series_id)
        soup = _soup(self._page(f"/list?toon={sid}", f"Loading series {sid}"))
        h1 = soup.select_one("h1.w-title")
        title = _clean(h1.get_text(" ") if h1 else "")
        if not title:
            raise LayoutChanged("this series's title")
        cover = soup.select_one(".thumb-wrap img")
        summary = soup.select_one("#summary")
        return SeriesInfo(self.name, sid, title, f"{self.base_url}/list?toon={sid}",
                          https_only(cover.get("src")) if cover else "",
                          description=_clean(summary.get_text(" ")) if summary else "",
                          content_type=ContentType.MANHWA.value, language="ko")

    def get_chapters(self, series_id: str):
        sid = self._series_id(series_id)
        rows: dict[int, str] = {}
        for page in range(1, MAX_LIST_PAGES + 1):
            soup = _soup(self._page(f"/list?toon={sid}&s=o&pg={page}",
                                    f"Loading episode list page {page} of series {sid}"))
            before = len(rows)
            for a in soup.select("a.ep-item[href]"):
                m = _VIEW_HREF.match(str(a["href"]))
                if not (m and m.group(1) == sid):
                    continue
                name = a.select_one(".ep-title")
                rows.setdefault(int(m.group(2)), _clean(name.get_text(" ") if name else "")
                                or f"Chapter {m.group(2)}")
            if len(rows) == before:
                break
        else:
            raise LayoutChanged("the end of the episode list")
        if not rows:
            raise LayoutChanged("any episodes in this series")
        return [ChapterInfo(self.name, sid, str(num), title, f"{self.base_url}/view?toon={sid}&num={num}")
                for num, title in sorted(rows.items())]

    def get_pages(self, chapter):
        sid = self._series_id(chapter.series_id)
        num = str(chapter.chapter_id or "")
        if not _ID.fullmatch(num):
            raise LayoutChanged("a usable chapter id")
        html = self._page(f"/view?toon={sid}&num={num}", f"Loading chapter {chapter.title}")
        urls = []
        for img in _soup(html).select("#vimg-area img[data-src]"):
            url = https_only(img.get("data-src"))
            if url and url not in urls:
                urls.append(url)
        if not urls:
            raise LayoutChanged("any page images in this chapter")
        referer = self.base_url + "/"
        return [PageRef(self.name, num, i, u, headers={"Referer": referer}) for i, u in enumerate(urls)]

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        parts = urlsplit(url)
        # Only the toon and num parameters count; a tracking suffix must not hide a valid link.
        toon = re.search(r"(?:^|&)toon=([0-9]{1,12})(?:&|$)", parts.query)
        num = re.search(r"(?:^|&)num=([0-9]{1,12})(?:&|$)", parts.query)
        if parts.path == "/view" and toon and num:
            return ("chapter", ChapterInfo(self.name, toon.group(1), num.group(1), num.group(1),
                                           f"{self.base_url}/view?toon={toon.group(1)}&num={num.group(1)}"))
        if parts.path == "/list" and toon:
            return ("series", toon.group(1))
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML (server-rendered, EUC-KR); page images are "
                                 "img[data-src] inside #vimg-area; no JavaScript runs",
            "browser_required": False,
            "images": "served by a separate image host that answers 403 unless the request carries a "
                      "Referer of the site (plain hotlink check, images are not scrambled). Ad images "
                      "and ad links are never fetched",
        }
        caps.terms = {
            "robots_txt": "Absent: /robots.txt answers with the site's error page (2026-11-26).",
            "read": "No terms page was found.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
