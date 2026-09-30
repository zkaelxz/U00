"""
sources/base.py -- the adapter interface (Step 23 item 1).

An adapter is one class per site. It declares what it serves (content
types, languages, URL patterns) and implements whichever of these it can:

    search(query)                 -> [SearchResult]
    get_series(series_id)         -> SeriesInfo
    get_chapters(series_id)       -> [ChapterInfo]
    get_pages(chapter)            -> [PageRef]          (image sources)
    download_page(page)           -> (bytes, ext)       (image sources)
    get_chapter_text(chapter)     -> str                (text sources)
    login(url)                    -> LoginCheck         (Step 23k: the person
                                                         signs in in a real browser
                                                         window; no credentials
                                                         pass through the app)

No adapter has to implement everything: an unimplemented method raises
NotSupportedError, which callers catch and show as "this source doesn't
offer X" -- never a traceback. Image sources and text sources share the
one interface without either pretending to be the other.

All network access goes through `self.client` (sources.http.SourceClient),
so pacing, retries, challenge hand-off, health and caching apply to every
adapter automatically -- an adapter can't forget them.
"""

import re
from urllib.parse import urlsplit

from . import store
from .http import PacingPolicy, SourceClient
from .models import (NotSupportedError, Requirement, SourceCapabilities)



def host_url_search(patterns, url, flags=0):
    """True if `url` is an http(s) URL whose *parsed host* is matched by one of
    the host+path regexes in `patterns` (B-25).

    The regex runs on ``hostname + path [+ ?query]`` and must start at the
    beginning of the host or right after a dot inside it (so ``www.``/``m.``
    subdomains match, ``evilbilibili.com`` / ``x.com/?b23.tv/`` /
    ``bilibili.com@evil.com`` / ``bilibili.com.evil.com`` do not)."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower()
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https") or not host:
        return False
    cand = host + (parts.path or "/") + ("?" + parts.query if parts.query else "")
    for p in patterns:
        for m in re.finditer(p, cand, flags):
            if m.start() < len(host) and m.end() >= len(host) and \
                    (m.start() == 0 or cand[m.start() - 1] == "." or cand[m.start()] == "."):
                return True
    return False


class SourceAdapter:
    #: Stable id, used as the key everywhere (health, capabilities, tracking).
    name = ""
    #: Human-facing name.
    display_name = ""
    #: [ContentType values]
    content_types = []
    #: ISO-639-1 codes
    languages = []
    #: Regexes matched against a pasted URL, for the front door's routing.
    url_patterns = []
    #: Stricter-than-default minimum gap per host, in seconds -- e.g. a
    #: site's own robots.txt Crawl-delay.
    host_min_interval = {}
    #: Headers every request to this source carries (Referer etc.).
    default_headers = {}
    #: Adapter-specific auth. `auth_required` seeds the capability
    #: record's `authentication_required` (REQUIRED if True, else UNKNOWN
    #: until a real attempt observes it).
    auth_supported = False
    auth_required = False
    #: Where login() opens the signed-in browser when no page is given.
    login_url = ""
    #: True if the site gates some works behind its own "I'm an adult"
    #: switch (usually a cookie) and this adapter knows how to send it.
    #: The Sources tab then shows a per-source toggle for it.
    supports_adult_toggle = False
    #: False for a request not from this PC (the API sets it per job):
    #: an adapter that opens a browser inside get_series/get_chapters
    #: must refuse instead (docs/remote-access-decision.md: "no browser").
    allow_browser = True

    def __init__(self, client: SourceClient = None, allow_adult: bool = None, **client_kwargs):
        if client is None:
            client_kwargs.setdefault("policy", PacingPolicy.from_settings(self.host_min_interval))
            client_kwargs.setdefault("default_headers", dict(self.default_headers))
            client = SourceClient(self.name, **client_kwargs)
        self.client = client
        # Off unless the person switched it on for this source in the
        # Sources tab (or a caller passes it explicitly, e.g. tests).
        if allow_adult is None:
            allow_adult = self.supports_adult_toggle and store.adult_enabled(self.name)
        self.allow_adult = bool(allow_adult) and self.supports_adult_toggle

    def adult_hidden_message(self, what: str = "this work") -> str:
        """The ContentHidden text for a work the site keeps behind its
        adult switch -- names the exact setting that changes it."""
        name = self.display_name or self.name
        if self.supports_adult_toggle:
            return (f"{name} keeps {what} behind its adult-content switch. To include "
                    f"adult-flagged works, turn on \"🔞 Include adult-flagged works\" for "
                    f"{name} under Sources → Sources, health & diagnostics.")
        return f"{name} keeps {what} behind an adult-content switch this source can't send."

    # -- the interface -------------------------------------------------------
    def search(self, query: str, page: int = 1):
        raise NotSupportedError(f"{self.display_name or self.name} doesn't support search.")

    def get_series(self, series_id: str):
        raise NotSupportedError(f"{self.display_name or self.name} doesn't provide series details.")

    def get_chapters(self, series_id: str):
        raise NotSupportedError(f"{self.display_name or self.name} doesn't provide a chapter list.")

    def get_pages(self, chapter):
        raise NotSupportedError(f"{self.display_name or self.name} doesn't provide page images.")

    def download_page(self, page):
        raise NotSupportedError(f"{self.display_name or self.name} doesn't download page images.")

    def get_chapter_text(self, chapter):
        raise NotSupportedError(f"{self.display_name or self.name} doesn't provide chapter text.")

    def get_audio_url(self, chapter):
        """Optional: an audio source's equivalent of get_pages()/
        get_chapter_text() (roadmap Step 94) -- resolves one episode to
        its real, playable location. Returns models.AudioRef."""
        raise NotSupportedError(f"{self.display_name or self.name} doesn't provide episode audio.")

    def login(self, url: str = "", launcher=None):
        """Step 23k's manual login: opens this source's persistent browser
        profile at `url` (or `login_url`) for the person to sign in
        themselves, waits until they close the window, then checks the
        page is actually visible in that session. The app never sees or
        takes a password, and never solves a CAPTCHA, passes MFA or
        touches a purchase check. Returns sources.auth_browser.LoginCheck;
        raises TermsProhibited for a source whose terms forbid automated
        access -- before any window opens."""
        url = url or self.login_url
        if not url:
            raise NotSupportedError(f"{self.display_name or self.name} has no login page set -- "
                                    "paste a page from it to sign in there.")
        from . import auth_browser
        return auth_browser.manual_login(url, source=self.name, default=self.capabilities(),
                                         launcher=launcher)

    def refresh_session(self):
        # Deliberately unsupported: a persistent browser profile's session
        # is kept fresh by the site itself during ordinary visits. The app
        # never refreshes, re-issues or forges a session on its own.
        raise NotSupportedError(f"{self.display_name or self.name} has no session to refresh.")

    # -- helpers ---------------------------------------------------------------
    def supports(self, method: str) -> bool:
        """True if this adapter overrides `method` -- lets the UI hide a
        button instead of offering one that can only fail."""
        mine = getattr(type(self), method, None)
        base = getattr(SourceAdapter, method, None)
        return mine is not None and mine is not base

    @classmethod
    def matches_url(cls, url: str) -> bool:
        return host_url_search(cls.url_patterns, url)

    def parse_url(self, url: str):
        """Optional: map a pasted URL to ("series", series_id) or
        ("chapter", ChapterInfo). None if it isn't recognized."""
        return None

    def capabilities(self) -> SourceCapabilities:
        """The record an adapter ships with. `status` stays UNTESTED until a
        real import or a Test Now button moves it."""
        return SourceCapabilities(
            platform=self.display_name or self.name,
            content_types=list(self.content_types), languages=list(self.languages),
            authentication_required=(Requirement.REQUIRED.value if self.auth_required
                                     else Requirement.UNKNOWN.value),
            auth_supported=self.auth_supported)
