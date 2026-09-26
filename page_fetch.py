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

import os
import re
import threading
from contextlib import contextmanager

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


def fetch_static(url: str, timeout: int = 20):
    """Plain fetch. Returns (html, text). Raises on network failure."""
    import requests
    from bs4 import BeautifulSoup

    headers = {"User-Agent": "Mozilla/5.0 (compatible; BaiheStudio/1.0)"}
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
    return html, text


def fetch_rendered(url: str, timeout: int = 30, wait_selector: str = None,
                    wait_ms: int = 2500):
    """
    Fetches with a real browser engine so JavaScript actually runs.

    Requires: pip install playwright && playwright install chromium
    (the second command downloads the browser itself -- easy to forget,
    so the error message below says so explicitly).

    Returns (html, text). Raises ImportError with install instructions
    if Playwright isn't set up.
    """
    with _rendered_page(url, timeout, wait_selector, wait_ms) as page:
        html = page.content()
    return html, _visible_lines(html)


# Resolves every `<img src="blob:...">` on the page into real bytes from
# inside that page's own JS context, before the browser (and with it, the
# blob's only storage) closes. Manhuaku's own real readPic() mechanism
# (Step 23j) writes decrypted page images into the DOM exactly this way --
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
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (compatible; BaiheStudio/1.0)")
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
            except Exception:
                pass  # a scroll-triggered navigation or a slow settle isn't fatal
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass  # selector guess was wrong; use whatever did render
            else:
                page.wait_for_timeout(wait_ms)
            yield page
        finally:
            browser.close()


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Rendering JavaScript pages needs Playwright:\n"
            "    pip install playwright\n"
            "    playwright install chromium\n"
            "The second command downloads the browser and is easy to miss."
        )
    return sync_playwright


def _visible_lines(html: str) -> str:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())


# ---------------------------------------------------------------------------
# Persistent browser profiles (Step 23k)
# ---------------------------------------------------------------------------
#
# One Chromium profile directory per source, opened with Playwright's
# launch_persistent_context: whatever the person's own sign-in left in
# that profile (cookies, local storage) is there again on the next visit,
# so they aren't asked to log in on every import.
#
# What persists is the profile directory -- the browser process itself is
# started per call and closed after. Playwright's sync objects only work
# on the thread that created them (Streamlit runs each rerun on its own
# thread), a Chromium profile can only be open in one browser at a time,
# and the visible sign-in window and the headless reads need separate
# launches anyway. Login state survives all of that because it lives in
# the profile, which is exactly what a persistent context is for.
#
# The session data never leaves Chromium: nothing here reads cookies or
# storage state, and the only thing handed back is the page as rendered.

_PROFILE_LOCKS = {}
_PROFILE_LOCKS_GUARD = threading.Lock()
PROFILE_BUSY_WAIT = 120  # seconds a read waits for another use of the same profile


class ProfileBusy(RuntimeError):
    """The profile is already open (e.g. its sign-in window is still up)."""


def _profile_lock(profile_dir: str) -> threading.Lock:
    key = os.path.abspath(profile_dir)
    with _PROFILE_LOCKS_GUARD:
        return _PROFILE_LOCKS.setdefault(key, threading.Lock())


def _launch_persistent(profile_dir: str, headless: bool):
    """(playwright, context) for one persistent-profile launch. The
    browser's own user agent is kept -- the same browser the person signed
    in with, not a disguised one."""
    sync_playwright = _require_playwright()
    pw = sync_playwright().start()
    try:
        context = pw.chromium.launch_persistent_context(profile_dir, headless=headless)
    except Exception:
        pw.stop()
        raise
    return pw, context


def _shut(pw, context):
    for fn in (getattr(context, "close", None), getattr(pw, "stop", None)):
        try:
            if fn is not None:
                fn()
        except Exception:
            pass


def fetch_with_profile(url: str, profile_dir: str, timeout: int = 30, wait_selector: str = None,
                       wait_ms: int = 2500, launcher=None):
    """Like fetch_rendered, but inside the persistent profile at
    `profile_dir`, so a site the person already signed in to sees that
    same signed-in browser. Returns (html, text). `launcher(profile_dir,
    headless)` -> (playwright, context) is injectable for tests."""
    lock = _profile_lock(profile_dir)
    if not lock.acquire(timeout=PROFILE_BUSY_WAIT):
        raise ProfileBusy("This site's browser profile is still in use (is its sign-in "
                          "window still open?). Finish there and close it first.")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        pw, context = (launcher or _launch_persistent)(profile_dir, True)
        try:
            page = context.new_page()
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(wait_ms)
            html = page.content()
        finally:
            _shut(pw, context)
    finally:
        lock.release()
    return html, _visible_lines(html)


def open_login_window(url: str, profile_dir: str, launcher=None):
    """Opens a visible browser window on the persistent profile at `url`
    and waits -- with no timeout -- until the person closes it. They sign
    in (and pass any CAPTCHA/MFA the site asks for) themselves, the normal
    way; nothing here types, clicks, solves or reads anything."""
    lock = _profile_lock(profile_dir)
    if not lock.acquire(blocking=False):
        raise ProfileBusy("This site's browser profile is already open -- finish in that "
                          "window (or wait for the import using it) first.")
    try:
        os.makedirs(profile_dir, exist_ok=True)
        pw, context = (launcher or _launch_persistent)(profile_dir, False)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
            except Exception:
                pass  # the window is still open; the person can navigate there themselves
            context.wait_for_event("close", timeout=0)   # 0 = wait for the person, however long
        finally:
            _shut(pw, context)
    finally:
        lock.release()


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
        result["message"] = f"Couldn't reach that page: {e}"
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
                f"empty shell.\n\n{e}\n\nOr use the manual paste option below.")
            return result
        except Exception as e:
            result["needs_manual"] = True
            result["message"] = f"Browser rendering failed: {e}. Try the manual paste option."
            return result

    result["needs_manual"] = True
    result["message"] = (
        "This page is built with JavaScript -- a plain fetch returns only the page "
        "furniture, not the listings. Enable browser rendering or paste the text manually.")
    return result


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

# Sites known to send X-Frame-Options / frame-ancestors headers that stop
# them being embedded. Not exhaustive -- most large sites do this.
KNOWN_FRAME_BLOCKERS = [
    "jjwxc.net", "missevan.com", "bilibili.com", "kuaikanmanhua.com",
    "naver.com", "kakao.com", "lezhin.com", "ridibooks.com",
    "bookwalker.jp", "dlsite.com", "fantia.jp",
]


def can_probably_embed(url: str) -> dict:
    """
    Best-effort guess at whether a URL can be shown in an iframe.

    Most substantial sites block framing for clickjacking protection, so
    an embedded browser panel will usually render blank. This lets the UI
    warn up front rather than showing an empty box and leaving you to
    wonder what broke.
    """
    lowered = (url or "").lower()
    for blocker in KNOWN_FRAME_BLOCKERS:
        if blocker in lowered:
            return {"embeddable": False, "reason": f"{blocker} blocks iframe embedding",
                    "certain": True}
    return {"embeddable": True,
            "reason": "Not on the known-blocked list, but many sites block framing -- "
                      "if the panel below is blank, that's why.",
            "certain": False}
