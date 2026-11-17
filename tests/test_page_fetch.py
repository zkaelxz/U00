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


class TestImageOnlyPages:
    """A comic chapter page is images, scripts and a "Loading" caption: little text,
    as in a shell, but its page images are the content."""
    COMIC_HTML = ('<html><head>' + ('<script src="r.js"></script>' * 12) + '</head><body>'
                  '<img src="/static/logo.png"><p>Loading...</p>'
                  + ''.join(f'<img data-src="https://img.example/ch1/{i}.jpg">' for i in range(10))
                  + '<!-- ' + 'pad ' * 600 + '--></body></html>')

    def test_an_image_only_comic_page_is_not_a_shell(self):
        assert pf.looks_like_unrendered_shell(self.COMIC_HTML, "Loading...")["is_shell"] is False

    def test_the_source_ladder_does_not_call_it_an_empty_shell_either(self):
        from sources import detect
        assert detect.classify(200, {}, self.COMIC_HTML, "https://comic.example/ch/1", "") == []

    def test_a_rendered_app_page_holding_its_images_is_not_a_shell(self):
        html = self.COMIC_HTML.replace("<body>", '<body><div id="app">').replace(
            "</body>", "</div></body>")
        assert pf.looks_like_unrendered_shell(html, "Loading...")["is_shell"] is False

    def test_a_shell_with_covers_ads_and_pixels_is_still_a_shell(self):
        stray = ('<img src="https://cdn.example/covers/1.jpg"><img src="https://cdn.example/covers/2.jpg">'
                 '<img src="https://cdn.example/covers/3.jpg"><img class="thumb" src="/t/4.jpg">'
                 '<img src="https://ads.example/a.jpg"><img src="https://ads2.example/b.png">'
                 '<img src="/static/blank.gif"><img :src="p.url"><img src="data:image/gif;base64,R0">'
                 '<img src="https://t.example/p.gif" width="1" height="1">'
                 '<noscript><img src="https://px.example/tr?id=1"><img src="https://px.example/tr?id=2">'
                 '<img src="https://px.example/tr?id=3"></noscript>')
        html = SPA_SHELL_HTML.replace('<div id="app"></div>', '<div id="app"></div>' + stray)
        assert pf.looks_like_unrendered_shell(html, SPA_SHELL_TEXT)["is_shell"] is True

    def test_page_images_outside_an_empty_app_root_do_not_hide_it(self):
        pages = ''.join(f'<img src="https://img.example/ch1/{i}.jpg">' for i in range(5))
        html = SPA_SHELL_HTML.replace('<div id="app"></div>', pages + '<div id="app"></div>')
        assert "page uses a JavaScript app container" in pf.looks_like_unrendered_shell(
            html, SPA_SHELL_TEXT)["reasons"]

    def test_a_shell_with_only_a_logo_and_icons_is_still_a_shell(self):
        html = SPA_SHELL_HTML.replace('<div id="app"></div>', '<div id="app"><img src="/logo.png">'
                                      '<img src="/icons/a.svg"><img src="/icons/b.svg"></div>')
        assert pf.looks_like_unrendered_shell(html, SPA_SHELL_TEXT)["is_shell"] is True


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
