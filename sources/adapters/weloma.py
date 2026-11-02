"""
sources/adapters/weloma.py -- Weloma (weloma.net, Japanese raw manga).

Weloma is a server-rendered PHP site, so plain GETs are enough and no
JavaScript runs. Selectors and endpoints were checked against the live site
on 2026-11-02:

  search      GET  /app/manga/controllers/search.single.php?q=<q>
                                                  JSON suggest list: title, cover,
                                                  url "/m/<code>" (one list, no paging)
  series      GET  /m/<code>                      breadcrumb title, cover, author,
                                                  genre and status items, and the
                                                  chapter links /c/<code> (newest first)
  pages       GET  /c/<code>                      one <img class="chapter-img"> per
                                                  page; its image URL is in data-img

The chapter page carries each image URL base64-encoded in `data-img`, and the
site's own lazy-loader calls atob() on it to set the image source. That is the
page's lazy-load format, not a protection: no key is involved and the image
files behind the URLs are ordinary unscrambled JPEGs. The adapter reads the
URL the same way and refuses anything that isn't an https URL.

Series and chapter ids come from URLs the site hands us and end up in
request paths and file names, so anything that isn't a plain code is refused
rather than passed through.
"""

import base64
import binascii
import json
import re
from html import unescape
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter, layout_changed_class
from ..http_transport import header
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError)
from ..registry import register

BASE_URL = "https://weloma.net"
REFERER = BASE_URL + "/"
HOST = "weloma.net"

_CODE = re.compile(r"[A-Za-z0-9]{3,16}")
_TITLE = re.compile(r"<li class=\"breadcrumb-item active\"[^>]*>(.*?)</li>", re.S)
_COVER = re.compile(r"<img class=\"thumbnail\"[^>]*\bsrc=\"(https://[^\"]+)\"")
_ITEM = r"<b>(?:<i[^>]*></i>\s*)*\s*{label}</b>\s*:(.*?)</li>"
_AUTHOR_ITEM = re.compile(_ITEM.format(label=r"Author\(s\)"), re.S)
_GENRE_ITEM = re.compile(_ITEM.format(label=r"Genre\(s\)"), re.S)
_STATUS_ITEM = re.compile(_ITEM.format(label="Status"), re.S)
_ITEM_LINK = re.compile(r"<a [^>]*>([^<]+)</a>")
_CHAPTER_LIST = re.compile(r"<ul class=\"list-chapters at-series\">(.*?)</ul>", re.S)
_CHAPTER_LINK = re.compile(r"<a href=\"/c/([A-Za-z0-9]+)\" title=\"([^\"]*)\"")
_PAGE_IMG = re.compile(r"<img\b[^>]*\bclass='chapter-img[^']*'[^>]*\bdata-img='([A-Za-z0-9+/=]+)'", re.S)
_TAGS = re.compile(r"<[^>]+>")

LayoutChanged = layout_changed_class("Weloma")


def _safe_code(value, what: str) -> str:
    code = str(value or "")
    if not _CODE.fullmatch(code):
        raise LayoutChanged(f"a usable {what}")
    return code


def _text(fragment: str) -> str:
    return unescape(_TAGS.sub("", fragment)).strip()


def _https(url) -> str:
    url = str(url or "")
    return url if urlsplit(url).scheme == "https" else ""


def _decode_image_url(encoded: str) -> str:
    try:
        return _https(base64.b64decode(encoded, validate=True).decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError):
        return ""


def _item_values(pattern, html: str) -> list:
    m = pattern.search(html)
    return [unescape(v).strip() for v in dict.fromkeys(_ITEM_LINK.findall(m.group(1)))] if m else []


@register
class WelomaSource(SourceAdapter):
    name = "weloma"
    display_name = "Weloma"
    content_types = [ContentType.MANGA.value]
    languages = ["ja"]
    url_patterns = [r"weloma\.net/m/[A-Za-z0-9]"]
    # robots.txt has no Crawl-delay and the site has no terms page to vet, so Fast stays off.
    host_min_interval = {HOST: 2.5}
    default_headers = {"Referer": REFERER}

    def search(self, query: str, page: int = 1):
        query = query.strip()
        if page > 1 or not query:
            # The suggest list is one fixed-size answer with no paging.
            return []
        resp = self.client.get(f"{BASE_URL}/app/manga/controllers/search.single.php?q={quote(query, safe='')}",
                               action=f"Searching Weloma for {query!r}", use_cache=False)
        try:
            groups = json.loads(resp.text)
            rows = [row for g in groups for row in g.get("data", [])]
        except (ValueError, AttributeError, TypeError):
            raise LayoutChanged("the search answer")
        out, seen = [], set()
        for row in rows:
            m = re.fullmatch(r"/m/([A-Za-z0-9]+)", str(row.get("url") or ""))
            title = _text(str(row.get("primary") or ""))
            if not m or not _CODE.fullmatch(m.group(1)) or m.group(1) in seen or not title:
                continue
            seen.add(m.group(1))
            out.append(SearchResult(self.name, m.group(1), title, f"{BASE_URL}/m/{m.group(1)}",
                                    _https(row.get("image"))))
        return out

    def _series_html(self, series_id: str) -> str:
        series_id = _safe_code(series_id, "series id")
        return self.client.get(f"{BASE_URL}/m/{series_id}", action=f"Loading series {series_id}",
                               use_cache=False).text

    def get_series(self, series_id: str):
        html = self._series_html(series_id)
        title_m = _TITLE.search(html)
        title = _text(title_m.group(1)) if title_m else ""
        if not title:
            raise LayoutChanged("this series's title")
        status_m = _STATUS_ITEM.search(html)
        status_text = _text(status_m.group(1)).lower() if status_m else ""
        status = "completed" if "complete" in status_text else "ongoing" if status_text else "unknown"
        cover = _COVER.search(html)
        return SeriesInfo(
            self.name, series_id, title, f"{BASE_URL}/m/{series_id}", cover.group(1) if cover else "",
            authors=_item_values(_AUTHOR_ITEM, html), genres=_item_values(_GENRE_ITEM, html),
            status=status, content_type=ContentType.MANGA.value, language="ja")

    def get_chapters(self, series_id: str):
        html = self._series_html(series_id)
        block = _CHAPTER_LIST.search(html)
        rows, seen = [], set()
        for code, title in _CHAPTER_LINK.findall(block.group(1) if block else ""):
            if code in seen or not _CODE.fullmatch(code):
                continue
            seen.add(code)
            rows.append((code, unescape(title).strip() or f"Chapter {code}"))
        if not rows:
            raise LayoutChanged("any chapters in this series")
        # The site lists newest first; the app wants oldest first.
        return [ChapterInfo(self.name, series_id, code, title, f"{BASE_URL}/c/{code}")
                for code, title in reversed(rows)]

    def get_pages(self, chapter):
        code = _safe_code(chapter.chapter_id, "chapter id")
        html = self.client.get(f"{BASE_URL}/c/{code}", action=f"Loading chapter {chapter.title}").text
        urls = list(dict.fromkeys(u for u in map(_decode_image_url, _PAGE_IMG.findall(html)) if u))
        if not urls:
            raise LayoutChanged("any page images in this chapter")
        return [PageRef(self.name, chapter.chapter_id, i, u, headers={"Referer": REFERER})
                for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        ctype = header(resp.headers, "content-type").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/"):
            # An error or block page sent with a 200: never saved as a page.
            raise SourceError(f"Page {page.index + 1} didn't come back as an image.",
                              FailureReason.HTTP_ERROR)
        name = urlsplit(page.url).path.rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        parts = [p for p in urlsplit(url).path.split("/") if p]
        # A chapter link carries no series code, so only series pages are recognised.
        if len(parts) == 2 and parts[0] == "m" and _CODE.fullmatch(parts[1]):
            return ("series", parts[1])
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML and one JSON suggest endpoint: the series page's "
                                 "breadcrumb, info items and chapter links, and the chapter page's "
                                 "image tags, whose data-img attribute is the image URL in plain "
                                 "base64 (the site's own lazy-load format, no key); no JavaScript runs",
            "browser_required": False,
            "images": "s4.ihlv1.xyz JPEG/WebP files, unscrambled; served with or without a Referer, "
                      "one is sent anyway",
        }
        caps.terms = {
            "robots_txt": "weloma.net/robots.txt (fetched 2026-11-02): `User-agent: *` with "
                          "`Allow: /` and no Crawl-delay; AI crawlers (ClaudeBot, anthropic-ai, "
                          "GPTBot and others) are disallowed by name. Content signals: "
                          "`search=yes, ai-train=no, use=reference`; the site reserves AI training.",
            "read": "No terms page was found on the site (checked 2026-11-02).",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
