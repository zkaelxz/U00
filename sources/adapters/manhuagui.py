"""
sources/adapters/manhuagui.py -- 漫画柜 / ManHuaGui (zh), roadmap Step 23b.

Technique read from Keiyoushi's actively maintained Mihon extension
(keiyoushi/extensions-source, src/zh/manhuagui, Apache-2.0) and
reimplemented here -- no code is shared, only the observed protocol:

  search      GET /s/<query>_p<page>.html        div.book-result > ul > li
  series      GET /comic/<id>/                   div.book-title > h1, #intro-all, ...
  chapters    same page                          [id^=chapter-list-] ul > li > a.status0
  pages       GET /comic/<id>/<chapter>.html     page script: an LZString-compressed,
                                                 p.a.c.k.e.r-packed call carrying
                                                 {"files": [...], "path": ..., "sl": {e, m}}
  images      https://i.hamreus.com<path><file>?e=..&m=..   (Referer required)

Plain HTTP/HTML only -- no JavaScript is executed and no challenge
handling exists (none is needed; if one ever appears, the shared client
stops and hands it to the person like everywhere else). Decoding the
page script is the same unpacking the site's own script does for every
visitor; the image URL's e/m expiry token is used exactly as the page
issues it, never generated or altered.

Pacing: the main site's robots.txt asks generic clients for a 10-second
crawl delay, so every request to a manhuagui host waits at least 10s
after the last one (stricter than Keiyoushi's 10-per-10s default).
Image CDN requests use the normal 1-3s pace (well under Keiyoushi's 4/s).

Adult-flagged works: the site hides their chapter list (an
LZString-compressed #__VIEWSTATE) unless an `isAdult=1` cookie is sent --
the same thing its own "I'm an adult" switch sets in a browser. Off by
default; the person turns it on per source in the Sources tab
(`supports_adult_toggle`). Off, such a work fails with a ContentHidden
message naming that toggle. The cookie only goes to the main site, never
the image CDN (same as the extension's interceptor).
"""

import json
import re
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter
from ..lzstring import decompress_from_base64
from ..models import (ChapterInfo, ContentAccess, ContentHidden, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError)
from ..registry import register

MIRRORS = ["https://www.manhuagui.com", "https://tw.manhuagui.com",
           "https://www.mhgui.com", "https://tw.mhgui.com"]
IMAGE_SERVERS = ["https://i.hamreus.com", "https://cf.hamreus.com"]
CRAWL_DELAY = 10.0

_PACKED = re.compile(r'window\[".*?"\](\(.*\)\s*\{[\s\S]+\}\s*\(.*\))')
_PACKED_CONTENT = re.compile(r"""['"]([0-9A-Za-z+/=]+)['"]\[['"].*?['"]\]\(['"].*?['"]\)""")
_JSON_BLOCK = re.compile(r"\{.*\}", re.S)
# ASCII-only, like the JS packer's own \w -- Python's default \w would also
# match CJK, gluing a packed key to adjacent Chinese text ("第a话").
_WORD = re.compile(r"\w+", re.ASCII)


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"manhuagui's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _radix62(word: str) -> int:
    n = 0
    for ch in word:
        if "0" <= ch <= "9":
            d = ord(ch) - 48
        elif "a" <= ch <= "z":
            d = ord(ch) - 87
        elif "A" <= ch <= "Z":
            d = ord(ch) - 29
        else:
            return -1
        n = n * 62 + d
    return n


def unpack_packer(script: str) -> str:
    """Unpacks Dean Edwards' p.a.c.k.e.r output of the shape
    `...}('<payload>',62,N,'<w0|w1|...>'.split('|'),0,{}))`: every \\w+ token
    in the payload is a base-62 index into the word list."""
    start = script.find("}('")
    end = script.find(".split('|'),0,{}))", start)
    if start < 0 or end < 0:
        return ""
    packed = script[start + 3:end].replace("\\'", '"')
    sep = packed.find("',")
    if sep < 0:
        return ""
    payload = packed[:sep]
    rest = packed[sep + 2:]
    q1 = rest.find("'")
    q2 = rest.find("'", q1 + 1)
    if q1 < 0 or q2 < 0:
        return ""
    words = rest[q1 + 1:q2].split("|")

    def sub(m):
        key = m.group(0)
        i = _radix62(key)
        if 0 <= i < len(words) and words[i]:
            return words[i]
        return key
    return _WORD.sub(sub, payload)


