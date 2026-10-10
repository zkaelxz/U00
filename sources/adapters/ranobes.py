"""
sources/adapters/ranobes.py -- ranobes.net (en-translated web/light novels).

Real technique read directly from the live site while building this adapter
(no reference scraper existed to read from -- independently investigated):

  search        POST /index.php?do=search  do=search/subaction=search/story=
                                            <query> (DataLife Engine's own
                                            search action -- the `generator`
                                            meta tag confirms DLE) ->
                                            redirects to /search/<query>/
                                            article.block.story.shortstory
                                            > h2.title > a
  series        GET /novels/<id>-<slug>.html
                                            h1.title (subtitle span holds the
                                            author), .moreless__full /
                                            .moreless__short (description),
                                            figure.cover's inline
                                            background-image (cover)
  chapters      GET /chapters/<id>/  and
                    /chapters/<id>/page/<n>/  for n in 2..pages_count
                                            a `window.__DATA__ = {...}` JSON
                                            blob embedded directly in the
                                            static HTML -- the dedicated
                                            /chapters/ page is a Vue app
                                            (confirmed: its own chapters.js
                                            reads `window.__DATA__` as the
                                            component's initial state), but
                                            that same JSON is what the server
                                            already rendered into the page,
                                            so no JS execution is needed to
                                            read it. `pages_count` in the
                                            blob gives the real page count.
  chapter body  GET <chapter url>          div#arrticle (real site typo,
                                            confirmed directly, not a typo
                                            introduced here) > <p> tags

Domain: ranobes.net loads cleanly over plain HTTP with no active challenge
(confirmed directly: homepage, book page, chapter page, and chapters/
listing all returned real content on a first request). ranobes.top -- an
alternate TLD for the same brand -- was also checked and hits a live
Cloudflare "Just a moment..." interactive challenge on its own robots.txt,
so this adapter targets ranobes.net specifically, the same
domain-verification discipline `xbanxia.py`'s docstring already documents
for its own site.

`robots.txt` (fetched directly): disallows only `/engine/` (DLE's own admin
path), a handful of listing/tag pagination paths, and `ia_archiver`
entirely -- none of the paths this adapter uses (`/novels/*`, `/chapters/*`,
individual chapter pages, `/index.php?do=search`) are disallowed.
`/rules.html` ("The rules of the site", fetched and read directly) is a
community-conduct policy (no insults, no spam, no account trading) -- it
says nothing about automated access/scraping either way, so
`automation_permission` stays UNKNOWN per this project's "unread/unclear
terms are never PERMITTED by default" rule, not PERMITTED just because
nothing forbids it.
"""

import json
import re
from urllib.parse import urljoin

from ..base import SourceAdapter
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SearchResult, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://ranobes.net"
SEARCH_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}
_DATA_ASSIGN = re.compile(r"__DATA__\s*=\s*")
_COVER_URL = re.compile(r"background-image:\s*url\(([^)]+)\)")
# pages_count comes from the page itself; a hostile or broken value would
# otherwise have us walk an unbounded list, one paced request per page.
MAX_LIST_PAGES = 200


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"ranobes' page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _extract_data_blob(html: str) -> dict:
    """Pulls the `window.__DATA__ = {...}` (or bare `__DATA__ = {...}`)
    JSON object a chapters-listing page embeds server-side -- brace-matched
    rather than regexed to the closing `}`, since the blob itself contains
    nested objects."""
    m = _DATA_ASSIGN.search(html or "")
    if not m:
        raise LayoutChanged("the chapters page's embedded data")
    start = html.find("{", m.end())
    if start < 0:
        raise LayoutChanged("the chapters page's embedded data")
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except ValueError as e:
                    raise LayoutChanged(f"a readable chapters data blob ({e})") from None
    raise LayoutChanged("the chapters page's embedded data")


