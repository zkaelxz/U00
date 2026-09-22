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


class TestEmbedPrediction:
    def test_known_blockers_flagged_certain(self):
        for site in ("https://www.jjwxc.net/x", "https://www.missevan.com/y",
                     "https://www.lezhin.com/z", "https://bookwalker.jp/a"):
            v = pf.can_probably_embed(site)
            assert v["embeddable"] is False and v["certain"] is True

    def test_unknown_site_uncertain_not_promised(self):
        v = pf.can_probably_embed("https://some-unknown-site.example/x")
        assert v["embeddable"] is True
        assert v["certain"] is False
        assert "block" in v["reason"].lower()

    def test_case_insensitive_matching(self):
        assert pf.can_probably_embed("HTTPS://WWW.JJWXC.NET/X")["embeddable"] is False

    def test_empty_url_does_not_crash(self):
        assert isinstance(pf.can_probably_embed("")["embeddable"], bool)
        assert isinstance(pf.can_probably_embed(None)["embeddable"], bool)

    def test_all_known_blockers_are_strings(self):
        assert all(isinstance(b, str) and b for b in pf.KNOWN_FRAME_BLOCKERS)


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
