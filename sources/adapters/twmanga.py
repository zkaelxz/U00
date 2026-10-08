"""
sources/adapters/twmanga.py -- 包子漫畫 on its regional mirrors
www.twmanga.com and www.twbzmg.com (zh manhua, traditional script).

Same brand as the apex baozimh.com (which sits behind a gatekeeper
challenge and is never fetched), but a different site from the
godamh/baozimh.org family in baozimh.py: it is a Nuxt SSR app with its own
URL scheme. Checked against the live site on 2026-10-08, a handful of
requests at least 5 s apart (docs/research/twmanga-vetting.md has the
earlier check; its redirect chain and image delivery notes apply here):

  search     GET /search?q=<query>              .comics-card (a.comics-card__poster
                                                href=/comic/<slug>, h3 title)
  series     GET /comic/<slug>                  h1.comics-detail__title,
                                                h2.comics-detail__author,
                                                .tag-list .tag (first is the
                                                status), p.comics-detail__desc
  chapters   same page                          a.comics-chapters__item, href
                                                /user/page_direct?comic_id=..
                                                &section_slot=S&chapter_slot=N
  pages      GET /comic/chapter/<slug>/S_N.html amp-img.comic-contain__item
                                                src=https://s1.bzcdn.net/...jpg

Things that are easy to get wrong:

* Chapter order is not the DOM order. A short series lists newest first
  under "最新章節"; a long one lists oldest first, with only the first
  chapters in #chapter-items and the rest in the hidden
  #chapters_other_list. Both are in the static HTML, so the adapter reads
  every link and sorts by (section_slot, chapter_slot).
* The chapter link is a page_direct redirect that the site answers with a
  302 to the other mirror. The slots in the link are the chapter's own
  address, so the chapter URL is built from them and the redirect is never
  requested.
* Image URLs are plain, unsigned and need no Referer or cookie. The URL
  comes out of fetched HTML, so the adapter only requests numbered shards
  of the site's CDN (s1-s9.bzcdn.net) and refuses a download whose final
  URL is anywhere else.
* Page and series URLs may be served by either mirror. SourceClient has no
  per-request redirect control, so a redirect to another host is followed
  by the transport (each hop still passes url_guard.resolve_public) before
  the adapter sees the final URL; the adapter then refuses the response if
  it ended on any other host, or off https. The adapter never requests
  such a host itself; it cannot stop a mirror from sending it there.

Not verified: series with more than one section_slot, chapters whose
images are split over several pages, search pagination (the first page
already returned ~90 hits, so only page 1 is offered), and whether the
site rate-limits faster than the pacing below.
"""

import re
from urllib.parse import parse_qs, quote, urlsplit

from ..base import SourceAdapter
from ..models import (ChapterInfo, ContentAccess, ContentType, FailureReason, PageRef,
                      SearchResult, SeriesInfo, SourceError)
from ..registry import register

PAGE_HOSTS = ("www.twmanga.com", "www.twbzmg.com")
MIRRORS = [f"https://{h}" for h in PAGE_HOSTS]
# Only s1 was seen live; the numbered pattern is an assumption about how the
# CDN shards, kept to single digits so the pacing floors below can name
# every accepted host (the client's floors are per exact host).
IMAGE_HOSTS = tuple(f"s{n}.bzcdn.net" for n in range(1, 10))

# Floors on top of the global pace, which is already 3-8 s per request.
# The CDN gets the same floor: nothing was measured that justifies faster.
PAGE_INTERVAL = 5.0
HOST_INTERVALS = {h: PAGE_INTERVAL for h in (*PAGE_HOSTS, *IMAGE_HOSTS)}

_SLUG = r"[A-Za-z0-9_-]+"
# fullmatch with explicit ASCII classes: `$` also matches before a trailing
# newline, and `\d` / str.isdigit() accept non-ASCII digits like "²".
_SLUG_RE = re.compile(_SLUG)
_CHAPTER_ID_RE = re.compile(r"([0-9]+)_([0-9]+)")
_DIGITS_RE = re.compile(r"[0-9]+")
_SERIES_URL = re.compile(rf"/comic/({_SLUG})/?\Z")
_CHAPTER_URL = re.compile(rf"/comic/chapter/({_SLUG})/(\d+_\d+)\.html\Z")
_STATUS = {"連載中": "ongoing", "连载中": "ongoing", "已完結": "completed", "已完结": "completed",
           "完結": "completed", "完结": "completed"}


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"twmanga's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


