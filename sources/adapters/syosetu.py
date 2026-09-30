"""
sources/adapters/syosetu.py -- 小説家になろう (Syosetu / Narou, ncode.syosetu.com),
ja web novels.

Technique read directly from the live site (2026-09-30, a handful of GETs
with an honest User-Agent, no cookies); no reference scraper's code was
used:

  series page    GET /<ncode>/               h1.p-novel__title, .p-novel__author a,
                                             #novel_ex.p-novel__summary (synopsis,
                                             <br> line breaks), and -- for a serial --
                                             the first page of .p-eplist
  chapter list   GET /<ncode>/?p=<n>         100 episodes per page. .p-eplist holds, in
                                             site order, optional
                                             div.p-eplist__chapter-title headings and
                                             div.p-eplist__sublist entries
                                             (a.p-eplist__subtitle href="/<ncode>/<n>/",
                                             div.p-eplist__update). The pager
                                             (.c-pager__item--last, href "...?p=N") is a
                                             link only when there is another page; on the
                                             last page it is a plain <span>. A page does
                                             not repeat the heading of a part that began
                                             on an earlier page, so the current part is
                                             carried across pages.
  one-shot       GET /<ncode>/               a 短編 has no .p-eplist and no episode
                                             numbers: the text is on this same page
                                             (.p-novel__body), and /<ncode>/1/ is a 404.
                                             It becomes one chapter whose URL is the
                                             top page.
  chapter body   GET /<ncode>/<n>/           .p-novel__body holds one or more
                                             div.p-novel__text blocks of <p id="L1">…;
                                             a blank line is <p><br /></p>. An author's
                                             note is its own block, with an extra class:
                                             p-novel__text--preface / --afterword.
  search         GET yomou.syosetu.com/search.php?word=<q>&p=<n>
                                             div.searchkekka_box, 20 per page.

Choices: the preface and afterword blocks are the author's notes, not story
text, so they are left out of the imported text; ruby keeps the base text
only (<rt>/<rp> readings are dropped, as lightnovel_fun does).

Not supported, on purpose: 18+ works. They live on novel18.syosetu.com behind
the site's own over-18 switch; this adapter never sends an age cookie and
reports such a work as unsupported (ContentHidden).

UNVERIFIED LIVE (no example available or deliberately not requested): what an
18+ work's ncode.syosetu.com URL does (assumed to redirect to
novel18.syosetu.com), what a login-only or removed work's page looks like on
a 200, and the mobile / other alternate hosts. Those branches are written
from the plan, not from a page seen, and fail with a clear message rather
than guessing.

robots.txt (ncode.syosetu.com and yomou.syosetu.com, fetched 2026-09-30):
`User-agent: *` with `Crawl-delay: 1` and no Disallow lines. The terms of
service (https://syosetu.com/site/rule/, read 2026-09-30, revision dated
令和8年6月9日) forbid automated access, see `capabilities()`.
"""

import re
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter, host_url_search
from ..models import (ChapterInfo, ContentAccess, ContentHidden, ContentType, FailureReason,
                      FetchFailed, SearchResult, SeriesInfo, SourceError, AutomationPermission)
from ..registry import register

HOST = "ncode.syosetu.com"
BASE_URL = "https://" + HOST
SEARCH_HOST = "yomou.syosetu.com"
SEARCH_URL = "https://" + SEARCH_HOST + "/search.php"
ADULT_HOST = "novel18.syosetu.com"

# n + digits + letters, e.g. n0063lr. The site answers /N0063LR/ too, so the
# id is lowercased everywhere.
_NCODE = r"n\d{1,6}[a-z]{1,4}"
_CHAPTER_HREF = re.compile(r"^(?:https?://[^/]+)?/(" + _NCODE + r")/(\d+)/?$", re.I)
_PAGE_PARAM = re.compile(r"[?&]p=(\d+)")
_EPISODES = re.compile(r"全\s*(\d+)\s*エピソード")

# Hard cap on list pages walked (100 episodes each), so a broken pager can't
# loop forever.
MAX_LIST_PAGES = 200

