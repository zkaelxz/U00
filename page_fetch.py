"""
page_fetch.py -- fetching pages that may or may not be JavaScript-rendered.

Why this exists: a plain requests+BeautifulSoup fetch of a modern
single-page app returns the HTML *shell* -- navigation, filter controls,
empty containers -- and none of the actual content, because the data is
loaded by JavaScript after the page arrives. baihehub.com is exactly
this: fetching its novel listing statically returns the page furniture
and the literal text "共 0 条数据" (0 items).

The old behaviour was to hand that empty shell to the LLM extractor,
which would dutifully find nothing and report "couldn't extract
metadata" -- indistinguishable from a genuinely empty page. That's a
silent failure, and silent failures are worse than loud ones.

So this module detects when a fetch returned a shell rather than
content, and renders pages properly with a headless browser.
"""

import re
from contextlib import contextmanager

from page_fetch_browser import _require_playwright
from page_fetch_guard import _goto, _guarded_chromium, _guarded_page
from page_scroll import scroll_through_and_settle
import storage

# Root containers common to SPA frameworks. Their presence alongside
# very little text is a strong signal the content hasn't rendered.
SPA_ROOT_MARKERS = [
    'id="app"', "id='app'", 'id="root"', "id='root'",
    'id="__next"', 'id="__nuxt"', 'data-reactroot', 'ng-app',
]

# Empty-state strings sites render before data loads.
EMPTY_STATE_MARKERS = [
    "共 0 条", "0 条数据", "暂无数据", "没有找到", "loading",
    "no results", "no data",
]


# An image-only comic page has almost no text and plenty of scripts, which is exactly
# what a shell looks like; its page images are what tell it apart. No "<" inside the
# tag, so an unclosed one can't make the match scan to the end of the page.
_IMG_TAG_RE = re.compile(r"<img\b[^<>]*>", re.I)
# A template binding such as :src="p.url" is a shell's unfilled markup, not an image.
_IMG_SRC_RE = re.compile(r"""(?<![\w:-])(?:data-)?src\s*=\s*["']?([^"'\s>]+)""", re.I)
_FURNITURE_IMG_RE = re.compile(r"logo|icon|avatar|sprite|banner|emoji|qrcode|loading|blank|placeholder|"
                               r"spacer|pixel|transparent|\bthumb|\bcover", re.I)
_TINY_IMG_RE = re.compile(r"""\b(?:width|height)\s*=\s*["']?[01]\b""", re.I)
# Tracking pixels sit in <noscript>, and commented-out markup is not on the page.
_HIDDEN_HTML_RE = re.compile(r"<noscript\b.*?</noscript\s*>|<!--.*?-->", re.I | re.S)
MIN_CONTENT_IMAGES = 3


def _content_image_folders(html: str) -> list:
    """The host-and-folder of each image that could be a page of the chapter."""
    folders = []
    for tag in _IMG_TAG_RE.findall(_HIDDEN_HTML_RE.sub(" ", html or "")):
        m = _IMG_SRC_RE.search(tag)
        if not m or _FURNITURE_IMG_RE.search(tag) or _TINY_IMG_RE.search(tag):
            continue
        src = m.group(1)
        if src.lower().startswith("data:"):
            continue
        folders.append(src.split("?", 1)[0].rsplit("/", 1)[0])
    return folders


def _is_image_page(html: str) -> bool:
    """A chapter's pages share one host and folder; a shell's stray ads, pixels and
    covers come from all over."""
    folders = _content_image_folders(html)
    return bool(folders) and max(folders.count(f) for f in set(folders)) >= MIN_CONTENT_IMAGES


def looks_like_unrendered_shell(html: str, extracted_text: str) -> dict:
    """
    Heuristic check for "this fetch returned a shell, not content".

    Returns {"is_shell": bool, "confidence": float, "reasons": [str]}.
    Deliberately returns reasons rather than a bare boolean so the UI
    can tell the person *why* it thinks the fetch failed.
    """
    reasons = []
    score = 0.0

    text_len = len((extracted_text or "").strip())
    html_len = len(html or "")
    image_page = _is_image_page(html)

    # A content page has a decent text-to-markup ratio. A shell doesn't.
    if html_len > 2000 and text_len < 600 and not image_page:
        reasons.append("very little text relative to page size")
        score += 0.4

    # An app container still counts unless the page images come after it: a shell's root is empty.
    roots = [html.find(m) for m in SPA_ROOT_MARKERS if m in (html or "")]
    if roots and not (image_page and _is_image_page(html[min(roots):])):
        reasons.append("page uses a JavaScript app container")
        score += 0.3

    lowered = (extracted_text or "").lower()
    for marker in EMPTY_STATE_MARKERS:
        if marker in lowered or marker in (extracted_text or ""):
            reasons.append(f"page shows an empty/loading state ({marker!r})")
            score += 0.35
            break

    # Lots of <script> and little else.
    script_count = len(re.findall(r"<script", html or "", flags=re.I))
    if script_count > 8 and text_len < 1500 and not image_page:
        reasons.append(f"{script_count} scripts but little rendered text")
        score += 0.2

    return {
        "is_shell": score >= 0.5,
        "confidence": min(score, 1.0),
        "reasons": reasons,
    }


def fetch_rendered(url: str, timeout: int = 30, wait_selector: str | None = None,
                    wait_ms: int = 2500):
    """
    Fetches with a real browser engine so JavaScript actually runs.

    Requires the playwright package and Chrome, Edge or Playwright's Chromium.

    Returns (html, text). Raises ImportError with install instructions
    if Playwright isn't set up.
    """
    with _rendered_page(url, timeout, wait_selector, wait_ms) as page:
        html = page.content()
    return html, _visible_lines(html)


def _url_matches(url: str, pattern) -> bool:
    """`pattern` is a plain substring or a compiled regex; either way,
    answers "does this response belong to the endpoint a caller is
    watching for"."""
    if hasattr(pattern, "search"):
        return bool(pattern.search(url))
    return pattern in url


def _capture_entry(url: str, status: int, content_type: str, body: bytes | None = None,
                   max_body_bytes: int = 5_000_000) -> dict:
    """One recorded response, with the body dropped (not raised past)
    when it's missing or larger than the cap -- so one huge, merely
    URL-matching download can't crowd out everything else a caller was
    watching for."""
    entry = {"url": url, "status": status, "content_type": content_type, "body": None}
    if body is not None and len(body) <= max_body_bytes:
        entry["body"] = body
    return entry


@contextmanager
def api_capture_session(url: str, url_pattern, timeout: int = 30, wait_ms: int = 3000,
                        max_body_bytes: int = 5_000_000, wait_until: str = "domcontentloaded"):
    """Opens `url` in a real browser and records every network response
    whose URL matches `url_pattern`, as the page's own JavaScript makes
    them -- yielding `(page, captured)` so the caller can also drive the
    page further (click, scroll, call a viewer's own API).

    For a site that protects its own content API with something computed
    client-side -- a request signature built from a nonce, a timestamp,
    a device token and a salt; a rotating token; anything this project
    has no business reverse-engineering -- the site's own JavaScript
    already knows how to build a valid request. So let it: capture the
    real, already-signed request/response pairs the page makes on its
    own, instead of porting the signing algorithm to Python. The same
    "let the site's own execution path produce the result" principle
    Bilibili Manga's adapter applies to signed tokens,
    extended here to an API a site protects with a computed signature
    rather than encrypted output. Nothing about the signature is ever
    inspected, guessed at, or reproduced -- only the response body the
    site's own request already earned.

    `url_pattern`: a substring, or a compiled regex, matched against
    each response's URL -- e.g. `"/api/chapter/"` or a compiled
    regex for a version-numbered content path.

    `captured` is a plain list that fills in live as matching responses
    arrive (read it after `page.wait_for_timeout()`, a click, or a
    `page.evaluate()` -- whatever the caller does inside the `with`).
    Each entry is `{"url", "status", "content_type", "body"}`; `body` is
    `None` when it couldn't be read (aborted, redirected away, or over
    `max_body_bytes`) rather than raising, since one bad response
    shouldn't cost the caller every other one that did work. Empty if
    the page never made a matching request at all -- that is itself a
    real finding (the content loads a different way than expected), not
    an error this function should paper over.
    """
    sync_playwright = _require_playwright()
    captured = []

    def on_response(response):
        if not _url_matches(response.url, url_pattern):
            return
        body = None
        try:
            content_length = response.headers.get("content-length")
            if not content_length or int(content_length) <= max_body_bytes:
                body = response.body()
        except Exception:
            body = None
        captured.append(_capture_entry(response.url, response.status,
                                       response.headers.get("content-type", ""),
                                       body, max_body_bytes))

    with storage.playwright_session(sync_playwright) as p:
        with _guarded_chromium(p) as (browser, proxy):
            page = _guarded_page(browser)
            page.on("response", on_response)
            _goto(page, url, proxy, timeout=timeout * 1000, wait_until=wait_until)
            page.wait_for_timeout(wait_ms)
            yield page, captured


@contextmanager
def _rendered_page(url: str, timeout: int, wait_selector: str | None, wait_ms: int):
    """A rendered, settled page, open for the caller to read from --
    closed automatically on exit. Shared by the rendered-fetch helpers
    so they all wait the same way.

    Scrolls to the bottom once after the initial load: a real, confirmed
    need (a lazy-loading chapter reader) for content some sites only
    populate on a scroll/resize event (jquery.lazyload and similar), not
    on the initial page load -- reproduced directly: the same chapter URL
    rendered with zero real reader images without this scroll, and real
    images consistently after it. Wrapped defensively, since a scroll can
    itself trigger a navigation on some sites (also observed directly: a
    responsive-redirect script reacting to the resulting resize event) --
    that isn't fatal, just settled with another wait."""
    sync_playwright = _require_playwright()
    with storage.playwright_session(sync_playwright) as p:
        with _guarded_chromium(p) as (browser, proxy):
            page = _guarded_page(browser)
            _goto(page, url, proxy, timeout=timeout * 1000, wait_until="networkidle")
            scroll_through_and_settle(page, timeout * 1000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass  # selector guess was wrong; use whatever did render
            else:
                page.wait_for_timeout(wait_ms)
            yield page


def _visible_lines(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
