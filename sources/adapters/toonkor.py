"""
sources/adapters/toonkor.py -- 툰코 ToonKor (ko manhwa).

Technique read from Keiyoushi's actively maintained Mihon extension
(keiyoushi/extensions-source, src/ko/toonkor, Apache-2.0) and
reimplemented here -- no code is shared, only the observed protocol.
Every selector and the decode step below were independently re-verified
against the live site while building this adapter (not assumed from the
extension source or the roadmap alone):

  search      GET /bbs/search.php?sfl=wr_subject%7C%7Cwr_content&stx=<query>
                                                    div.section-item-inner
  listing     same container/selectors as search results
  series      GET /<slug>                          table.bt_view1
                                                    (td.bt_title/td.bt_label/
                                                    td.bt_over/td.bt_thumb img)
  chapters    same page                            table.web_list, rows
                                                    identified by
                                                    td.content__title, chapter
                                                    URL from that cell's own
                                                    data-role attribute
  pages       GET <chapter url>                     a <script> tag whose text
                                                    contains a `toon_img`
                                                    assignment
                                                    (`var toon_img = '<b64>'`);
                                                    the whole payload is a
                                                    single Base64 blob (no
                                                    XOR/second decode step,
                                                    unlike miaoqumh) that
                                                    decodes straight to an
                                                    HTML fragment of <img>
                                                    tags; page URLs are the
                                                    `src="..."` values in that
                                                    fragment, used exactly as
                                                    the site's own JS uses
                                                    them after decoding.

This is real, light obfuscation (hiding the image list from a page-source
view), not encryption and not JavaScript-execution-required -- a static
Base64 decode plus a regex is enough, the same thing the site's own
inline script does with `atob()`.

`toonkor0.org` is the current domain, but this site has a confirmed real
history of moving domains (the reason the roadmap explicitly calls out
re-verifying at build time rather than trusting the vetting-time domain).
Re-confirmed live immediately before writing this file: still the correct,
reachable, unchallenged domain. The domain is a list (`base_urls`,
sources/domains.py): the owner can edit it, the last one that worked is
remembered, and a redirect from a listed domain to a new host whose page
passes `verify_site` becomes a proposal for the owner to confirm -- a
rotation needs no code change.

`robots.txt` is fully permissive (`Allow: /`, no disallow lines);
Cloudflare (observed via response headers) acts only as a CDN in front of
the site, not as an active challenge -- no browser tier is needed for any
of this adapter's requests.
"""

import re
from urllib.parse import quote, urljoin

from ..base import SourceAdapter
from ..domains import SiteDomains
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SearchResult, SeriesInfo, SourceError
from ..pacing import PaceLevel, PacingProfile
from ..registry import register

BASE_URL = "https://toonkor0.org"

_TOON_IMG_SCRIPT = re.compile(r"var\s+toon_img\s*=\s*'([^']*)'")
_IMG_SRC = re.compile(r'src="([^"]*)"')


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"ToonKor's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def decode_toon_img(html: str) -> list:
    """Finds the `var toon_img = '<base64>'` assignment inside any <script>
    tag, Base64-decodes it to an HTML fragment, and returns every image
    URL in reading order -- the same thing the page's own inline script
    does with `atob()` before writing the fragment into the DOM."""
    import base64
    m = _TOON_IMG_SCRIPT.search(html or "")
    if not m:
        raise LayoutChanged("the chapter's toon_img script")
    try:
        decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
    except Exception as e:
        raise LayoutChanged(f"a readable toon_img payload ({e})") from None
    urls = _IMG_SRC.findall(decoded)
    if not urls:
        raise LayoutChanged("any page images inside the decoded toon_img payload")
    return urls


def _parse_listing(soup) -> list:
    out = []
    for item in soup.select("div.section-item-inner"):
        a = item.select_one("div.section-item-title a")
        if a is None:
            continue
        href = a.get("href", "")
        if not href:
            continue
        title = a.get("alt") or a.get_text(strip=True)
        img = item.select_one("img")
        cover = img.get("src", "") if img is not None else ""
        out.append((href, title, cover))
    return out