class WrongHost(SourceError):
    def __init__(self, what: str):
        super().__init__(f"twmanga was asked for {what} outside its own sites, so it was "
                         "refused.", FailureReason.ACCESS_DENIED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def is_page_host(url: str) -> bool:
    try:
        parts = urlsplit(url or "")
        return parts.scheme == "https" and (parts.hostname or "").lower() in PAGE_HOSTS
    except ValueError:
        return False


def is_image_url(url: str) -> bool:
    try:
        parts = urlsplit(url or "")
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    return parts.scheme == "https" and host in IMAGE_HOSTS


def _chapter_path(slug: str, chapter_id: str) -> str:
    return f"/comic/chapter/{slug}/{chapter_id}.html"


def _slots(href: str):
    """(comic slug, "section_chapter") from a page_direct link, or None."""
    parts = urlsplit(href or "")
    if not parts.path.endswith("/user/page_direct"):
        return None
    q = parse_qs(parts.query)
    slug = (q.get("comic_id") or [""])[0]
    section = (q.get("section_slot") or [""])[0]
    chapter = (q.get("chapter_slot") or [""])[0]
    if not (_SLUG_RE.fullmatch(slug) and _DIGITS_RE.fullmatch(section)
            and _DIGITS_RE.fullmatch(chapter)):
        return None
    try:
        return slug, f"{int(section)}_{int(chapter)}"
    except ValueError:          # int() refuses absurdly long digit runs
        raise LayoutChanged("a valid chapter link") from None


@register
class TwmangaSource(SourceAdapter):
    name = "twmanga"
    display_name = "包子漫畫 Twmanga/Twbzmg"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [r"(?:twmanga|twbzmg)\.com/(?:comic/|user/page_direct\?)"]
    host_min_interval = dict(HOST_INTERVALS)

    def __init__(self, client=None, mirrors=None, **client_kwargs):
        if client is None and "policy" not in client_kwargs:
            from ..http import PacingPolicy
            policy = PacingPolicy.from_settings(self.host_min_interval)
            # The site has no stated limit; one request at a time is the
            # conservative choice whatever the global setting says.
            policy.max_concurrent = 1
            client_kwargs["policy"] = policy
        super().__init__(client, **client_kwargs)
        self.mirrors = list(mirrors or MIRRORS)
        self._series_pages = {}

    def _get(self, path: str, action: str):
        resp = self.client.get_with_mirrors(path, self.mirrors, action=action)
        # Checked after the fact: the transport has already followed any
        # redirect, so this refuses the response, it does not prevent the GET.
        if not is_page_host(resp.url or getattr(resp, "mirror", "")):
            raise WrongHost("a page")
        return resp.text, getattr(resp, "mirror", self.mirrors[0])

    def search(self, query: str, page: int = 1):
        if int(page) > 1:
            return []
        html, base = self._get(f"/search?q={quote(query.strip())}",
                               f"Searching twmanga for {query!r}")
        out, seen = [], set()
        for card in _soup(html).select(".comics-card"):
            a = card.select_one("a.comics-card__poster")
            href = urlsplit(a.get("href", "")) if a is not None else None
            # A card linking to another site is not a series on this one.
            m = _SERIES_URL.search(href.path) if href is not None \
                and (not href.netloc or href.hostname in PAGE_HOSTS) else None
            if m is None or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            h3 = card.select_one("h3")
            img = card.select_one("amp-img[src]")
            out.append(SearchResult(self.name, m.group(1),
                                    h3.get_text(strip=True) if h3 else a.get("title", m.group(1)),
                                    f"{base}/comic/{m.group(1)}",
                                    img.get("src", "") if img is not None else ""))
        return out

    def _series_page(self, series_id: str):
        if not _SLUG_RE.fullmatch(series_id or ""):
            raise LayoutChanged("a valid series id")
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/comic/{series_id}",
                                                       f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html, base = self._series_page(series_id)
        soup = _soup(html)
        title_el = soup.select_one("h1.comics-detail__title")
        if title_el is None:
            raise LayoutChanged("the series title")
        author_el = soup.select_one("h2.comics-detail__author")
        desc_el = soup.select_one("p.comics-detail__desc")
        cover = soup.select_one(".de-info__box amp-img[src]")
        tags = [t.get_text(strip=True) for t in soup.select(".tag-list .tag")]
        tags = [t for t in tags if t]
        status = next((_STATUS[t] for t in tags if t in _STATUS), "unknown")
        return SeriesInfo(
            self.name, series_id, title_el.get_text(strip=True), f"{base}/comic/{series_id}",
            cover.get("src", "") if cover is not None else "",
            authors=[author_el.get_text(strip=True)] if author_el is not None
            and author_el.get_text(strip=True) else [],
            description=desc_el.get_text(strip=True) if desc_el is not None else "",
            genres=[t for t in tags if t not in _STATUS],
            status=status, content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        html, base = self._series_page(series_id)
        found = {}
        for a in _soup(html).select("a.comics-chapters__item"):
            slots = _slots(a.get("href", ""))
            if slots is None or slots[0] != series_id:
                continue
            found.setdefault(slots[1], a.get_text(strip=True) or slots[1])
        if not found:
            raise LayoutChanged("the chapter list")
        try:
            ordered = sorted(found, key=lambda cid: tuple(int(n) for n in cid.split("_")))
        except ValueError:      # int() refuses absurdly long digit runs
            raise LayoutChanged("a valid chapter list") from None
        return [ChapterInfo(self.name, series_id, cid, found[cid],
                            base + _chapter_path(series_id, cid)) for cid in ordered]

    def get_pages(self, chapter):
        if not (_SLUG_RE.fullmatch(chapter.series_id or "") and _CHAPTER_ID_RE.fullmatch(chapter.chapter_id or "")):
            raise LayoutChanged("a valid chapter address")
        html, _ = self._get(_chapter_path(chapter.series_id, chapter.chapter_id),
                            f"Loading chapter {chapter.title}")
        urls = []
        for img in _soup(html).select("amp-img.comic-contain__item"):
            url = img.get("src") or img.get("data-src") or ""
            if url and url not in urls:
                urls.append(url)
        if not urls:
            raise LayoutChanged("any page images in the chapter")
        if not all(is_image_url(u) for u in urls):
            raise WrongHost("page images")
        return [PageRef(self.name, chapter.chapter_id, i, u) for i, u in enumerate(urls)]

    def download_page(self, page):
        if not is_image_url(page.url):
            raise WrongHost("a page image")
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        if resp.url and not is_image_url(resp.url):
            raise WrongHost("a page image")
        name = page.url.split("?", 1)[0].rsplit("/", 1)[-1]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        if not is_page_host(url):
            return None
        parts = urlsplit(url)
        slots = _slots(url)
        if slots is not None:
            slug, cid = slots
            return ("chapter", ChapterInfo(self.name, slug, cid, cid,
                                           f"https://{parts.hostname.lower()}{_chapter_path(slug, cid)}"))
        m = _CHAPTER_URL.search(parts.path)
        if m:
            return ("chapter", ChapterInfo(self.name, m.group(1), m.group(2), m.group(2), url))
        m = _SERIES_URL.search(parts.path)
        return ("series", m.group(1)) if m and m.group(1) != "chapter" else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.technical = {
            "extraction_method": "static HTML: search cards, series page and chapter page are "
                                 "server-rendered; page images are plain unsigned URLs on the "
                                 "site's CDN that need no Referer or cookie.",
            "browser_required": False,
            "mirrors": list(PAGE_HOSTS),
        }
        caps.terms = {
            "robots_txt": "None: /robots.txt answers 200 with the site's 404 page, so there are "
                          "no rules to follow or quote (2026-10-08).",
            "tos": "Not reviewed. /privacy and /dmca were read and say nothing about automated "
                   "access; the \"服務使用協議\" they refer to was not found. Absence is not a "
                   "clearance.",
            "tos_prohibited": False,
        }
        return caps
