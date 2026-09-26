"""
tests/test_sources_xbanxia.py -- Step 23e: the xbanxia.cc adapter,
against recorded-shape fixtures only. No request ever reaches the real
site.
"""
import pytest

from sources.adapters import xbanxia
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = xbanxia.BASE_URL

SEARCH_RESULTS_PAGE = ("<html><body><div class='book-list'><ul>"
                       "<li><a href='/2001/' title='Test Novel One'>Test Novel One</a></li>"
                       "<li><a href='/2002/' title='Test Novel Two'>Test Novel Two</a></li>"
                       "</ul></div></body></html>")

SERIES_PAGE = ("<html><body><div class='book-describe'>"
              "<h1>Test Novel Title</h1>"
              "<p>最近更新：2026-01-01</p>"
              "<p>類型：言情, 古风</p>"
              "</div>"
              "<img data-original='//img.xbanxia.cc/covers/2001.jpg'>"
              "<div class='book-list'><ul>"
              "<li><a href='/2001/1.html'>第一章 初遇</a></li>"
              "<li><a href='/2001/2.html'>第二章 相知</a></li>"
              "</ul></div></body></html>")

CHAPTER_PAGE = "<html><body><div id='nr1'><p>正文第一段。</p><p>正文第二段。</p></div></body></html>"

CHAPTER_PAGE_NO_NR1 = ("<html><body>"
                       "<div class='ads'>广告位</div>"
                       "<div class='content-body'>这里是真正的章节正文内容，篇幅比广告长很多。"
                       "这里是真正的章节正文内容，篇幅比广告长很多。</div>"
                       "</body></html>")

EMPTY_SERIES_PAGE = "<html><body><div class='not-a-series-page'></div></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("xbanxia", t, clock, max_retries=kw.pop("max_retries", 0))
    return xbanxia.XbanxiaSource(client=client, **kw), t


class TestSearch:
    def test_posts_to_the_search_endpoint_with_the_expected_fields(self):
        a, t = _adapter({f"{BASE}/modules/article/search_t.php": html(SEARCH_RESULTS_PAGE)})
        results = a.search("测试")
        assert [r.title for r in results] == ["Test Novel One", "Test Novel Two"]
        assert t.calls[0]["method"] == "POST"
        assert t.calls[0]["data"]["searchkey"] == "测试"

    def test_sends_the_static_charset_cookie(self):
        a, t = _adapter({f"{BASE}/modules/article/search_t.php": html(SEARCH_RESULTS_PAGE)})
        a.search("测试")
        assert "jieqiUserCharset=utf-8" in t.calls[0]["headers"].get("Cookie", "")


class TestSeries:
    def test_parses_title_genres_and_cover(self):
        a, t = _adapter({f"{BASE}/2001/": html(SERIES_PAGE)})
        info = a.get_series("2001")
        assert info.title == "Test Novel Title"
        assert info.genres == ["言情", "古风"]
        assert info.cover_url == "//img.xbanxia.cc/covers/2001.jpg"

    def test_layout_changed_when_title_is_missing(self):
        a, t = _adapter({f"{BASE}/2001/": html(EMPTY_SERIES_PAGE)})
        with pytest.raises(SourceError):
            a.get_series("2001")


class TestChapters:
    def test_parses_the_flat_chapter_list(self):
        a, t = _adapter({f"{BASE}/2001/": html(SERIES_PAGE)})
        chapters = a.get_chapters("2001")
        assert [c.title for c in chapters] == ["第一章 初遇", "第二章 相知"]
        assert [c.chapter_id for c in chapters] == ["1", "2"]

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{BASE}/2001/": html(SERIES_PAGE)})
        a.get_series("2001")
        a.get_chapters("2001")
        assert len(t.calls) == 1


class TestChapterText:
    def test_extracts_nr1_content(self):
        a, t = _adapter({f"{BASE}/2001/1.html": html(CHAPTER_PAGE)})
        chapter = ChapterInfo("xbanxia", "2001", "1", "第一章 初遇", f"{BASE}/2001/1.html")
        text = a.get_chapter_text(chapter)
        assert "正文第一段。" in text and "正文第二段。" in text

    def test_falls_back_to_the_largest_text_block_when_nr1_is_missing(self):
        a, t = _adapter({f"{BASE}/2001/1.html": html(CHAPTER_PAGE_NO_NR1)})
        chapter = ChapterInfo("xbanxia", "2001", "1", "第一章 初遇", f"{BASE}/2001/1.html")
        text = a.get_chapter_text(chapter)
        assert "真正的章节正文内容" in text
        assert "广告位" not in text

    def test_layout_changed_when_no_text_at_all(self):
        a, t = _adapter({f"{BASE}/2001/1.html": html("<html><body></body></html>")})
        chapter = ChapterInfo("xbanxia", "2001", "1", "第一章 初遇", f"{BASE}/2001/1.html")
        with pytest.raises(SourceError):
            a.get_chapter_text(chapter)


class TestCapabilitiesRecordsTheDomainCaveat:
    def test_domain_caveat_is_recorded(self):
        a, t = _adapter({})
        caps = a.capabilities()
        assert "xbanxia.cc" in caps.technical["domain_caveat"]
        assert "banxia.cc" in caps.technical["domain_caveat"]


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://xbanxia.cc/2001/")
        assert adapter is not None
        assert adapter.name == "xbanxia"
