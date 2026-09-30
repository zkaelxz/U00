"""
tests/test_sources_piaotian.py -- the 飘天文学 (www.piaotia.com) adapter, against
small invented GBK fixtures shaped like the live pages seen 2026-09-30.
No request ever reaches the real site, and no real novel text is used.
"""
import pytest

from sources import registry
from sources.adapters import piaotian
from sources.http import Response
from sources.models import (AutomationPermission, ChallengeDetected, ChapterInfo, ContentHidden,
                            FailureReason, FetchFailed, SourceError)

from .sources_helpers import FakeClock, ScriptedTransport, make_client

BASE = piaotian.BASE_URL
SEARCH = BASE + piaotian.SEARCH_PATH
ALLOWED_HOSTS = {"www.piaotia.com"}

# Failures are recorded in source health, so each test gets a fresh database.
pytestmark = pytest.mark.usefixtures("isolated_db")


def gbk(body: str, status: int = 200, url: str = "", declared: bool = True) -> Response:
    """A page as the site serves it: GBK bytes, the charset only in a <meta>,
    none in the Content-Type header."""
    return Response(status, {"content-type": "text/html"}, body.encode("gbk"), url)


def _book(book_id=146, title="测试之书", author="试作者", category="都市言情", state="已完成",
          synopsis="第一行简介<br />\r\n&nbsp;&nbsp;&nbsp;&nbsp;第二行简介<br /><br />"):
    d = book_id // 1000
    return (
        '<html><head><meta http-equiv="Content-Type" content="text/html; charset=gbk" />'
        f'<title>{title}最新章节-飘天文学</title></head><body>'
        '<div id="left"><div class="sidebar-item"><a href="/bookinfo/2/2452.html">'
        '<img src="/files/article/image/2/2452/2452s.jpg" alt="别的书"></a>'
        '<h2>别的书</h2></div></div>'
        '<div id="centerm"><div id="content"><table><tr><td>'
        f'<table><tr><td colspan="4"><h1>{title}</h1></td></tr>'
        f'<tr><td>类&nbsp;&nbsp;&nbsp; 别：{category}</td><td>作&nbsp;&nbsp;&nbsp; 者：{author}</td>'
        '<td>管 理 员：</td><td>全文长度：3023字</td></tr>'
        f'<tr><td>最后更新：2012-05-03</td><td>文章状态：{state}</td></tr></table>'
        '<table><tr><td>'
        f'<a href="{BASE}/html/{d}/{book_id}/"><img src="/images/dian.gif"></a></td>'
        f'<td><a href="{BASE}/files/article/image/{d}/{book_id}/{book_id}s.jpg">'
        f'<img src="{BASE}/files/article/image/{d}/{book_id}/{book_id}s.jpg" align="right"></a>'
        '<div style="float:left"><span class="hottext">最新章节：</span>'
        f'<a href="{BASE}/html/{d}/{book_id}/300.html">尾章</a><br /><br />'
        f'<span class="hottext">内容简介：</span><br />{synopsis}</div></td></tr></table>'
        '</td></tr></table></div></div></body></html>')


def _li(cid, title):
    return f'<li><a href="{cid}.html">{title}</a></li>'


PAD = "<li>&nbsp;</li>"


def _list(items, book_id=146, footer=True):
    """items: ("part", heading) or (chapter id, title); grouped four to a ul as the site does."""
    out, ul = [], []

    def flush():
        if ul:
            out.append("<ul>" + "".join(ul) + PAD * (-len(ul) % 4) + "</ul>")
            ul.clear()

    for kind, val in items:
        if kind == "part":
            flush()
            out.append(f'<div class="list">{val}</div>')
        else:
            ul.append(_li(kind, val))
            if len(ul) == 4:
                flush()
    flush()
    other = (f'<a href="{BASE}/html/4/4317/">别的书</a><a href="http://evil.example/html/0/146/9.html">x</a>'
             if footer else "")
    return ('<html><head><meta http-equiv="Content-Type" content="text/html; charset=gbk" />'
            '<title>测试之书最新章节</title></head>'
            '<div id="tl"><a href="https://www.piaotia.com/">飘天文学</a></div>'
            '<div class="title"><h1>测试之书最新章节</h1></div>'
            '<div class="mainbody"><div class="list">作者：试作者 <a href="javascript:void(0)">收藏</a></div>'
            f'<div class="centent">{"".join(out)}</div></div>'
            f'<div class="bottom">好书推荐：{other}</div></html>')


NAV = ('<div class="toplink"><a href="1.html">上一章</a> <a href="./">返回目录</a> '
       '<a href="3.html">下一章</a></div>')
ADS = ('<table align=center border="0"><tr><td><table border="0" align="left"><tr><td> '
       '<script language="javascript" src="/scripts/read/style12.js"></script></td></tr></table>'
       '</td></tr></table>\r\n<br>\r\n')
AFTER = ('\r\n</div>\r\n<!-- 翻页上AD开始 --><center><script src="/scripts/read/style2.js"></script></center>'
         '<div class="bottomlink"><a href="1.html">上一章</a></div>'
         '<div align="center">重要声明：内容均由网友上传。</div></body></html>')


def _chapter(paragraphs, tail=""):
    nb = "&nbsp;" * 4
    body = "<br /><br />".join(nb + p for p in paragraphs) + tail
    return ('<html><head><meta HTTP-EQUIV="Content-Type" content="text/html; charset=gb2312" />'
            '<title>测试之书,第一章,PT文学</title>'
            '<script>var next_page = "3.html";</script></head><body>'
            '<div id="guild"><a href="/">飘天文学</a></div>'
            '<H1><a href="' + BASE + '/bookinfo/0/146.html">测试之书</a> 第一章 开始</H1>'
            + NAV + ADS + body + AFTER)


PARAS = ["第一段，有一些字。", "第二段：“对白”。", "第三段结束。"]

ERROR_PAGE = ('<html><head><meta http-equiv="content-type" content="text/html; charset=gbk" />'
              '<title>出现错误！</title></head><body><div class="blocktitle">出现错误！</div>'
              '<div style="padding:10px"><br />错误原因：对不起，该文章不存在！<br /></div></body></html>')

SEARCH_HEAD = '<html><head><title>搜索结果</title></head><body><div id="content"><table class="grid">' \
              '<caption>搜索结果</caption><tr><th>文章名称</th><th>最新章节</th><th>作者</th>' \
              '<th>字数</th><th>更新</th><th>状态</th></tr>'


def _row(bid, title, author="作者甲", state="连载"):
    return (f'<tr><td class="odd"><a href="{BASE}/bookinfo/{bid // 1000}/{bid}.html">{title}</a></td>'
            f'<td class="even"><a href="{BASE}/html/{bid // 1000}/{bid}/index.html"> 第9章 x</a></td>'
            f'<td class="odd">{author}</td><td class="even">10K</td>'
            f'<td class="odd">26-09-30</td><td class="even">{state}</td></tr>')


def _adapter(routes, **kw):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("piaotian", t, clock, max_retries=kw.pop("max_retries", 0))
    return piaotian.PiaotianSource(client=client, **kw), t


def _chap(cid="2", book="146", title="t"):
    return ChapterInfo("piaotian", book, cid, title, f"{BASE}/html/0/{book}/{cid}.html")


class TestUrls:
    @pytest.mark.parametrize("url", [
        "https://www.piaotia.com/bookinfo/0/146.html",
        "http://www.piaotia.com/bookinfo/0/146.html",
        "https://piaotia.com/bookinfo/0/146.html",
        "http://ptwxz.com/bookinfo/0/146.html",
        "http://www.ptwxz.com/bookinfo/0/146.html",
        "https://www.ptwxz.com/bookinfo/16/16054.html?x=1#top",
        "https://www.piaotia.com/html/0/146/",
        "https://www.piaotia.com/html/0/146",
        "https://www.piaotia.com/html/0/146/index.html",
        "https://www.piaotia.com/html/0/146/20709.html",
        "http://www.ptwxz.com/html/4/4159/",
        "https://WWW.PIAOTIA.COM/html/0/146/20709.html",
    ])
    def test_accepts(self, url):
        assert piaotian.PiaotianSource.matches_url(url)

    @pytest.mark.parametrize("url", [
        "https://evilptwxz.com/bookinfo/0/146.html",
        "https://evilpiaotia.com/bookinfo/0/146.html",
        "https://ptwxz.com.evil.com/bookinfo/0/146.html",
        "https://www.piaotia.com.evil.com/html/0/146/",
        "https://www.piaotia.com@evil.com/html/0/146/",
        "https://evil.com/?www.piaotia.com/bookinfo/0/146.html",
        "https://evil.com/www.piaotia.com/bookinfo/0/146.html",
        "https://www.ptwwxz.com/bookinfo/4/4333.html",
        "https://m.piaotia.com/bookinfo/0/146.html",
        "https://x.www.piaotia.com/html/0/146/",
        "https://www.piaotia.com/",
        "https://www.piaotia.com/login.php",
        "https://www.piaotia.com/modules/article/packshow.php?id=146",
        "https://www.piaotia.com/bookinfo/0/146",
        "https://www.xbanxia.cc/books/1.html",
        "ftp://www.piaotia.com/bookinfo/0/146.html",
    ])
    def test_rejects(self, url):
        assert not piaotian.PiaotianSource.matches_url(url)

    def test_registered_and_found_for_a_pasted_url(self):
        assert registry.adapter_classes()["piaotian"] is piaotian.PiaotianSource
        assert registry.adapter_class_for_url("http://ptwxz.com/bookinfo/0/146.html") \
            is piaotian.PiaotianSource

    def test_parse_series_and_chapter_normalise_to_the_real_host(self):
        a, _ = _adapter({})
        for url in ("http://ptwxz.com/bookinfo/16/16054.html", "https://www.piaotia.com/html/16/16054/",
                    "https://piaotia.com/html/16/16054/index.html"):
            assert a.parse_url(url) == ("series", "16054")
        kind, ch = a.parse_url("http://www.ptwxz.com/html/0/146/20709.html")
        assert kind == "chapter"
        assert (ch.series_id, ch.chapter_id, ch.url) == ("146", "20709",
                                                         f"{BASE}/html/0/146/20709.html")
        assert a.parse_url("https://evil.com/html/0/146/") is None
        assert a.parse_url("https://www.piaotia.com/") is None

    def test_a_wrong_directory_for_the_id_is_not_recognised(self):
        a, _ = _adapter({})
        assert a.parse_url("https://www.piaotia.com/bookinfo/5/146.html") is None
        assert a.parse_url("https://www.piaotia.com/html/5/146/1.html") is None

    def test_bad_ids_are_refused_before_any_request(self):
        a, t = _adapter({})
        for bad in ("../etc/passwd", "evil.com/x", "", "0", "12a"):
            with pytest.raises(SourceError):
                a.get_series(bad)
            with pytest.raises(SourceError):
                a.get_chapters(bad)
        with pytest.raises(SourceError):
            a.get_chapter_text(ChapterInfo("piaotian", "146", "../1", "t"))
        assert t.calls == []


class TestSeries:
    def test_metadata_decoded_from_gbk(self):
        a, t = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(_book())})
        s = a.get_series("146")
        assert (s.source, s.series_id, s.title) == ("piaotian", "146", "测试之书")
        assert s.url == f"{BASE}/bookinfo/0/146.html"
        assert s.authors == ["试作者"] and s.genres == ["都市言情"]
        assert s.status == "completed"
        assert s.description == "第一行简介\n第二行简介"
        assert s.cover_url == f"{BASE}/files/article/image/0/146/146s.jpg"
        assert (s.content_type, s.language) == ("novel", "zh")
        assert t.urls() == [f"{BASE}/bookinfo/0/146.html"]

    def test_gbk_fixture_bytes_really_are_gbk_and_decode_through_the_client(self):
        page = _book(title="重生之大涅磐")
        raw = gbk(page).content
        assert "重生之大涅磐".encode("gbk") in raw and "重生之大涅磐".encode("utf-8") not in raw
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(page)})
        assert a.get_series("146").title == "重生之大涅磐"

    def test_ongoing_and_unknown_status(self):
        a, _ = _adapter({f"{BASE}/bookinfo/16/16054.html": gbk(_book(16054, state="连载中")),
                         f"{BASE}/bookinfo/1/1000.html": gbk(_book(1000, state="不明"))})
        assert a.get_series("16054").status == "ongoing"
        assert a.get_series("1000").status == "unknown"

    def test_cover_from_another_host_or_book_is_ignored(self):
        page = _book().replace(f"{BASE}/files/article/image/0/146/146s.jpg",
                               "https://cdn.example/files/article/image/0/146/146s.jpg")
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(page)})
        assert a.get_series("146").cover_url == ""

    def test_missing_title_is_a_layout_error(self):
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk("<html><body><p>x</p></body></html>")})
        with pytest.raises(SourceError) as e:
            a.get_series("146")
        assert e.value.reason == FailureReason.LAYOUT_CHANGED and "title" in str(e.value)

    def test_title_without_details_is_a_layout_error(self):
        page = '<div id="centerm"><h1>书名</h1></div>'
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(page)})
        with pytest.raises(SourceError) as e:
            a.get_series("146")
        assert e.value.reason == FailureReason.LAYOUT_CHANGED

    def test_series_and_chapters_use_separate_pages_and_each_is_fetched_once(self):
        a, t = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(_book()),
                         f"{BASE}/html/0/146/": gbk(_list([("1", "一")]))})
        a.get_series("146")
        a.get_series("146")
        a.get_chapters("146")
        assert t.urls() == [f"{BASE}/bookinfo/0/146.html", f"{BASE}/html/0/146/"]


class TestChapters:
    def test_full_list_in_site_order_with_ids_urls_and_groups(self):
        items = [("part", "作品相关"), ("20693", "群"), ("20694", "感言"),
                 ("part", "第一卷 起"), ("20709", "第一章 甲"), ("20710", "第二章 乙"),
                 ("20711", "第三章 丙"), ("20712", "第四章 丁"), ("20713", "第五章 戊"),
                 ("part", "最终卷"), ("263410", "尾声"), ("20700", "id 回退")]
        a, t = _adapter({f"{BASE}/html/0/146/": gbk(_list(items))})
        ch = a.get_chapters("146")
        assert [c.chapter_id for c in ch] == ["20693", "20694", "20709", "20710", "20711",
                                              "20712", "20713", "263410", "20700"]
        assert [c.group for c in ch] == ["作品相关"] * 2 + ["第一卷 起"] * 5 + ["最终卷"] * 2
        assert ch[2].title == "第一章 甲"
        assert ch[2].url == f"{BASE}/html/0/146/20709.html"
        assert all(c.source == "piaotian" and c.series_id == "146" for c in ch)
        assert piaotian.PiaotianSource.chapters_in_site_order is True
        assert t.urls() == [f"{BASE}/html/0/146/"]

    def test_list_without_headings_has_empty_groups(self):
        a, _ = _adapter({f"{BASE}/html/16/16054/": gbk(_list([("11980989", "第1章")], 16054))})
        (c,) = a.get_chapters("16054")
        assert (c.chapter_id, c.group, c.url) == ("11980989", "",
                                                  f"{BASE}/html/16/16054/11980989.html")

    def test_padding_items_and_footer_links_to_other_books_are_ignored(self):
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk(_list([("1", "一")]))})
        assert [c.chapter_id for c in a.get_chapters("146")] == ["1"]

    def test_links_to_other_books_or_hosts_inside_the_list_are_dropped(self):
        page = _list([("1", "一")]).replace(
            "</ul>", '<li><a href="/html/4/4317/9.html">别书</a></li>'
                     '<li><a href="https://evil.example/html/0/146/8.html">外站</a></li>'
                     '<li><a href="../../4/4317/7.html">别书2</a></li>'
                     '<li><a href="6.html">六</a></li></ul>', 1)
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk(page)})
        assert [c.chapter_id for c in a.get_chapters("146")] == ["1", "6"]

    def test_duplicates_are_dropped_keeping_first_position(self):
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk(_list([("1", "一"), ("2", "二"), ("1", "一again")]))})
        ch = a.get_chapters("146")
        assert [(c.chapter_id, c.title) for c in ch] == [("1", "一"), ("2", "二")]

    def test_hard_cap_is_an_error_not_a_truncation(self, monkeypatch):
        monkeypatch.setattr(piaotian, "MAX_CHAPTERS", 3)
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk(_list([(str(i), "c") for i in range(1, 5)]))})
        with pytest.raises(SourceError, match="more than 3"):
            a.get_chapters("146")
        a2, _ = _adapter({f"{BASE}/html/0/146/": gbk(_list([(str(i), "c") for i in range(1, 4)]))})
        assert len(a2.get_chapters("146")) == 3

    def test_empty_list_is_an_error_not_an_empty_success(self):
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk(_list([("part", "第一卷")]))})
        with pytest.raises(SourceError, match="empty"):
            a.get_chapters("146")

    def test_missing_list_container_is_a_layout_error(self):
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk("<html><body><ul><li><a href='1.html'>x</a></li></ul></body></html>")})
        with pytest.raises(SourceError) as e:
            a.get_chapters("146")
        assert e.value.reason == FailureReason.LAYOUT_CHANGED