@register
class ToonkorSource(SourceAdapter):
    name = "toonkor"
    display_name = "툰코 ToonKor"
    content_types = [ContentType.MANHWA.value]
    languages = ["ko"]
    pacing_profile = PacingProfile(
        fast=PaceLevel(min_delay=1.0, max_delay=2.5, max_concurrent=2),
        evidence=("toonkor2.org robots.txt (2026-10-09): User-agent * Allow /, no Crawl-delay; no terms page found on the home page."),
        fast_allowed=True)
    # toonkor0.org today; the site has a real history of moving, so this
    # only anchors on "toonkor" plus a trailing digit and common TLDs
    # rather than hardcoding the current numeral.
    url_patterns = [r"toonkor\d*\.(?:org|com|net)"]
    base_urls = [BASE_URL]

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.site = SiteDomains(self, base_url)
        self._series_pages = {}

    @property
    def base_url(self) -> str:
        """The listed origin that last answered (links are resolved against it)."""
        return self.site.base

    @classmethod
    def verify_site(cls, text: str) -> bool:
        """A home or listing page: 툰코/ToonKor in the <title> and at least three
        webtoon cards in the listing markup search results also use
        (div.section-item-inner with a titled link), with distinct slugs."""
        soup = _soup(text)
        title = soup.title.get_text() if soup.title is not None else ""
        if "툰코" not in title and "toonkor" not in title.lower():
            return False
        slugs = {href.strip("/").rsplit("/", 1)[-1] for href, title_text, _ in _parse_listing(soup)
                 if title_text and href.strip("/")}
        return len(slugs) >= 3

    def _get(self, path: str, action: str) -> str:
        return self.site.get(path, action=action).text

    def search(self, query: str, page: int = 1):
        html = self._get(f"/bbs/search.php?sfl=wr_subject%7C%7Cwr_content&stx={quote(query.strip())}",
                         f"Searching ToonKor for {query!r}")
        soup = _soup(html)
        out = []
        for href, title, cover in _parse_listing(soup):
            slug = href.strip("/").rsplit("/", 1)[-1]
            if not slug or not title:
                continue
            out.append(SearchResult(self.name, slug, title, urljoin(self.base_url, href),
                                    urljoin(self.base_url, cover) if cover else ""))
        return out

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/{series_id}", f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        table = soup.select_one("table.bt_view1")
        if table is None:
            raise LayoutChanged("the series detail table")
        title_el = table.select_one("td.bt_title")
        if title_el is None:
            raise LayoutChanged("the series title")
        thumb = table.select_one("td.bt_thumb img")
        desc_el = table.select_one("td.bt_over")
        return SeriesInfo(
            self.name, series_id, title_el.get_text(strip=True),
            urljoin(self.base_url, f"/{series_id}"),
            urljoin(self.base_url, thumb.get("src", "")) if thumb is not None else "",
            description=desc_el.get_text(" ", strip=True) if desc_el is not None else "",
            content_type=ContentType.MANHWA.value, language="ko")

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        rows = soup.select("table.web_list tr:has(td.content__title)")
        if not rows:
            raise LayoutChanged("the chapter list")
        chapters = []
        for row in rows:
            cell = row.select_one("td.content__title")
            href = cell.get("data-role", "") if cell is not None else ""
            if not href:
                continue
            title = cell.get_text(strip=True)
            chapter_id = href.strip("/").rsplit("/", 1)[-1].removesuffix(".html")
            chapters.append(ChapterInfo(self.name, series_id, chapter_id, title or chapter_id,
                                        urljoin(self.base_url, href)))
        return chapters

    def get_chapter_pages_html(self, chapter) -> list:
        """Returns the decoded page-image URLs for a chapter -- shared by
        get_pages() and the base-64/script-decode test coverage."""
        html = self._get(chapter.url, f"Loading chapter {chapter.title}")
        return decode_toon_img(html)

    def get_pages(self, chapter):
        from ..models import PageRef
        urls = self.get_chapter_pages_html(chapter)
        return [PageRef(self.name, chapter.chapter_id, i, urljoin(self.base_url, u))
                for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        name = page.url.split("?", 1)[0]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        m = re.search(r"toonkor\d*\.(?:org|com|net)/([^/?#]+\.html)", url or "")
        if m:
            slug = m.group(1).removesuffix(".html")
            return ("chapter", ChapterInfo(self.name, "", slug, slug, url))
        m = re.search(r"toonkor\d*\.(?:org|com|net)/([^/?#]+)$", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML; page images come from a Base64-encoded "
                                 "`var toon_img = '...'` assignment inside a <script> tag, "
                                 "decoded the same way the page's own inline script decodes it "
                                 "(a plain base64 decode to an HTML fragment, then the src= "
                                 "attributes of that fragment's <img> tags)",
            "browser_required": False,
            "domain_note": "toonkor0.org confirmed live and current as of this adapter's build "
                           "date -- this site has a real history of moving domains, re-verify "
                           "before trusting url_patterns/base_url long-term.",
            "reference": "keiyoushi/extensions-source src/ko/toonkor (Apache-2.0)",
        }
        caps.terms = {
            "robots_txt": "User-agent: * with Allow: / and no Disallow lines -- fully "
                          "permissive. Cloudflare observed acting only as a CDN, not an active "
                          "challenge. (Re-verified by direct fetch while building this adapter.)",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps
