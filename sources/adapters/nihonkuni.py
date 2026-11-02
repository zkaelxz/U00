"""
sources/adapters/nihonkuni.py -- NihonKuni (nihonkuni.com, Japanese raw manga).

NihonKuni is a server-rendered PHP site, so plain GETs are enough and no
JavaScript runs. Selectors were checked against the live site on 2026-11-02:

  search      GET  /manga-list.html?name=<q>&page=<n>   cards: a.manga-title
                                                  href="manga-<slug>.html"
  series      GET  /manga-<slug>.html             redirects to
                                                  /manga/<slug>-<id>.html; og:title,
                                                  og:image, author / genre / status
                                                  blocks, and every chapter link
                                                  read-<slug>-chapter-<n>.html
  pages       GET  /read-<slug>-chapter-<n>.html  <img class="chapter-img" src=...>
                                                  in reading order

The page title is also sent base64-encoded in a data-enc attribute; the
plain og:title is used instead, so nothing is decoded.

Series and chapter ids come from URLs the site hands us and end up in
request paths and file names, so anything that isn't a plain slug is
refused rather than passed through.
"""

import re
from html import unescape
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter, layout_changed_class
from ..http_transport import header
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError)
from ..registry import register

BASE_URL = "https://nihonkuni.com"
REFERER = BASE_URL + "/"
HOST = "nihonkuni.com"

_SAFE_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,199}")
_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}")
_CARD = re.compile(r"<div class=\"manga-card\">(.*?)</div>\s*</div>", re.S)
_CARD_TITLE = re.compile(r"class=\"manga-title\"\s+href=\"manga-([^\"/]+)\.html\"[^>]*>(.*?)</a>", re.S)
_CARD_COVER = re.compile(r"url\('(https://[^'\" ]+)")
_OG = re.compile(r"<meta property=\"og:(title|image)\" content=\"([^\"]*)\"")
_COVER_TITLE = re.compile(r"<img[^>]*title=\"([^\"]*)\"[^>]*class=\"manga-cover-image\"")
_AUTHOR = re.compile(r"href='manga-author-[^']*'>([^<]+)</a>")
_GENRE = re.compile(r"class=\"btn btn-xs btn-danger\" href='manga-list-genre-[^']*'>([^<]+)</a>")
_STATUS = re.compile(r"info-field-label\">Status</div>\s*<div class=\"info-field-value\">(.*?)</div>", re.S)
_CHAPTER = re.compile(r"<a href=\"read-([a-z0-9_-]+)-chapter-([^\"/]+)\.html\"[^>]*>\s*"
                      r"<span class=\"chapter-name\">(.*?)</span>", re.S)
_PAGE_IMG = re.compile(r"<img\b[^>]*\bclass=\"chapter-img\"[^>]*\bsrc=\"(https://[^\"]+)\"", re.S)
_TAGS = re.compile(r"<[^>]+>")

LayoutChanged = layout_changed_class("NihonKuni")


def _safe_slug(value, what: str) -> str:
    slug = str(value or "")
    if not _SAFE_SLUG.fullmatch(slug):
        raise LayoutChanged(f"a usable {what}")
    return slug


def _safe_label(value) -> str:
    label = str(value or "")
    if not _SAFE_LABEL.fullmatch(label):
        raise LayoutChanged("a usable chapter id")
    return label


def _text(fragment: str) -> str:
    return unescape(_TAGS.sub("", fragment)).strip()


def _https(url) -> str:
    url = str(url or "")
    return url if urlsplit(url).scheme == "https" else ""


def _number_key(label: str) -> float:
    try:
        return float(label)
    except ValueError:
        return 0.0


def _series_url(series_id: str) -> str:
    return f"{BASE_URL}/manga-{series_id}.html"


def _chapter_url(series_id: str, label: str) -> str:
    return f"{BASE_URL}/read-{series_id}-chapter-{label}.html"


