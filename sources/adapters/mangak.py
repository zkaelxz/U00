"""
sources/adapters/mangak.py -- MangaK (mangak.io, English manga/manhwa/manhua).

MangaK is a Next.js site: every page carries its data as server-rendered
JSON in `<script id="__NEXT_DATA__">` (props.pageProps), so plain GETs
are enough and no JavaScript runs. The endpoints were first read from
Yui007/mangak-downloader (MIT) and then re-checked against the live site
on 2026-10-03; no code is shared.

  search      GET /search?q=<query>[&page=<n>]         pageProps.ssrItems
  series      GET /<slug>                              pageProps.initialManga
                                                       (`id`, `cv`)
  chapters    GET api.mangak.io/titles/<id>/chapters?cv=<cv>
                                                       JSON data.chapters
                                                       (the series page only
                                                       embeds the latest 50)
  pages       GET /<slug>/<chapter slug>               pageProps.initialChapter
                                                       .pages[].url
  images      GET <cdn url> (rx.qvzr*.org)             webp; answers 403
                                                       without a mangak.io
                                                       Referer

Series and chapter ids are the site's own slugs, which end up in URLs and
may end up in file names, so anything that isn't a plain slug is refused
rather than passed through.
"""

import json
import re
from urllib.parse import quote, urljoin, urlsplit

from ..base import SourceAdapter
from ..http import _header
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      PageRef, SearchResult, SeriesInfo, SourceError)
from ..registry import register

BASE_URL = "https://mangak.io"
API_URL = "https://api.mangak.io"
REFERER = BASE_URL + "/"

_NEXT_DATA = re.compile(r'<script[^>]*\bid="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,199}")
# First path segments that are site pages, not series.
_RESERVED = {"search", "genres", "authors", "tags", "browse", "ranking", "trending", "dmca",
             "terms-of-service", "privacy-policy", "contact", "login", "register", "user",
             "bookmarks", "history", "_next", "api"}
_TYPES = {t.value for t in (ContentType.MANGA, ContentType.MANHWA, ContentType.MANHUA)}


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup or its embedded data. Reported plainly, never
    guessed around."""

    def __init__(self, what: str):
        super().__init__(f"MangaK's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _safe_slug(value, what: str) -> str:
    slug = str(value or "")
    if not _SAFE_SLUG.fullmatch(slug):
        raise LayoutChanged(f"a usable {what} (got {slug[:60]!r})")
    return slug


def page_props(html: str) -> dict:
    """pageProps from the page's __NEXT_DATA__ JSON."""
    m = _NEXT_DATA.search(html or "")
    if not m:
        raise LayoutChanged("this page's embedded __NEXT_DATA__")
    try:
        props = json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError, TypeError):
        raise LayoutChanged("readable page data in __NEXT_DATA__") from None
    if not isinstance(props, dict):
        raise LayoutChanged("readable page data in __NEXT_DATA__")
    return props


def _https(url) -> str:
    url = str(url or "")
    return url if urlsplit(url).scheme == "https" else ""