@register
class RanobesSource(SourceAdapter):
    name = "ranobes"
    display_name = "ranobes.net"
    content_types = [ContentType.NOVEL.value]
    languages = ["en"]
    url_patterns = [r"ranobes\.net/(?:novels/\d+[^/\s]*\.html|[^/\s]+-\d+/\d+\.html)"]
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._series_pages = {}

    def _get(self, path: str, action: str) -> str:
        resp = self.client.get(urljoin(self.base_url, path), action=action)
        return resp.text

    def _series_id_num(self, series_id: str) -> str:
        m = re.search(r"(\d+)", series_id or "")
        if not m:
            raise LayoutChanged("a numeric series id")
        return m.group(1)

    def search(self, query: str, page: int = 1):
        resp = self.client.post(
            urljoin(self.base_url, "/index.php?do=search"),
            data={"do": "search", "subaction": "search", "story": query},
            headers=SEARCH_HEADERS, action=f"Searching ranobes for {query!r}")
        soup = _soup(resp.text)
        out = []
        seen = set()
        for article in soup.select("article.block.story.shortstory"):
            a = article.select_one("h2.title a[href]")
            if a is None:
                continue
            href = a.get("href", "")
            m = re.search(r"/novels/(\d+)[^/]*\.html", href)
            if not m or m.group(1) in seen:
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            seen.add(m.group(1))
            series_id = href.rsplit("/novels/", 1)[-1]
            series_id = "novels/" + series_id
            out.append(SearchResult(self.name, series_id, title, urljoin(self.base_url, href)))
        return out

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/{series_id}", f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        h1 = soup.select_one("h1.title")
        if h1 is None:
            raise LayoutChanged("the series title")
        subtitle = h1.select_one("span.subtitle")
        author = ""
        if subtitle is not None:
            author = re.sub(r"^by\s+", "", subtitle.get_text(strip=True), flags=re.I)
            subtitle.extract()
        title = h1.get_text(strip=True)
        desc_el = soup.select_one(".moreless__full") or soup.select_one(".moreless__short")
        description = desc_el.get_text(" ", strip=True) if desc_el is not None else ""
        cover = ""
        fig = soup.select_one("figure.cover")
        if fig is not None:
            m = _COVER_URL.search(fig.get("style", ""))
            if m:
                cover = urljoin(self.base_url, m.group(1).strip("'\""))
        return SeriesInfo(
            self.name, series_id, title, urljoin(self.base_url, f"/{series_id}"), cover,
            authors=[author] if author else [], description=description,
            content_type=ContentType.NOVEL.value, language="en")

    def get_chapters(self, series_id: str):
        book_id = self._series_id_num(series_id)
        first = self._get(f"/chapters/{book_id}/", f"Loading chapter list for {series_id}")
        data = _extract_data_blob(first)
        chapters = list(data.get("chapters") or [])
        pages_count = min(int(data.get("pages_count") or 1), MAX_LIST_PAGES)
        for n in range(2, pages_count + 1):
            html = self._get(f"/chapters/{book_id}/page/{n}/",
                             f"Loading chapter list for {series_id} (page {n}/{pages_count})")
            chapters.extend(_extract_data_blob(html).get("chapters") or [])
        if not chapters:
            raise LayoutChanged("any chapters")
        out = []
        for c in chapters:
            cid = str(c.get("id") or "")
            if not cid:
                continue
            out.append(ChapterInfo(self.name, series_id, cid, c.get("title") or cid,
                                   c.get("link") or ""))
        return out

    def get_chapter_text(self, chapter) -> str:
        html = self._get(chapter.url, f"Loading chapter {chapter.title}")
        soup = _soup(html)
        container = soup.select_one("div#arrticle") or soup.select_one("div.text#arrticle")
        if container is None:
            raise LayoutChanged("the chapter text container")
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if p)
        if not text:
            raise LayoutChanged("any chapter text")
        return text

    def parse_url(self, url: str):
        m = re.search(r"ranobes\.net/([^/]+-\d+)/(\d+)\.html", url or "")
        if m:
            slug_id, chapter_id = m.group(1), m.group(2)
            # A chapter link doesn't carry the series' own `/novels/<id>-...`
            # slug -- only the numeric id, embedded at the end of
            # `<slug>-<id>`, is stable enough to round-trip into get_series()/
            # get_chapters(), which both accept a bare numeric-prefixed id.
            m2 = re.search(r"-(\d+)$", slug_id)
            series_id = f"novels/{m2.group(1)}.html" if m2 else slug_id
            return ("chapter", ChapterInfo(self.name, series_id, chapter_id, chapter_id, url))
        m = re.search(r"ranobes\.net/(novels/\d+[^/]*\.html)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered HTML for series/chapter pages; the "
                                 "chapters listing is a Vue-mounted page but the same chapter "
                                 "data it mounts from is already present server-side as a plain "
                                 "`window.__DATA__` JSON blob in the static response, so no "
                                 "browser/JS execution is needed to read it",
            "browser_required": False,
            "search_method": "DataLife Engine's own `do=search` POST action (`generator` meta "
                             "tag confirms DLE), not a guessed endpoint",
            "domain_note": "ranobes.top (an alternate TLD for the same brand) hits a live "
                           "Cloudflare interactive challenge even on robots.txt -- confirmed "
                           "directly, not assumed -- so this adapter targets ranobes.net, the "
                           "domain that actually loads cleanly.",
        }
        caps.terms = {
            "robots_txt": "Disallows only /engine/ (DLE's admin path), a few listing/tag "
                          "pagination paths, and ia_archiver entirely -- none of the paths this "
                          "adapter fetches. (Fetched and read directly while building this "
                          "adapter.)",
            "tos": "/rules.html (\"The rules of the site\"), fetched and read directly: a "
                  "community-conduct policy (no insults, no spam, no account trading/selling). "
                  "Says nothing about automated access or scraping either way.",
            "tos_prohibited": False,
        }
        return caps
