"""
tests/test_sources_zgzl.py -- the unlisted m.zgzl.net novel adapter, against small
invented fixtures shaped like the live pages seen 2026-11-26. No request reaches the
real site.
"""
import pytest

from sources import registry
from sources.adapters import zgzl
from sources.models import ChapterInfo, FetchFailed, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = zgzl.BASE_URL

pytestmark = pytest.mark.usefixtures("isolated_db")

BOOK = ('<html><head><meta property="og:novel:book_name" content="試作の本"/>'
        '<meta property="og:novel:author" content="試作者"/>'
        '<meta property="og:description" content="  一行目\n"/></head><body>'
        '<div class="tab1"><p class="p4">分类</p><p class="p5">连载中</p></div>'
        '<div class="info_chapters"><p class="p1">最新章节</p>'
        '<ul class="p2"><li><a href="/read_h/c3.html">第三章</a></li></ul>'
        '<p class="p1">全部章节 ( 試作の本 )</p><ul class="p2">'
        '<li><a href="/read_h/c1.html">第一章</a></li><li><a href="/read_h/c2.html">第二章</a></li>'
        '<li><a href="/read_h/c2.html">第二章</a></li><li><a href="/read_x/c9.html">别的书</a></li>'
        '<li><a href="https://elsewhere.example/read_h/c8.html">外链</a></li>'
        '<li><a href="/read_h/c3.html">第三章</a></li></ul></div></body></html>')

CHAPTER = ('<html><body><h1>第一章</h1><div id="novelcontent" class="novelcontent">'
           '<p><font>《关闭小说畅读模式体验更好》</font></p>'
           '&nbsp;&nbsp;&nbsp;&nbsp;第一段。<br><br>&nbsp;&nbsp;&nbsp;&nbsp;第二段。<br><br>'
           '&nbsp;&nbsp;&nbsp;&nbsp;<scrpt>();</scrpt><br><br>'
           '&nbsp;&nbsp;&nbsp;&nbsp;【本章阅读完毕，更多请搜索某站;http://m.zgzl.net 阅读更多精彩小说】</div></body></html>')


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("zgzl", t, clock, max_retries=0)
    return zgzl.ZgzlSource(client=client), t


class TestUrls:
    @pytest.mark.parametrize("url", [f"{BASE}/info_h/", f"{BASE}/read_h/400.html",
                                     "https://www.zgzl.net/info_h/"])
    def test_book_and_chapter_links_match(self, url):
        assert registry.adapter_class_for_url(url) is zgzl.ZgzlSource

    @pytest.mark.parametrize("url", ["https://zgzl.net.example/info_h/", "https://notzgzl.net/info_h/",
                                     f"{BASE}/gengxin/1.html", f"{BASE}/"])
    def test_other_links_do_not_match(self, url):
        assert not zgzl.ZgzlSource.matches_url(url)

    def test_parse_url(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/info_h/") == ("series", "h")
        kind, ch = a.parse_url("https://www.zgzl.net/read_h/400.html")
        assert kind == "chapter" and (ch.series_id, ch.chapter_id) == ("h", "400")


class TestBook:
    def test_series_and_chapters_share_one_request(self):
        a, t = _adapter({f"{BASE}/info_h/": html(BOOK)})
        s = a.get_series("h")
        assert (s.title, s.authors, s.status, s.description) == ("試作の本", ["試作者"], "ongoing", "一行目")
        chapters = a.get_chapters("h")
        assert [(c.chapter_id, c.title) for c in chapters] == [("c1", "第一章"), ("c2", "第二章"), ("c3", "第三章")]
        assert chapters[0].url == f"{BASE}/read_h/c1.html"
        assert t.urls() == [f"{BASE}/info_h/"]

    def test_missing_list_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/info_h/": html("<html><body>x</body></html>")})
        with pytest.raises(SourceError):
            a.get_chapters("h")

    def test_bad_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.get_series("../x")
        assert t.urls() == []

    def test_removed_book_says_so(self):
        a, _ = _adapter({})
        with pytest.raises(FetchFailed) as e:
            a.get_series("h")
        assert "doesn't exist" in str(e.value)


def test_chapter_text_drops_the_sites_injected_lines():
    url = f"{BASE}/read_h/c1.html"
    a, _ = _adapter({url: html(CHAPTER)})
    assert a.get_chapter_text(ChapterInfo("zgzl", "h", "c1", "第一章", url)) == "第一段。\n第二段。"


class TestUnlisted:
    def test_it_is_registered_but_in_no_list(self, isolated_db):
        assert "zgzl" in registry.adapter_classes()
        from services import sources_registry_service
        assert "zgzl" not in {s["name"] for s in sources_registry_service.list_sources()}

    def test_it_is_not_on_the_status_board(self):
        from scripts import source_status
        assert "zgzl" not in source_status.registered_adapters()
