"""
tests/test_sources_syosetu.py -- the 小説家になろう (ncode.syosetu.com) adapter,
against small invented fixtures shaped like the live pages seen 2026-09-30.
No request ever reaches the real site.
"""
import pytest

from sources import registry, site_terms
from sources.adapters import syosetu
from sources.http import Response
from sources.models import (AutomationPermission, ChallengeDetected, ChapterInfo, ContentHidden,
                            FailureReason, FetchFailed, SourceError)

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = syosetu.BASE_URL
SEARCH = syosetu.SEARCH_URL
ALLOWED_HOSTS = {"ncode.syosetu.com", "yomou.syosetu.com"}

# Failures are recorded in source health, so each test gets a fresh database.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _pager(code, last=None):
    if last:
        return (f'<div class="c-pager"><div class="c-pager__pager">'
                f'<a href="/{code}/?p=2" class="c-pager__item c-pager__item--next">次へ</a>'
                f'<a href="/{code}/?p={last}" class="c-pager__item c-pager__item--last">最後へ</a>'
                f'</div></div>')
    return ('<div class="c-pager"><div class="c-pager__pager">'
            '<span class="c-pager__item c-pager__item--next">次へ</span>'
            '<span class="c-pager__item c-pager__item--last">最後へ</span></div></div>')


def _episode(code, n, title):
    return (f'<div class="p-eplist__sublist"><a href="/{code}/{n}/" class="p-eplist__subtitle">\n'
            f'{title}\n</a><div class="p-eplist__update">2026/01/01 10:00</div></div>')


def _top(code, items, last=None, title="テスト作品", author="試作者", summary="あらすじ一行目<br />二行目"):
    """items: ("part", heading) or (number, title)."""
    rows = []
    for kind, val in items:
        rows.append(f'<div class="p-eplist__chapter-title">{val}</div>' if kind == "part"
                    else _episode(code, kind, val))
    return ('<html><body><article class="p-novel">'
            f'<h1 class="p-novel__title">{title}</h1>'
            f'<div class="p-novel__author">作者：<a href="https://mypage.syosetu.com/1/">{author}</a></div>'
            f'<div id="novel_ex" class="p-novel__summary">{summary}</div>'
            f'{_pager(code, last)}<div class="p-eplist">{"".join(rows)}</div>'
            '</article></body></html>')


ONE_SHOT = ('<html><body><article class="p-novel">'
            '<h1 class="p-novel__title">短い話</h1>'
            '<div class="p-novel__author">作者：<a href="https://mypage.syosetu.com/2/">短編作者</a></div>'
            '<div class="p-novel__date-published">掲載日：2026/09/22</div>'
            '<div class="p-novel__body"><div class="js-novel-text p-novel__text">'
            '<p id="L1">　あら、こんにちは。</p><p id="L2"><br /></p><p id="L3">　さようなら。</p>'
            '</div></div></article></body></html>')

CHAPTER = ('<html><body><article class="p-novel">'
           '<div class="p-novel__number js-siori">3/5</div>'
           '<h1 class="p-novel__title p-novel__title--rensai">第3話　テスト</h1>'
           '<div class="p-novel__body">'
           '<div class="js-novel-text p-novel__text p-novel__text--preface">'
           '<p id="Lp1">前書きです。</p></div>'
           '<div class="js-novel-text p-novel__text">'
           '<p id="L1">　最初の段落。</p>'
           '<p id="L2"><br /></p>'
           '<p id="L3">　<ruby>妹<rp>(</rp><rt>わたし</rt><rp>)</rp></ruby>の話。<br />改行あり。</p>'
           '<p id="L4">「台詞」</p>'
           '<p id="L5"><br /></p>'
           '</div>'
           '<div class="js-novel-text p-novel__text p-novel__text--afterword">'
           '<p id="La1">後書きです。</p></div>'
           '</div></article></body></html>')

ERROR_PAGE = ('<html><body><main><div class="p-novelcom-text p-novelcom-text--error">'
              '指定されたURLが存在しません</div></main></body></html>')

SEARCH_PAGE = ('<html><body>'
               '<div class="searchkekka_box"><div class="novel_h">'
               '<a class="tl" target="_blank" href="https://ncode.syosetu.com/n1393mv/">検索テストA</a></div>'
               '作者：<a href="https://mypage.syosetu.com/3/">作者A</a>／Nコード：N1393MV'
               '<table><tr><td class="left"><br /> 連載中 <br />(全10エピソード) </td>'
               '<td><div class="ex">概要A</div></td></tr></table></div>'
               '<div class="searchkekka_box"><div class="novel_h">'
               '<a class="tl" href="https://ncode.syosetu.com/n9940mq/">検索テストB</a></div>'
               '作者：<a href="https://mypage.syosetu.com/4/">作者B</a>'
               '<table><tr><td class="left"> 完結済 <br />(全3エピソード) </td>'
               '<td><div class="ex">概要B</div></td></tr></table></div>'
               '</body></html>')


