"""
tests/test_page_fetch.py -- shell detection and embed prediction.

The shell fixtures below reproduce the shape of a real observed
response: a JS-app container, many script tags, and an empty-state
string, with none of the actual listings.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import page_fetch as pf

SPA_SHELL_HTML = ('<html><head>' + ('<script src="x.js"></script>' * 12) +
                  '</head><body><div id="app"></div></body></html>')
SPA_SHELL_TEXT = '小说 广播剧 漫画 游戏 搜索 共 0 条数据 添加筛选范围 排序方式 FAQ'

REAL_HTML = '<html><body>' + ('<p>Genuine paragraph of page content.</p>' * 80) + '</body></html>'
REAL_TEXT = 'Genuine paragraph of page content. ' * 80


class TestShellDetection:
    def test_detects_js_app_shell(self):
        assert pf.looks_like_unrendered_shell(SPA_SHELL_HTML, SPA_SHELL_TEXT)["is_shell"] is True

    def test_reports_reasons(self):
        reasons = pf.looks_like_unrendered_shell(SPA_SHELL_HTML, SPA_SHELL_TEXT)["reasons"]
        assert len(reasons) >= 2

    def test_detects_empty_state_marker(self):
        reasons = pf.looks_like_unrendered_shell(SPA_SHELL_HTML, SPA_SHELL_TEXT)["reasons"]
        assert any("empty" in r or "loading" in r for r in reasons)

    def test_real_content_not_flagged(self):
        assert pf.looks_like_unrendered_shell(REAL_HTML, REAL_TEXT)["is_shell"] is False

    def test_confidence_between_zero_and_one(self):
        for html, text in [(SPA_SHELL_HTML, SPA_SHELL_TEXT), (REAL_HTML, REAL_TEXT)]:
            c = pf.looks_like_unrendered_shell(html, text)["confidence"]
            assert 0.0 <= c <= 1.0

    def test_handles_none_inputs(self):
        assert isinstance(pf.looks_like_unrendered_shell(None, None)["is_shell"], bool)

    def test_handles_empty_strings(self):
        assert isinstance(pf.looks_like_unrendered_shell("", "")["is_shell"], bool)

    def test_english_empty_state_marker(self):
        html = '<html><head>' + ('<script></script>' * 12) + '</head><body><div id="root"></div></body></html>'
        assert pf.looks_like_unrendered_shell(html, "No results found")["is_shell"] is True


class TestSmartFetchContract:
    def test_unreachable_url_returns_failed_status_not_exception(self):
        r = pf.smart_fetch("http://127.0.0.1:9/definitely-nothing-here",
                            allow_render=False, timeout=2)
        assert r["method"] == "failed"
        assert r["needs_manual"] is True
        assert r["message"]

    def test_result_always_has_expected_keys(self):
        r = pf.smart_fetch("http://127.0.0.1:9/nope", allow_render=False, timeout=2)
        for key in ("text", "method", "shell_check", "needs_manual", "message"):
            assert key in r


class TestApiCaptureEntryLogic:
    """Pure logic behind `api_capture_session` -- the part that decides
    what to keep from a matched network response -- tested without a
    real browser. The session itself (opening Playwright, wiring
    `page.on("response", ...)`) is a thin, untestable-without-a-browser
    shell around this."""

    def test_substring_pattern_matches(self):
        assert pf._url_matches("https://site.invalid/api/v2/chapter/7", "/api/v2/chapter/")
        assert not pf._url_matches("https://site.invalid/static/logo.png", "/api/v2/chapter/")

    def test_regex_pattern_matches(self):
        import re
        pattern = re.compile(r"/v\d+/content")
        assert pf._url_matches("https://site.invalid/v3/content?id=1", pattern)
        assert not pf._url_matches("https://site.invalid/v3/other", pattern)

    def test_a_normal_response_keeps_its_body(self):
        entry = pf._capture_entry("https://site.invalid/api/x", 200, "application/json",
                                  b'{"ok": true}')
        assert entry == {"url": "https://site.invalid/api/x", "status": 200,
                         "content_type": "application/json", "body": b'{"ok": true}'}

    def test_a_missing_body_is_recorded_as_none_not_dropped(self):
        """A response the caller was watching for still shows up -- e.g.
        to notice it happened and failed -- even when its body couldn't
        be read (aborted, redirected away)."""
        entry = pf._capture_entry("https://site.invalid/api/x", 200, "application/json", None)
        assert entry["body"] is None

    def test_an_oversized_body_is_dropped_not_kept_whole(self):
        """One huge, merely URL-matching download (a bundled asset that
        happens to share the API path prefix) must not be kept in full --
        the caller is watching for small signed API responses, not for
        whatever else shares that URL shape."""
        big = b"x" * 100
        entry = pf._capture_entry("https://site.invalid/api/x", 200, "application/octet-stream",
                                  big, max_body_bytes=50)
        assert entry["body"] is None
        assert entry["status"] == 200   # the rest of the entry still records what happened

    def test_a_body_exactly_at_the_cap_is_kept(self):
        body = b"x" * 50
        entry = pf._capture_entry("https://site.invalid/api/x", 200, "text/plain",
                                  body, max_body_bytes=50)
        assert entry["body"] == body


class TestFetchStaticCap:
    def _get(self, monkeypatch, resp):
        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: resp)

    def test_an_oversized_page_is_refused(self, monkeypatch):
        class Endless:
            headers = {}
            encoding = "utf-8"
            closed = False

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                while True:
                    yield b"<p>x</p>" * 100

            def close(self):
                Endless.closed = True
        monkeypatch.setattr(pf, "STATIC_FETCH_MAX_BYTES", 1000)
        self._get(monkeypatch, Endless())
        import pytest
        with pytest.raises(ValueError, match="too large"):
            pf.fetch_static("https://example.com/")
        assert Endless.closed

    def test_a_page_under_the_cap_is_decoded(self, monkeypatch):
        class Small:
            headers = {}
            encoding = None

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                yield "<p>你好</p>".encode()

            def close(self):
                pass
        self._get(monkeypatch, Small())
        html, text = pf.fetch_static("https://example.com/")
        assert text == "你好"
