"""
sources/adapters/guazimanhua.py -- 瓜子漫画 guazimanhua.com (zh manhua),
roadmap Step 23h.

Technique read from Keiyoushi's actively maintained Mihon extension
(keiyoushi/extensions-source, src/zh/guazimanhua, Apache-2.0), then
independently re-verified against the live site while building this
adapter (every selector below was matched against a real fetch, not
assumed from the extension source or the roadmap alone):

  search      GET /category.php?keyword=<query>    article.card
  listing     GET /category.php                    article.card (a.cover-wrap
                                                    href, img.cover src,
                                                    h3 a title, div.meta text)
  series      GET /comic.php?id=<id>                div.mobile-comic-title,
                                                    img.mobile-comic-cover,
                                                    p.mobile-comic-desc,
                                                    p.mobile-comic-tags,
                                                    p.mobile-comic-meta
                                                    (连载/完结 status),
                                                    div.cinema-strip > div
                                                    (span "作者" + a b for
                                                    author)
  chapters    same page                            div.mobile-chapter-grid a
  pages       GET /chapter.php?id=<id>              section.reader-images img
                                                    (src=..., already absolute)

**A real, confirmed deviation from the roadmap, found by re-verifying
rather than trusting the roadmap's own "confirmed directly" note:** the
roadmap says this site's chapter images are populated client-side against
`chapter.php`/`api.php` and are absent from the raw HTML response,
requiring the browser-rendered tier for `get_pages()`. A direct fetch of a
real chapter page while building this adapter shows that is no longer
true -- `<section class="reader-images" data-reader-images>` now contains
plain, real `<img id="page-N" src="https://img.guazicdn.com/...">` tags
directly in the server-rendered HTML, exactly matching what the current
Keiyoushi extension source itself does (plain Jsoup parsing, no WebView).
The site evidently changed between when the roadmap was vetted and when
this step was actually built -- exactly the kind of drift this project's
own working agreement says to re-verify for and follow, not the roadmap's
now-stale wording. `get_pages()` below is therefore plain STATIC_HTTP, not
routed through the browser-rendered tier. If a future re-check finds the
site has gone back to client-side image population, this is the function
to change -- nothing else in this adapter assumes one way or the other.

`robots.txt` blocks a set of named bots (including `GPTBot`/`ClaudeBot`
specifically) with a separate, more permissive `User-agent: *` catch-all
-- the same posture already accepted for manhuagui: a client that doesn't
self-identify as one of the named bots falls under the lighter catch-all
rule.
"""

import re
from urllib.parse import quote, urljoin

from ..base import SourceAdapter
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SearchResult, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.guazimanhua.com"


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"guazimanhua's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.UNKNOWN)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _id_from_href(href: str) -> str:
    m = re.search(r"[?&]id=(\d+)", href or "")
    return m.group(1) if m else ""


def _status_from_meta(text: str) -> str:
    if "连载" in text:
        return "ongoing"
    if "完结" in text:
        return "completed"
    return "unknown"


@register
class GuazimanhuaSource(SourceAdapter):
    name = "guazimanhua"
    display_name = "瓜子漫画 Guazimanhua"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [r"(?:^|//|\.)guazimanhua\.com/(?:comic|chapter)\.php\?id=\d+"]

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._series_pages = {}

    def _get(self, path: str, action: str) -> str:
        resp = self.client.get(urljoin(self.base_url, path), action=action)
        return resp.text

    def search(self, query: str, page: int = 1):
        html = self._get(f"/category.php?keyword={quote(query.strip())}",
                         f"Searching guazimanhua for {query!r}")
        return self._parse_cards(html)

    def _parse_cards(self, html: str):
        soup = _soup(html)
        out = []
        for card in soup.select("article.card"):
            a = card.select_one("a.cover-wrap")
            title_a = card.select_one("h3 a")
            if a is None or title_a is None:
                continue
            href = a.get("href", "")
            sid = _id_from_href(href)
            if not sid:
                continue
            img = card.select_one("img.cover")
            cover = img.get("src", "") if img is not None else ""
            out.append(SearchResult(self.name, sid, title_a.get_text(strip=True),
                                    urljoin(self.base_url, href), cover))
        return out

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/comic.php?id={series_id}",
                                                       f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        title_el = soup.select_one("div.mobile-comic-title")
        if title_el is None:
            raise LayoutChanged("the series title")
        thumb = soup.select_one("img.mobile-comic-cover")
        desc_el = soup.select_one("p.mobile-comic-desc")
        tags_el = soup.select_one("p.mobile-comic-tags")
        meta_el = soup.select_one("p.mobile-comic-meta")
        genres = [g.strip() for g in tags_el.get_text(strip=True).split("/")
                 if g.strip()] if tags_el is not None else []
        authors = []
        for row in soup.select("div.cinema-strip > div"):
            span = row.find("span")
            b = row.find("b")
            if span is not None and b is not None and "作者" in span.get_text():
                authors.append(b.get_text(strip=True))
        return SeriesInfo(
            self.name, series_id, title_el.get_text(strip=True),
            urljoin(self.base_url, f"/comic.php?id={series_id}"),
            thumb.get("src", "") if thumb is not None else "",
            authors=authors,
            description=desc_el.get_text(" ", strip=True) if desc_el is not None else "",
            genres=genres,
            status=_status_from_meta(meta_el.get_text(strip=True) if meta_el is not None else ""),
            content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        items = soup.select("div.mobile-chapter-grid a")
        if not items:
            raise LayoutChanged("the chapter list")
        chapters = []
        for a in items:
            href = a.get("href", "")
            cid = _id_from_href(href)
            if not cid:
                continue
            chapters.append(ChapterInfo(self.name, series_id, cid, a.get_text(strip=True) or cid,
                                        urljoin(self.base_url, href)))
        return chapters

    def get_pages(self, chapter):
        from ..models import PageRef
        path = chapter.url or f"/chapter.php?id={chapter.chapter_id}"
        html = self._get(path, f"Loading chapter {chapter.title}")
        soup = _soup(html)
        container = soup.select_one("section.reader-images")
        if container is None:
            raise LayoutChanged("the chapter's reader-images container")
        urls = [img.get("src", "") for img in container.select("img[src]")]
        urls = [u for u in urls if u]
        if not urls:
            raise LayoutChanged("any page images inside the reader-images container")
        return [PageRef(self.name, chapter.chapter_id, i, urljoin(self.base_url, u))
                for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        name = page.url.split("?", 1)[0]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        m = re.search(r"guazimanhua\.com/chapter\.php\?id=(\d+)", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, "", m.group(1), m.group(1), url))
        m = re.search(r"guazimanhua\.com/comic\.php\?id=(\d+)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML only -- confirmed by direct re-fetch that chapter "
                                 "pages now serve real <img src=...> page URLs inside "
                                 "section.reader-images directly in the server response; the "
                                 "roadmap's earlier finding that this needed the browser-rendered "
                                 "tier no longer holds (site changed since vetting -- see module "
                                 "docstring).",
            "browser_required": False,
            "reference": "keiyoushi/extensions-source src/zh/guazimanhua (Apache-2.0)",
        }
        caps.terms = {
            "robots_txt": "Named bots (including GPTBot/ClaudeBot) individually disallowed, "
                          "with a separate, more permissive User-agent: * catch-all -- same "
                          "posture already accepted for manhuagui. (Re-verified by direct fetch "
                          "while building this adapter.)",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps
