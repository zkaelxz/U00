"""
sources/adapters/miaoqumh.py -- 妙趣漫画 miaoqumh.org (zh manhua),
roadmap Step 23h.

Technique read from Keiyoushi's actively maintained Mihon extension
(keiyoushi/extensions-source, src/zh/miaoqu/Miaoqu.kt, built on the shared
`MCCMSWeb` multisrc base class -- Apache-2.0), then independently
re-verified end-to-end against the live site while building this adapter,
including running the real decode pipeline against a real chapter's data
(not assumed from reading the Kotlin source alone):

  listing     GET /category/order/hits/page/<n>    #mangawrap > li (a.manga-img
                                                    style="background: url(...)",
                                                    .manga-name, .manga-author)
  series      GET https://m.<host>/<slug>          .infobox (.title, first img,
                                                    .tage lines prefixed
                                                    作者：/类型：/更新于), .text
                                                    for description
  chapters    same mobile page                     ul.list > li > a
  pages       GET https://www.<host>/<series>/<chapter>.html
                                                    body contains
                                                    `var DATA='<base64>'`;
                                                    cid = int(chapter id from
                                                    the URL); key = one of 10
                                                    fixed 8-byte ASCII keys
                                                    selected by cid % 10;
                                                    base64-decode DATA, XOR
                                                    every byte against the
                                                    key (cyclic, key[i % 8]),
                                                    base64-decode the result
                                                    AGAIN, parse as JSON
                                                    `[{"id","url"}, ...]`.

The full decode pipeline (base64 -> XOR -> base64 -> JSON) was run against
a real fetched chapter while building this adapter and produced real,
correctly-shaped image URLs -- confirming both the key table and the
double-base64 step exactly as the extension's own source has them, not a
guessed shape.

**search() is deliberately left unsupported (base class default), not
guessed at.** Three real, live checks were tried while building this
adapter: `MCCMSWeb`'s own default search path (`/search/<query>/<page>`);
the site's own real, live "search by author" links embedded directly in
its listing pages (`/search?key=<query>`, copied verbatim from a real
page, not constructed); and the mobile host for the same path. All three
returned a plain HTTP 404 on direct fetch. This looks like a currently
broken/decommissioned search backend on the site's own end (the link
markup is real and present, the endpoint behind it isn't), not a
selector-writing mistake -- the same "don't guess when a real check comes
back empty" discipline `52shuku.py` already documents for its own
unsupported `search()`.

Cloudflare observed acting only as a CDN (`cf-cache-status` headers seen,
no challenge). `robots.txt` itself returned an HTTP 403 on direct fetch
(couldn't confirm its exact content) -- the site's actual content pages
are reachable and unchallenged regardless, the more load-bearing signal
per this project's own access-ladder logic (same posture already recorded
for this exact robots.txt behavior in the roadmap).
"""

import base64
import json
import re
from urllib.parse import urljoin

from ..base import SourceAdapter
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.miaoqumh.org"
MOBILE_BASE_URL = "https://m.miaoqumh.org"

# The 10 fixed 8-byte XOR keys, selected by (chapter id % 10). Verified
# character-for-character against the extension's own source and against
# a real decode of a live chapter while building this adapter.
_XOR_KEYS = ["8-bXd9iN", "8-RXyjry", "8-oYvwVy", "8-4ZY57U", "8-mbJpU7",
            "8-6MM2Ei", "8-54TiQr", "8-Ph5xx9", "8-bYgePR", "8-Z9A3bW"]

_DATA_VAR = re.compile(r"var\s+DATA\s*=\s*'([^']*)'")


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"miaoqumh's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def decode_page_data(html: str, cid: int) -> list:
    """base64 -> XOR (cyclic, 8-byte key keyed by cid % 10) -> base64 ->
    JSON `[{"id","url"}, ...]`. Verified against a real chapter's data
    while building this adapter (module docstring)."""
    m = _DATA_VAR.search(html or "")
    if not m:
        raise LayoutChanged("the chapter's DATA payload")
    key = _XOR_KEYS[cid % 10].encode("ascii")
    try:
        raw = base64.b64decode(m.group(1))
    except Exception as e:
        raise LayoutChanged(f"a readable base64 DATA payload ({e})") from None
    xored = bytes(b ^ key[i % 8] for i, b in enumerate(raw))
    try:
        decrypted = base64.b64decode(xored).decode("utf-8")
        items = json.loads(decrypted)
    except Exception as e:
        raise LayoutChanged(f"a readable page list after decoding ({e})") from None
    urls = [item.get("url", "") for item in items if isinstance(item, dict)]
    urls = [u for u in urls if u]
    if not urls:
        raise LayoutChanged("any page images in the decoded DATA payload")
    return urls