class TestChapterText:
    def test_paragraphs_are_lines_and_indent_and_layout_breaks_are_removed(self):
        a, t = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS))})
        assert a.get_chapter_text(_chap()) == "\n".join(PARAS)
        assert t.urls() == [f"{BASE}/html/0/146/2.html"]

    def test_navigation_ads_title_and_footer_are_not_in_the_text(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS))})
        text = a.get_chapter_text(_chap())
        for junk in ("上一章", "下一章", "返回目录", "重要声明", "测试之书", "第一章 开始",
                     "style12", "飘天文学", "var next_page"):
            assert junk not in text

    def test_trailing_www_stub_is_removed_but_not_other_text(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS, "<br /><br />&nbsp;&nbsp;&nbsp;&nbsp;www."))})
        assert a.get_chapter_text(_chap()) == "\n".join(PARAS)
        a2, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS, "<br /><br />www.example 说明"))})
        assert a2.get_chapter_text(_chap()).endswith("www.example 说明")

    def test_single_br_separated_paragraphs_and_full_width_indent(self):
        page = _chapter(["甲"]).replace("&nbsp;&nbsp;&nbsp;&nbsp;甲", "　　甲甲甲<br />　　乙乙乙<br />丙丙丙")
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(page)})
        assert a.get_chapter_text(_chap()) == "甲甲甲\n乙乙乙\n丙丙丙"

    def test_url_is_built_from_ids_when_missing_and_ptwxz_url_is_never_contacted(self):
        a, t = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS))})
        a.get_chapter_text(ChapterInfo("piaotian", "146", "2", "t"))
        a.get_chapter_text(ChapterInfo("piaotian", "146", "2", "t", "http://ptwxz.com/html/0/146/2.html"))
        assert t.urls() == [f"{BASE}/html/0/146/2.html"] * 2

    def test_foreign_or_mismatched_chapter_urls_are_refused(self):
        a, t = _adapter({})
        for url in ("https://evil.example/html/0/146/2.html", "https://www.piaotia.com.evil.com/html/0/146/2.html",
                    f"{BASE}/html/0/147/2.html", f"{BASE}/html/0/146/3.html", f"{BASE}/login.php"):
            with pytest.raises(SourceError):
                a.get_chapter_text(ChapterInfo("piaotian", "146", "2", "t", url))
        assert t.calls == []

    def test_empty_body_is_an_error(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter([]))})
        with pytest.raises(SourceError, match="no text"):
            a.get_chapter_text(_chap())

    def test_too_short_body_is_an_error(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(["字"]))})
        with pytest.raises(SourceError, match="no text"):
            a.get_chapter_text(_chap())

    def test_only_a_watermark_stub_is_an_error(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(["www."]))})
        with pytest.raises(SourceError):
            a.get_chapter_text(_chap())

    def test_missing_navigation_is_a_layout_error(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk("<html><body>&nbsp;&nbsp;文字文字文字文字<br /></body></html>")})
        with pytest.raises(SourceError) as e:
            a.get_chapter_text(_chap())
        assert e.value.reason == FailureReason.LAYOUT_CHANGED