def decode_image_data(html: str) -> dict:
    """The {"files", "path", "sl"} object a chapter page's script builds."""
    m = _PACKED.search(html or "")
    if not m:
        raise LayoutChanged("the chapter page's image script")
    code = m.group(1)

    def expand(match):
        decoded = decompress_from_base64(match.group(1))
        if decoded is None:
            raise LayoutChanged("readable page data in the image script")
        return "'" + decoded + "'.split('|')"
    code = _PACKED_CONTENT.sub(expand, code, count=1)
    unpacked = unpack_packer(code.replace("\\'", "-"))
    jm = _JSON_BLOCK.search(unpacked)
    if not jm:
        raise LayoutChanged("the image list inside the page script")
    try:
        return json.loads(jm.group(0))
    except ValueError as e:
        raise LayoutChanged(f"valid image data ({e})") from None


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _series_id_from_href(href: str) -> str:
    m = re.search(r"/comic/(\d+)", href or "")
    return m.group(1) if m else ""


@register
class ManhuaguiSource(SourceAdapter):
    name = "manhuagui"
    display_name = "漫画柜 ManHuaGui"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    # Chapters within a section are split across several <ul> blocks whose
    # inner order the fixtures pin as newest-first, so the raw list isn't
    # reliably in reading order.
    chapters_in_site_order = False
    url_patterns = [r"(?:^|//|\.)(?:manhuagui|mhgui)\.com/comic/\d+"]
    host_min_interval = {urlsplit(u).netloc: CRAWL_DELAY for u in MIRRORS}
    default_headers = {
        "Referer": MIRRORS[0] + "/",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    supports_adult_toggle = True

    def __init__(self, client=None, allow_adult: bool = None, mirrors=None, **client_kwargs):
        super().__init__(client, allow_adult=allow_adult, **client_kwargs)
        self.mirrors = list(mirrors or MIRRORS)
        # get_series() and get_chapters() read the same page; one adapter
        # instance fetches it once rather than hitting the site twice.
        self._series_pages = {}

    # -- plumbing ----------------------------------------------------------
    def _headers(self) -> dict:
        return {"Cookie": "isAdult=1"} if self.allow_adult else {}

    def _get(self, path: str, action: str):
        resp = self.client.get_with_mirrors(path, self.mirrors, headers=self._headers(),
                                            use_cache=False, action=action)
        return resp.text, getattr(resp, "mirror", self.mirrors[0])

    # -- interface -----------------------------------------------------------
    def search(self, query: str, page: int = 1):
        html, base = self._get(f"/s/{quote(query.strip())}_p{int(page)}.html",
                               f"Searching manhuagui for {query!r}")
        soup = _soup(html)
        items = soup.select("div.book-result > ul > li")
        if not items and not soup.select("div.book-result, div.result-none, .no-result"):
            raise LayoutChanged("the search results list")
        out = []
        for li in items:
            a = li.select_one("div.book-detail dl > dt > a")
            if a is None:
                continue
            href = a.get("href", "")
            sid = _series_id_from_href(href)
            if not sid:
                continue
            img = li.select_one("div.book-cover > a.bcover > img")
            cover = (img.get("src") or img.get("data-src") or "") if img else ""
            out.append(SearchResult(self.name, sid, a.get("title") or a.get_text(strip=True),
                                    base + href, _abs(cover)))
        return out

    def _series_page(self, series_id: str):
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/comic/{series_id}/",
                                                      f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html, base = self._series_page(series_id)
        return self._parse_series(series_id, html, base)

    def _parse_series(self, series_id, html, base):
        soup = _soup(html)
        h1 = soup.select_one("div.book-title > h1")
        if h1 is None:
            raise LayoutChanged("the series title")
        cover = soup.select_one("p.hcover > img")

        def labelled(*labels):
            # Same as the extension's `span:contains(label) > a`.
            for span in soup.find_all("span"):
                if any(lbl in span.get_text() for lbl in labels):
                    links = span.find_all("a", recursive=False)
                    if links:
                        return [a.get_text(strip=True) for a in links]
            return []
        status_el = soup.select_one("div.book-detail > ul.detail-list > li.status > span > span")
        status_txt = status_el.get_text(strip=True) if status_el else ""
        status = {"连载中": "ongoing", "連載中": "ongoing",
                  "已完结": "completed", "已完結": "completed"}.get(status_txt, "unknown")
        intro = soup.select_one("div#intro-all")
        return SeriesInfo(self.name, series_id, h1.get_text(strip=True),
                          f"{base}/comic/{series_id}/",
                          _abs(cover.get("src") or cover.get("data-src") or "") if cover else "",
                          authors=labelled("漫画作者", "漫畫作者"),
                          description=intro.get_text(" ", strip=True) if intro else "",
                          genres=labelled("漫画剧情", "漫畫劇情"), status=status,
                          content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        html, base = self._series_page(series_id)
        return self._parse_chapters(series_id, html, base)

    def _parse_chapters(self, series_id, html, base):
        soup = _soup(html)
        hidden = soup.select_one("#__VIEWSTATE")
        if hidden is not None:
            if not self.allow_adult:
                raise ContentHidden(self.adult_hidden_message("this work's chapter list"),
                                    FailureReason.COOKIE_REQUIRED)
            decoded = decompress_from_base64(hidden.get("value") or "")
            if not decoded:
                raise LayoutChanged("the hidden chapter list")
            fragment = _soup(decoded)
            placeholder = soup.select_one("#erroraudit_show")
            if placeholder is not None:
                placeholder.replace_with(fragment)
            else:
                (soup.body or soup).append(fragment)
            hidden.decompose()

        sections = soup.select("[id^=chapter-list-]")
        if not sections:
            raise LayoutChanged("the chapter list")
        chapters = []
        for section in sections:
            heading = section.find_previous_sibling("h4")
            group = heading.get_text(strip=True) if heading else ""
            for ul in reversed(section.find_all("ul")):
                for a in ul.select("li > a.status0"):
                    href = a.get("href", "")
                    m = re.search(r"/comic/\d+/(\d+)\.html", href)
                    if not m:
                        continue
                    span = a.find("span")
                    title = a.get("title") or (
                        (span.find(string=True, recursive=False) or "").strip() if span else "")
                    chapters.append(ChapterInfo(self.name, series_id, m.group(1),
                                                title or m.group(1), base + href, group=group))
        return chapters

    def get_pages(self, chapter):
        path = urlsplit(chapter.url).path if chapter.url else \
            f"/comic/{chapter.series_id}/{chapter.chapter_id}.html"
        html, base = self._get(path, f"Loading chapter {chapter.title}")
        if "erroraudit_show" in html and not self.allow_adult:
            raise ContentHidden(self.adult_hidden_message("this chapter"),
                                FailureReason.COOKIE_REQUIRED)
        data = decode_image_data(html)
        files = data.get("files") or []
        if not files:
            raise LayoutChanged("any page images for this chapter")
        sl = data.get("sl") or {}
        referer = base + "/"
        return [PageRef(self.name, chapter.chapter_id, i,
                        f"{data.get('path') or ''}{f}?e={sl.get('e', '')}&m={sl.get('m', '')}",
                        headers={"Referer": referer})
                for i, f in enumerate(files)]

    def download_page(self, page):
        """`page.url` is the CDN path+query; tried against each image server."""
        resp = self.client.get_with_mirrors(page.url, IMAGE_SERVERS, headers=page.headers,
                                            classify_body=False,
                                            action=f"Downloading page {page.index + 1}")
        name = page.url.split("?", 1)[0]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        m = re.search(r"/comic/(\d+)/(\d+)\.html", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2),
                                           MIRRORS[0] + f"/comic/{m.group(1)}/{m.group(2)}.html"))
        m = re.search(r"/comic/(\d+)", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML; chapter page script is LZString + p.a.c.k.e.r "
                                 "packed and unpacked here the same way the page does it",
            "browser_required": False,
            "mirrors": MIRRORS,
            "image_servers": IMAGE_SERVERS,
            "notes": "Image URLs carry an e/m expiry token issued by the chapter page; used as "
                     "issued. Adult-flagged works need the isAdult cookie, sent only when the "
                     "Sources tab's adult toggle is on for this source.",
            "reference": "keiyoushi/extensions-source src/zh/manhuagui (Apache-2.0)",
        }
        caps.terms = {
            "robots_txt": "User-agent: * -> Crawl-delay: 10; Disallow: /yzadmin/, /lib/. "
                          "Separately names and disallows specific crawlers, including AI "
                          "crawlers GPTBot, ClaudeBot, Claude-SearchBot, meta-externalagent, "
                          "Bytespider and CCBot. (Recorded from the roadmap's direct curl "
                          "check; not re-fetched while building this adapter.)",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps


def _abs(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url
