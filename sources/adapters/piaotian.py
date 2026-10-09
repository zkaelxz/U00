"""
sources/adapters/piaotian.py -- 飘天文学 (Piaotian, www.piaotia.com; the older
name ptwxz.com redirects there), zh web novels on static GBK pages.

Technique read directly from the live site (2026-09-30, about 20 plain GETs
and 3 search POSTs, honest User-Agent, no cookies, 2 s or more apart); no
reference scraper's code was used:

  book info      GET /bookinfo/<a>/<id>.html   <a> is <id> // 1000 (146 -> 0,
                                               16054 -> 16). `#centerm h1` is the
                                               title; the cells "类 别：", "作 者：",
                                               "文章状态：" (连载中 / 已完成) sit in
                                               `td`s; the synopsis is the text after
                                               `span.hottext` "内容简介：" up to the end
                                               of its box; the cover is
                                               /files/article/image/<a>/<id>/<id>s.jpg.
  chapter list   GET /html/<a>/<id>/           ONE page, the whole book (924 chapters
                                               on one long serial). `div.centent`
                                               holds, in site order, `div.list`
                                               headings ("第一卷 ..." or "作品相关")
                                               and `ul`s of `li > a` with relative
                                               hrefs like 20709.html; padding `li`s
                                               are `&nbsp;`. Site order is not
                                               numeric (a chapter id can jump). Links
                                               outside `.centent` (the "好书推荐"
                                               footer) are other books.
  chapter body   GET /html/<a>/<id>/<cid>.html No container: the text is bare text
                                               nodes and `<br>`s straight after the
                                               `div.toplink` nav and the ad-script
                                               tables, ending at the `div.bottomlink`
                                               nav block. A paragraph is
                                               "&nbsp;&nbsp;&nbsp;&nbsp;text<br /><br />".
  search         POST /modules/article/search.php  form fields searchtype=articlename,
                                               searchkey=<GBK bytes, percent-encoded>.
                                               One exact match answers 302 to the
                                               book page; otherwise a `table.grid` of
                                               30 rows and a pager whose links are
                                               GET /modules/article/search.php?
                                               searchtype=..&searchkey=..&page=<n>.

Pages declare GBK (`gbk` on book pages, `gb2312` on chapters) in a <meta> and
send no charset header; sources.http.decode_html reads the meta and decodes
with the GB18030 superset, so `resp.text` is already text.

A missing book page is served with HTTP 200 and a page titled 出现错误！ ("错误原因：
对不起，该文章不存在！"); a missing list or chapter is a plain nginx 404.

Not solved, on purpose: a challenge is handed off by the client, never passed.
The site set a `jieqiVisitTime` cookie on search (its own search rate limit);
this adapter sends no cookies, so it paces itself instead (host_min_interval).

UNVERIFIED LIVE: any login or age interstitial (none was met; the branches
below only recognise a password field / 18+ wording and fail plainly), the
search pager's page>1 GET, and what search does when the site throttles it.

robots.txt (fetched 2026-09-30, http://ptwxz.com/ 301s to it): `User-agent: *`,
Disallow /files/article/attachment/, /login.php, /modules/article/packshow.php,
/admin/; no Crawl-delay. /modules/article/search.php is not disallowed. The
adapter never requests a disallowed path. No terms page was found (see
`capabilities()`).
"""

import re
from urllib.parse import quote_from_bytes, urljoin, urlsplit

from ..base import SourceAdapter, host_url_search
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentHidden,
                      ContentType, FailureReason, FetchFailed, SearchResult, SeriesInfo,
                      SourceError)
from ..pacing import PaceLevel, PacingProfile
from ..registry import register

HOST = "www.piaotia.com"
BASE_URL = "https://" + HOST
# Every host a pasted URL may use. All are requested as HOST: ptwxz.com only
# redirects there, so it is never contacted.
ACCEPTED_HOSTS = ("piaotia.com", "www.piaotia.com", "ptwxz.com", "www.ptwxz.com")
SEARCH_PATH = "/modules/article/search.php"

# Hard cap on a chapter list. The longest list read was 924 chapters.
MAX_CHAPTERS = 10000
# Below this many characters (whitespace excluded) a chapter is treated as a
# page that didn't load properly. The shortest real chapter read was ~100.
MIN_CHAPTER_CHARS = 5

_ID = r"\d{1,9}"
_BOOK_PATH = re.compile(r"^/bookinfo/(\d+)/(" + _ID + r")\.html$")
_LIST_PATH = re.compile(r"^/html/(\d+)/(" + _ID + r")(?:/|/index\.html)?$")
_CHAPTER_PATH = re.compile(r"^/html/(\d+)/(" + _ID + r")/(" + _ID + r")\.html$")
_CHAPTER_HREF = re.compile(r"^(" + _ID + r")\.html$")
# The trailing "www." fragment seen once at the end of a chapter body (2026-09-30).
_WATERMARK_STUB = re.compile(r"^www\.?$", re.I)
_ADULT_MARKERS = ("年满18", "未满18", "18岁以上", "18禁")


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"Piaotian's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _norm_id(series_id) -> str:
    sid = str(series_id or "").strip()
    if not re.fullmatch(_ID, sid) or int(sid) == 0:
        raise SourceError(f"{series_id!r} is not a Piaotian book number (like 146).",
                          FailureReason.UNKNOWN)
    return str(int(sid))


