"""
sources/adapters/52shuku.py -- 52shuku.net (zh, BL/GL/romance novels).

No Keiyoushi/Mihon extension exists for this site (checked directly, a
full local clone grepped for the name -- zero matches). Technique read
from real working scrapers instead: Moleys/vbook-ext (a vbook plugin)
and Lieatfhy/spiderNovel / AgonyNihility/novel (plain Python
requests+parsel), read directly, not ported:

  chapter list   GET /<category>/b/<id>.html          ul.list.clearfix > li.mulu > a
  chapter body   GET /<category>/b/<id>_<n>.html       article.article-content div#text > p

Plain server-rendered HTML, no JavaScript/decoding step -- unlike
manhuagui's packed-script case. Only the chapter-list and chapter-body
selectors above were independently confirmed against a real fetch;
search() has no verified real markup behind it (neither reference
scraper's search endpoint was itself read in enough detail to reimplement
faithfully), so it's left unsupported here rather than guessed --
matching this project's "never guess at behavior" rule. get_series()
similarly has no confirmed title/author/cover selector for this specific
site (that detail was recorded for xbanxia, not 52shuku) -- it returns
what a raw <title> tag safely gives, not an invented CSS selector.

Cloudflare sits in front of this site as a CDN, not an active challenge
(confirmed via a direct header check: cf-cache-status: HIT). But
404-novel-project/novel-downloader's own source notes 52shuku "is strict
about concurrency," configuring concurrencyLimit: 1 -- so this adapter
forces max_concurrent=1 regardless of the app's own (more permissive)
global pacing setting, rather than assuming a higher one is safe just
because one test fetch succeeded.
"""

import re
from urllib.parse import urljoin

from ..base import SourceAdapter
from .. import pacing
from ..http import PacingPolicy
from ..models import ChapterInfo, ContentAccess, ContentType, FailureReason, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.52shuku.net"


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"52shuku's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


@register
class FiftyTwoShukuSource(SourceAdapter):
    name = "52shuku"
    display_name = "52shuku.net"
    content_types = [ContentType.NOVEL.value]
    languages = ["zh"]
    # Fast covers book and chapter pages only. robots.txt disallows /so/, so
    # a search added later must be paced at normal, not fast.
    pacing_profile = pacing.PacingProfile(
        fast=pacing.PaceLevel(min_delay=1.0, max_delay=2.0, max_concurrent=2),
        evidence=("robots.txt fetched 2026-10-09: no Crawl-delay, /e/ /d/ /so/ disallowed "
                  "(adapter avoids them); banquan.html copyright notice has no "
                  "automated-access or rate clause."),
        fast_allowed=True)
    # A real, confirmed site change (2026-09-26 live check): the site's
    # real book-URL shape is now `/<category>/<N>_b/<alnum-id>.html` (e.g.
    # `/KeHuan/20_b/bkceK.html`), not the plain `/<category>/b/<id>` this
    # pattern originally assumed -- the `(?:\d+_)?` and `[A-Za-z0-9]+`
    # additions match both shapes.
    url_patterns = [r"52shuku\.(?:net|top|vip|org)/[^/]+/(?:\d+_)?b/[A-Za-z0-9]+"]
    default_headers = {"Referer": BASE_URL + "/", "Accept-Language": "zh-CN,zh;q=0.9"}

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        if client is None and "policy" not in client_kwargs:
            policy = pacing.for_source(PacingPolicy.from_settings(self.host_min_interval), self.name, self.pacing_profile)
            # Concurrency-sensitive (Cloudflare) per the module docstring --
            # this overrides even a more permissive global setting.
            policy.max_concurrent = 1
            client_kwargs["policy"] = policy
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._series_pages = {}

    def _get(self, path: str, action: str) -> str:
        resp = self.client.get(urljoin(self.base_url, path), action=action)
        return resp.text

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(series_id, f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else series_id
        # Strip a common "Title_SiteName" / "Title - SiteName" suffix a raw
        # <title> tag carries, without depending on any site-specific class.
        title = re.split(r"[-_]\s*52", title)[0].strip() or title
        return SeriesInfo(self.name, series_id, title, urljoin(self.base_url, series_id),
                          content_type=ContentType.NOVEL.value, language="zh")

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        items = soup.select("ul.list.clearfix > li.mulu > a")
        if not items:
            raise LayoutChanged("the chapter list")
        chapters = []
        for a in items:
            href = a.get("href", "")
            if not href:
                continue
            m = re.search(r"_(\d+)\.html", href) or re.search(r"/(\d+)\.html", href)
            chapter_id = m.group(1) if m else href
            chapters.append(ChapterInfo(self.name, series_id, chapter_id,
                                        a.get_text(strip=True) or chapter_id,
                                        urljoin(self.base_url, href)))
        return chapters

    def get_chapter_text(self, chapter) -> str:
        path = chapter.url or f"{chapter.series_id}_{chapter.chapter_id}.html"
        html = self._get(path, f"Loading chapter {chapter.title}")
        soup = _soup(html)
        # `div.content.contentmargin` is the real container confirmed live
        # (2026-09-26) -- the site moved off `article.article-content
        # div#text`/`div#text`, which are kept as fallbacks rather than
        # removed outright, in case an older template variant still uses them.
        container = (soup.select_one("div.content.contentmargin")
                    or soup.select_one("article.article-content div#text")
                    or soup.select_one("div#text"))
        if container is None:
            raise LayoutChanged("the chapter text container")
        paragraphs = [p.get_text(strip=True) for p in container.find_all("p")]
        text = "\n\n".join(p for p in paragraphs if p)
        if not text:
            raise LayoutChanged("any chapter text")
        return text

    def parse_url(self, url: str):
        # Same real book-URL shape as `url_patterns` above -- and, a second,
        # pre-existing bug fixed alongside it: the captured group never
        # includes `.html`, but `_series_page()`/`get_series()`/
        # `get_chapters()` all expect a series_id that does (matching the
        # shape a real search or listing result would carry), so both
        # branches append it back rather than returning a path that would
        # 404 when fetched.
        m = re.search(r"/([^/]+/(?:\d+_)?b/[A-Za-z0-9]+)_(\d+)\.html", url or "")
        if m:
            series_id, chapter_id = m.group(1) + ".html", m.group(2)
            return ("chapter", ChapterInfo(self.name, series_id, chapter_id, chapter_id, url))
        m = re.search(r"/([^/]+/(?:\d+_)?b/[A-Za-z0-9]+)\.html", url or "")
        return ("series", m.group(1) + ".html") if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered HTML, no JavaScript/decoding step",
            "browser_required": False,
            "concurrency": "forced to 1 -- site is Cloudflare-fronted and reported "
                           "concurrency-sensitive by an independent scraper",
            "reference": "Moleys/vbook-ext; Lieatfhy/spiderNovel; AgonyNihility/novel",
        }
        caps.terms = {
            "robots_txt": "Blocks only named crawlers (AhrefsBot, Baiduspider, 360Spider, "
                          "Sogou) plus a handful of internal paths (/e/*, /d/*, /so/*) -- "
                          "no blanket User-agent: * disallow. (Recorded from the roadmap's "
                          "direct check; not re-fetched while building this adapter.)",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps
