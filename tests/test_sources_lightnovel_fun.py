"""
tests/test_sources_lightnovel_fun.py -- Step 115: the 轻之国度
(lightnovel.fun) adapter, against recorded-shape fixtures only. No request
ever reaches the real site.
"""
import pytest

from sources import registry
from sources.adapters import lightnovel_fun
from sources.models import ChallengeDetected, ChapterInfo, ContentHidden, FailureReason

from . import lightnovel_fun_fixtures as fx
from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = lightnovel_fun.BASE_URL
BOOK_URL = f"{BASE}/book/33139"


def _reader(cid):
    return f"{BASE}/reader/33139/{cid}"


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("lightnovel_fun", t, clock, max_retries=0)
    return lightnovel_fun.LightnovelFunSource(client=client), t


def _chapter(cid, title="t"):
    return ChapterInfo("lightnovel_fun", "33139", cid, title, _reader(cid))


class TestDevalue:
    def test_rebuilds_nested_objects_from_the_flat_array(self):
        data = lightnovel_fun._payload(fx.BOOK_PAGE)
        book = data["pc-book-detail-33139"]["book"]
        assert book["title"] == fx.BOOK["title"]
        assert book["tags"] == ["百合", "恋爱", "校园", "青春"]
        assert data["pc-auth-state"]["user"] is None

    def test_negative_indices_and_wrappers(self):
        arr = [["ShallowReactive", 1], {"data": 2}, ["Reactive", 3], {"a": -1, "b": 4, "d": 5},
               ["Date", "2026-09-30T00:00:00.000Z"], ["Set", 6], "x"]
        assert lightnovel_fun._devalue(arr) == {
            "data": {"a": None, "b": "2026-09-30T00:00:00.000Z", "d": ["x"]}}

    def test_a_cycle_does_not_recurse_forever(self):
        assert lightnovel_fun._devalue([{"self": 0}]) == {"self": None}

    def test_missing_or_broken_payload_is_empty(self):
        assert lightnovel_fun._payload("<html><body>no data</body></html>") == {}
        broken = "<script id='__NUXT_DATA__' type='application/json'>[not json</script>"
        assert lightnovel_fun._payload(broken) == {}


class TestSearch:
    def test_parses_results_from_the_search_page(self):
        a, t = _adapter({f"{BASE}/search?keyword=%E7%99%BE%E5%90%88&page=1": html(fx.SEARCH_PAGE)})
        results = a.search("百合")
        assert [r.series_id for r in results] == ["993", "1353", "33139"]
        assert results[0].title == "将放言说不会输的高颜值女孩，全力征服的百合故事"
        assert results[0].url == f"{BASE}/book/993"
        assert results[0].cover_url.startswith("https://api.lightnovel.fun/")
        assert results[2].extra["chapterCount"] == 13
        assert t.calls[0]["method"] == "GET"

    def test_the_side_board_is_not_a_result(self):
        a, _ = _adapter({f"{BASE}/search?keyword=%E7%99%BE%E5%90%88&page=1": html(fx.SEARCH_PAGE)})
        assert "1338" not in [r.series_id for r in a.search("百合")]

    def test_page_number_is_passed_through(self):
        a, t = _adapter({f"{BASE}/search?keyword=%E7%99%BE%E5%90%88&page=2": html(fx.SEARCH_PAGE)})
        a.search("百合", page=2)
        assert t.urls() == [f"{BASE}/search?keyword=%E7%99%BE%E5%90%88&page=2"]

    def test_markup_change_is_reported(self):
        a, _ = _adapter({f"{BASE}/search?keyword=x&page=1": html("<html><body></body></html>")})
        with pytest.raises(lightnovel_fun.LayoutChanged):
            a.search("x")


class TestSeries:
    def test_series_details(self):
        a, _ = _adapter({BOOK_URL: html(fx.BOOK_PAGE)})
        s = a.get_series("33139")
        assert s.title == fx.BOOK["title"]
        assert s.authors == ["能代リョウ", "べにしゃけ"]
        assert s.genres == ["百合", "恋爱", "校园", "青春"]
        assert s.status == "ongoing"
        assert s.language == "zh" and s.content_type == "novel"
        assert s.cover_url == fx.BOOK["cover"]

    def test_series_description_carries_the_site_notice(self):
        a, _ = _adapter({BOOK_URL: html(fx.BOOK_PAGE)})
        s = a.get_series("33139")
        assert s.description.startswith("她的优点可不止")
        for notice in ("仅供个人学习交流使用，禁作商业用途", "禁止转载", "禁止二改二传"):
            assert notice in s.description

    def test_markup_change_is_reported(self):
        a, _ = _adapter({BOOK_URL: html("<html><body><h1>x</h1></body></html>")})
        with pytest.raises(lightnovel_fun.LayoutChanged):
            a.get_series("33139")


class TestChapters:
    def test_reads_every_chapter_of_the_loaded_volume_not_just_the_rendered_grid(self):
        a, _ = _adapter({BOOK_URL: html(fx.BOOK_PAGE), _reader("323701"): html(fx.READER_323701),
                         _reader("323778"): html(fx.READER_323778)})
        chapters = a.get_chapters("33139")
        vol1 = [c for c in chapters if c.group == "第一卷"]
        assert len(vol1) == 10  # the HTML grid only shows 8
        assert vol1[2].chapter_id == "323385"
        assert vol1[2].title == "第一话 我才不会输给矢来同学呢。"
        assert vol1[2].url == _reader("323385")

    def test_walks_into_an_unloaded_volume_through_the_reader_pages(self):
        a, t = _adapter({BOOK_URL: html(fx.BOOK_PAGE), _reader("323701"): html(fx.READER_323701),
                         _reader("323778"): html(fx.READER_323778)})
        chapters = a.get_chapters("33139")
        assert len(chapters) == 13
        assert [(c.chapter_id, c.group) for c in chapters[-3:]] == [
            ("323778", "web版"), ("323779", "web版"), ("323829", "web版")]
        # book page, then last chapter of 第一卷 -> its next chapter.
        assert t.urls() == [BOOK_URL, _reader("323701"), _reader("323778")]

    def test_walks_backwards_when_only_a_later_volume_is_loaded(self):
        book = fx.page({"pc-book-detail-33139": {
            "book": fx.BOOK, "catalog": fx.catalog(first_loaded=False, web_loaded=True)}})
        before_web = fx.reader_page("323778", fx.reader_chapter(
            "323778", "制作信息", "50789", "323701", "323779", "<p>x</p>"),
            fx.catalog(first_loaded=False, web_loaded=True))
        a, t = _adapter({BOOK_URL: html(book), _reader("323778"): html(before_web),
                         _reader("323701"): html(fx.READER_323701)})
        chapters = a.get_chapters("33139")
        assert len(chapters) == 13
        assert chapters[0].chapter_id == "323383" and chapters[0].group == "第一卷"
        assert t.urls() == [BOOK_URL, _reader("323778"), _reader("323701")]

    def test_a_volume_the_walk_cannot_reach_is_left_out_not_fatal(self):
        dead_end = fx.reader_page("323701", fx.reader_chapter(
            "323701", "Bookwalker特典", "50701", "323700", None, "<p>x</p>"), fx.catalog())
        a, t = _adapter({BOOK_URL: html(fx.BOOK_PAGE), _reader("323701"): html(dead_end)})
        chapters = a.get_chapters("33139")
        assert len(chapters) == 10
        assert t.urls() == [BOOK_URL, _reader("323701")]

    def test_chapter_lists_are_never_served_from_cache(self):
        a, t = _adapter({BOOK_URL: html(fx.BOOK_PAGE), _reader("323701"): html(fx.READER_323701),
                         _reader("323778"): html(fx.READER_323778)})
        a.get_chapters("33139")
        a.get_chapters("33139")
        assert t.urls().count(BOOK_URL) == 2

    def test_markup_change_is_reported(self):
        a, _ = _adapter({BOOK_URL: html(fx.page({"pc-book-detail-33139": {"book": fx.BOOK,
                                                                          "catalog": []}}))})
        with pytest.raises(lightnovel_fun.LayoutChanged):
            a.get_chapters("33139")


class TestChapterText:
    def test_reads_the_chapter_body(self):
        a, _ = _adapter({_reader("323385"): html(fx.READER_323385)})
        text = a.get_chapter_text(_chapter("323385"))
        assert text.split("\n") == ["昨天，也就是暑假最后一天，我把头发剪了。",
                                    "一直留到腰间的黑发，被我一口气剪掉了。", "难以理解。"]

    def test_real_credits_page_keeps_link_text_and_never_follows_it(self):
        a, t = _adapter({_reader("323778"): html(fx.READER_323778)})
        text = a.get_chapter_text(_chapter("323778"))
        assert "原作链接：https://kakuyomu.jp/works/1177354054919288428" in text
        assert "仅供个人学习交流使用，禁作商业用途。" in text
        assert "原文已断更。目前共有41-87话及两篇番外。" in text
        assert t.urls() == [_reader("323778")]

    def test_an_illustrations_only_chapter_is_empty_text(self):
        a, _ = _adapter({_reader("323384"): html(fx.READER_323384)})
        assert a.get_chapter_text(_chapter("323384")) == ""

    def test_a_locked_chapter_is_hidden_and_never_unlocked(self):
        a, t = _adapter({_reader("323829"): html(fx.READER_LOCKED)})
        with pytest.raises(ContentHidden) as e:
            a.get_chapter_text(_chapter("323829", "第42话"))
        assert e.value.reason == FailureReason.PURCHASE_REQUIRED
        assert "20 轻币" in str(e.value)
        assert t.urls() == [_reader("323829")]
        assert all(c["method"] == "GET" for c in t.calls)

    def test_falls_back_to_the_rendered_text_without_a_payload(self):
        page = ("<html><body><div class='reader-text'><p>第一段。</p><p>第二段<ruby>漢<rt>kan</rt>"
                "</ruby>。</p></div></body></html>")
        a, _ = _adapter({_reader("1"): html(page)})
        assert a.get_chapter_text(_chapter("1")) == "第一段。\n第二段漢。"

    def test_markup_change_is_reported(self):
        a, _ = _adapter({_reader("1"): html("<html><body><div>nothing</div></body></html>")})
        with pytest.raises(lightnovel_fun.LayoutChanged):
            a.get_chapter_text(_chapter("1"))

    def test_a_cloudflare_challenge_is_a_wall(self):
        a, _ = _adapter({_reader("1"): html(fx.CLOUDFLARE_PAGE, status=403)})
        with pytest.raises(ChallengeDetected):
            a.get_chapter_text(_chapter("1"))


class TestUrlsAndRegistry:
    def test_registered_and_routes_pasted_urls(self):
        assert registry.adapter_classes()["lightnovel_fun"] is lightnovel_fun.LightnovelFunSource
        assert registry.adapter_class_for_url(
            "https://www.lightnovel.fun/book/33139") is lightnovel_fun.LightnovelFunSource
        assert lightnovel_fun.LightnovelFunSource.matches_url("https://www.lightnovel.fun/book/33139")
        assert lightnovel_fun.LightnovelFunSource.matches_url(
            "https://www.lightnovel.fun/reader/33139/323385")
        assert not lightnovel_fun.LightnovelFunSource.matches_url("https://www.lightnovel.fun/settings/")

    def test_parse_url(self):
        a, _ = _adapter({})
        kind, ch = a.parse_url("https://www.lightnovel.fun/reader/33139/323385")
        assert kind == "chapter" and (ch.series_id, ch.chapter_id) == ("33139", "323385")
        assert a.parse_url("https://www.lightnovel.fun/book/33139") == ("series", "33139")
        assert a.parse_url("https://www.lightnovel.fun/category/lightnovel") is None

    def test_capabilities_record_the_terms_and_notices(self):
        caps = lightnovel_fun.LightnovelFunSource(client=make_client("lightnovel_fun")).capabilities()
        assert caps.automation_permission == "UNKNOWN"
        assert not caps.terms_restrictions()
        assert "禁止二改二传" in caps.terms["notices"]
        assert "/settings/" in caps.terms["robots_txt"]
        assert "unlock" in caps.technical["never_used"]

    def test_site_terms_entry(self):
        from sources import site_terms
        caps = site_terms.capabilities_for("https://www.lightnovel.fun/book/33139")
        assert caps is not None
        assert caps.automation_permission == "UNKNOWN"
        assert not caps.terms_restrictions()
        assert "禁止转载" in caps.terms["notices"]
