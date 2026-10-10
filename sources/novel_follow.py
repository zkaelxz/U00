"""
sources/novel_follow.py -- following next-chapter links from a pasted novel
URL. It builds on import_novel / extract_novel from sources/adaptive.py.
"""

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from . import generic_import, profiles
from .adaptive import (ExtractionReport, _log, _note_access, _unreachable_reason, extract_novel,
                       import_novel, page_heading)
from .generic_import import NovelImportResult
from .http import Cancelled
from .models import SourceError

DEFAULT_FOLLOW_PAGES = 10
MAX_FOLLOW_PAGES = 50
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
    tier: str = None


@dataclass
class FollowResult:
    """The first page exactly as import_novel returned it, then every page
    read by following its next-chapter link, in order."""
    first: NovelImportResult
    report: ExtractionReport
    pages: list = field(default_factory=list)    # FollowedPage, the first included
    stop: str = ""                               # one of FOLLOW_STOPS
    handoff: dict = None                         # a followed page's hand-off


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
                 progress=None) -> FollowResult:
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
    the keyword link or the AI's pick). A link that is really the next
    *part* of the same chapter (e.g. "下一页") is followed like any other,
    so such a part arrives as its own page, under its own heading.

    The first page behaves exactly as in import_novel: it raises
    NoContentFound, and a hand-off there returns with no pages."""
    max_pages = max(1, min(int(max_pages), MAX_FOLLOW_PAGES))
    client = generic_import.http_client(client, url)
    first, report = import_novel(url, engine=engine, client=client, rendered_fetch=rendered_fetch,
                                 use_cache=use_cache, allow_signed_in=allow_signed_in,
                                 allow_browser=allow_browser, remember=remember,
                                 hold_profiles=hold_profiles)
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
    while True:
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
        text = page.get("content") or ""
        title = page_heading(page, lr.html, nxt)
        if total + len(text) + len(title) > max_chars:
            out.stop = "chars"
            break
        out.pages.append(FollowedPage(nxt, title, text, lr.tier))
        total += len(text) + len(title)
        current, nxt = nxt, page.get("next_url")
    return out