class TestFailures:
    def test_404_says_the_page_is_gone_for_each_kind_of_page(self):
        nginx = Response(404, {"content-type": "text/html"}, b"<html><center>404 Not Found</center></html>", "")
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": nginx, f"{BASE}/html/0/146/": nginx,
                         f"{BASE}/html/0/146/2.html": nginx})
        with pytest.raises(FetchFailed, match="doesn't exist"):
            a.get_series("146")
        with pytest.raises(FetchFailed, match="doesn't exist"):
            a.get_chapters("146")
        with pytest.raises(FetchFailed, match="doesn't exist"):
            a.get_chapter_text(_chap())

    def test_410_is_treated_the_same(self):
        gone = Response(410, {"content-type": "text/html"}, b"gone", "")
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gone})
        with pytest.raises(FetchFailed, match="doesn't exist"):
            a.get_chapter_text(_chap())

    def test_server_error_is_a_fetch_failure(self):
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk("<html>oops</html>", status=503)})
        with pytest.raises(FetchFailed):
            a.get_series("146")

    def test_missing_book_page_served_with_200_reports_the_sites_message(self):
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(ERROR_PAGE)})
        with pytest.raises(FetchFailed, match="该文章不存在") as e:
            a.get_series("146")
        assert e.value.reason == FailureReason.NOT_FOUND

    def test_error_page_served_with_200_for_list_and_chapter(self):
        a, _ = _adapter({f"{BASE}/html/0/146/": gbk(ERROR_PAGE), f"{BASE}/html/0/146/2.html": gbk(ERROR_PAGE)})
        with pytest.raises(FetchFailed, match="该文章不存在"):
            a.get_chapters("146")
        with pytest.raises(FetchFailed, match="该文章不存在"):
            a.get_chapter_text(_chap())

    def test_challenge_page_is_handed_off(self):
        page = "<html><body><div class='g-recaptcha'></div>verify you are human</body></html>"
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(page)})
        with pytest.raises(ChallengeDetected):
            a.get_series("146")

    def test_login_form_page_names_authentication(self):
        page = "<html><body><form><input type='password' name='p'></form></body></html>"
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(page)})
        with pytest.raises(SourceError) as e:
            a.get_chapter_text(_chap())
        assert e.value.reason == FailureReason.AUTHENTICATION_REQUIRED

    def test_redirect_to_the_login_page_names_authentication(self):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk("<html>login</html>", url=f"{BASE}/login.php?jumpurl=x")})
        with pytest.raises(SourceError) as e:
            a.get_chapter_text(_chap())
        assert e.value.reason == FailureReason.AUTHENTICATION_REQUIRED

    def test_age_check_wording_is_unsupported(self):
        a, _ = _adapter({f"{BASE}/bookinfo/0/146.html": gbk("<html><body>未满18岁请离开</body></html>")})
        with pytest.raises(ContentHidden):
            a.get_series("146")

    @pytest.mark.parametrize("final", ["https://elsewhere.example/html/0/146/2.html",
                                       "http://ptwxz.com/html/0/146/2.html",
                                       "https://piaotia.com/html/0/146/2.html"])
    def test_redirect_to_another_host_is_refused(self, final):
        a, _ = _adapter({f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS), url=final)})
        with pytest.raises(FetchFailed, match="different site"):
            a.get_chapter_text(_chap())


