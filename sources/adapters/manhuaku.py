"""
sources/adapters/manhuaku.py -- 漫画库 manhuaku.net (zh manhua), roadmap
Step 23j.

**Confirmed platform**: a real, stock MCCMS (`chshcms/mccms`) deployment
(the page itself carries an explicit "Mccms core JS build, must be
loaded" comment and loads `/packs/mccms/base.js`) -- not bespoke, but
running its own fully custom template (`/template/pc/30/`), so none of
`MCCMSWeb`'s own default selectors apply unmodified; every selector below
was read directly from a real fetched page while building this adapter,
the same as if no reference existed at all.

**Confirmed protection, and the deliberate design choice around it.** The
roadmap's deep-vetting pass found chapter-reader image data passed through
a `readPic(...)` call wrapped in a commercial JS obfuscator (jsjiami.com.v7)
and AES-encrypted with an embedded key -- real, corroborated by a public
CVE (CVE-2025-50234) documenting the same scheme server-side in MCCMS's
own code. **This adapter never deobfuscates that code or reimplements its
AES decryption, even though the key is real and findable.** A commercial
code-obfuscator plus an embedded key exists specifically to raise the cost
of exactly that kind of static, non-browser extraction -- the same intent
(if not the same mechanism) as this project's own CAPTCHA/anti-bot-challenge
line. Instead, `get_pages()` below routes **exclusively** through the
browser-rendered tier (`page_fetch.fetch_rendered`, invoked directly, not
via the ladder's own static-first escalation -- see the note in
`get_pages()` for why): a real headless browser runs the site's own real,
obfuscated JS with its own real key exactly as it would for any ordinary
visitor, and the adapter scrapes the resulting real image URLs from the
already-decrypted, rendered DOM afterward. The same real image URLs reach
the pipeline either way; the difference is entirely in whether this app
independently reverse-engineers a deliberate protection mechanism or lets
the site's own legitimate execution path produce the result -- the same
principle already applied to Bilibili Manga's signed image tokens (Step
23f) and manhuaku's own case here is the direct model for it.

**A real, useful side finding**: this specific detail page aggregates a
given title from more than one upstream source (a "source" tab list was
observed naming 催漫画网 and 包子漫画/baozimh -- the same baozimh this
project already has a dedicated adapter for). Only the default/first
source's chapter list is present in the static HTML; other sources' lists
appear to load only on demand (their containers weren't present
statically) -- this adapter reads whichever source is server-rendered by
default and doesn't attempt to switch sources.

**`search()` deliberately left unsupported, not guessed at.** The site
has a real, correctly-shaped search endpoint (`/search/<query>`, a real
HTML form posts to `/index.php/search`) that renders a genuine "no
results" page -- not a 404, not a stub. But three separate, plausible
real queries (a substring of a real title on this exact site, more of
that same title, and a globally well-known unrelated title) all came back
with zero results while building this adapter, suggesting the search
backend itself isn't functioning (a required parameter or session cookie
this adapter doesn't have, most likely) rather than those titles
genuinely not existing. Matches this project's own "don't guess when a
real check comes back empty" precedent (`52shuku.py`, `miaoqumh.py`).

`robots.txt` returned a real HTTP 403 (openresty-served, not a proxy
block) on three separate direct fetches across this project's research
and build passes -- reproduced again while building this adapter, not
transient. This is recorded as "crawl guidance is inaccessible," never as
"no restrictions, so anything goes."
"""

import re
from urllib.parse import urljoin

from ..base import SourceAdapter
from ..models import ChapterInfo, ContentAccess, ContentHidden, ContentType, FailureReason, SeriesInfo, SourceError
from ..registry import register

BASE_URL = "https://www.manhuaku.net"

_STATUS_MAP = {"连载中": "ongoing", "連載中": "ongoing", "已完结": "completed", "已完結": "completed",
              "已完成": "completed"}


class LayoutChanged(SourceError):
    """The page didn't have what this adapter expects -- most likely the
    site changed its markup. Reported plainly, never guessed around."""

    def __init__(self, what: str):
        super().__init__(f"manhuaku's page layout has changed -- couldn't find {what}. "
                         "The adapter needs updating.", FailureReason.UNKNOWN)


def _soup(html: str):
    from bs4 import BeautifulSoup
    return BeautifulSoup(html or "", "html.parser")


@register
class ManhuakuSource(SourceAdapter):
    name = "manhuaku"
    display_name = "漫画库 Manhuaku"
    content_types = [ContentType.MANHUA.value]
    languages = ["zh"]
    url_patterns = [r"manhuaku\.net/chapter/[^/?#]+\.html", r"manhuaku\.net/[^/?#]+/?$"]

    def __init__(self, client=None, base_url: str = None, rendered_fetch=None, **client_kwargs):
        super().__init__(client, **client_kwargs)
        self.base_url = base_url or BASE_URL
        self._rendered_fetch = rendered_fetch
        self._series_pages = {}

    def _get(self, path: str, action: str) -> str:
        resp = self.client.get(urljoin(self.base_url, path), action=action)
        return resp.text

    def _series_page(self, series_id: str) -> str:
        if series_id not in self._series_pages:
            self._series_pages[series_id] = self._get(f"/{series_id}", f"Loading series {series_id}")
        return self._series_pages[series_id]

    def get_series(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        h1 = soup.select_one("div.cy_title h1")
        if h1 is None:
            raise LayoutChanged("the series title")
        author_a = soup.select_one("span.cy_author a")
        genre_as = soup.select("span.cy_type a")
        status_el = soup.select_one("span.cy_serialize font")
        desc_el = soup.select_one("#comic-description")
        cover = soup.select_one("div.cy_info_cover img")
        status = _STATUS_MAP.get(status_el.get_text(strip=True) if status_el else "", "unknown")
        return SeriesInfo(
            self.name, series_id, h1.get_text(strip=True),
            urljoin(self.base_url, f"/{series_id}"),
            cover.get("src", "") if cover is not None else "",
            authors=[author_a.get_text(strip=True)] if author_a is not None else [],
            description=desc_el.get_text(strip=True) if desc_el is not None else "",
            genres=[a.get_text(strip=True) for a in genre_as],
            status=status, content_type=ContentType.MANHUA.value, language="zh")

    def get_chapters(self, series_id: str):
        html = self._series_page(series_id)
        soup = _soup(html)
        items = soup.select("ul[id^=mh-chapter-list-ol] li.chapter__item a")
        if not items:
            raise LayoutChanged("the chapter list")
        chapters = []
        for a in reversed(items):
            href = a.get("href", "")
            m = re.search(r"/chapter/([^/.]+)\.html", href)
            if not m:
                continue
            title = a.get("title") or a.get_text(strip=True)
            chapters.append(ChapterInfo(self.name, series_id, m.group(1), title or m.group(1),
                                        urljoin(self.base_url, href)))
        return chapters

    def get_pages(self, chapter):
        """Exclusively browser-rendered, per the module docstring's
        non-negotiable design choice -- never the static tier first. A
        real static fetch here would return HTML whose image data is
        still jsjiami-obfuscated and AES-encrypted; letting a real
        headless browser run the page's own real JS (with its own real
        key) is what turns that into real, usable image URLs. Reusing the
        static-then-render ladder would be wrong here specifically
        because the static tier would "succeed" (a real, non-shell page)
        without ever finding real images, so the ladder would never
        escalate to rendering on its own."""
        from .. import generic_import
        from ..models import PageRef

        def fetch(url):
            fn = self._rendered_fetch
            if fn is None:
                from page_fetch import fetch_rendered as fn
            return fn(url)

        html, _text = self.client.paced(fetch, chapter.url, "Browser session",
                                        action=f"Rendering chapter {chapter.title}")
        candidates = generic_import.image_candidates(html, chapter.url)
        if not candidates:
            raise LayoutChanged("any page images on the rendered chapter page")
        # A real, confirmed finding (2026-09-26 live check), not a
        # hypothetical: this site's real `readPic()` writes the decrypted
        # page images into the DOM as JS-created `blob:` object URLs, which
        # exist only inside that one browser tab's memory and can never be
        # independently re-`GET`ted the way every other candidate below is.
        # Every real page-image candidate on such a chapter is therefore
        # `blob:` and always fails the download-and-measure step further
        # down -- refuse clearly here instead of silently falling through
        # to whatever unrelated images (other titles' cover thumbnails from
        # the page's own recommendation sidebar, confirmed live) happen to
        # also be on the page and pass the filter below.
        if any(c.url.startswith("blob:") for c in candidates):
            raise ContentHidden(
                f"{self.display_name}'s real chapter-reader images are written into the page "
                "as browser-internal blob: URLs by its own decryption script -- this adapter "
                "can't independently download them the way it does every other source's page "
                "images. Reading them would need capturing the bytes from inside the rendered "
                "page itself, which this adapter doesn't do yet.",
                FailureReason.ENCRYPTED_RESOURCE)
        for c in candidates:
            try:
                resp = self.client.get(c.url, classify_body=False, headers={"Referer": chapter.url},
                                       action=f"Checking image {c.order + 1}/{len(candidates)}")
                c.content = resp.content
                generic_import._measure(c)
            except SourceError as e:
                c.reject_reason = f"couldn't download ({e.reason.value})"
        kept, _rejected = generic_import.filter_page_images(candidates, chapter.url)
        if not kept:
            raise LayoutChanged("any real page images among the rendered page's image candidates")
        return [PageRef(self.name, chapter.chapter_id, i, c.url) for i, c in enumerate(kept)]

    def download_page(self, page):
        resp = self.client.get(page.url, classify_body=False, headers=page.headers,
                               action=f"Downloading page {page.index + 1}")
        name = page.url.split("?", 1)[0]
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ".jpg"
        return resp.content, ext

    def parse_url(self, url: str):
        m = re.search(r"manhuaku\.net/chapter/([^/?#]+)\.html", url or "")
        if m:
            return ("chapter", ChapterInfo(self.name, "", m.group(1), m.group(1), url))
        m = re.search(r"manhuaku\.net/([^/?#]+)/?$", url or "")
        return ("series", m.group(1)) if m else None

    def capabilities(self):
        caps = super().capabilities()
        caps.content_access_status = ContentAccess.IMAGES.value
        caps.access_method = "RENDERED_BROWSER"
        caps.technical = {
            "extraction_method": "static HTML for series/chapters; get_pages() is exclusively "
                                 "browser-rendered -- this adapter never deobfuscates the site's "
                                 "jsjiami-wrapped code or reimplements its AES decryption, even "
                                 "though the embedded key is real and findable (module docstring).",
            "browser_required_for_pages": True,
            "protection_detected": "chapter-reader image data passed through a readPic(...) call "
                                   "wrapped in a commercial JS obfuscator (jsjiami.com.v7) and "
                                   "AES-encrypted with an embedded key -- corroborated by a public "
                                   "CVE (CVE-2025-50234) documenting the same scheme server-side "
                                   "in MCCMS's own code.",
            "multi_source_note": "this site aggregates some titles from more than one upstream "
                                 "source (a real 'source' tab list was observed naming 催漫画网 "
                                 "and baozimh); only the default/first source's chapter list is "
                                 "read -- other sources' lists appeared to load only on demand.",
            "search_unsupported": "the search endpoint renders a real, correctly-shaped page but "
                                  "returned zero results for three separate plausible real "
                                  "queries, including a globally well-known title -- looks like a "
                                  "non-functioning backend, not a selector mistake here.",
        }
        caps.terms = {
            "robots_txt": "Returned a real HTTP 403 (openresty-served) on three separate direct "
                          "fetches across this project's research and build -- reproduced again "
                          "while building this adapter, not transient. Recorded as 'crawl "
                          "guidance is inaccessible', never as 'no restrictions declared'.",
            "tos": "Not reviewed.",
            "tos_prohibited": False,
        }
        return caps
