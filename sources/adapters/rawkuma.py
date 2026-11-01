"""
sources/adapters/rawkuma.py -- Rawkuma (rawkuma.net, Japanese raw manga).

Rawkuma is a server-rendered WordPress site, so plain GETs are enough and
no JavaScript runs. Selectors and endpoints were checked against the live
site on 2026-11-01:

  search      GET  /wp-admin/admin-ajax.php?type=search_form&action=get_nonce
                                                  hidden `search_nonce` value
              POST /wp-admin/admin-ajax.php?nonce=<n>&action=search
                   body `search_nonce=<n>&query=<q>`   an HTML fragment of
                                                  <a href=".../manga/<slug>/">
                                                  cards (img + h3)
  series      GET  /manga/<slug>/                 ld+json ComicSeries (name,
                                                  author, genre, status) and
                                                  every chapter link
                                                  /manga/<slug>/chapter-<n>.<id>/
  pages       GET  /manga/<slug>/chapter-<n>.<id>/  <img src='https://cdn...'>
                                                  tags under /<slug>/chapter-<n>/

Series and chapter ids come from URLs the site hands us and end up in
request paths and file names, so anything that isn't a plain slug is
refused rather than passed through.
"""

import json
import re
from html import unescape
from urllib.parse import quote, urljoin, urlsplit

from ..base import SourceAdapter, layout_changed_class
from ..http_transport import header
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError)
from ..pacing import PaceLevel, PacingProfile
from ..registry import register

BASE_URL = "https://rawkuma.net"
AJAX_URL = BASE_URL + "/wp-admin/admin-ajax.php"
REFERER = BASE_URL + "/"
HOST = "rawkuma.net"

_SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
_NONCE = re.compile(r"name=['\"]search_nonce['\"]\s+value=['\"]([A-Za-z0-9]{4,40})['\"]")
_CARD = re.compile(r"<a\s+href=\"https?://rawkuma\.net/manga/([^\"/]+)/\"[^>]*>(.*?)</a>", re.S)
_CARD_IMG = re.compile(r"<img[^>]*\bsrc=\"([^\"]+)\"")
_CARD_TITLE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_CHAPTER_LINK = re.compile(r"href=\"\s*https?://rawkuma\.net/manga/([^\"/]+)/(chapter-([\d.]+?)\.(\d+))/\s*\"")
_PAGE_IMG = re.compile(r"<img[^>]*\bsrc=['\"](https://cdn\.rawkuma\.com/[^'\"]+)['\"]")
_LD_JSON = re.compile(r"<script type=\"application/ld\+json\">(.*?)</script>", re.S)
_TAGS = re.compile(r"<[^>]+>")

LayoutChanged = layout_changed_class("Rawkuma")


def _safe_slug(value, what: str) -> str:
    slug = str(value or "")
    if not _SAFE_SLUG.fullmatch(slug):
        raise LayoutChanged(f"a usable {what}")
    return slug


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


def _comic_ld(html: str) -> dict:
    for m in _LD_JSON.finditer(html or ""):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        types = data.get("@type") if isinstance(data, dict) else None
        if types and "ComicSeries" in (types if isinstance(types, list) else [types]):
            return data
    return {}