def _adapter(routes, **kw):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("syosetu", t, clock, max_retries=kw.pop("max_retries", 0))
    return syosetu.SyosetuSource(client=client, **kw), t


def _hosts(t):
    from urllib.parse import urlsplit
    return {urlsplit(u).hostname for u in t.urls()}


class TestUrls:
    @pytest.mark.parametrize("url", [
        "https://ncode.syosetu.com/n0063lr/",
        "https://ncode.syosetu.com/n0063lr",
        "http://ncode.syosetu.com/n0063lr/",
        "https://ncode.syosetu.com/n0063lr/?p=2",
        "https://ncode.syosetu.com/n0063lr/12/",
        "https://ncode.syosetu.com/n0063lr/12",
        "https://ncode.syosetu.com/N0063LR/",
    ])
    def test_accepts(self, url):
        assert syosetu.SyosetuSource.matches_url(url)

    @pytest.mark.parametrize("url", [
        "https://novel18.syosetu.com/n0063lr/",
        "https://syosetu.org/novel/12345/",
        "https://evilsyosetu.com/n0063lr/",
        "https://ncode.syosetu.com.evil.com/n0063lr/",
        "https://ncode.syosetu.com@evil.com/n0063lr/",
        "https://evil.com/?ncode.syosetu.com/n0063lr/",
        "https://x.ncode.syosetu.com/n0063lr/",
        "https://ncode.syosetu.com/",
        "https://ncode.syosetu.com/txtdownload/top/ncode/1/",
        "ftp://ncode.syosetu.com/n0063lr/",
    ])
    def test_rejects(self, url):
        assert not syosetu.SyosetuSource.matches_url(url)

    def test_registered_and_found_for_a_pasted_url(self):
        assert registry.adapter_classes()["syosetu"] is syosetu.SyosetuSource
        assert registry.adapter_class_for_url("https://ncode.syosetu.com/n0063lr/") \
            is syosetu.SyosetuSource

    def test_parse_series_and_chapter(self):
        a, _ = _adapter({})
        assert a.parse_url("https://ncode.syosetu.com/N0063LR/?p=2") == ("series", "n0063lr")
        kind, ch = a.parse_url("http://ncode.syosetu.com/n0063lr/7")
        assert kind == "chapter"
        assert (ch.series_id, ch.chapter_id, ch.url) == ("n0063lr", "7", f"{BASE}/n0063lr/7/")
        assert a.parse_url("https://novel18.syosetu.com/n0063lr/") is None
        assert a.parse_url("https://ncode.syosetu.com/") is None

    def test_bad_series_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.get_series("../etc/passwd")
        with pytest.raises(SourceError):
            a.get_chapters("evil.com/x")
        assert t.calls == []


class TestSeries:
    def test_metadata(self):
        a, t = _adapter({f"{BASE}/n0063lr/": html(_top("n0063lr", [(1, "第1話")]))})
        info = a.get_series("N0063LR")
        assert info.series_id == "n0063lr"
        assert info.title == "テスト作品"
        assert info.authors == ["試作者"]
        assert info.description == "あらすじ一行目\n二行目"
        assert info.status == "unknown"
        assert info.content_type == "novel" and info.language == "ja"
        assert info.url == f"{BASE}/n0063lr/"

    def test_one_shot_is_marked_completed(self):
        a, _ = _adapter({f"{BASE}/n1111aa/": html(ONE_SHOT)})
        info = a.get_series("n1111aa")
        assert info.title == "短い話" and info.authors == ["短編作者"]
        assert info.status == "completed" and info.description == ""

    def test_missing_title_is_a_layout_error_naming_it(self):
        a, _ = _adapter({f"{BASE}/n0063lr/": html("<html><body><p>hi</p></body></html>")})
        with pytest.raises(SourceError, match="work title"):
            a.get_series("n0063lr")

    def test_series_and_chapters_share_the_first_fetch(self):
        a, t = _adapter({f"{BASE}/n0063lr/": html(_top("n0063lr", [(1, "a")]))})
        a.get_series("n0063lr")
        a.get_chapters("n0063lr")
        assert len(t.calls) == 1


class TestChapters:
    def test_single_page_list_with_part_headings(self):
        page = _top("n0063lr", [("part", "第一章"), (1, "第1話"), (2, "第2話"),
                                ("part", "第二章"), (3, "第3話")])
        a, t = _adapter({f"{BASE}/n0063lr/": html(page)})
        chapters = a.get_chapters("n0063lr")
        assert [(c.chapter_id, c.title, c.group) for c in chapters] == [
            ("1", "第1話", "第一章"), ("2", "第2話", "第一章"), ("3", "第3話", "第二章")]
        assert chapters[0].url == f"{BASE}/n0063lr/1/"
        assert a.chapters_in_site_order is True
        assert len(t.calls) == 1

    def test_walks_every_page_and_carries_the_part_heading_across_pages(self):
        routes = {
            f"{BASE}/n0063lr/": html(_top("n0063lr", [("part", "第一章"), (1, "a"), (2, "b")], last=3)),
            f"{BASE}/n0063lr/?p=2": html(_top("n0063lr", [(3, "c"), ("part", "第二章"), (4, "d")], last=None)),
            f"{BASE}/n0063lr/?p=3": html(_top("n0063lr", [(5, "e")])),
        }
        a, t = _adapter(routes)
        chapters = a.get_chapters("n0063lr")
        assert [c.chapter_id for c in chapters] == ["1", "2", "3", "4", "5"]
        assert [c.group for c in chapters] == ["第一章", "第一章", "第一章", "第二章", "第二章"]
        assert t.urls() == [f"{BASE}/n0063lr/", f"{BASE}/n0063lr/?p=2", f"{BASE}/n0063lr/?p=3"]

    def test_duplicates_across_pages_are_dropped_keeping_first_order(self):
        routes = {
            f"{BASE}/n0063lr/": html(_top("n0063lr", [(1, "a"), (2, "b")], last=2)),
            f"{BASE}/n0063lr/?p=2": html(_top("n0063lr", [(2, "b again"), (3, "c")])),
        }
        a, _ = _adapter(routes)
        chapters = a.get_chapters("n0063lr")
        assert [(c.chapter_id, c.title) for c in chapters] == [("1", "a"), ("2", "b"), ("3", "c")]

    def test_page_walk_is_capped(self, monkeypatch):
        monkeypatch.setattr(syosetu, "MAX_LIST_PAGES", 3)
        routes = {f"{BASE}/n0063lr/": html(_top("n0063lr", [(1, "a")], last=9999))}
        for p in (2, 3, 4):
            routes[f"{BASE}/n0063lr/?p={p}"] = html(_top("n0063lr", [(p, str(p))]))
        a, t = _adapter(routes)
        chapters = a.get_chapters("n0063lr")
        assert len(t.calls) == 3
        assert [c.chapter_id for c in chapters] == ["1", "2", "3"]

    def test_links_to_other_works_are_ignored(self):
        page = _top("n0063lr", [(1, "a")]).replace(
            "</div></article>", '<div class="p-eplist__sublist"><a href="/n9999zz/1/" '
            'class="p-eplist__subtitle">other</a></div></div></article>')
        a, _ = _adapter({f"{BASE}/n0063lr/": html(page)})
        assert [c.chapter_id for c in a.get_chapters("n0063lr")] == ["1"]

    def test_one_shot_becomes_one_chapter_pointing_at_the_top_page(self):
        a, t = _adapter({f"{BASE}/n1111aa/": html(ONE_SHOT)})
        chapters = a.get_chapters("n1111aa")
        assert len(chapters) == 1
        assert chapters[0].title == "短い話" and chapters[0].url == f"{BASE}/n1111aa/"
        assert len(t.calls) == 1

    def test_empty_list_is_an_error_not_an_empty_success(self):
        a, _ = _adapter({f"{BASE}/n0063lr/": html(_top("n0063lr", []))})
        with pytest.raises(SourceError, match="episodes"):
            a.get_chapters("n0063lr")

    def test_no_list_and_no_body_is_a_layout_error(self):
        page = '<html><body><article class="p-novel"><h1 class="p-novel__title">x</h1></article></body></html>'
        a, _ = _adapter({f"{BASE}/n0063lr/": html(page)})
        with pytest.raises(SourceError, match="episode list"):
            a.get_chapters("n0063lr")


class TestChapterText:
    def _chapter(self, a):
        return a.parse_url(f"{BASE}/n0063lr/3/")[1]

    def test_paragraphs_blank_lines_and_ruby(self):
        a, _ = _adapter({f"{BASE}/n0063lr/3/": html(CHAPTER)})
        text = a.get_chapter_text(self._chapter(a))
        assert text == "　最初の段落。\n\n　妹の話。\n改行あり。\n「台詞」"

    def test_preface_and_afterword_are_left_out(self):
        a, _ = _adapter({f"{BASE}/n0063lr/3/": html(CHAPTER)})
        text = a.get_chapter_text(self._chapter(a))
        assert "前書き" not in text and "後書き" not in text

    def test_one_shot_text_from_the_top_page(self):
        a, t = _adapter({f"{BASE}/n1111aa/": html(ONE_SHOT)})
        ch = a.get_chapters("n1111aa")[0]
        assert a.get_chapter_text(ch) == "　あら、こんにちは。\n\n　さようなら。"

    def test_url_is_built_from_ids_when_missing(self):
        a, t = _adapter({f"{BASE}/n0063lr/3/": html(CHAPTER)})
        a.get_chapter_text(ChapterInfo("syosetu", "N0063LR", "3", "t"))
        assert t.urls() == [f"{BASE}/n0063lr/3/"]

    def test_foreign_chapter_url_is_refused(self):
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.get_chapter_text(ChapterInfo("syosetu", "n0063lr", "3", "t", "https://evil.example/n0063lr/3/"))
        assert t.calls == []

    def test_empty_body_is_an_error(self):
        page = ('<html><body><div class="p-novel__body"><div class="p-novel__text">'
                '<p id="L1"><br /></p><p id="L2">　</p></div></div></body></html>')
        a, _ = _adapter({f"{BASE}/n0063lr/3/": html(page)})
        with pytest.raises(SourceError, match="no text"):
            a.get_chapter_text(self._chapter(a))

    def test_only_author_notes_is_an_error(self):
        page = ('<html><body><div class="p-novel__body">'
                '<div class="p-novel__text p-novel__text--preface"><p>前書き</p></div>'
                '</div></body></html>')
        a, _ = _adapter({f"{BASE}/n0063lr/3/": html(page)})
        with pytest.raises(SourceError, match="main text"):
            a.get_chapter_text(self._chapter(a))

    def test_missing_body_is_a_layout_error_naming_it(self):
        a, _ = _adapter({f"{BASE}/n0063lr/3/": html("<html><body><p>hi</p></body></html>")})
        with pytest.raises(SourceError, match="chapter text"):
            a.get_chapter_text(self._chapter(a))


class TestFailures:
    def test_404_says_the_work_or_episode_is_gone(self):
        a, _ = _adapter({f"{BASE}/n0063lr/": html(ERROR_PAGE, status=404)})
        with pytest.raises(FetchFailed, match="doesn't exist"):
            a.get_series("n0063lr")

    def test_410_is_treated_the_same(self):
        a, _ = _adapter({f"{BASE}/n0063lr/3/": html(ERROR_PAGE, status=410)})
        with pytest.raises(FetchFailed, match="doesn't exist"):
            a.get_chapter_text(ChapterInfo("syosetu", "n0063lr", "3", "t"))

    def test_server_error_is_a_fetch_failure(self):
        a, _ = _adapter({f"{BASE}/n0063lr/": html("<html>oops</html>", status=503)})
        with pytest.raises(FetchFailed):
            a.get_series("n0063lr")

    def test_error_page_served_with_200_reports_the_sites_message(self):
        a, _ = _adapter({f"{BASE}/n0063lr/": html(ERROR_PAGE)})
        with pytest.raises(FetchFailed, match="指定されたURLが存在しません"):
            a.get_series("n0063lr")

    def test_challenge_page_is_handed_off(self):
        page = "<html><body><div class='g-recaptcha'></div>verify you are human</body></html>"
        a, _ = _adapter({f"{BASE}/n0063lr/": html(page)})
        with pytest.raises(ChallengeDetected):
            a.get_series("n0063lr")

    def test_redirect_to_the_adult_site_is_unsupported(self):
        resp = html("<html><body>age check</body></html>", url="https://novel18.syosetu.com/n0063lr/")
        a, t = _adapter({f"{BASE}/n0063lr/": resp})
        with pytest.raises(ContentHidden, match="18"):
            a.get_series("n0063lr")
        assert all(c["headers"].get("Cookie") is None for c in t.calls)

    def test_age_gate_page_is_unsupported_and_no_cookie_is_sent(self):
        page = "<html><body><p>18歳以上ですか？ 年齢確認</p></body></html>"
        a, t = _adapter({f"{BASE}/n0063lr/": html(page)})
        with pytest.raises(ContentHidden):
            a.get_series("n0063lr")
        assert "over18" not in str(t.calls[0]["headers"]).lower()

    def test_login_only_page_names_authentication(self):
        page = "<html><body><form><input type='password' name='p'></form></body></html>"
        a, _ = _adapter({f"{BASE}/n0063lr/": html(page)})
        with pytest.raises(SourceError) as e:
            a.get_series("n0063lr")
        assert e.value.reason == FailureReason.AUTHENTICATION_REQUIRED

    def test_redirect_to_another_site_is_refused(self):
        resp = html(CHAPTER, url="https://elsewhere.example/n0063lr/3/")
        a, _ = _adapter({f"{BASE}/n0063lr/3/": resp})
        with pytest.raises(FetchFailed, match="different site"):
            a.get_chapter_text(ChapterInfo("syosetu", "n0063lr", "3", "t"))


class TestSearch:
    def test_parses_results(self):
        a, t = _adapter({f"{SEARCH}?word=%E8%BB%A2%E7%94%9F": html(SEARCH_PAGE)})
        results = a.search("転生")
        assert [(r.series_id, r.title, r.url) for r in results] == [
            ("n1393mv", "検索テストA", f"{BASE}/n1393mv/"), ("n9940mq", "検索テストB", f"{BASE}/n9940mq/")]
        assert results[0].extra == {"author": "作者A", "status": "ongoing", "episodes": 10,
                                    "description": "概要A"}
        assert results[1].extra["status"] == "completed"

    def test_page_two_uses_the_p_parameter(self):
        a, t = _adapter({f"{SEARCH}?word=x&p=2": html(SEARCH_PAGE)})
        assert a.search("x", page=2)
        assert t.urls() == [f"{SEARCH}?word=x&p=2"]

    def test_blank_query_sends_nothing(self):
        a, t = _adapter({})
        assert a.search("  ") == []
        assert t.calls == []

    def test_no_results_is_an_empty_list(self):
        a, _ = _adapter({f"{SEARCH}?word=x": html("<html><body>0作品</body></html>")})
        assert a.search("x") == []

    def test_boxes_without_links_are_a_layout_error(self):
        a, _ = _adapter({f"{SEARCH}?word=x": html("<div class='searchkekka_box'>x</div>")})
        with pytest.raises(SourceError):
            a.search("x")


class TestHygiene:
    def test_only_the_expected_hosts_are_contacted_and_every_request_has_a_timeout(self):
        routes = {
            f"{BASE}/n0063lr/": html(_top("n0063lr", [(1, "a")], last=2)),
            f"{BASE}/n0063lr/?p=2": html(_top("n0063lr", [(2, "b")])),
            f"{BASE}/n0063lr/1/": html(CHAPTER),
            f"{SEARCH}?word=x": html(SEARCH_PAGE),
        }
        a, t = _adapter(routes)
        a.get_series("n0063lr")
        chapters = a.get_chapters("n0063lr")
        a.get_chapter_text(chapters[0])
        a.search("x")
        assert _hosts(t) <= ALLOWED_HOSTS   # ScriptedTransport itself asserts a timeout per call

    def test_requests_carry_no_cookies(self):
        a, t = _adapter({f"{BASE}/n0063lr/": html(_top("n0063lr", [(1, "a")]))})
        a.get_series("n0063lr")
        assert not any(k.lower() == "cookie" for c in t.calls for k in c["headers"])

    def test_pacing_is_polite(self):
        assert syosetu.SyosetuSource.host_min_interval["ncode.syosetu.com"] >= 1.0

    def test_capabilities_record_the_terms_finding(self):
        caps = syosetu.SyosetuSource(client=make_client("syosetu", ScriptedTransport({}))).capabilities()
        assert caps.content_types == ["novel"] and caps.languages == ["ja"]
        assert caps.terms["tos_prohibited"] is True
        assert caps.automation_permission == AutomationPermission.EXPLICITLY_RESTRICTED.value

    def test_site_terms_entry(self):
        caps = site_terms.capabilities_for("https://ncode.syosetu.com/n0063lr/")
        assert caps is not None
        assert caps.automation_permission == AutomationPermission.EXPLICITLY_RESTRICTED.value
        assert site_terms.capabilities_for("https://novel18.syosetu.com/x/") is not None
        assert site_terms.capabilities_for("https://syosetu.org/novel/1/") is None