@register
class MiaoqumhSource(SourceAdapter):
    name = "miaoqumh"
    display_name = "妙趣漫画 Miaoqumh"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [r"(?:^|//|\.)miaoqumh\.org/\d+/\d+\.html", r"(?:^|//|\.)miaoqumh\.org/[^/?#]+/?$"]

    def __init__(self, client=None, base_url: str = None, mobile_base_url: str = None,
                **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self.mobile_base_url = mobile_base_url or MOBILE_BASE_URL
        self._series_pages = {}

    def _get(self, base: str, path: str, action: str) -> str:
        resp = self.client.get(urljoin(base, path), action=action)
        return resp.text

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        infobox = soup.select_one(".infobox")
        if infobox is None:
            raise LayoutChanged("the series info box")
        title_el = infobox.select_one(".title")
        if title_el is None:
            raise LayoutChanged("the series title")
        img = infobox.select_one("img")
        text_el = soup.select_one(".text")
        description = text_el.get_text(strip=True) if text_el is not None else ""
        author, genre = "", ""
        for tage in infobox.select(".tage"):
            text = tage.get_text(strip=True)
            prefix = text[:3]
            if prefix == "作者：":
                author = text[3:].strip()
            elif prefix == "类型：":
                genre = ", ".join(a.get_text(strip=True) for a in tage.select("a"))
            elif text[:3] == "更新于":
                description = f"{text}\n\n{description}" if description else text
        return SeriesInfo(
            self.name, series_id, title_el.get_text(strip=True),
            urljoin(self.mobile_base_url, f"/{series_id}"),
            img.get("src", "") if img is not None else "",
            authors=[author] if author else [],
            description=description,
            genres=[g.strip() for g in genre.split(",") if g.strip()],
            content_type=ContentType.MANHUA.value, language="zh")

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(self.mobile_base_url, f"/{series_id}",
                                                       f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        items = soup.select("ul.list > li > a")
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

    def get_pages(self, chapter):
        from ..models import PageRef
        path = chapter.url or f"/{chapter.series_id}/{chapter.chapter_id}.html"
        html = self._get(self.base_url, path, f"Loading chapter {chapter.title}")
        try:
            cid = int(chapter.chapter_id)
        except (TypeError, ValueError):
            m = re.search(r"/(\d+)\.html", path)
            if not m:
                raise LayoutChanged("a numeric chapter id in this chapter's URL") from None
            cid = int(m.group(1))
        urls = decode_page_data(html, cid)
        return [PageRef(self.name, chapter.chapter_id, i, u) for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        name = page.url.split("?", 1)[0]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        m = re.search(r"miaoqumh\.org/(\d+)/(\d+)\.html", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2), url))
        m = re.search(r"miaoqumh\.org/([^/?#]+)/?$", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML; page data is a base64 blob that's XOR-decoded "
                                 "against one of 10 fixed keys (selected by chapter id % 10), "
                                 "then base64-decoded again to a JSON page list -- the full "
                                 "pipeline was run against a real chapter and produced correct "
                                 "image URLs while building this adapter.",
            "browser_required": False,
            "search_unsupported": "the site's own real, live search links (copied verbatim from "
                                  "its own markup) 404 directly -- appears to be a currently "
                                  "broken/decommissioned endpoint on the site's own end, not a "
                                  "selector mistake here.",
            "reference": "keiyoushi/extensions-source src/zh/miaoqu (Apache-2.0), built on the "
                         "shared MCCMSWeb multisrc base class",
        }
        caps.terms = {
            "robots_txt": "Returned HTTP 403 on direct fetch -- exact content unconfirmed. "
                          "Content pages themselves are reachable and unchallenged regardless. "
                          "(Re-verified by direct fetch while building this adapter.)",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps
