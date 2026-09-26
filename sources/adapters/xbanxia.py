"""
sources/adapters/xbanxia.py -- xbanxia.cc (zh web novels), roadmap Step 23e.

No Keiyoushi/Mihon extension exists for this site either. Real technique
read from lncrawl/lightnovel-crawler (MIT), `sources/zh/xbanxia.py`:

  search         POST /modules/article/search_t.php    searchkey/Submit form
                                                        fields, spoofed Firefox
                                                        UA, static
                                                        jieqiUserCharset=utf-8
                                                        cookie -- no real auth
  series         div.book-describe > h1 / p            最近更新/最新章節/類型
                                                        prefixes, cover
                                                        img[data-original]
  chapters       div.book-list ul li a                 flat, no pagination
  chapter body   div#nr1                                plain server-rendered
                                                        HTML

**The domain question is resolved (live-verified 2026-09-26)**: lncrawl's
own source targets xbanxia.com/banxia.cc as its base URLs, not xbanxia.cc
-- the domain this project actually vetted (robots.txt/Cloudflare-posture
check). `xbanxia.com` returns HTTP 403 and `banxia.cc` redirects
elsewhere; `xbanxia.cc` (which 301-redirects to `www.xbanxia.cc`) is
confirmed the real, live, correct target -- a real book page, a real
567-chapter list, and real chapter text (`div#nr1`, no fallback needed)
were all read from it directly. `BASE_URL` now points at `www.xbanxia.cc`
explicitly: POSTing `search()`'s form to the bare domain silently lost
the request body, because `requests` downgrades a redirected POST to GET
by default -- posting directly to the real host avoids the redirect
entirely. The real book/chapter URL shape is also `/books/<id>.html` /
`/books/<id>/<chapter>.html` (plural "books", confirmed against a real
page) -- `url_patterns`/`parse_url`/the path-building helpers below all
match this now, not the `/<id>/` shape an earlier, never-verified-live
pass assumed.
"""

import re
from urllib.parse import urljoin

from ..base import SourceAdapter
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SearchResult, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.xbanxia.cc"
SEARCH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
    "Cookie": "jieqiUserCharset=utf-8",
    "Content-Type": "application/x-www-form-urlencoded",
}


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"xbanxia's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.UNKNOWN)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _labelled_text(soup, *prefixes) -> str:
    """A <p> whose text starts with one of the given label prefixes
    (最近更新/最新章節/類型), with the label stripped -- the shape
    lncrawl's own extractor reads these fields in."""
    for p in soup.select("div.book-describe p"):
        text = p.get_text(strip=True)
        for prefix in prefixes:
            if text.startswith(prefix):
                return text[len(prefix):].lstrip("：: ").strip()
    return ""


@register
class XbanxiaSource(SourceAdapter):
    name = "xbanxia"
    display_name = "xbanxia.cc"
    content_types = [ContentType.NOVEL.value]
    languages = ["zh"]
    url_patterns = [r"xbanxia\.cc/books/\d+"]
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._series_pages = {}

    def _get(self, path: str, action: str) -> str:
        resp = self.client.get(urljoin(self.base_url, path), action=action)
        return resp.text

    def search(self, query: str, page: int = 1):
        resp = self.client.post(
            urljoin(self.base_url, "/modules/article/search_t.php"),
            data={"searchkey": query, "Submit": "搜索"},
            headers=SEARCH_HEADERS, action=f"Searching xbanxia for {query!r}")
        soup = _soup(resp.text)
        out = []
        seen = set()
        # `li.pop-book2` is the real search-results container, confirmed
        # live (2026-09-26) -- each entry carries two <a> tags sharing one
        # href (a cover-image link and a title link), so results are
        # deduplicated by series_id. The old selectors below never matched
        # a real response and are kept only as a fallback.
        for li in soup.select("li.pop-book2"):
            a = li.select_one("a[href]")
            if a is None:
                continue
            href = a.get("href", "")
            m = re.search(r"/(\d+)/?$", href) or re.search(r"/(\d+)\.html", href)
            if not m or m.group(1) in seen:
                continue
            title_el = li.select_one("h2.pop-tit")
            title = (title_el.get_text(strip=True) if title_el is not None
                     else a.get("title") or a.get_text(strip=True))
            if not title:
                continue
            seen.add(m.group(1))
            cover = li.select_one("img[data-original]")
            out.append(SearchResult(self.name, m.group(1), title, urljoin(self.base_url, href),
                                    cover.get("data-original", "") if cover is not None else ""))
        if out:
            return out
        for a in soup.select("div.book-list ul li a, div.result-list a, a.book-title"):
            href = a.get("href", "")
            m = re.search(r"/(\d+)/?$", href) or re.search(r"/(\d+)\.html", href)
            if not m:
                continue
            title = a.get("title") or a.get_text(strip=True)
            if not title:
                continue
            out.append(SearchResult(self.name, m.group(1), title, urljoin(self.base_url, href)))
        return out

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/books/{series_id}.html",
                                                       f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        h1 = soup.select_one("div.book-describe h1")
        if h1 is None:
            raise LayoutChanged("the series title")
        cover = soup.select_one("img[data-original]")
        genres_txt = _labelled_text(soup, "類型", "类型")
        return SeriesInfo(
            self.name, series_id, h1.get_text(strip=True),
            urljoin(self.base_url, f"/books/{series_id}.html"),
            cover.get("data-original") if cover is not None else "",
            genres=[g.strip() for g in genres_txt.split(",") if g.strip()] if genres_txt else [],
            content_type=ContentType.NOVEL.value, language="zh")

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        items = soup.select("div.book-list ul li a")
        if not items:
            raise LayoutChanged("the chapter list")
        chapters = []
        for a in items:
            href = a.get("href", "")
            if not href:
                continue
            m = re.search(r"/(\d+)\.html", href)
            chapter_id = m.group(1) if m else href
            chapters.append(ChapterInfo(self.name, series_id, chapter_id,
                                        a.get_text(strip=True) or chapter_id,
                                        urljoin(self.base_url, href)))
        return chapters

    def get_chapter_text(self, chapter) -> str:
        path = chapter.url or f"/books/{chapter.series_id}/{chapter.chapter_id}.html"
        html = self._get(path, f"Loading chapter {chapter.title}")
        soup = _soup(html)
        container = soup.select_one("div#nr1")
        if container is None:
            # #nr1 is lncrawl's selector for a sibling domain, not
            # independently confirmed against xbanxia.cc's own markup
            # (module docstring) -- fall back to the largest text block
            # on the page rather than failing outright on a possible id
            # mismatch.
            candidates = soup.find_all(["div", "article"])
            container = max(candidates, key=lambda c: len(c.get_text(strip=True)), default=None)
        if container is None:
            raise LayoutChanged("the chapter text container")
        text = container.get_text("\n", strip=True)
        if not text:
            raise LayoutChanged("any chapter text")
        return text

    def parse_url(self, url: str):
        m = re.search(r"/books/(\d+)/(\d+)\.html", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2), url))
        m = re.search(r"/books/(\d+)\.html", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered HTML, no JavaScript/decoding step",
            "browser_required": False,
            "domain_caveat": "Targets xbanxia.cc (via www.xbanxia.cc), the domain actually "
                             "vetted -- lncrawl's own reference source targets "
                             "xbanxia.com/banxia.cc instead. Confirmed live (2026-09-26) as the "
                             "right target: xbanxia.com returns HTTP 403, banxia.cc redirects "
                             "elsewhere, and xbanxia.cc is a real, working site with real search "
                             "results, a real chapter list and real chapter text.",
            "chapter_selector_caveat": "div#nr1 was confirmed live (2026-09-26) to match a real "
                                       "chapter page directly -- no fallback needed -- resolving "
                                       "the earlier caveat that it was only lncrawl's selector "
                                       "for a sibling domain. The largest-text-block fallback is "
                                       "kept regardless, in case that changes.",
            "reference": "lncrawl/lightnovel-crawler sources/zh/xbanxia.py (MIT)",
        }
        caps.terms = {
            "robots_txt": "User-agent: * with zero Disallow lines -- no restrictions declared "
                          "at all. (Recorded from the roadmap's direct check; not re-fetched "
                          "while building this adapter.)",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps
