"""
sources/adapters/bilibili.py -- Bilibili video links: which URLs are
Bilibili's, the b23.tv short-link hop, and the adapter's capabilities.

Reading a video's metadata or downloading it is yt-dlp's maintained
Bilibili extractor, which replicates the API calls Bilibili's own public web
player makes (WBI request signing included); no CAPTCHA solving or anti-bot
challenge defeat happens here. That code is site-supplied and does its own
networking, so it never runs in this process: the Sources preview reads the
title through services/ytdlp_child behind the guarded egress proxy (the
front door's `video_probe`), and a download is Workspace's Video URL
(services/url_media_service).

Bilibili is a VIDEO source, so the comic/novel methods of SourceAdapter
don't apply; it subclasses it for registry and front-door participation.
"""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..base import SourceAdapter, host_url_search
from ..models import AccessTier, CapabilityStatus, ContentType, SourceError, TechnicalStatus
from ..registry import register

_URL_PATTERNS = [
    r"bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)",
    r"bilibili\.com/bangumi/play/",
    r"b23\.tv/",
]


class BilibiliDownloadError(SourceError):
    """A Bilibili link refused before yt-dlp sees it, as a SourceError the
    front door shows as a note."""


@register
class BilibiliSource(SourceAdapter):
    name = "bilibili"
    display_name = "Bilibili"
    homepage = "https://www.bilibili.com"
    content_types = [ContentType.VIDEO.value]
    languages = ["zh"]
    url_patterns = _URL_PATTERNS
    auth_supported = True  # cookie-based

    def __init__(self, client=None, url_resolver=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self._url_resolver = url_resolver or self._real_url_resolver

    def _real_url_resolver(self, url: str) -> str:
        """Expands a b23.tv short link through the shared, paced
        SourceClient transport rather than a bare `requests.head()` call --
        pacing/rate-limiting applies to this hop too, same as every other
        real network call this adapter makes."""
        return self.client.request("HEAD", url).url

    # -- URL handling ---------------------------------------------------------

    def can_handle(self, url: str) -> bool:
        return self.matches_url(url)

    def normalize_url(self, url: str) -> str:
        """Resolves a b23.tv short link, strips tracking params, and
        keeps only an explicit ?p=N part selector (item 1, item 3)."""
        url = (url or "").strip()
        if host_url_search([r"b23\.tv/"], url):
            url = self._url_resolver(url)
            # The short link may redirect anywhere; only a real
            # bilibili video/bangumi URL may reach yt-dlp.
            if not host_url_search(_URL_PATTERNS[:2], url or ""):
                raise BilibiliDownloadError(
                    "This short link does not lead to a Bilibili video page.")
        parts = urlsplit(url)
        kept = [(k, v) for k, v in parse_qsl(parts.query) if k == "p"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))

    # -- capabilities ------------------------------------------------------------

    def capabilities(self):
        caps = super().capabilities()
        caps.status = CapabilityStatus.VERIFIED.value
        caps.technical_status = TechnicalStatus.SUPPORTED.value
        caps.access_method = AccessTier.STATIC_HTTP.value
        caps.technical["extraction_method"] = (
            "yt-dlp's maintained Bilibili extractor -- replicates the same API calls "
            "Bilibili's own public web player makes (including WBI request signing), "
            "not a defeat of any anti-bot challenge.")
        caps.technical["no_challenge_defeat"] = True
        caps.terms["checked"] = (
            "Not independently re-read for this adapter -- yt-dlp's own maintained "
            "extractor is the basis for treating ordinary public-video access as "
            "supported; a video that needs login shows a clear message rather than "
            "attempting to work around the requirement.")
        return caps
