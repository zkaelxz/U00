"""The comic 'no pages' 422 names the real cause: a browser tier that could
not start comes before the Bilibili sign-in hint."""
import page_fetch_browser
import pytest
from services import sources_import_service as imp
from sources import adaptive, ladder
from sources.models import AccessTier

BILI = "https://manga.bilibili.com/mc40746/2120616"
OTHER = "https://example.com/chapter/1"
SHELL = '<html><body><div id="app-vm"></div></body></html>'
PAGE = "<html><body>" + "<p>reader text</p>" * 80 + "</body></html>"


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    # The hosts stand for public sites; an unresolvable name drops the
    # browser tier these tests exercise.
    monkeypatch.setattr("lib.url_guard.resolve_public", lambda url: "93.184.216.34")


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
    assert "Capture whole chapter" in msg


def test_browser_not_found_says_chrome_or_edge():
    err = _error(BILI, raises=page_fetch_browser.BrowserNotFound("no browser"))
    assert "no Chrome or Edge" in err["message"] and "Sign in" not in err["message"]
    assert "Capture whole chapter" in err["message"]


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
    assert "browser extension's 'Capture whole chapter'" in msg


def test_non_bilibili_text_unchanged():
    err = _error(OTHER, raises=ImportError("No module named 'playwright'"))
    assert err["message"].startswith("No comic pages were found on that page.")
    assert "Playwright" not in err["message"] and "Bilibili" not in err["message"]


LOCKED = "Kuaikan keeps this chapter locked (needs purchase or VIP access)."


class _Adapter:
    name = "kuaikan"

    def __init__(self, pages=None, error=None):
        self._pages, self._error = pages or [], error

    def supports(self, method):
        return True

    def get_pages(self, chapter):
        if self._error:
            raise self._error
        return self._pages

    def download_page(self, ref):
        return b"bytes", ".jpg"


def _found(adapter):
    from sources.models import ChapterInfo
    return adapter, ChapterInfo("kuaikan", "", "1003", "Chapter", "https://www.kuaikanmanhua.com/web/comic/1003")


def test_a_locked_chapter_says_it_is_locked_not_that_the_page_failed(monkeypatch):
    from services.sources_url_service import JobFailed
    from sources.models import ContentHidden, FailureReason
    failed = {}
    monkeypatch.setattr(imp.background_jobs, "set_result", lambda jid, r: failed.update(r))
    adapter = _Adapter(error=ContentHidden(LOCKED, FailureReason.PURCHASE_REQUIRED))
    with pytest.raises(JobFailed):
        imp._import_with_adapter("j", _found(adapter), "https://m.kuaikanmanhua.com/mobile/comics/1003", 1)
    assert failed["error"]["message"] == LOCKED
    assert failed["error"]["details"]["reason"] == "PURCHASE_REQUIRED"


def test_an_adapter_that_cannot_read_the_chapter_leaves_the_generic_message():
    assert imp._import_with_adapter("j", None, "u", 1) is False
    assert imp._import_with_adapter("j", _found(_Adapter(error=RuntimeError("boom"))), "u", 1) is False
    assert imp._import_with_adapter("j", _found(_Adapter(pages=[])), "u", 1) is False


def test_pages_the_adapter_serves_are_written_as_a_chapter(monkeypatch):
    from sources.models import PageRef
    written, result = {}, {}
    monkeypatch.setattr(imp.extraction, "drop_review", lambda d: None)
    monkeypatch.setattr(imp.drama_service, "set_source_url_once", lambda d, u: None)
    monkeypatch.setattr(imp.background_jobs, "update_progress", lambda *a: None)
    monkeypatch.setattr(imp.background_jobs, "set_result", lambda jid, r: result.update(r))

    def write(drama_id, items, job_id=None, chapter=None):
        written["bytes"] = [content() for _, content in items]
        return len(written["bytes"]), []
    monkeypatch.setattr(imp.extraction, "write_pages", write)
    adapter = _Adapter(pages=[PageRef("kuaikan", "1003", 0, "https://i/1.jpg"),
                              PageRef("kuaikan", "1003", 1, "https://i/2.jpg")])
    assert imp._import_with_adapter("j", _found(adapter), "u", 1) is True
    assert written["bytes"] == [b"bytes", b"bytes"] and result["pages_added"] == 2


def test_a_mobile_link_is_read_from_its_desktop_page():
    found = imp._adapter_chapter("https://m.kuaikanmanhua.com/mobile/comics/1001")
    assert found[1].url == "https://www.kuaikanmanhua.com/web/comic/1001"
    assert imp._adapter_chapter("https://example.com/chapter/1") is None