class TestSearch:
    def test_post_is_gbk_encoded_and_results_are_parsed(self):
        page = SEARCH_HEAD + _row(16054, "书甲") + _row(146, "书乙", "作者乙", "完结") + "</table></div></body></html>"
        a, t = _adapter({SEARCH: gbk(page)})
        results = a.search("重生")
        call = t.calls[0]
        assert call["method"] == "POST" and call["url"] == SEARCH
        assert call["data"] == "searchtype=articlename&searchkey=" + "%D6%D8%C9%FA"
        assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert [(r.series_id, r.title, r.url) for r in results] == [
            ("16054", "书甲", f"{BASE}/bookinfo/16/16054.html"), ("146", "书乙", f"{BASE}/bookinfo/0/146.html")]
        assert results[0].extra == {"author": "作者甲", "latest_chapter": "第9章 x", "status": "ongoing"}
        assert results[1].extra["status"] == "completed"

    def test_single_exact_match_arrives_as_the_book_page(self):
        a, _ = _adapter({SEARCH: gbk(_book(), url=f"{BASE}/bookinfo/0/146.html")})
        (r,) = a.search("测试之书")
        assert (r.series_id, r.title, r.url) == ("146", "测试之书", f"{BASE}/bookinfo/0/146.html")
        assert r.extra["author"] == "试作者"

    def test_page_two_uses_the_sites_pager_get(self):
        a, t = _adapter({f"{SEARCH}?searchtype=articlename&searchkey=%D6%D8%C9%FA&page=2":
                         gbk(SEARCH_HEAD + _row(146, "书乙") + "</table></div></body></html>")})
        assert a.search("重生", page=2)
        assert t.calls[0]["method"] == "GET"

    def test_blank_query_sends_nothing(self):
        a, t = _adapter({})
        assert a.search("  ") == []
        assert t.calls == []

    def test_no_results_is_an_empty_list(self):
        a, _ = _adapter({SEARCH: gbk(SEARCH_HEAD + "</table></div></body></html>")})
        assert a.search("x") == []

    def test_rows_without_links_are_a_layout_error(self):
        a, _ = _adapter({SEARCH: gbk(SEARCH_HEAD + "<tr><td>a</td><td>b</td></tr></table></div></body></html>")})
        with pytest.raises(SourceError):
            a.search("x")

    def test_page_without_a_table_is_an_error(self):
        a, _ = _adapter({SEARCH: gbk("<html><body>?</body></html>")})
        with pytest.raises(SourceError):
            a.search("x")


class TestHygiene:
    def test_only_the_real_host_is_contacted_and_every_request_has_a_timeout(self):
        routes = {
            f"{BASE}/bookinfo/0/146.html": gbk(_book()),
            f"{BASE}/html/0/146/": gbk(_list([("2", "a")])),
            f"{BASE}/html/0/146/2.html": gbk(_chapter(PARAS)),
            SEARCH: gbk(SEARCH_HEAD + _row(146, "书乙") + "</table></div></body></html>"),
        }
        a, t = _adapter(routes)
        a.get_series("146")
        chapters = a.get_chapters("146")
        a.get_chapter_text(chapters[0])
        a.search("x")
        from urllib.parse import urlsplit
        assert {urlsplit(u).hostname for u in t.urls()} <= ALLOWED_HOSTS  # the transport asserts a timeout per call
        assert all(urlsplit(u).path not in ("/login.php",) and "packshow" not in u
                   and "/admin/" not in u and "attachment" not in u for u in t.urls())

    def test_requests_carry_no_cookies(self):
        a, t = _adapter({f"{BASE}/bookinfo/0/146.html": gbk(_book()), SEARCH: gbk(SEARCH_HEAD + "</table></div>")})
        a.get_series("146")
        a.search("x")
        assert not any(k.lower() == "cookie" for c in t.calls for k in c["headers"])

    def test_pacing_is_polite(self):
        assert piaotian.PiaotianSource.host_min_interval["www.piaotia.com"] >= 2.0

    def test_capabilities_record_the_findings_without_claiming_a_terms_clause(self):
        caps = piaotian.PiaotianSource(client=make_client("piaotian", ScriptedTransport({}))).capabilities()
        assert caps.content_types == ["novel"] and caps.languages == ["zh"]
        assert caps.terms["tos_prohibited"] is False
        assert "Disallow" in caps.terms["robots_txt"] and "No terms" in caps.terms["read"]
        assert caps.automation_permission == AutomationPermission.UNKNOWN.value