@register
class RawkumaSource(SourceAdapter):
    name = "rawkuma"
    display_name = "Rawkuma"
    content_types = [ContentType.MANGA.value]
    languages = ["ja"]
    url_patterns = [r"rawkuma\.net/manga/[A-Za-z0-9]"]
    # Slow on purpose: the site asks for nothing, so the floor is ours.
    host_min_interval = {HOST: 2.5}
    pacing_profile = PacingProfile(
        fast=PaceLevel(min_delay=1.5, max_delay=2.5, max_concurrent=1),
        evidence="robots.txt fetched 2026-11-01: only /wp-admin/ disallowed (admin-ajax allowed), "
                 "no Crawl-delay; no terms page found.",
        fast_allowed=True)
    default_headers = {"Referer": REFERER}

    def _nonce(self) -> str:
        resp = self.client.get(f"{AJAX_URL}?type=search_form&action=get_nonce",
                               action="Preparing the Rawkuma search", use_cache=False)
        m = _NONCE.search(resp.text)
        if not m:
            raise LayoutChanged("the search form's token")
        return m.group(1)

    def search(self, query: str, page: int = 1):
        query = query.strip()
        if page > 1 or not query:
            # The site's search box returns one fixed-size list with no paging.
            return []
        nonce = self._nonce()
        resp = self.client.post(
            f"{AJAX_URL}?nonce={nonce}&action=search",
            data=f"search_nonce={nonce}&query=" + quote(query, safe=""),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            action=f"Searching Rawkuma for {query!r}")
        out, seen = [], set()
        for slug, body in _CARD.findall(resp.text):
            title_m = _CARD_TITLE.search(body)
            if not _SAFE_SLUG.fullmatch(slug) or slug in seen or not title_m:
                continue
            seen.add(slug)
            img = _CARD_IMG.search(body)
            out.append(SearchResult(self.name, slug, _text(title_m.group(1)),
                                    f"{BASE_URL}/manga/{slug}/", _https(img.group(1)) if img else ""))
        return out

    def _series_html(self, series_id: str) -> str:
        series_id = _safe_slug(series_id, "series id")
        return self.client.get(f"{BASE_URL}/manga/{series_id}/", action=f"Loading series {series_id}",
                               use_cache=False).text

    def get_series(self, series_id: str):
        html = self._series_html(series_id)
        ld = _comic_ld(html)
        if not ld.get("name"):
            raise LayoutChanged("this series's details")
        author = ld.get("author")
        author_list = author if isinstance(author, list) else [author]
        authors = [a["name"] for a in author_list if isinstance(a, dict) and a.get("name")]
        raw_genres = ld.get("genre")
        genres = [str(g) for g in raw_genres] if isinstance(raw_genres, list) else []
        image = ld.get("image")
        cover = image.get("url") if isinstance(image, dict) else image
        done = str(ld.get("isCompleted")).lower() == "true"
        return SeriesInfo(
            self.name, series_id, unescape(str(ld["name"])), f"{BASE_URL}/manga/{series_id}/",
            _https(cover), authors=authors, description=unescape(str(ld.get("description") or "")),
            genres=genres,
            status="completed" if done else "ongoing",
            content_type=ContentType.MANGA.value, language="ja")

    def get_chapters(self, series_id: str):
        html = self._series_html(series_id)
        rows, seen = [], set()
        for slug, chapter_id, label, _site_id in _CHAPTER_LINK.findall(html):
            if slug != series_id or chapter_id in seen:
                continue
            seen.add(chapter_id)
            rows.append((_number_key(label), label, chapter_id))
        if not rows:
            raise LayoutChanged("any chapters in this series")
        rows.sort()
        return [ChapterInfo(self.name, series_id, chapter_id, f"Chapter {label}",
                            f"{BASE_URL}/manga/{series_id}/{chapter_id}/")
                for _, label, chapter_id in rows]

    def get_pages(self, chapter):
        path = f"/manga/{_safe_slug(chapter.series_id, 'series id')}/" \
               f"{_safe_slug(chapter.chapter_id, 'chapter id')}/"
        html = self.client.get(urljoin(BASE_URL, path), action=f"Loading chapter {chapter.title}").text
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
        parts = [p for p in urlsplit(url).path.split("/") if p]
        if len(parts) < 2 or parts[0] != "manga" or not _SAFE_SLUG.fullmatch(parts[1]):
            return None
        if len(parts) == 2:
            return ("series", parts[1])
        if len(parts) == 3 and parts[2].startswith("chapter-") and _SAFE_SLUG.fullmatch(parts[2]):
            return ("chapter", ChapterInfo(self.name, parts[1], parts[2], parts[2],
                                           f"{BASE_URL}/manga/{parts[1]}/{parts[2]}/"))
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML: the series page's ld+json and chapter links, the "
                                 "chapter page's image tags, and the site's public admin-ajax "
                                 "search; no JavaScript runs",
            "browser_required": False,
            "images": "cdn.rawkuma.com JPEGs; served without a Referer too, one is sent anyway",
        }
        caps.terms = {
            "robots_txt": "rawkuma.net/robots.txt (fetched 2026-11-01): `User-agent: *`, "
                          "`Disallow: /wp-admin/` with `Allow: /wp-admin/admin-ajax.php`; no "
                          "Crawl-delay.",
            "read": "No terms page was found on the site (checked 2026-11-01).",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps

