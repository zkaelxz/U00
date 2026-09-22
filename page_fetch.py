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
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ImportError(
            "Rendering JavaScript pages needs Playwright:\n"
            "    pip install playwright\n"
            "    playwright install chromium\n"
            "The second command downloads the browser and is easy to miss."
        )

    from bs4 import BeautifulSoup

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent="Mozilla/5.0 (compatible; BaiheStudio/1.0)")
            page.goto(url, timeout=timeout * 1000, wait_until="networkidle")
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=timeout * 1000)
                except Exception:
                    pass  # selector guess was wrong; use whatever did render
            else:
                page.wait_for_timeout(wait_ms)
            html = page.content()
        finally:
            browser.close()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = "\n".join(l.strip() for l in soup.get_text("\n").splitlines() if l.strip())
    return html, text


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
