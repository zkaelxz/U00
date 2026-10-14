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

So this module does three things:
  1. Detects when a fetch returned a shell rather than content.
  2. Optionally renders the page properly with a headless browser.
  3. Falls back to an explicit "paste the text yourself" path that
     always works, instead of pretending.
"""

import re
from contextlib import contextmanager

from page_fetch_browser import _require_playwright
from page_fetch_guard import _UA, _goto, _guarded_chromium, _guarded_page
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

    # A content page has a decent text-to-markup ratio. A shell doesn't.
    if html_len > 2000 and text_len < 600:
        reasons.append("very little text relative to page size")
        score += 0.4

    if any(m in (html or "") for m in SPA_ROOT_MARKERS):
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
    if script_count > 8 and text_len < 1500:
        reasons.append(f"{script_count} scripts but little rendered text")
        score += 0.2

    return {
        "is_shell": score >= 0.5,
        "confidence": min(score, 1.0),
        "reasons": reasons,
    }


STATIC_FETCH_MAX_BYTES = 5_000_000
STATIC_FETCH_MAX_REDIRECTS = 5


class HTTPStatusError(Exception):
    """The site answered with an error status."""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def fetch_static(url: str, timeout: int = 20):
    """Plain fetch. Returns (html, text). Raises on network failure, and
    `url_guard.UnsafeURLError` when the URL or any redirect hop is not a
    public http(s) address."""
    from bs4 import BeautifulSoup
    from lib import http, url_guard

    # The raw guard, not `http.check_public`, so the caller still sees the
    # guard's own exceptions and fixed messages (see `_redact`).
    resp = http.get(url, timeout=timeout, max_bytes=STATIC_FETCH_MAX_BYTES,
                    deadline=max(timeout, 1) * 3, max_redirects=STATIC_FETCH_MAX_REDIRECTS,
                    guard=url_guard.resolve_public, headers={"User-Agent": _UA})
    if resp.status >= 400:
        raise HTTPStatusError(resp.status)
    html = resp.text()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
    return html, text


def fetch_rendered(url: str, timeout: int = 30, wait_selector: str = None,
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


# Resolves every `<img src="blob:...">` on the page into real bytes from
# inside that page's own JS context, before the browser (and with it, the
# blob's only storage) closes. Manhuaku's own real readPic() mechanism
# writes decrypted page images into the DOM exactly this way --
# a blob: URL only exists in that one tab's memory and can never be
# independently re-fetched afterward. Chunked base64 encoding avoids
# blowing the call stack on a large image (a naive
# String.fromCharCode(...spread) over a multi-MB Uint8Array can).
_BLOB_RESOLVE_JS = """
async () => {
    const out = {};
    const imgs = Array.from(document.querySelectorAll('img[src^="blob:"]'));
    for (const img of imgs) {
        try {
            const resp = await fetch(img.src);
            const buf = new Uint8Array(await resp.arrayBuffer());
            let binary = '';
            const chunkSize = 8192;
            for (let i = 0; i < buf.length; i += chunkSize) {
                binary += String.fromCharCode.apply(null, buf.subarray(i, i + chunkSize));
            }
            out[img.src] = btoa(binary);
        } catch (e) {
            // left out; the Python side treats a missing key as
            // "couldn't capture", not an error
        }
    }
    return out;
}
"""


def fetch_rendered_resolving_blobs(url: str, timeout: int = 30, wait_selector: str = None,
                                   wait_ms: int = 2500):
    """Like fetch_rendered(), but additionally resolves any `blob:` object
    URLs found in `<img>` tags into real bytes before the browser closes.

    Returns (html, text, blob_bytes) -- blob_bytes maps each `blob:` URL
    string to the real bytes fetched from inside the page context. A blob
    whose fetch/decode failed is simply left out of the dict, not raised
    as an error here; the caller decides what a missing blob means.
    """
    import base64
    with _rendered_page(url, timeout, wait_selector, wait_ms) as page:
        html = page.content()
        raw = page.evaluate(_BLOB_RESOLVE_JS) or {}
    blob_bytes = {}
    for blob_url, b64 in raw.items():
        try:
            blob_bytes[blob_url] = base64.b64decode(b64)
        except (ValueError, TypeError):
            continue
    return html, _visible_lines(html), blob_bytes


def _url_matches(url: str, pattern) -> bool:
    """`pattern` is a plain substring or a compiled regex; either way,
    answers "does this response belong to the endpoint a caller is
    watching for"."""
    if hasattr(pattern, "search"):
        return bool(pattern.search(url))
    return pattern in url


def _capture_entry(url: str, status: int, content_type: str, body: bytes = None,
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
                        max_body_bytes: int = 5_000_000):
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
    manhuaku.py already applies to descrambling,
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
            _goto(page, url, proxy, timeout=timeout * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(wait_ms)
            yield page, captured


@contextmanager
def _rendered_page(url: str, timeout: int, wait_selector: str, wait_ms: int):
    """A rendered, settled page, open for the caller to read from --
    closed automatically on exit. Shared by fetch_rendered() and
    fetch_rendered_resolving_blobs() so both wait the same way.

    Scrolls to the bottom once after the initial load: a real, confirmed
    need (manhuaku.net's chapter reader) for content some sites only
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


def _redact(exc) -> str:
    """A fixed-text reason for a failed fetch. str(exc) is never echoed: a
    requests error carries the URL it connected to (the pinned IP, or a
    redirect target the site chose) and may hold query-string tokens, which
    redact_secrets does not mask. Only the guard's own fixed messages and
    the install hint for a missing browser are passed through."""
    from engine_backends.shared import redact_secrets
    from lib import url_guard
    if isinstance(exc, (url_guard.UnsafeURLError, url_guard.URLResolveError, ImportError)):
        return redact_secrets(str(exc))
    if isinstance(exc, HTTPStatusError):
        return f"the site answered HTTP {exc.status}"
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return f"the site answered HTTP {status}"
    return "the connection failed"


def smart_fetch(url: str, allow_render: bool = True, timeout: int = 20):
    """
    Fetches a page and tells you honestly what you got.

    Tries a static fetch first (fast, no dependencies). If that looks
    like an unrendered shell and allow_render is on, retries with a
    headless browser. Never silently returns an empty shell as though
    it were real content.

    Returns:
      {"text": str, "method": "static"|"rendered"|"failed",
       "shell_check": {...}, "needs_manual": bool, "message": str}
    """
    result = {"text": "", "method": "failed", "shell_check": {},
              "needs_manual": False, "message": ""}

    try:
        html, text = fetch_static(url, timeout=timeout)
    except Exception as e:
        result["needs_manual"] = True
        result["message"] = f"Couldn't reach that page: {_redact(e)}"
        return result

    check = looks_like_unrendered_shell(html, text)
    result["shell_check"] = check

    if not check["is_shell"]:
        result["text"] = text
        result["method"] = "static"
        result["message"] = "Fetched successfully."
        return result

    # It's a shell. Try rendering it properly.
    if allow_render:
        try:
            _html_r, text_r = fetch_rendered(url, timeout=max(timeout, 30))
            recheck = looks_like_unrendered_shell(_html_r, text_r)
            if not recheck["is_shell"]:
                result["text"] = text_r
                result["method"] = "rendered"
                result["message"] = "This page needed JavaScript; rendered it with a browser."
                result["shell_check"] = recheck
                return result
            result["text"] = text_r
            result["method"] = "rendered"
            result["needs_manual"] = True
            result["message"] = (
                "Rendered the page, but it still looks empty -- the content may be "
                "behind a login, or loaded only after interaction. "
                "Try the manual paste option.")
            result["shell_check"] = recheck
            return result
        except ImportError as e:
            result["needs_manual"] = True
            result["message"] = (
                "This page is built with JavaScript, so a plain fetch only returns an "
                f"empty shell.\n\n{_redact(e)}\n\nOr use the manual paste option below.")
            return result
        except Exception as e:
            result["needs_manual"] = True
            result["message"] = f"Browser rendering failed: {_redact(e)}. Try the manual paste option."
            return result

    result["needs_manual"] = True
    result["message"] = (
        "This page is built with JavaScript -- a plain fetch returns only the page "
        "furniture, not the listings. Enable browser rendering or paste the text manually.")
    return result
