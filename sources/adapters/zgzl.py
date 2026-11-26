"""
sources/adapters/zgzl.py -- m.zgzl.net, zh web novels (an unlisted source).

Unlisted on purpose (the owner's call): it never appears in the Sources list,
search, the status board or the docs; it only answers when a link to it is
pasted. See `unlisted` in sources/registry.py.

Plain server-rendered HTML, read live 2026-11-26 with honest GETs:

  book page      GET /info_<book>/      og:novel:book_name / og:novel:author /
                                        og:description, and div.info_chapters:
                                        a "newest" list (newest first, not read)
                                        then the "全部章节" list: every chapter
                                        link in reading order, on one page.
  chapter        GET /read_<book>/<id>.html
                                        h1 title and div#novelcontent. The site
                                        adds a "close reading mode" line, an
                                        ad stub and a "read more at the site"
                                        line to that div; those are dropped.

The site has no robots.txt (404) and no terms page was found, so automation
permission is recorded as UNKNOWN, never as permitted.
"""

import re
from urllib.parse import urlsplit

from ..base import SourceAdapter, layout_changed_class, soup as _soup
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      FetchFailed, SeriesInfo, SourceError)
from ..registry import register

HOST = "m.zgzl.net"
BASE_URL = "https://" + HOST
# The desktop host serves the same pages; links to it are accepted and read from HOST.
_HOSTS = {HOST, "www.zgzl.net"}

_KEY = r"[0-9a-z]{1,12}"
_CHAPTER_HREF = re.compile(r"^/read_(" + _KEY + r")/(" + _KEY + r")\.html$")

# Lines the site injects into every chapter body.
_NOISE = re.compile(r"关闭小说畅读模式|本章阅读完毕|^\(\);?$")

LayoutChanged = layout_changed_class("this site")


def _key(value: str) -> str:
    text = (value or "").strip().strip("/")
    if not re.fullmatch(_KEY, text):
        raise SourceError(f"{value!r} is not a book id for this site.", FailureReason.UNKNOWN)
    return text


@register
class ZgzlSource(SourceAdapter):
    name = "zgzl"
    display_name = "zgzl.net"
    homepage = BASE_URL
    content_types = [ContentType.NOVEL.value]
    languages = ["zh"]
    unlisted = True
    url_patterns = [r"zgzl\.net/(?:info|read)_" + _KEY]
    host_min_interval = {HOST: 2.0}
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str | None = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._books = {}

    @classmethod
    def matches_url(cls, url: str) -> bool:
        try:
            host = (urlsplit((url or "").strip()).hostname or "").lower()
        except ValueError:
            return False
        return host in _HOSTS and any(re.search(p, url or "") for p in cls.url_patterns)

    def _get(self, path: str, action: str) -> str:
        try:
            resp = self.client.get(self.base_url + path, action=action, use_cache=False)
        except FetchFailed as e:
            status = getattr(e.attempt, "http_status", None)
            if status in (404, 410):
                raise FetchFailed("The site says this page doesn't exist (HTTP "
                                  f"{status}): the book or chapter was removed or the address "
                                  "is wrong.", e.reason, e.attempt) from None
            raise
        return resp.text

    def _book(self, book: str) -> str:
        if book not in self._books:
            self._books[book] = self._get(f"/info_{book}/", f"Loading book {book}")
        return self._books[book]

    @staticmethod
    def _meta(soup, prop: str) -> str:
        tag = soup.find("meta", attrs={"property": prop})
        content = tag.get("content") if tag is not None else ""
        return re.sub(r"\s+", " ", content).strip() if isinstance(content, str) else ""

    def get_series(self, series_id: str):
        book = _key(series_id)
        soup = _soup(self._book(book))
        title = self._meta(soup, "og:novel:book_name")
        if not title:
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 is not None else ""
        if not title:
            raise LayoutChanged("the book title")
        author = self._meta(soup, "og:novel:author")
        status_tag = soup.select_one(".tab1 .p5")
        status_text = status_tag.get_text(strip=True) if status_tag is not None else ""
        status = "completed" if "完" in status_text else "ongoing" if "连载" in status_text else "unknown"
        return SeriesInfo(self.name, book, title, f"{self.base_url}/info_{book}/",
                          authors=[author] if author else [],
                          description=self._meta(soup, "og:description"), status=status,
                          content_type=ContentType.NOVEL.value, language="zh")

    def get_chapters(self, series_id: str):
        book = _key(series_id)
        soup = _soup(self._book(book))
        heading = next((p for p in soup.select(".info_chapters p.p1") if "全部章节" in p.get_text()), None)
        box = heading.find_next_sibling("ul") if heading is not None else None
        if box is None:
            raise LayoutChanged("the chapter list")
        chapters, seen = [], set()
        for a in box.find_all("a", href=True):
            m = _CHAPTER_HREF.match(str(a["href"]))
            if not m or m.group(1) != book or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            chapters.append(ChapterInfo(self.name, book, m.group(2),
                                        a.get_text(strip=True) or m.group(2),
                                        f"{self.base_url}/read_{book}/{m.group(2)}.html"))
        if not chapters:
            raise LayoutChanged("any chapters in the list")
        return chapters

    def get_chapter_text(self, chapter) -> str:
        book, chapter_id = _key(chapter.series_id), _key(chapter.chapter_id)
        html = self._get(f"/read_{book}/{chapter_id}.html", f"Loading chapter {chapter.title}")
        body = _soup(html).select_one("#novelcontent")
        if body is None:
            raise LayoutChanged("the chapter text")
        # The reading-mode notice is its own block with no line break after it, so it would
        # otherwise glue itself to the first paragraph and take it out with it.
        for notice in body.find_all("p"):
            if _NOISE.search(notice.get_text()):
                notice.decompose()
        for br in body.find_all("br"):
            br.replace_with("\n")
        lines = [re.sub(r"[\s 　]+", " ", line).strip() for line in body.get_text().split("\n")]
        text = "\n".join(line for line in lines if line and not _NOISE.search(line))
        if not text:
            raise SourceError("The page for this chapter had no text.", FailureReason.UNKNOWN)
        return text

    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        path = urlsplit(url.strip()).path
        m = re.fullmatch(r"/read_(" + _KEY + r")/(" + _KEY + r")\.html", path)
        if m:
            book, chapter_id = m.groups()
            return ("chapter", ChapterInfo(self.name, book, chapter_id, chapter_id,
                                           f"{self.base_url}/read_{book}/{chapter_id}.html"))
        m = re.fullmatch(r"/info_(" + _KEY + r")/?", path)
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered HTML, no JavaScript needed",
            "browser_required": False,
        }
        caps.terms = {
            "robots_txt": "none (HTTP 404, checked 2026-11-26)",
            "read": "no terms page found; not cleared, not permitted",
            "enforcement_note": "ToS/robots.txt enforcement is off app-wide (user decision "
                                "2026-09-27, sources/ladder.py check_terms).",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
