"""
sources/adapters/kakuyomu.py -- カクヨム (Kakuyomu, kakuyomu.jp), ja web novels.

Technique read from the live site (2026-11-26, plain GETs with an honest
User-Agent, no cookies, no login):

  search         GET /search?q=<query>&page=<n>
                 Server-rendered Next.js page. Result order is the order of the
                 /works/<id> links in the HTML; the title, author, synopsis and
                 status of each work are in the page's __NEXT_DATA__ Apollo state
                 (Work:<id> entries).
  series         GET /works/<id>            Work:<id> (title, introduction,
                 serialStatus, author -> UserAccount.activityName) in the same state.
  chapter list   the same page: Work.tableOfContentsV2 -> TableOfContentsChapter
                 (chapter -> Chapter.title when the work has parts, episodeUnions
                 -> Episode:<id> with title and publishedAt). One request, every
                 episode, no paging.
  chapter text   GET /works/<id>/episodes/<eid>
                 div.widget-episodeBody holds one <p id="pN"> per line; a blank
                 line is <p class="blank"><br /></p>.

Ruby keeps the base text only (<rt>/<rp> readings are dropped, as syosetu does).

robots.txt (fetched 2026-11-26): `User-agent: *` disallows only login, report and
feedback paths and /works/*/episodes/*/read$ (never used here); the one
crawler-specific rule asks ClaudeBot for Crawl-delay: 1, so two seconds is used
for everyone.
"""

import json
import re
from urllib.parse import quote, urlsplit

from ..base import SourceAdapter, layout_changed_class, soup as _soup
from ..models import (AutomationPermission, ChapterInfo, ContentAccess, ContentType, FailureReason,
                      FetchFailed, SearchResult, SeriesInfo, SourceError)
from ..registry import register

HOST = "kakuyomu.jp"
BASE_URL = "https://" + HOST

_ID = r"\d{10,25}"
_WORK_HREF = re.compile(r'href="/works/(' + _ID + r')"')
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

LayoutChanged = layout_changed_class("Kakuyomu")


def _norm_id(value: str) -> str:
    text = (value or "").strip().strip("/")
    if not re.fullmatch(_ID, text):
        raise SourceError(f"{value!r} is not a Kakuyomu work id (a long number).",
                          FailureReason.UNKNOWN)
    return text


def _lines(node) -> str:
    """A node's text with <br> as line breaks and ruby readings dropped."""
    for tag in node.find_all(["rt", "rp"]):
        tag.decompose()
    for br in node.find_all("br"):
        br.replace_with("\n")
    return node.get_text()


def _apollo_state(html: str) -> dict:
    m = _NEXT_DATA.search(html or "")
    if m is None:
        raise LayoutChanged("the page data")
    try:
        state = json.loads(m.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]
    except (ValueError, KeyError, TypeError):
        raise LayoutChanged("the page data") from None
    if not isinstance(state, dict):
        raise LayoutChanged("the page data")
    return state


def _ref(state: dict, value):
    """The state entry a {"__ref": key} points at, or None."""
    if isinstance(value, dict) and isinstance(value.get("__ref"), str):
        entry = state.get(value["__ref"])
        return entry if isinstance(entry, dict) else None
    return None


def _status(raw) -> str:
    return {"RUNNING": "ongoing", "COMPLETED": "completed"}.get(str(raw or ""), "unknown")


