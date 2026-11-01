"""
sources/novel_follow.py -- following next-chapter links from a pasted novel
URL. It builds on import_novel / extract_novel from sources/adaptive.py.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlsplit

from . import ai_extract, generic_import, profiles
from .adaptive import (ExtractionReport, _log, _note_access, _unreachable_reason, extract_novel,
                       import_novel, page_heading)
from .generic_import import NovelImportResult
from .http_transport import Cancelled
from .models import SourceError

DEFAULT_FOLLOW_PAGES = 10
MAX_FOLLOW_PAGES = 50
# A chapter a site splits over pages rarely runs past a few; more than this
# from one pasted address is more likely a whole book paged the same way.
MAX_CHAPTER_PARTS = 10
# All followed pages together; each page is also held to ax.MAX_NOVEL_CHARS.
MAX_FOLLOW_CHARS = 1_500_000

# Why a chain stopped (a code; the screen words it).
FOLLOW_STOPS = ("cap", "no_next", "cycle", "other_host", "downgrade", "gate", "not_public",
                "handoff", "unreachable", "invalid", "chars")

# A sign-in, sign-out, age-check or payment page is never followed: an
# unattended hop must not act on the person's account or decide for them.
_GATE_PATH = re.compile(
    r"(?:^|[/_.\-?&=])(?:log-?in|sign-?in|sign_in|sign-?up|register|passport|o?auth|"
    r"log-?out|sign-?out|sign_out|"
    r"age-?gate|age_gate|age-?check|age_check|age-?verif\w*|age_verif\w*|adult-?check|"
    r"over-?18|buy|purchase|pay|payment|checkout|unlock|subscribe|subscription|recharge|"
    r"top-?up|cart)(?:$|[/_.\-?&=])", re.I)
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass
class FollowedPage:
    url: str            # the full URL: kept on the server only
    title: str
    text: str
    tier: str | None = None
    part: bool = False  # a later page of the chapter on the page before it


# The link a chapter's page uses for its own next page, and for the next
# chapter. Many sites label the next chapter "下一页" too, so the words only
# decide whether a page is worth reading as a part; its heading decides.
_PAGE_WORDS = ("下一页", "下一頁", "下页", "下頁", "next page", "次のページ", "다음 페이지")
_CHAPTER_WORDS = ("下一章", "下章", "下一话", "下一話", "后一章", "next chapter", "次の話", "次話",
                  "다음화")
# A page marker on a part's heading: 第一章 初见（第2/2页）, (2/3), 第2页, (Page 2 of 3).
_PART_MARK = re.compile(
    r"\s*(?:[（(【\[]\s*(?:第\s*)?\d{1,3}\s*[/／]\s*\d{1,3}\s*[页頁]?\s*[）)】\]]"
    r"|[（(【\[]\s*第?\s*\d{1,3}\s*[页頁]\s*[）)】\]]"
    r"|[（(\[]\s*(?:page|p\.)\s*\d{1,3}(?:\s*(?:of|/)\s*\d{1,3})?\s*[）)\]]"
    r"|[-_—–:：]?\s*第\s*\d{1,3}\s*[页頁])\s*$", re.I)
_PAGE_QUERY_KEYS = ("page", "p", "pn", "pg")


def chapter_heading(title: str | None) -> str:
    """A part's heading without its page marker, for the joined chapter."""
    return _PART_MARK.sub("", title or "").strip()


def _link_text(html: str, page_url: str, link: str) -> str:
    for l in ai_extract.PageModel(html or "", page_url).links:
        if l.url == link:
            return l.text.strip().lower()
    return ""


def _is_part_url(link: str, chapter_url: str, by_query: bool = True) -> bool:
    """`link` is named as a later page of the chapter at `chapter_url`:
    1.html -> 1_2.html / 1-2.html, or (`by_query`) the same path with a page
    number."""
    a, b = urlsplit(link or ""), urlsplit(chapter_url or "")
    if profiles.domain_of(link) != profiles.domain_of(chapter_url):
        return False
    if a.path == b.path:
        if not by_query:
            return False
        extra = dict(parse_qsl(a.query)).items() - dict(parse_qsl(b.query)).items()
        return bool(extra) and all(k.lower() in _PAGE_QUERY_KEYS and v.isdigit()
                                   for k, v in extra)
    stem, dot, ext = b.path.rpartition(".")
    if not dot or "/" in ext:
        stem, ext = b.path.rstrip("/"), ""
    tail = r"\." + re.escape(ext) if ext else "/?"
    return a.query == b.query and bool(
        re.fullmatch(re.escape(stem) + r"[_-]\d{1,3}" + tail, a.path))


def _maybe_part(link_text: str, link: str, chapter_url: str) -> bool:
    """Worth reading as the next page of the chapter (before fetching it)."""
    if any(w in link_text for w in _CHAPTER_WORDS):
        return False
    return _said_page(link_text) or _is_part_url(link, chapter_url)


