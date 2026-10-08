"""The comic 'no pages' 422 names the real cause: a browser tier that could
not start comes before the Bilibili sign-in hint."""
import page_fetch
from services import sources_import_service as imp
from sources import adaptive, ladder
from sources.models import AccessTier

BILI = "https://manga.bilibili.com/mc40746/2120616"
OTHER = "https://example.com/chapter/1"
SHELL = '<html><body><div id="app-vm"></div></body></html>'
PAGE = "<html><body>" + "<p>reader text</p>" * 80 + "</body></html>"


def _error(url, rendered_ok=None, raises=None, tiers_html=PAGE):
    def fetch(u):
        if raises:
            raise raises
        return tiers_html, ""
    tier = ladder.rendered_tier(fetch_rendered=fetch)
    lr = ladder.run_ladder(url, {AccessTier.RENDERED_BROWSER: tier}, log=False)
    report = adaptive.ExtractionReport(url, "comic", reason="The page has no image tags")
    adaptive._note_access(report, lr)
    exc = Exception("x")
    exc.report = report
    return imp._no_pages_error(exc, url)


def test_playwright_missing_leads_with_install_steps():
    err = _error(BILI, raises=ImportError("No module named 'playwright'"))
    msg = err["message"]
    assert "Playwright package is not installed" in msg and "Diagnostics > Packages" in msg
    assert "Sign in" not in msg
    assert msg.index("Playwright") < msg.index("Why:")
    assert err["status"] == 422 and err["details"]["reason"] == "NO_CONTENT"
    assert err["details"]["diagnostic"]


def test_browser_not_found_says_chrome_or_edge():
    err = _error(BILI, raises=page_fetch.BrowserNotFound("no browser"))
    assert "no Chrome or Edge" in err["message"] and "Sign in" not in err["message"]


def test_rendered_tier_ran_keeps_sign_in_hint():
    err = _error(BILI)
    assert "Sign in to Bilibili Manga" in err["message"]
    assert "Playwright" not in err["message"]


def test_no_browser_tried_does_not_blame_sign_in():
    lr = ladder.run_ladder(BILI, {}, log=False)
    report = adaptive.ExtractionReport(BILI, "comic")
    adaptive._note_access(report, lr)
    exc = Exception("x")
    exc.report = report
    msg = imp._no_pages_error(exc, BILI)["message"]
    assert "No browser was used" in msg and "Sign in" not in msg


def test_non_bilibili_text_unchanged():
    err = _error(OTHER, raises=ImportError("No module named 'playwright'"))
    assert err["message"].startswith("No comic pages were found on that page.")
    assert "Playwright" not in err["message"] and "Bilibili" not in err["message"]