@register
class KakuyomuSource(SourceAdapter):
    name = "kakuyomu"
    display_name = "カクヨム (Kakuyomu)"
    homepage = BASE_URL
    content_types = [ContentType.NOVEL.value]
    languages = ["ja"]
    url_patterns = [r"kakuyomu\.jp/works/" + _ID + r"(?:[/?#]|$)"]
    host_min_interval = {HOST: 2.0}
    default_headers = {"Referer": BASE_URL + "/"}

    def __init__(self, client=None, base_url: str | None = None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = (base_url or BASE_URL).rstrip("/")
        self._works = {}

    @classmethod
    def matches_url(cls, url: str) -> bool:
        try:
            host = (urlsplit((url or "").strip()).hostname or "").lower()
        except ValueError:
            return False
        return host == HOST and any(re.search(p, url or "") for p in cls.url_patterns)

    # -- fetching ------------------------------------------------------------
    def _get(self, url: str, action: str) -> str:
        try:
            resp = self.client.get(url, action=action, use_cache=False)
        except FetchFailed as e:
            status = getattr(e.attempt, "http_status", None)
            if status in (404, 410):
                raise FetchFailed("Kakuyomu says this page doesn't exist (HTTP "
                                  f"{status}): the work or episode was removed, is private, "
                                  "or the address is wrong.", e.reason, e.attempt) from None
            raise
        final = (urlsplit(resp.url or "").hostname or "").lower()
        if final and final != HOST:
            raise FetchFailed("Kakuyomu redirected to a different site, so nothing was read.",
                              FailureReason.HTTP_ERROR)
        return resp.text

    # -- series ----------------------------------------------------------------
    def _work_state(self, work_id: str) -> dict:
        if work_id not in self._works:
            html = self._get(f"{self.base_url}/works/{work_id}", f"Loading work {work_id}")
            self._works[work_id] = _apollo_state(html)
        return self._works[work_id]

    def _work(self, state: dict, work_id: str) -> dict:
        work = state.get(f"Work:{work_id}")
        if not isinstance(work, dict) or not work.get("title"):
            raise LayoutChanged("the work")
        return work

    def _author(self, state: dict, work: dict) -> str:
        user = _ref(state, work.get("author")) or {}
        return str(user.get("activityName") or user.get("name") or "")

    def get_series(self, series_id: str):
        work_id = _norm_id(series_id)
        state = self._work_state(work_id)
        work = self._work(state, work_id)
        author = self._author(state, work)
        return SeriesInfo(
            self.name, work_id, str(work["title"]), f"{self.base_url}/works/{work_id}",
            authors=[author] if author else [],
            description=str(work.get("introduction") or "").strip(),
            status=_status(work.get("serialStatus")),
            content_type=ContentType.NOVEL.value, language="ja")

    # -- chapters ---------------------------------------------------------------
    def get_chapters(self, series_id: str):
        work_id = _norm_id(series_id)
        state = self._work_state(work_id)
        work = self._work(state, work_id)
        toc = work.get("tableOfContentsV2")
        if not isinstance(toc, list):
            raise LayoutChanged("the table of contents")
        chapters, seen = [], set()
        for part_ref in toc:
            part = _ref(state, part_ref) or {}
            heading = str((_ref(state, part.get("chapter")) or {}).get("title") or "")
            for ep_ref in part.get("episodeUnions") or []:
                ep = _ref(state, ep_ref)
                if not ep or not ep.get("title") or not ep.get("id"):
                    continue
                ep_id = str(ep["id"])
                if ep_id in seen:
                    continue
                seen.add(ep_id)
                chapters.append(ChapterInfo(
                    self.name, work_id, ep_id, str(ep["title"]),
                    f"{self.base_url}/works/{work_id}/episodes/{ep_id}", heading))
        if not chapters:
            raise LayoutChanged("any episodes in the table of contents")
        return chapters

    # -- chapter text -------------------------------------------------------------
    def get_chapter_text(self, chapter) -> str:
        work_id = _norm_id(chapter.series_id)
        ep_id = _norm_id(chapter.chapter_id)
        html = self._get(f"{self.base_url}/works/{work_id}/episodes/{ep_id}",
                         f"Loading chapter {chapter.title}")
        body = _soup(html).select_one("div.widget-episodeBody")
        if body is None:
            raise LayoutChanged("the chapter text")
        lines = [_lines(p).strip("\r\n").rstrip() for p in body.find_all("p")]
        text = "\n".join(lines).strip("\n")
        if not text.strip():
            raise SourceError("Kakuyomu's page for this chapter had no text.",
                              FailureReason.UNKNOWN)
        return text

    # -- search ---------------------------------------------------------------------
    def search(self, query: str, page: int = 1):
        query = (query or "").strip()
        if not query:
            return []
        url = f"{self.base_url}/search?q={quote(query)}" + (f"&page={page}" if page > 1 else "")
        html = self._get(url, f"Searching Kakuyomu for {query!r}")
        state = _apollo_state(html)
        out, seen = [], set()
        for work_id in _WORK_HREF.findall(html):
            work = state.get(f"Work:{work_id}")
            if work_id in seen or not isinstance(work, dict) or not work.get("title"):
                continue
            seen.add(work_id)
            extra = {"status": _status(work.get("serialStatus")),
                     "description": str(work.get("introduction") or "").strip()}
            author = self._author(state, work)
            if author:
                extra["author"] = author
            count = work.get("publicEpisodeCount")
            if str(count).isdigit():
                extra["episodes"] = int(count)
            out.append(SearchResult(self.name, work_id, str(work["title"]),
                                    f"{self.base_url}/works/{work_id}", "", extra))
        if not out and re.search(r'href="/works/', html):
            raise LayoutChanged("the search results")
        return out

    # -- URLs -------------------------------------------------------------------------
    def parse_url(self, url: str):
        if not self.matches_url(url):
            return None
        path = urlsplit(url.strip()).path
        m = re.fullmatch(r"/works/(" + _ID + r")/episodes/(" + _ID + r")/?", path)
        if m:
            work_id, ep_id = m.groups()
            return ("chapter", ChapterInfo(self.name, work_id, ep_id, ep_id,
                                           f"{self.base_url}/works/{work_id}/episodes/{ep_id}"))
        m = re.fullmatch(r"/works/(" + _ID + r")/?", path)
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.TEXT.value
        caps.technical = {
            "extraction_method": "static server-rendered HTML; work, chapter list and search "
                                 "metadata from the page's embedded Next.js JSON",
            "browser_required": False,
        }
        caps.terms = {
            "robots_txt": "kakuyomu.jp (fetched 2026-11-26): `User-agent: *` disallows only "
                          "login, report and feedback paths and /works/*/episodes/*/read$; "
                          "ClaudeBot is asked for Crawl-delay: 1.",
            "read": "https://kakuyomu.jp/legal/tos (利用規約), read directly 2026-11-26.",
            "clause": "第12条 forbids using data obtained through the service beyond the private "
                      "use the copyright law allows (copying, selling, publishing); 第13条 forbids "
                      "commercial use. No clause names crawlers, bots or automated access, so "
                      "this is recorded as not cleared, not as permitted.",
            "enforcement_note": "ToS/robots.txt enforcement is off app-wide (user decision "
                                "2026-09-27, sources/ladder.py check_terms); the finding is "
                                "recorded here, not enforced by this adapter.",
        }
        caps.automation_permission = AutomationPermission.UNKNOWN.value
        return caps
