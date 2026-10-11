"""
tests/test_page_fetch.py -- shell detection and embed prediction.

The shell fixtures below reproduce the shape of a real observed
response: a JS-app container, many script tags, and an empty-state
string, with none of the actual listings.
"""
from lib import http
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

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


PUBLIC_IP = "93.184.216.34"


def _dns(monkeypatch, mapping=None):
    import socket

    def fake(host, port, **kw):
        return [(2, 1, 6, "", ((mapping or {}).get(host, PUBLIC_IP), port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def _pinned(monkeypatch, responses, calls=None):
    from services import metadata_service
    it = iter(responses)

    def fake(url, ip, headers, timeout=None):
        assert timeout is not None
        if calls is not None:
            calls.append((url, ip))
        return next(it)
    monkeypatch.setattr(http, "pinned_get", fake)


class _Page:
    def __init__(self, body=b"", status=200, headers=None, encoding="utf-8"):
        self.status_code = status
        self.headers = headers or {}
        self.encoding = encoding
        self.body = body
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        yield self.body

    def close(self):
        self.closed = True


def _redirect(location, status=302):
    return _Page(status=status, headers={"Location": location})


class TestFetchStaticCap:
    def test_an_oversized_page_is_refused(self, monkeypatch):
        class Endless(_Page):
            def iter_content(self, size):
                while True:
                    yield b"<p>x</p>" * 100
        page = Endless()
        monkeypatch.setattr(pf, "STATIC_FETCH_MAX_BYTES", 1000)
        _dns(monkeypatch)
        _pinned(monkeypatch, [page])
        with pytest.raises(ValueError, match="too large"):
            pf.fetch_static("https://example.com/")
        assert page.closed

    def test_a_page_under_the_cap_is_decoded(self, monkeypatch):
        _dns(monkeypatch)
        _pinned(monkeypatch, [_Page("<p>你好</p>".encode(), encoding=None)])
        html, text = pf.fetch_static("https://example.com/")
        assert text == "你好"


class TestFetchStaticRedirectGuard:
    """Every hop -- the first included -- goes through url_guard and is
    fetched pinned to the validated IP; nothing non-public is ever
    connected to."""

    @pytest.mark.parametrize("target, mapping", [
        ("http://127.0.0.1/admin", {}),
        ("http://localhost:8600/api/settings", {"localhost": "127.0.0.1"}),
        ("http://[::1]/", {"::1": "::1"}),
        ("http://169.254.169.254/latest/meta-data/", {"169.254.169.254": "169.254.169.254"}),
        ("http://metadata.google.internal/", {"metadata.google.internal": "169.254.169.254"}),
        ("http://intranet.example/", {"intranet.example": "10.0.0.5"}),
        ("http://router.example/", {"router.example": "192.168.1.1"}),
        ("file:///etc/passwd", {}),
        ("ftp://files.example/x", {}),
        ("http://user:pass@public.example/", {}),
    ])
    def test_redirect_to_a_non_public_target_is_refused(self, monkeypatch, target, mapping):
        from lib import url_guard
        mapping = {"127.0.0.1": "127.0.0.1", **mapping}
        _dns(monkeypatch, mapping)
        calls = []
        first = _redirect(target)
        _pinned(monkeypatch, [first], calls)
        with pytest.raises(url_guard.UnsafeURLError):
            pf.fetch_static("https://public.example/start")
        assert calls == [("https://public.example/start", PUBLIC_IP)]
        assert first.closed

    def test_a_private_first_url_is_refused_without_connecting(self, monkeypatch):
        from lib import url_guard
        _dns(monkeypatch, {"127.0.0.1": "127.0.0.1"})
        calls = []
        _pinned(monkeypatch, [], calls)
        with pytest.raises(url_guard.UnsafeURLError):
            pf.fetch_static("http://127.0.0.1:8600/")
        assert calls == []

    def test_dns_rebinding_on_a_later_hop_is_refused(self, monkeypatch):
        """The redirect target is re-resolved and re-checked, not trusted
        because an earlier hop was public."""
        from lib import url_guard
        _dns(monkeypatch, {"rebind.example": "127.0.0.1"})
        _pinned(monkeypatch, [_redirect("http://rebind.example/")])
        with pytest.raises(url_guard.UnsafeURLError):
            pf.fetch_static("https://public.example/")

    def test_a_safe_redirect_is_followed_and_pinned(self, monkeypatch):
        _dns(monkeypatch, {"cdn.example": "93.184.216.35"})
        calls = []
        first = _redirect("https://cdn.example/page", status=301)
        _pinned(monkeypatch, [first, _redirect("/final", status=307),
                              _Page(b"<p>arrived</p>")], calls)
        _html, text = pf.fetch_static("https://public.example/start")
        assert text == "arrived"
        assert calls == [("https://public.example/start", PUBLIC_IP),
                         ("https://cdn.example/page", "93.184.216.35"),
                         ("https://cdn.example/final", "93.184.216.35")]
        assert first.closed

    def test_too_many_redirects_are_refused(self, monkeypatch):
        from lib import url_guard
        _dns(monkeypatch)
        calls = []
        hops = [_redirect(f"https://public.example/{i}")
                for i in range(pf.STATIC_FETCH_MAX_REDIRECTS + 1)]
        _pinned(monkeypatch, hops, calls)
        with pytest.raises(url_guard.UnsafeURLError):
            pf.fetch_static("https://public.example/start")
        assert len(calls) == pf.STATIC_FETCH_MAX_REDIRECTS + 1
        assert all(h.closed for h in hops)


class TestSmartFetchRedaction:
    def test_failure_text_never_echoes_the_fetched_url_or_its_query(self, monkeypatch):
        def boom(url, timeout=20):
            raise RuntimeError("404 Client Error: Not Found for url: "
                               "https://93.184.216.34/list?sig=abc123secret")
        monkeypatch.setattr(pf, "fetch_static", boom)
        r = pf.smart_fetch("https://public.example/", allow_render=False)
        assert "abc123secret" not in r["message"]
        assert "93.184.216.34" not in r["message"]

    def test_http_status_and_guard_reasons_are_kept(self, monkeypatch):
        class Resp:
            status_code = 403

        class HTTPErr(Exception):
            response = Resp()

        from lib import url_guard

        def refused(url, timeout=20):
            raise url_guard.UnsafeURLError("That address isn't allowed.")
        monkeypatch.setattr(pf, "fetch_static", refused)
        assert "isn't allowed" in pf.smart_fetch("https://x.example/", allow_render=False)["message"]

        def forbidden(url, timeout=20):
            raise HTTPErr("403 for url: https://1.2.3.4/?t=zzz")
        monkeypatch.setattr(pf, "fetch_static", forbidden)
        msg = pf.smart_fetch("https://x.example/", allow_render=False)["message"]
        assert "HTTP 403" in msg and "zzz" not in msg

    def test_exception_text_with_credentials_is_redacted(self, monkeypatch):
        def boom(url, timeout=20):
            raise RuntimeError("connect failed: https://alice:hunter2@proxy.example/x "
                               "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456")
        monkeypatch.setattr(pf, "fetch_static", boom)
        r = pf.smart_fetch("https://public.example/", allow_render=False)
        assert r["method"] == "failed"
        assert "hunter2" not in r["message"]
        assert "alice" not in r["message"]
        assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in r["message"]

    def test_render_failure_text_is_redacted(self, monkeypatch):
        monkeypatch.setattr(pf, "fetch_static", lambda url, timeout=20: (SPA_SHELL_HTML,
                                                                         SPA_SHELL_TEXT))

        def boom(url, timeout=30):
            raise RuntimeError("navigation to https://bob:s3cret@site.example/ failed")
        monkeypatch.setattr(pf, "fetch_rendered", boom)
        r = pf.smart_fetch("https://public.example/", allow_render=True)
        assert r["needs_manual"] is True
        assert "s3cret" not in r["message"]