def _own_heading(data: dict) -> str | None:
    """The chapter title the page shows, not one taken from its <title>,
    which also names the book and the site."""
    return data.get("chapter_title") if data.get("chapter_title_block") else ""


def _said_page(link_text: str) -> bool:
    return any(w in link_text for w in _PAGE_WORDS)


def _same_chapter(chapter: dict, page: dict, page_url: str, chapter_url: str,
                  said_page: bool) -> bool:
    """The page read is a part of the chapter, not the chapter after it:
    the same heading once page markers are off, or, when either page has
    no heading of its own, an address named as the chapter's next page.
    Some sites number their chapters with ?p=, so a page number in the
    query counts only when the link also said "next page"."""
    a, b = (ai_extract.squash(chapter_heading(_own_heading(d))) for d in (chapter, page))
    if a and b:
        return a == b
    return _is_part_url(page_url, chapter_url, by_query=said_page)


@dataclass
class FollowResult:
    """The first page exactly as import_novel returned it, then every page
    read by following its next-chapter link, in order."""
    first: NovelImportResult
    report: ExtractionReport
    pages: list = field(default_factory=list)    # FollowedPage, the first included
    stop: str = ""                               # one of FOLLOW_STOPS
    handoff: dict | None = None                         # a followed page's hand-off


def _follow_key(url: str) -> tuple:
    """Same page for the cycle check: fragment, scheme, "www." and a
    trailing slash don't make a different page."""
    p = urlsplit(url or "")
    return profiles.domain_of(url), p.path.rstrip("/") or "/", p.query


def _host_key(url: str):
    """(host without "www.", port or None for the scheme's default), or
    None for a URL that isn't http(s) with a valid host and port."""
    p = urlsplit(url or "")
    scheme = p.scheme.lower()
    try:
        port = p.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not p.hostname:
        return None
    return profiles.domain_of(url), None if port in (None, _DEFAULT_PORTS[scheme]) else port


def _unfollowable(url: str, current: str, seen: set):
    """The stop code for a next link found on the page at `current` that
    must not be followed, else None."""
    p = urlsplit(url or "")
    key = _host_key(url)
    if key is None or key != _host_key(current):
        return "other_host"
    if urlsplit(current).scheme.lower() == "https" and p.scheme.lower() != "https":
        return "downgrade"
    if _follow_key(url) in seen:
        return "cycle"
    if _GATE_PATH.search(p.path) or _GATE_PATH.search(p.query):
        return "gate"
    return None


def follow_novel(url: str, max_pages: int = DEFAULT_FOLLOW_PAGES, engine=None, client=None,
                 rendered_fetch=None, use_cache: bool = True, allow_signed_in: bool = True,
                 allow_browser: bool = True, remember: bool = True, hold_profiles: bool = False,
                 max_chars: int = MAX_FOLLOW_CHARS, url_check=None, cancel_check=None,
                 progress=None, parts_only: bool = False) -> FollowResult:
    """import_novel on `url`, then its next-chapter link, page by page,
    through the same client (so the same pacing, terms check, page-size
    caps and per-hop address guard) and the same ladder and checks as the
    first page. Writes nothing to any drama.

    Stops, keeping the pages read so far, at `max_pages` (capped at
    MAX_FOLLOW_PAGES), when a page has no next link, when the link leads
    to a page already read (a cycle or a self-link), to another host or
    port, from https to plain http, to a sign-in, sign-out, age-check or
    payment page, or to an address `url_check(url) -> bool` refuses, at a
    verification page (handed off, never worked around), at a page that
    can't be loaded (over the size cap, too slow, refused) or whose text
    fails the import checks, and before `max_chars` (text and titles) in
    all would be passed. Only the first page can save or offer a site
    profile, record a profile's use or cache an AI result: the followed
    pages read profiles and the cache but write neither. `cancel_check()` true between
    pages raises Cancelled. `progress(pages_read, max_pages)` before each
    followed page.

    The next link is the one the extraction found (a saved profile's rule,
    the keyword link or the AI's pick). A page that is a later part of the
    chapter before it (its heading is that chapter's without a page marker
    such as "（第2/2页）", or it has none and its address is the chapter's
    own with a page number) comes back with `part` set. `parts_only`: read
    only the pasted chapter's own later pages; the chain ends ("no_next")
    at a link to, or a page of, another chapter.

    The first page behaves exactly as in import_novel: it raises
    NoContentFound, and a hand-off there returns with no pages."""
    client = generic_import.http_client(client, url)
    first, report = import_novel(url, engine=engine, client=client, rendered_fetch=rendered_fetch,
                                 use_cache=use_cache, allow_signed_in=allow_signed_in,
                                 allow_browser=allow_browser, remember=remember,
                                 hold_profiles=hold_profiles)
    return follow_from(url, first, report, max_pages, engine=engine, client=client,
                       rendered_fetch=rendered_fetch, use_cache=use_cache,
                       allow_signed_in=allow_signed_in, allow_browser=allow_browser,
                       remember=remember, max_chars=max_chars, url_check=url_check,
                       cancel_check=cancel_check, progress=progress, parts_only=parts_only)