@register
class MangaKSource(SourceAdapter):
    name = "mangak"
    display_name = "MangaK"
    content_types = [ContentType.MANGA.value, ContentType.MANHWA.value, ContentType.MANHUA.value]
    languages = ["en"]
    url_patterns = [r"mangak\.io/[A-Za-z0-9]"]
    default_headers = {"Referer": REFERER}

    def __init__(self, client=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self._manga = {}

    def _props(self, path: str, action: str, use_cache: bool = True) -> dict:
        resp = self.client.get(urljoin(BASE_URL, path), action=action, use_cache=use_cache)
        return page_props(resp.text)

    def _manga_data(self, series_id: str) -> dict:
        series_id = _safe_slug(series_id, "series id")
        if series_id not in self._manga:
            # Not cached: `cv` (the chapter list's cache version) changes
            # whenever a chapter is added.
            manga = self._props(f"/{series_id}", f"Loading series {series_id}",
                                use_cache=False).get("initialManga")
            if not isinstance(manga, dict) or not manga.get("id"):
                raise LayoutChanged("this series's details")
            self._manga[series_id] = manga
        return self._manga[series_id]

    def search(self, query: str, page: int = 1):
        path = f"/search?q={quote(query.strip())}" + (f"&page={int(page)}" if page > 1 else "")
        items = self._props(path, f"Searching MangaK for {query!r}", use_cache=False).get("ssrItems")
        if not isinstance(items, list):
            raise LayoutChanged("the search results")
        out = []
        for item in items:
            slug = str(item.get("slug") or "")
            if not _SAFE_SLUG.fullmatch(slug) or not item.get("name"):
                continue
            out.append(SearchResult(self.name, slug, item["name"], f"{BASE_URL}/{slug}",
                                    _https(item.get("cover")),
                                    extra={"status": str(item.get("status") or "").lower(),
                                           "adult": bool(item.get("isAdult")),
                                           "chapters": (item.get("stats") or {}).get("chaptersCount")}))
        return out

    def get_series(self, series_id: str):
        manga = self._manga_data(series_id)
        kind = str((manga.get("type") or {}).get("slug") or "")
        status = str(manga.get("status") or "").lower()
        return SeriesInfo(
            self.name, series_id, manga.get("name") or series_id, f"{BASE_URL}/{series_id}",
            _https(manga.get("cover")),
            authors=[a["name"] for a in manga.get("authors") or [] if a.get("name")],
            description=manga.get("summary") or "",
            genres=[g["name"] for g in manga.get("genres") or [] if g.get("name")],
            status=status if status in ("ongoing", "completed") else "unknown",
            content_type=kind if kind in _TYPES else ContentType.MANGA.value, language="en")

    def get_chapters(self, series_id: str):
        manga = self._manga_data(series_id)
        url = f"{API_URL}/titles/{quote(str(manga['id']), safe='')}/chapters"
        if manga.get("cv"):
            url += f"?cv={quote(str(manga['cv']), safe='')}"
        resp = self.client.get(url, action=f"Loading chapters of {series_id}", use_cache=False)
        try:
            rows = json.loads(resp.content)["data"]["chapters"]
        except (ValueError, KeyError, TypeError):
            rows = None
        if not isinstance(rows, list) or not rows:
            raise LayoutChanged("any chapters in this series's chapter list")
        # The API lists newest first; `number` is the site's own sequence.
        rows = sorted(rows, key=lambda r: r.get("number") if isinstance(r.get("number"), (int, float))
                      else 0)
        chapters = []
        for row in rows:
            slug = _safe_slug(row.get("slug"), "chapter id")
            chapters.append(ChapterInfo(self.name, series_id, slug, row.get("name") or slug,
                                        f"{BASE_URL}/{series_id}/{slug}"))
        return chapters

    def get_pages(self, chapter):
        if chapter.series_id:
            path = f"/{_safe_slug(chapter.series_id, 'series id')}/" \
                   f"{_safe_slug(chapter.chapter_id, 'chapter id')}"
        else:
            path = chapter.url
        info = self._props(path, f"Loading chapter {chapter.title}").get("initialChapter")
        if not isinstance(info, dict):
            raise LayoutChanged("this chapter's details")
        urls = [p.get("url") for p in info.get("pages") or [] if isinstance(p, dict)] \
            or list(info.get("images") or [])
        urls = [u for u in map(_https, urls) if u]
        if not urls:
            raise LayoutChanged("any page images in this chapter")
        return [PageRef(self.name, chapter.chapter_id, i, u, headers={"Referer": REFERER})
                for i, u in enumerate(urls)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        ctype = _header(resp.headers, "content-type").split(";")[0].strip().lower()
        if ctype and not ctype.startswith("image/"):
            # An error or block page sent with a 200: never saved as a page.
            raise SourceError(f"Page {page.index + 1} came back as {ctype[:40]}, not an image.",
                              FailureReason.HTTP_ERROR)
        name = urlsplit(page.url).path.rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".webp"
        return resp.content, ext

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        parts = [p for p in urlsplit(url).path.split("/") if p]
        if not parts or parts[0] in _RESERVED or not _SAFE_SLUG.fullmatch(parts[0]):
            return None
        if len(parts) == 1:
            return ("series", parts[0])
        if len(parts) == 2 and _SAFE_SLUG.fullmatch(parts[1]):
            return ("chapter", ChapterInfo(self.name, parts[0], parts[1], parts[1],
                                           f"{BASE_URL}/{parts[0]}/{parts[1]}"))
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML: the page's own __NEXT_DATA__ JSON, plus the "
                                 "site's public chapter-list JSON (api.mangak.io); no JavaScript "
                                 "runs",
            "browser_required": False,
            "images": "CDN webp images that need a mangak.io Referer, which every page request "
                      "sends",
            "adult": "the site marks some works isAdult but serves them without a switch; search "
                     "results carry the flag in `extra`",
        }
        caps.terms = {
            "robots_txt": "mangak.io/robots.txt and api.mangak.io/robots.txt both answer 404 "
                          "(fetched 2026-10-03): no rules, no crawl delay.",
            "read": "https://mangak.io/terms-of-service (titled MangaBuddy, \"Last updated: March "
                    "2025\"), read directly 2026-10-03.",
            "clause": "4. User Conduct: \"You agree not to: ... Use automated tools, bots, or "
                      "scrapers to access the service\". Section 2 says the site hosts nothing "
                      "itself and that content is \"sourced from third-party providers\".",
            "tos_prohibited": True,
            "enforcement_note": "ToS/robots.txt enforcement is off app-wide (user decision "
                                "2026-09-27, sources/ladder.py check_terms); the finding is "
                                "recorded here, not enforced by this adapter.",
        }
        caps.automation_permission = AutomationPermission.EXPLICITLY_RESTRICTED.value
        return caps
