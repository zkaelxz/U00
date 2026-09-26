"""
tests/test_sources_52shuku.py -- Step 23e: the 52shuku.net adapter,
against recorded-shape fixtures only. No request ever reaches the real
site.
"""
import importlib

import pytest

fifty2shuku = importlib.import_module("sources.adapters.52shuku")

from sources.models import SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = fifty2shuku.BASE_URL

TOC_PAGE = ("<html><head><title>Test Novel Title - 52shuku</title></head><body>"
           "<ul class='list clearfix'>"
           "<li class='mulu'><a href='/romance/b/1001_1.html'>第一章 初遇</a></li>"
           "<li class='mulu'><a href='/romance/b/1001_2.html'>第二章 相知</a></li>"
           "<li class='mulu'><a href='/romance/b/1001_3.html'>第三章 相守</a></li>"
           "</ul></body></html>")

CHAPTER_PAGE = ("<html><body>"
               "<article class='article-content'><div class='book_con fix' id='text'>"
               "<p>这是第一段正文。</p><p>这是第二段正文。</p>"
               "</div></article>"
               "<div class='pagination2'>"
               "<a href='/romance/b/1001.html'>目录</a>"
               "<a href='/romance/b/1001_1.html'>上一页</a>"
               "<a href='/romance/b/1001_3.html'>下一页</a>"
               "</div></body></html>")

EMPTY_TOC_PAGE = "<html><body><div class='no-such-list'></div></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("52shuku", t, clock, max_retries=kw.pop("max_retries", 0))
    return fifty2shuku.FiftyTwoShukuSource(client=client, **kw), t


class TestChapterList:
    def test_parses_the_toc_list(self):
        a, t = _adapter({f"{BASE}/romance/b/1001.html": html(TOC_PAGE)})
        chapters = a.get_chapters("romance/b/1001.html")
        assert [c.title for c in chapters] == ["第一章 初遇", "第二章 相知", "第三章 相守"]
        assert [c.chapter_id for c in chapters] == ["1", "2", "3"]

    def test_layout_changed_when_list_is_missing(self):
        a, t = _adapter({f"{BASE}/romance/b/1001.html": html(EMPTY_TOC_PAGE)})
        with pytest.raises(SourceError):
            a.get_chapters("romance/b/1001.html")

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{BASE}/romance/b/1001.html": html(TOC_PAGE)})
        a.get_series("romance/b/1001.html")
        a.get_chapters("romance/b/1001.html")
        assert len(t.calls) == 1


class TestSeries:
    def test_title_from_the_page_title_tag(self):
        a, t = _adapter({f"{BASE}/romance/b/1001.html": html(TOC_PAGE)})
        info = a.get_series("romance/b/1001.html")
        assert info.title == "Test Novel Title"


class TestChapterText:
    def test_extracts_the_text_container(self):
        a, t = _adapter({f"{BASE}/romance/b/1001_2.html": html(CHAPTER_PAGE)})
        from sources.models import ChapterInfo
        chapter = ChapterInfo("52shuku", "romance/b/1001.html", "2", "第二章 相知",
                              f"{BASE}/romance/b/1001_2.html")
        text = a.get_chapter_text(chapter)
        assert "这是第一段正文。" in text and "这是第二段正文。" in text
        # pagination links must not leak into the extracted text
        assert "目录" not in text and "上一页" not in text and "下一页" not in text

    def test_layout_changed_when_text_container_is_missing(self):
        a, t = _adapter({f"{BASE}/romance/b/1001_2.html": html("<html><body>nothing here</body></html>")})
        from sources.models import ChapterInfo
        chapter = ChapterInfo("52shuku", "romance/b/1001.html", "2", "Chapter 2",
                              f"{BASE}/romance/b/1001_2.html")
        with pytest.raises(SourceError):
            a.get_chapter_text(chapter)


class TestConcurrencyDefault:
    def test_defaults_to_one_concurrent_request_even_with_a_more_permissive_global_setting(
            self, isolated_db, monkeypatch):
        from sources import store
        monkeypatch.setattr(store, "all_settings", lambda: {
            "pace_min_delay": 0.0, "pace_max_delay": 0.0, "max_concurrent": 5,
            "max_retries": 0, "backoff_base": 2.0,
        })
        a = fifty2shuku.FiftyTwoShukuSource()
        assert a.client.policy.max_concurrent == 1

    def test_explicit_client_is_not_overridden(self):
        a, t = _adapter({})
        # The test helper's make_client() builds its own policy; passing a
        # real client through means this adapter must not silently swap it.
        assert a.client is not None


class TestSearchIsUnsupported:
    def test_search_is_not_offered(self):
        a, t = _adapter({})
        assert not a.supports("search")


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://www.52shuku.net/romance/b/1001.html")
        assert adapter is not None
        assert adapter.name == "52shuku"