def follow_from(url: str, first: NovelImportResult, report: ExtractionReport,
                max_pages: int = DEFAULT_FOLLOW_PAGES, engine=None, client=None,
                rendered_fetch=None, use_cache: bool = True, allow_signed_in: bool = True,
                allow_browser: bool = True, remember: bool = True,
                max_chars: int = MAX_FOLLOW_CHARS, url_check=None, cancel_check=None,
                progress=None, parts_only: bool = False) -> FollowResult:
    """follow_novel after its first page: `first`, `report` are what
    import_novel returned for `url`, read through `client`."""
    max_pages = max(1, min(int(max_pages), MAX_FOLLOW_PAGES))
    client = generic_import.http_client(client, url)
    out = FollowResult(first, report)
    if first.ladder is not None and first.ladder.handoff:
        out.stop = "handoff"
        return out
    out.pages.append(FollowedPage(url, first.title, first.text,
                                  getattr(first.ladder, "tier", None)))
    data = report.data or {}
    if not data.get("valid"):
        out.stop = "invalid"
        return out
    seen = {_follow_key(url)}
    total = len(first.text or "") + len(first.title or "")
    current, nxt = url, data.get("next_url")
    current_html = getattr(first.ladder, "html", "") or ""
    chapter_url, chapter_data = url, data
    while True:
        link_text = _link_text(current_html, current, nxt) if nxt else ""
        maybe_part = bool(nxt) and _maybe_part(link_text, nxt, chapter_url)
        if parts_only and not maybe_part:
            out.stop = "no_next"
            break
        if len(out.pages) >= max_pages:
            out.stop = "cap"
            break
        if not nxt:
            out.stop = "no_next"
            break
        why = _unfollowable(nxt, current, seen)
        if why is None and url_check is not None and not url_check(nxt):
            why = "not_public"
        if why:
            out.stop = why
            break
        if cancel_check is not None and cancel_check():
            raise Cancelled("Cancelled.")
        if progress is not None:
            progress(len(out.pages), max_pages)
        seen.add(_follow_key(nxt))
        # A hop the person didn't choose never saves or offers a site profile.
        page_report = ExtractionReport(nxt, "novel", hold_profiles=True)
        try:
            lr = generic_import.fetch_page(nxt, client, rendered_fetch, None,
                                           allow_signed_in=allow_signed_in,
                                           allow_browser=allow_browser, record=remember)
        except SourceError:
            # Over the size cap, past the deadline or refused: the pages
            # already read are kept.
            page_report.reason = "Couldn't load this page."
            _log(page_report)
            out.stop = "unreachable"
            break
        _note_access(page_report, lr)
        if lr.handoff:
            page_report.reason = (f"Stopped at a browser verification page "
                                  f"({lr.handoff['reason']}) -- handed to you.")
            _log(page_report)
            out.stop, out.handoff = "handoff", lr.handoff
            break
        if not lr.ok:
            page_report.reason = _unreachable_reason(page_report)
            _log(page_report)
            out.stop = "unreachable"
            break
        page, page_report = extract_novel(lr.html, nxt, engine, use_cache, page_report,
                                          remember=False)
        _log(page_report)
        if page is None or not page.get("valid"):
            out.stop = "invalid"
            break
        part = maybe_part and _same_chapter(chapter_data, page, nxt, chapter_url,
                                            _said_page(link_text))
        if parts_only and not part:
            out.stop = "no_next"
            break
        text = page.get("content") or ""
        title = page_heading(page, lr.html, nxt)
        if total + len(text) + len(title) > max_chars:
            out.stop = "chars"
            break
        out.pages.append(FollowedPage(nxt, title, text, lr.tier, part=part))
        total += len(text) + len(title)
        if not part:
            chapter_url, chapter_data = nxt, page
        current, nxt, current_html = nxt, page.get("next_url"), lr.html
    return out


def joined_chapters(entries) -> list:
    """(text, heading, url, pages read) per chapter from (text, heading,
    url, part) in reading order, None for a page left out: each part's text
    appended to the chapter it continues, under that chapter's heading
    without its page marker. A part right after a page left out stands on
    its own."""
    out, joinable = [], False
    for entry in entries:
        if entry is None:
            joinable = False
            continue
        text, heading, url, part = entry
        if part and joinable:
            prev_text, prev_heading, first_url, n = out[-1]
            out[-1] = (f"{prev_text}\n{text}", chapter_heading(prev_heading) or prev_heading,
                       first_url, n + 1)
        else:
            out.append((text, heading, url, 1))
        joinable = True
    return out
