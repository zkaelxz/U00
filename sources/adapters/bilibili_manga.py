"""
sources/adapters/bilibili_manga.py -- 哔哩哔哩漫画 / Bilibili Manga (zh):
a candidate authenticated comic source, investigated
rather than assumed. Its own adapter, not a reuse of the
BilibiliSource -- they may eventually share authentication/session
plumbing, but the extraction logic is nothing alike (video's yt-dlp-
based approach vs. manga's token-gated single-page-app reality).

Confirmed directly (real fetches, not assumed):
  - No robots.txt exists for manga.bilibili.com (a real 404, not a
    proxy block).
  - The site is fully client-rendered: a real series page
    (manga.bilibili.com/detail/mc28793) returns essentially
    <div id="app-vm"></div> plus a <noscript> notice -- no server-side
    content leakage at all. This is the one zh source vetted so far
    that needs the browser-rendered tier as the MINIMUM, not an
    optional fallback.
  - A real, confirmed technical protection: signed/expiring image-
    delivery tokens (read directly from Armo00/bilibili-manga-downloader
    and lihe07/bilibili_comics_downloader's own source -- a GetImageIndex
    call for paths, then a separate ImageToken call appending a
    short-lived ?token= to each image URL). No tile-shuffling or
    canvas-rendering step was found in either tool -- the protection is
    token-based access gating, not image-level obfuscation. Absence of
    evidence isn't proof it doesn't exist for paid chapters specifically
    (neither tool's source was confirmed tested against a real paid
    chapter).
  - A Keiyoushi/Mihon extension existed but was removed after breakage
    (issue #6321) -- not available as an actively-maintained reference.

Could NOT be verified this pass, recorded honestly rather than guessed:
  - The real terms-of-service/purchase-agreement text. Four real
    agreement URLs exist (see AGREEMENT_URLS below) but each is an empty
    SPA shell whose text loads via client-side JS at runtime -- no
    JS-execution capability was available for THIS research pass, so the
    actual clause text was never read. This is "not yet checked," not
    "no relevant clause found," and needs a real browser read (logged
    out is fine) before this adapter should be trusted for real use.
  - Authenticated/paid-chapter behavior. No logged-in Bilibili session
    was available for this research. Whether a purchased chapter's
    tokens behave differently, whether extra protection appears on paid
    content specifically, and whether an ordinary authenticated browser
    session is actually sufficient are all real open questions that need
    the user's own account, not something researchable from outside one.

Design principle, non-negotiable: TOKEN REUSE, NEVER TOKEN GENERATION.
This adapter never calls Bilibili Manga's own ImageToken API or
constructs a token itself -- get_pages() below reuses
sources.generic_import.import_comic_page(), which loads the chapter URL
through the access ladder's browser-rendered tier (page_fetch.
fetch_rendered -- a real headless browser, so whatever session/cookies
it has receives whatever real, already-signed image URLs the site's own
JavaScript legitimately puts on the rendered page) and downloads exactly
those URLs, exactly as issued. This is the same "the browser is the
source of truth for what the user can actually access" principle the
challenge hand-off flow already applies to a CAPTCHA,
applied here to a token system instead -- and it doubles as the
"generic browser-based comic extraction as the fallback," since no
better-verified extraction technique exists for this site at all.

search()/get_series()/get_chapters() are left unsupported: no server-
rendered markup exists to parse (confirmed above), and no rendered-DOM
structure for search results, series metadata, or a chapter list was
independently verified this pass either -- only the single, generic
"reuse the rendered page's own image tags" mechanism get_pages() uses
was. A direct chapter URL still works via parse_url() + get_pages(),
the same "paste one URL, get its pages" path the front door already
offers for a site with no dedicated adapter at all -- this adapter's
real value is contributing a populated SourceCapabilities record (so a
future pass doesn't have to re-derive these same findings), source-health
tracking, and the token-reuse extraction mechanism itself, not a full
Mihon-style browsing experience.
"""

import re

from ..base import SourceAdapter
from ..models import (AccessTier, CapabilityStatus, ChapterInfo, ContentAccess, ContentHidden,
                      ContentType, FailureReason, PageRef, TechnicalStatus)
from ..registry import register
from .. import generic_import

# Real, dedicated agreement URLs found by direct fetch -- each is an
# empty SPA shell whose text loads via client-side JS at runtime, never
# actually read this pass (module docstring).
AGREEMENT_URLS = [
    "https://manga.bilibili.com/eden/app-agreement.html",
    "https://manga.bilibili.com/eden/payment-agreement.html",
    "https://manga.bilibili.com/eden/coupon-package-agreement.html",
    "https://manga.bilibili.com/eden/privacy-policy-detail.html",
]

# The one confirmed real URL shape is the series page
# (manga.bilibili.com/detail/mc28793); a chapter URL's own shape was
# NOT independently confirmed this pass. This pattern is a reasonable,
# explicitly-hedged guess at the typical detail/mc<id>/<chapter> or
# mc<id>/<chapter> shapes other Bilibili Manga tooling documents, not a
# verified fact -- re-check against a real chapter URL before relying on
# parse_url() for anything beyond a quick manual test.
_CHAPTER_URL = re.compile(
    r"manga\.bilibili\.com/(?:detail/mc\d+/(?P<ch1>\d+)|mc\d+/(?P<ch2>\d+))")


@register
class BilibiliMangaSource(SourceAdapter):
    name = "bilibili_manga"
    display_name = "哔哩哔哩漫画 Bilibili Manga"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [r"manga\.bilibili\.com/"]

    def __init__(self, client=None, rendered_fetch=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self._rendered_fetch = rendered_fetch
        # Bilibili Manga's image tokens are short-lived -- get_pages()
        # downloads and measures each page's bytes during its own single
        # rendered-page visit (via generic_import.import_comic_page(),
        # which already does exactly this) rather than deferring the
        # actual download to a later download_page() call, which could
        # easily run after the token has expired. download_page() just
        # returns what get_pages() already fetched.
        self._page_bytes = {}

    def get_pages(self, chapter):
        try:
            result = generic_import.import_comic_page(
                chapter.url, client=self.client, rendered_fetch=self._rendered_fetch,
                remember=True)
        except generic_import.NoContentFound as e:
            raise ContentHidden(
                f"{self.display_name}'s chapter pages are protected by a signed/expiring "
                "image-delivery token system. This chapter may be viewable in your own "
                "authenticated browser, but its images couldn't be imported through the "
                f"adapter. ({e})",
                FailureReason.SIGNED_RESOURCE) from e
        refs = []
        for c in result.images:
            ref = PageRef(self.name, chapter.chapter_id, c.order, c.url)
            self._page_bytes[(chapter.chapter_id, c.order)] = (c.content, c.ext or ".jpg")
            refs.append(ref)
        return refs

    def download_page(self, page):
        cached = self._page_bytes.pop((page.chapter_id, page.index), None)
        if cached is not None:
            return cached
        raise ContentHidden(
            f"This page's image token was only fetched once, during get_pages() -- "
            "download_page() must be called for it before starting a new chapter "
            "(Bilibili Manga's image tokens are short-lived).",
            FailureReason.SIGNED_RESOURCE)

    def parse_url(self, url: str):
        m = _CHAPTER_URL.search(url or "")
        if not m:
            return None
        chapter_id = m.group("ch1") or m.group("ch2")
        return ("chapter", ChapterInfo(self.name, "", chapter_id, chapter_id, url))

    def capabilities(self):
        caps = super().capabilities()
        # Per the user's own explicit instruction: neither DISQUALIFIED
        # nor VERIFIED -- every authentication-dependent state genuinely
        # is untested, not guessed at in either direction.
        caps.status = CapabilityStatus.UNTESTED.value
        caps.technical_status = TechnicalStatus.UNRESOLVED.value
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.access_method = AccessTier.RENDERED_BROWSER.value
        caps.technical = {
            "browser_accessible": True,
            "extraction_method": "browser-rendered, reused image tokens -- this adapter never "
                                 "calls the ImageToken API or constructs a token itself",
            "protection_detected": "signed/expiring image-delivery tokens (confirmed via "
                                   "Armo00/bilibili-manga-downloader and "
                                   "lihe07/bilibili_comics_downloader's own source); "
                                   "paid-chapter-specific additional protection unconfirmed",
            "no_robots_txt": True,
            "spa_only": "confirmed no server-side content leakage for series pages (and "
                       "presumably chapter pages) -- <div id=\"app-vm\"></div> plus a "
                       "<noscript> notice only",
            "chapter_url_shape": "not independently confirmed this pass -- parse_url()'s "
                                 "pattern is an explicitly-hedged guess, not a verified fact",
        }
        caps.terms = {
            "checked": False,
            "agreement_urls": list(AGREEMENT_URLS),
            "note": "All four are empty SPA shells whose real text loads via client-side JS "
                   "at runtime -- not yet read in a real browser. This is 'not yet checked,' "
                   "not 'no relevant clause found.' Must be read (logged out is fine) before "
                   "this adapter is trusted for real use.",
            "tos_prohibited": False,
        }
        return caps