@register
class NihonKuniSource(SourceAdapter):
    name = "nihonkuni"
    display_name = "NihonKuni"
    content_types = [ContentType.MANGA.value]
    languages = ["ja"]
    url_patterns = [r"nihonkuni\.com/(?:manga[-/]|read-)[a-z0-9]"]
    # robots.txt asks for a one second crawl delay; Fast stays off because the site's terms
    # could not be read, and the sources pacing tests allow Fast only for vetted sites.
    host_min_interval = {HOST: 1.0}
    default_headers = {"Referer": REFERER}

    def search(self, query: str, page: int = 1):
        query = query.strip()
        if not query:
            return []
        resp = self.client.get(f"{BASE_URL}/manga-list.html?name={quote(query, safe='')}&page={max(page, 1)}",
                               action=f"Searching NihonKuni for {query!r}", use_cache=False)
        out, seen = [], set()
        for card in _CARD.findall(resp.text):
            title_m = _CARD_TITLE.search(card)
            if not title_m or title_m.group(1) in seen or not _SAFE_SLUG.fullmatch(title_m.group(1)):
                continue
            slug = title_m.group(1)
            seen.add(slug)
            cover = _CARD_COVER.search(card)
            out.append(SearchResult(self.name, slug, _text(title_m.group(2)), _series_url(slug),
                                    _https(cover.group(1)) if cover else ""))
        return out

    def _series_html(self, series_id: str) -> str:
        series_id = _safe_slug(series_id, "series id")
        return self.client.get(_series_url(series_id), action=f"Loading series {series_id}",
                               use_cache=False).text

    def get_series(self, series_id: str):
        html = self._series_html(series_id)
        og = {k: unescape(v) for k, v in _OG.findall(html)}
        romaji = og.get("title", "").split(" - Read ", 1)[0].strip()
        native = _COVER_TITLE.search(html)
        title = unescape(native.group(1)).strip() if native and native.group(1).strip() else romaji
        if not title:
            raise LayoutChanged("this series's title")
        status_m = _STATUS.search(html)
        status_text = _text(status_m.group(1)).lower() if status_m else ""
        status = "completed" if "complete" in status_text else "ongoing" if status_text else "unknown"
        return SeriesInfo(
            self.name, series_id, title, _series_url(series_id), _https(og.get("image")),
            authors=[unescape(a).strip() for a in dict.fromkeys(_AUTHOR.findall(html))],
            genres=[unescape(g).strip() for g in dict.fromkeys(_GENRE.findall(html))],
            status=status, content_type=ContentType.MANGA.value, language="ja")

    def get_chapters(self, series_id: str):
        html = self._series_html(series_id)
        rows, seen = [], set()
        for slug, label, name in _CHAPTER.findall(html):
            if slug != series_id or label in seen or not _SAFE_LABEL.fullmatch(label):
                continue
            seen.add(label)
            rows.append((_number_key(label), label, _text(name) or f"Chapter {label}"))
        if not rows:
            raise LayoutChanged("any chapters in this series")
        rows.sort()
        return [ChapterInfo(self.name, series_id, label, name, _chapter_url(series_id, label))
                for _, label, name in rows]

    def get_pages(self, chapter):
        url = _chapter_url(_safe_slug(chapter.series_id, "series id"), _safe_label(chapter.chapter_id))
        html = self.client.get(url, action=f"Loading chapter {chapter.title}").text
        urls = list(dict.fromkeys(_PAGE_IMG.findall(html)))
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
        path = urlsplit(url).path.strip("/")
        m = re.fullmatch(r"read-([a-z0-9_-]+?)-chapter-([^/]+)\.html", path)
        if m and _SAFE_SLUG.fullmatch(m.group(1)) and _SAFE_LABEL.fullmatch(m.group(2)):
            slug, label = m.groups()
            return ("chapter", ChapterInfo(self.name, slug, label, f"Chapter {label}",
                                           _chapter_url(slug, label)))
        m = re.fullmatch(r"manga-([a-z0-9_-]+)\.html", path)
        if m and _SAFE_SLUG.fullmatch(m.group(1)):
            return ("series", m.group(1))
        # The series page the site redirects to carries a trailing numeric id.
        m = re.fullmatch(r"manga/([a-z0-9_-]+?)-\d+\.html", path)
        if m and _SAFE_SLUG.fullmatch(m.group(1)):
            return ("series", m.group(1))
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML: the search list, the series page's meta tags and "
                                 "chapter links, and the chapter page's image tags; no JavaScript runs",
            "browser_required": False,
            "images": "s4.ihlv1.xyz JPEG/WebP files; served with or without a Referer, one is sent anyway",
        }
        caps.terms = {
            "robots_txt": "nihonkuni.com/robots.txt (fetched 2026-11-02): `User-agent: *` allows "
                          "everything except /admin/, /account/, /download/ and /search? paths; "
                          "`Crawl-delay: 1`. Content signals: `search=yes, ai-train=no, "
                          "use=reference`; the site reserves training use.",
            "read": "No terms page was found on the site (checked 2026-11-02).",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