_ADULT_MARKERS = ("over18", "年齢確認", "18歳以上", "18歳未満")


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"Syosetu's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.LAYOUT_CHANGED)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


def _norm_ncode(series_id: str) -> str:
    code = (series_id or "").strip().strip("/").lower()
    if not re.fullmatch(_NCODE, code):
        raise SourceError(f"{series_id!r} is not a Syosetu work code (like n0063lr).",
                          FailureReason.UNKNOWN)
    return code


def _lines(node) -> str:
    """A node's text with <br> as line breaks and ruby readings dropped."""
    for tag in node.find_all(["rt", "rp"]):
        tag.decompose()
    for br in node.find_all("br"):
        br.replace_with("\n")
    return node.get_text()


@register
class SyosetuSource(SourceAdapter):
    name = "syosetu"
    display_name = "小説家になろう (Syosetu)"
    content_types = [ContentType.NOVEL.value]
    languages = ["ja"]
    url_patterns = [r"ncode\.syosetu\.com/" + _NCODE + r"(?:[/?#]|$)"]
    # robots.txt asks for Crawl-delay: 1; two seconds keeps well inside it.
    host_min_interval = {HOST: 2.0, SEARCH_HOST: 2.0}
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str = None, search_url: str = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self.search_url = search_url or SEARCH_URL
        self._tops = {}

    @classmethod
    def matches_url(cls, url: str) -> bool:
        # Exactly ncode.syosetu.com: host_url_search also lets a subdomain of
        # it through, and novel18./syosetu.org are different sites.
        try:
            host = (urlsplit((url or "").strip()).hostname or "").lower()
        except ValueError:
            return False
        return host == HOST and host_url_search(cls.url_patterns, url, re.I)

    # -- fetching ------------------------------------------------------------
    def _get(self, url: str, action: str, use_cache: bool = False) -> str:
        try:
            resp = self.client.get(url, action=action, use_cache=use_cache)
        except FetchFailed as e:
            status = getattr(e.attempt, "http_status", None)
            if status in (404, 410):
                raise FetchFailed("Syosetu says this page doesn't exist (HTTP "
                                  f"{status}): the work or episode was removed, is private, "
                                  "or the address is wrong.", e.reason, e.attempt) from None
            raise
        final = urlsplit(resp.url or "").hostname or ""
        if final and final.lower() != urlsplit(url).hostname.lower():
            if final.lower() == ADULT_HOST:
                raise ContentHidden("This is an 18+ work (novel18.syosetu.com), which sits "
                                    "behind the site's age check. This source doesn't support "
                                    "18+ works.")
            raise FetchFailed("Syosetu redirected to a different site, so nothing was read.",
                              FailureReason.HTTP_ERROR)
        return resp.text

    def _explain_missing(self, html: str, what: str) -> SourceError:
        """The page has no `what`: say why if the page says so, else the
        layout changed."""
        soup = _soup(html)
        err = soup.select_one(".p-novelcom-text--error")
        if err is not None and err.get_text(strip=True):
            return FetchFailed(f"Syosetu reports: {err.get_text(strip=True)}",
                               FailureReason.HTTP_ERROR)
        lower = (html or "").lower()
        if any(m in lower for m in _ADULT_MARKERS):
            return ContentHidden("This page asks for an age check (18+). This source doesn't "
                                 "support 18+ works and never sends an age cookie.")
        if soup.select_one("input[type=password]") is not None:
            return SourceError("Syosetu is asking for a login for this page; this source doesn't "
                               "sign in.", FailureReason.AUTHENTICATION_REQUIRED)
        return LayoutChanged(what)

    # -- series ----------------------------------------------------------------
    def _top(self, series_id: str, page: int = 1) -> str:
        code = _norm_ncode(series_id)
        key = (code, page)
        if key not in self._tops:
            suffix = f"?p={page}" if page > 1 else ""
            self._tops[key] = self._get(f"{self.base_url}/{code}/{suffix}",
                                        f"Loading {code}" + (f" (list page {page})" if page > 1
                                                             else ""))
        return self._tops[key]

    def get_series(self, series_id: str):
        code = _norm_ncode(series_id)
        html = self._top(code)
        soup = _soup(html)
        h1 = soup.select_one("h1.p-novel__title")
        if h1 is None:
            raise self._explain_missing(html, "the work title")
        title = h1.get_text(strip=True)
        if not title:
            raise LayoutChanged("the work title")
        author = ""
        a = soup.select_one(".p-novel__author a")
        if a is not None:
            author = a.get_text(strip=True)
        elif soup.select_one(".p-novel__author") is not None:
            author = re.sub(r"^作者[：:]\s*", "", soup.select_one(".p-novel__author")
                            .get_text(" ", strip=True))
        summary = soup.select_one("#novel_ex")
        description = _lines(summary).strip() if summary is not None else ""
        one_shot = soup.select_one(".p-eplist") is None and \
            soup.select_one(".p-novel__body") is not None
        return SeriesInfo(
            self.name, code, title, f"{self.base_url}/{code}/", authors=[author] if author else [],
            description=description,
            # The top page carries no serial/complete flag. A one-shot is
            # complete by definition; anything else stays unknown.
            status="completed" if one_shot else "unknown",
            content_type=ContentType.NOVEL.value, language="ja")

    # -- chapters ---------------------------------------------------------------
    @staticmethod
    def _last_page(soup) -> int:
        for link in soup.select("a.c-pager__item--last[href]"):
            m = _PAGE_PARAM.search(link.get("href", ""))
            if m:
                return int(m.group(1))
        return 1

    def _list_page(self, soup, code: str, group: str, chapters: list, seen: set) -> str:
        """Adds one list page's episodes to `chapters`; returns the part
        heading in force at the end of the page."""
        box = soup.select_one(".p-eplist")
        if box is None:
            raise LayoutChanged("the episode list")
        for node in box.find_all("div", recursive=False):
            classes = node.get("class") or []
            if "p-eplist__chapter-title" in classes:
                group = node.get_text(" ", strip=True)
                continue
            if "p-eplist__sublist" not in classes:
                continue
            link = node.select_one("a.p-eplist__subtitle[href]")
            if link is None:
                raise LayoutChanged("an episode link")
            m = _CHAPTER_HREF.match(link["href"].strip())
            if not m or m.group(1).lower() != code:
                continue
            number = m.group(2)
            if number in seen:
                continue
            seen.add(number)
            chapters.append(ChapterInfo(
                self.name, code, number, link.get_text(" ", strip=True) or number,
                f"{self.base_url}/{code}/{number}/", group))
        return group

    def get_chapters(self, series_id: str):
        code = _norm_ncode(series_id)
        html = self._top(code)
        soup = _soup(html)
        if soup.select_one("h1.p-novel__title") is None:
            raise self._explain_missing(html, "the work title")
        if soup.select_one(".p-eplist") is None:
            if soup.select_one(".p-novel__body") is None:
                raise self._explain_missing(html, "the episode list")
            title = soup.select_one("h1.p-novel__title").get_text(strip=True)
            return [ChapterInfo(self.name, code, "1", title or code, f"{self.base_url}/{code}/")]
        chapters, seen, group = [], set(), ""
        last = min(self._last_page(soup), MAX_LIST_PAGES)
        group = self._list_page(soup, code, group, chapters, seen)
        for n in range(2, last + 1):
            page = _soup(self._top(code, n))
            group = self._list_page(page, code, group, chapters, seen)
        if not chapters:
            raise LayoutChanged("any episodes in the list")
        return chapters

    # -- chapter text -------------------------------------------------------------
    def get_chapter_text(self, chapter) -> str:
        code = _norm_ncode(chapter.series_id)
        url = chapter.url or f"{self.base_url}/{code}/{chapter.chapter_id}/"
        m = re.match(r"^https?://([^/]+)/", url)
        if m and m.group(1).lower() != HOST:
            raise SourceError("That chapter isn't on ncode.syosetu.com.", FailureReason.UNKNOWN)
        html = self._get(url, f"Loading chapter {chapter.title}")
        soup = _soup(html)
        body = soup.select_one(".p-novel__body")
        if body is None:
            raise self._explain_missing(html, "the chapter text")
        blocks = [b for b in body.select(".p-novel__text")
                  if not ({"p-novel__text--preface", "p-novel__text--afterword"}
                          & set(b.get("class") or []))]
        if not blocks:
            raise LayoutChanged("the chapter's main text block")
        lines = []
        for block in blocks:
            for p in block.find_all("p"):
                lines.append(_lines(p).strip("\r\n").rstrip())
        text = "\n".join(lines).strip("\n")
        if not text.strip():
            raise SourceError("Syosetu's page for this chapter had no text.",
                              FailureReason.UNKNOWN)
        return text

    # -- search ---------------------------------------------------------------------
    def search(self, query: str, page: int = 1):
        query = (query or "").strip()
        if not query:
            return []
        url = f"{self.search_url}?word={quote(query)}" + (f"&p={page}" if page > 1 else "")
        html = self._get(url, f"Searching Syosetu for {query!r}")
        soup = _soup(html)
        boxes = soup.select("div.searchkekka_box")
        out, seen = [], set()
        for box in boxes:
            a = box.select_one(".novel_h a[href]")
            if a is None:
                continue
            m = re.search(r"/(" + _NCODE + r")/?$", a["href"].strip(), re.I)
            title = a.get_text(strip=True)
            if not m or not title or m.group(1).lower() in seen:
                continue
            code = m.group(1).lower()
            seen.add(code)
            extra = {}
            writer = box.select_one('a[href*="mypage.syosetu.com"]')
            if writer is not None:
                extra["author"] = writer.get_text(strip=True)
            state = box.select_one("td.left")
            if state is not None:
                text = state.get_text(" ", strip=True)
                extra["status"] = "completed" if "完結" in text else \
                    "ongoing" if "連載中" in text else "unknown"
                n = _EPISODES.search(text)
                if n:
                    extra["episodes"] = int(n.group(1))
            ex = box.select_one("div.ex")
            if ex is not None:
                extra["description"] = ex.get_text("\n", strip=True)
            out.append(SearchResult(self.name, code, title, f"{self.base_url}/{code}/", "", extra))
        if not out and boxes:
            raise LayoutChanged("the search result links")
        return out

    # -- URLs -------------------------------------------------------------------------
    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        path = urlsplit(url.strip()).path
        m = re.fullmatch(r"/(" + _NCODE + r")/(\d+)/?", path, re.I)
        if m:
            code, number = m.group(1).lower(), m.group(2)
            return ("chapter", ChapterInfo(self.name, code, number, number,
                                           f"{self.base_url}/{code}/{number}/"))
        m = re.fullmatch(r"/(" + _NCODE + r")/?", path, re.I)
        return ("series", m.group(1).lower()) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered HTML, no JavaScript needed",
            "browser_required": False,
            "unsupported": "18+ works (novel18.syosetu.com, behind an over-18 switch): reported, "
                           "no age cookie is ever sent",
            "unverified_live": "an 18+ ncode's redirect on ncode.syosetu.com, login-only and "
                               "removed-work pages served with a 200, mobile/alternate hosts",
        }
        caps.terms = {
            "robots_txt": "ncode.syosetu.com and yomou.syosetu.com (fetched 2026-09-30): "
                          "`User-agent: *` with `Crawl-delay: 1` and no Disallow lines; "
                          "meta-externalagent is disallowed entirely.",
            "read": "https://syosetu.com/site/rule/ (利用規約, revision dated 令和8年6月9日), "
                    "read directly 2026-09-30.",
            "clause": "第14条 (禁止事項) 23: \"なろうデベロッパーで提供しているAPIを利用する以外の"
                      "方法で、本サービスに自動化された手段を用いてアクセスしたり、データを収集した"
                      "りすること。\" (no automated access or data collection except through the "
                      "site's own なろうデベロッパー API). The API "
                      "(api.syosetu.com/novelapi) returns work metadata, not episode text.",
            "tos_prohibited": True,
            "enforcement_note": "ToS/robots.txt enforcement is off app-wide (user decision "
                                "2026-09-27, sources/ladder.py check_terms); the finding is "
                                "recorded here, not enforced by this adapter.",
        }
        caps.automation_permission = AutomationPermission.EXPLICITLY_RESTRICTED.value
        return caps
