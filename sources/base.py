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
    login(**credentials_free)     /  refresh_session()  (optional)

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

from .http import PacingPolicy, SourceClient
from .models import (NotSupportedError, SourceCapabilities)


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
    #: Whether login()/refresh_session() exist and are required.
    auth_supported = False
    auth_required = False

    def __init__(self, client: SourceClient = None, **client_kwargs):
        if client is None:
            client_kwargs.setdefault("policy", PacingPolicy.from_settings(self.host_min_interval))
            client_kwargs.setdefault("default_headers", dict(self.default_headers))
            client = SourceClient(self.name, **client_kwargs)
        self.client = client

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

    def login(self, **kwargs):
        raise NotSupportedError(f"{self.display_name or self.name} has no login step.")

    def refresh_session(self):
        raise NotSupportedError(f"{self.display_name or self.name} has no session to refresh.")

    # -- helpers ---------------------------------------------------------------
    def supports(self, method: str) -> bool:
        """True if this adapter overrides `method` -- lets the UI hide a
        button instead of offering one that can only fail."""
        mine = getattr(type(self), method, None)
        base = getattr(SourceAdapter, method, None)
        return mine is not None and mine is not base

    def matches_url(self, url: str) -> bool:
        return any(re.search(p, url or "") for p in self.url_patterns)

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
            auth_required=self.auth_required, auth_supported=self.auth_supported)

    @staticmethod
    def host_of(url: str) -> str:
        return urlsplit(url).netloc