def _dir(book_id: str) -> str:
    return str(int(book_id) // 1000)


def _book_url(book_id: str) -> str:
    return f"{BASE_URL}/bookinfo/{_dir(book_id)}/{book_id}.html"


def _list_url(book_id: str) -> str:
    return f"{BASE_URL}/html/{_dir(book_id)}/{book_id}/"


def _chapter_url(book_id: str, chapter_id: str) -> str:
    return f"{BASE_URL}/html/{_dir(book_id)}/{book_id}/{chapter_id}.html"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ").replace("　", " ")).strip()


def _accepted_host(url: str) -> bool:
    try:
        return (urlsplit((url or "").strip()).hostname or "").lower() in ACCEPTED_HOSTS
    except ValueError:
        return False


@register
class PiaotianSource(SourceAdapter):
    name = "piaotian"
    display_name = "飘天文学 (Piaotian)"
    content_types = [ContentType.NOVEL.value]
    languages = ["zh"]
    pacing_profile = PacingProfile(
        fast=PaceLevel(min_delay=2.5, max_delay=3.5, max_concurrent=1),
        evidence=("robots.txt fetched 2026-10-09: no Crawl-delay, only attachment/login/packshow/admin disallowed; no terms page found; 2.5 s floor kept for the site's own search throttle."),
        fast_allowed=True)
    url_patterns = [r"(?:www\.)?(?:piaotia|ptwxz)\.com/"
                    r"(?:bookinfo/\d+/\d+\.html|html/\d+/\d+(?:/|$|\.html))"]
    # No Crawl-delay in robots.txt; the owner asked for human-paced use.
    host_min_interval = {HOST: 2.5}
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._pages = {}

    @classmethod
    def matches_url(cls, url: str) -> bool:
        # host_url_search also lets a subdomain through; only the listed hosts count.
        return _accepted_host(url) and host_url_search(cls.url_patterns, url, re.I)

    # -- fetching ------------------------------------------------------------
    def _get(self, url: str, action: str) -> str:
        try:
            resp = self.client.get(url, action=action, use_cache=False)
        except FetchFailed as e:
            status = getattr(e.attempt, "http_status", None)
            if status in (404, 410):
                raise FetchFailed(f"Piaotian says this page doesn't exist (HTTP {status}): "
                                  "the book or chapter was removed or the address is wrong.",
                                  e.reason, e.attempt) from None
            raise
        return self._checked(resp, urlsplit(url).hostname)

    def _checked(self, resp, expected_host) -> str:
        parts = urlsplit(resp.url or "")
        final = (parts.hostname or "").lower()
        if final and final != (expected_host or "").lower():
            raise FetchFailed("Piaotian redirected to a different site, so nothing was read.",
                              FailureReason.HTTP_ERROR)
        if parts.path.startswith("/login.php"):
            raise SourceError("Piaotian is asking for a login for this page; this source "
                              "doesn't sign in.", FailureReason.AUTHENTICATION_REQUIRED)
        return resp.text

    def _explain_missing(self, html: str, what: str) -> SourceError:
        """The page has no `what`: say why if the page says so, else the
        layout changed."""
        soup = _soup(html)
        title = soup.title.get_text(strip=True) if soup.title else ""
        if "出现错误" in title:
            m = re.search(r"错误原因[：:]\s*([^\n\r<]+)", html or "")
            reason = m.group(1).strip() if m else title
            return FetchFailed(f"Piaotian reports: {reason}", FailureReason.NOT_FOUND)
        if soup.select_one("input[type=password]") is not None:
            return SourceError("Piaotian is asking for a login for this page; this source "
                               "doesn't sign in.", FailureReason.AUTHENTICATION_REQUIRED)
        if any(m in (html or "") for m in _ADULT_MARKERS):
            return ContentHidden("This page asks for an age check. This source doesn't pass "
                                 "age checks.")
        return LayoutChanged(what)

    # -- series ----------------------------------------------------------------
    def _page(self, kind: str, book_id: str) -> str:
        key = (kind, book_id)
        if key not in self._pages:
            if kind == "book":
                url, action = f"{self.base_url}/bookinfo/{_dir(book_id)}/{book_id}.html", \
                    f"Loading book {book_id}"
            else:
                url, action = f"{self.base_url}/html/{_dir(book_id)}/{book_id}/", \
                    f"Loading chapter list of book {book_id}"
            self._pages[key] = self._get(url, action)
        return self._pages[key]

    @staticmethod
    def _field(soup, label: str) -> str:
        """The value of a "类 别：都市言情" style cell."""
        pattern = re.compile(r"^" + r"\s*".join(label) + r"\s*[：:]\s*(.*)$")
        for td in soup.select("#centerm td"):
            if td.find("td") is not None:
                continue
            m = pattern.match(_clean(td.get_text(" ")))
            if m:
                return m.group(1).strip()
        return ""

    @staticmethod
    def _synopsis(soup) -> str:
        for span in soup.select("#centerm span.hottext"):
            if not _clean(span.get_text()).startswith("内容简介"):
                continue
            lines, cur = [], []
            for node in span.next_siblings:
                if getattr(node, "name", None) == "br":
                    lines.append("".join(cur))
                    cur = []
                elif getattr(node, "name", None):
                    cur.append(node.get_text())
                else:
                    cur.append(str(node))
            lines.append("".join(cur))
            return "\n".join(l for l in (_clean(x) for x in lines) if l)
        return ""

    def get_series(self, series_id: str):
        book_id = _norm_id(series_id)
        html = self._page("book", book_id)
        soup = _soup(html)
        h1 = soup.select_one("#centerm h1")
        if h1 is None or not h1.get_text(strip=True):
            raise self._explain_missing(html, "the book title")
        author = self._field(soup, "作者")
        category = self._field(soup, "类别")
        if not author and not category:
            raise LayoutChanged("the book details (author, category)")
        state = self._field(soup, "文章状态")
        status = "completed" if "完" in state else "ongoing" if "连载" in state else "unknown"
        cover = ""
        want = f"/files/article/image/{_dir(book_id)}/{book_id}/"
        for img in soup.select("#centerm img[src]"):
            src = urljoin(self.base_url + "/", img["src"].strip())
            parts = urlsplit(src)
            if (parts.hostname or "").lower() == HOST and parts.path.startswith(want):
                cover = src
                break
        return SeriesInfo(
            self.name, book_id, h1.get_text(strip=True), _book_url(book_id), cover,
            authors=[author] if author else [], description=self._synopsis(soup),
            genres=[category] if category else [], status=status,
            content_type=ContentType.NOVEL.value, language="zh")

    # -- chapters ---------------------------------------------------------------
    def get_chapters(self, series_id: str):
        book_id = _norm_id(series_id)
        html = self._page("list", book_id)
        soup = _soup(html)
        box = soup.select_one("div.centent")
        if box is None:
            raise self._explain_missing(html, "the chapter list")
        list_url = _list_url(book_id)
        want_dir = f"/html/{_dir(book_id)}/{book_id}/"
        chapters, seen, group = [], set(), ""
        for node in box.find_all(["div", "a"]):
            if node.name == "div":
                if "list" in (node.get("class") or []):
                    group = node.get_text(" ", strip=True)
                continue
            if node.find_parent("li") is None or not node.get("href"):
                continue
            target = urlsplit(urljoin(list_url, node["href"].strip()))
            m = re.fullmatch(re.escape(want_dir) + r"(" + _ID + r")\.html", target.path)
            if not m or (target.hostname or "").lower() != HOST:
                continue
            cid = m.group(1)
            if cid in seen:
                continue
            seen.add(cid)
            if len(chapters) >= MAX_CHAPTERS:
                raise SourceError(f"This book lists more than {MAX_CHAPTERS} chapters; "
                                  "refusing to import a truncated list.", FailureReason.UNKNOWN)
            chapters.append(ChapterInfo(self.name, book_id, cid,
                                        node.get_text(" ", strip=True) or cid,
                                        _chapter_url(book_id, cid), group))
        if not chapters:
            raise SourceError("Piaotian's chapter list for this book is empty.",
                              FailureReason.UNKNOWN)
        return chapters

    # -- chapter text -------------------------------------------------------------
    @staticmethod
    def _body_lines(soup) -> list:
        nav = soup.select_one("div.toplink")
        if nav is None:
            return None
        lines, cur, started = [], [], False
        for node in nav.next_siblings:
            name = getattr(node, "name", None)
            if name == "table":          # the ad-script tables sit between nav and text
                lines, cur, started = [], [], False
            elif name == "br":
                if started:
                    lines.append("".join(cur))
                    cur = []
            elif name:
                if started:
                    break
            elif type(node).__name__ == "NavigableString":   # not a Comment
                text = str(node)
                if text.strip(" \t\r\n"):
                    started = True
                if started:
                    cur.append(text)
        lines.append("".join(cur))
        return lines

    def get_chapter_text(self, chapter) -> str:
        book_id = _norm_id(chapter.series_id)
        cid = str(chapter.chapter_id or "").strip()
        if not re.fullmatch(_ID, cid):
            raise SourceError(f"{chapter.chapter_id!r} is not a Piaotian chapter number.",
                              FailureReason.UNKNOWN)
        if chapter.url:
            if not _accepted_host(chapter.url):
                raise SourceError("That chapter isn't on piaotia.com.", FailureReason.UNKNOWN)
            m = _CHAPTER_PATH.match(urlsplit(chapter.url.strip()).path)
            if not m or (m.group(2), m.group(3)) != (book_id, cid):
                raise SourceError("That address isn't this book's chapter.", FailureReason.UNKNOWN)
        url = f"{self.base_url}/html/{_dir(book_id)}/{book_id}/{cid}.html"
        html = self._get(url, f"Loading chapter {chapter.title}")
        soup = _soup(html)
        raw = self._body_lines(soup)
        if raw is None:
            raise self._explain_missing(html, "the chapter navigation and text")
        lines = [l for l in (_clean(x) for x in raw) if l]
        if lines and _WATERMARK_STUB.match(lines[-1]):
            lines.pop()
        text = "\n".join(lines)
        if len(re.sub(r"\s", "", text)) < MIN_CHAPTER_CHARS:
            raise SourceError("Piaotian's page for this chapter had no text (or almost none), "
                              "so nothing was imported.", FailureReason.UNKNOWN)
        return text

    # -- search ---------------------------------------------------------------------
    @staticmethod
    def _gbk(text: str) -> str:
        return quote_from_bytes(text.encode("gb18030", errors="replace"))

    def search(self, query: str, page: int = 1):
        query = (query or "").strip()
        if not query:
            return []
        action = f"Searching Piaotian for {query!r}"
        key = self._gbk(query)
        if page > 1:
            resp = self.client.get(f"{self.base_url}{SEARCH_PATH}?searchtype=articlename"
                                   f"&searchkey={key}&page={page}", action=action, use_cache=False)
        else:
            resp = self.client.post(
                self.base_url + SEARCH_PATH, data=f"searchtype=articlename&searchkey={key}",
                headers={"Content-Type": "application/x-www-form-urlencoded"}, action=action)
        html = self._checked(resp, HOST)
        m = _BOOK_PATH.match(urlsplit(resp.url or "").path)
        if m:
            # A single exact match: the site redirects straight to the book page.
            info = self._series_from_html(html, m.group(2))
            return [SearchResult(self.name, info.series_id, info.title, info.url, info.cover_url,
                                 {"author": ", ".join(info.authors), "status": info.status})]
        soup = _soup(html)
        table = soup.select_one("div#content table.grid")
        if table is None:
            raise self._explain_missing(html, "the search results table")
        out, seen, data_rows = [], set(), 0
        for tr in table.select("tr"):
            tds = tr.find_all("td")
            if not tds:
                continue
            data_rows += 1
            if len(tds) < 6:
                continue
            a = tds[0].find("a", href=True)
            bm = _BOOK_PATH.match(urlsplit(urljoin(self.base_url + "/", a["href"])).path) if a else None
            title = a.get_text(strip=True) if a else ""
            if not bm or not title or bm.group(2) in seen:
                continue
            seen.add(bm.group(2))
            state = tds[5].get_text(strip=True)
            out.append(SearchResult(self.name, bm.group(2), title, _book_url(bm.group(2)), "", {
                "author": tds[2].get_text(strip=True),
                "latest_chapter": tds[1].get_text(strip=True),
                "status": "completed" if "完" in state else "ongoing" if "连载" in state
                else "unknown"}))
        if not out and data_rows:
            raise LayoutChanged("the search result links")
        return out

    def _series_from_html(self, html: str, book_id: str):
        """get_series() on a book page already in hand (search's redirect)."""
        self._pages[("book", book_id)] = html
        return self.get_series(book_id)

    # -- URLs -------------------------------------------------------------------------
    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        path = urlsplit(url.strip()).path
        m = _CHAPTER_PATH.match(path)
        if m and m.group(1) == _dir(m.group(2)):
            book_id, cid = str(int(m.group(2))), m.group(3)
            return ("chapter", ChapterInfo(self.name, book_id, cid, cid,
                                           _chapter_url(book_id, cid)))
        m = _BOOK_PATH.match(path) or _LIST_PATH.match(path)
        if m and m.group(1) == _dir(m.group(2)):
            return ("series", str(int(m.group(2))))
        return None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered GBK HTML, no JavaScript needed",
            "browser_required": False,
            "unverified_live": "login or age interstitials (none met), the search pager's "
                               "page>1 GET, search behaviour under the site's own throttle",
        }
        caps.terms = {
            "robots_txt": "http://www.piaotia.com/robots.txt (fetched 2026-09-30; "
                          "ptwxz.com/robots.txt 301s to it): `User-agent: *` with Disallow "
                          "/files/article/attachment/, /login.php, "
                          "/modules/article/packshow.php, /admin/; no Crawl-delay. The adapter "
                          "requests none of those paths.",
            "read": "No terms of service or usage rules page was found (2026-09-30). Checked: "
                    "the home page footer, /help/ (a members' help centre: registering, "
                    "points, avatar; nothing on automated access or copying) and a book, "
                    "list and chapter page's footers.",
            "notices": "Footer notices (2026-09-30): works are user-uploaded or reposted from "
                       "other sites, copyright stays with the original authors, and the site "
                       "will delete a work on the rights holder's request; the site calls "
                       "itself non-profit and disowns members' content.",
            "tos_prohibited": False,
            "unverified": "Silence is not permission: UNKNOWN, not PERMITTED. The site hosts "
                          "reposted copyrighted fiction; see the owner's note in "
                          "docs/content-sources.md.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
