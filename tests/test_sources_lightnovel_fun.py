"""
tests/test_sources_lightnovel_fun.py -- Step 115: the 轻之国度
(lightnovel.fun) adapter, against recorded-shape fixtures only. No request
ever reaches the real site.
"""
import pytest

from sources import registry
from sources.adapters import lightnovel_fun
from sources.models import ChallengeDetected, ChapterInfo, ContentHidden, FailureReason, SourceError

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
        # First, so the 3-line clamp in the series panel doesn't hide it.
        assert s.description.startswith("轻之国度 works carry the uploaders' own notices")
        assert "她的优点可不止" in s.description
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
        a, _ = _adapter({BOOK_URL: html(fx.BOOK_PAGE), _reader("323701"): html(fx.READER_323701),
                         _reader("323778"): html(fx.READER_323778)})
        seen = []
        real_get = a.client.get

        def spy(url, **kw):
            seen.append(kw.get("use_cache", True))
            return real_get(url, **kw)

        a.client.get = spy
        a.get_chapters("33139")
        assert seen == [False, False, False]

    def test_a_volume_skipped_by_the_links_does_not_drop_later_ones(self):
        # 第一卷 -> (a volume with no public chapter) -> web版: the last chapter
        # of 第一卷 links straight into web版, whose page loads web版.
        cat = fx.catalog()
        cat.insert(1, {"id": "50750", "title": "付费卷", "chapters": [], "chapterCount": 2,
                       "chaptersLoaded": False})
        cat.append({"id": "50800", "title": "web版2", "chapters": [], "chapterCount": 1,
                    "chaptersLoaded": False})
        book = fx.page({"pc-book-detail-33139": {"book": fx.BOOK, "catalog": cat}})
        web_cat = [dict(v) for v in cat]
        web_cat[0] = dict(web_cat[0], chapters=[], chaptersLoaded=False)
        web_cat[2] = dict(web_cat[2], chapters=fx.VOLUME_WEB, chaptersLoaded=True)
        last_web = fx.reader_page("323829", fx.reader_chapter(
            "323829", "第42话", "50789", "323779", "400001", "<p>x</p>"), web_cat)
        web2_cat = [dict(v) for v in web_cat]
        web2_cat[2] = dict(web2_cat[2], chapters=[], chaptersLoaded=False)
        web2_cat[3] = dict(web2_cat[3], chapters=[fx._ch("400001", "第43话", 1)], chaptersLoaded=True)
        web2 = fx.reader_page("400001", fx.reader_chapter(
            "400001", "第43话", "50800", "323829", None, "<p>x</p>"), web2_cat)
        web_first = fx.reader_page("323778", fx.reader_chapter(
            "323778", "制作信息", "50789", "323701", "323779", "<p>x</p>"), web_cat)
        a, _ = _adapter({BOOK_URL: html(book), _reader("323701"): html(fx.READER_323701),
                         _reader("323778"): html(web_first), _reader("323829"): html(last_web),
                         _reader("400001"): html(web2)})
        chapters = a.get_chapters("33139")
        assert [c.group for c in chapters].count("web版") == 3
        assert chapters[-1].chapter_id == "400001" and chapters[-1].group == "web版2"
        assert "付费卷" not in {c.group for c in chapters}

    def test_a_deleted_chapter_in_the_walk_drops_that_volume_only(self):
        a, _ = _adapter({BOOK_URL: html(fx.BOOK_PAGE), _reader("323701"): html(fx.READER_323701),
                         _reader("323778"): html("<html>gone</html>", status=404)})
        chapters = a.get_chapters("33139")
        assert len(chapters) == 10

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
        assert "资源解锁后可用" not in str(e.value)
        assert t.urls() == [_reader("323829")]
        assert all(c["method"] == "GET" for c in t.calls)

    def test_without_the_page_data_the_chapter_is_refused_not_guessed(self):
        # The rendered text alone carries no lock flag; a locked chapter's
        # page still renders its teaser, so it must not be imported.
        page = ("<html><body><div class='reader-text'><p>轻之国度×天使动漫录入组</p>"
                "</div></body></html>")
        a, _ = _adapter({_reader("1"): html(page)})
        with pytest.raises(lightnovel_fun.LayoutChanged):
            a.get_chapter_text(_chapter("1"))

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


class TestHostilePayloads:
    """Security review (Step 115): the payload is third-party input."""

    @staticmethod
    def _dag(depth=60):
        # arr[k] = [k+1, k+1]: linear to decode, 2**depth elements if str()'d.
        return [[k + 1, k + 1] for k in range(depth)] + ["x"]

    def _book_page(self, **book_fields):
        import json
        tail = self._dag()
        base = 4
        arr = [["ShallowReactive", 1], {"data": 2}, {"pc-book-detail-33139": 3},
               {"book": base + len(tail)}]
        arr += [[x + base for x in pair] if isinstance(pair, list) else pair for pair in tail]
        book = {"id": len(arr) + 1, "title": len(arr) + 2}
        for k in book_fields:
            book[k] = base  # the root of the shared-node chain
        arr.append(book)
        arr += ["33139", "标题"]
        return f"<script id='__NUXT_DATA__' type='application/json'>{json.dumps(arr)}</script>"

    def test_a_shared_node_author_or_cover_is_ignored_not_stringified(self):
        a, _ = _adapter({BOOK_URL: html(self._book_page(author=1, cover=1, status=1))})
        s = a.get_series("33139")
        assert s.title == "标题" and s.authors == [] and s.cover_url == ""

    def test_a_shared_node_map_key_is_skipped(self):
        arr = [["Map", 1, 3], [2, 2], [3, 3], "v"]
        assert lightnovel_fun._devalue(arr) == {}

    def test_deep_nesting_is_a_layout_change_not_a_recursion_error(self):
        import json
        depth = 5000
        arr = [[k + 1] for k in range(depth)] + ["x"]
        page = f"<script id='__NUXT_DATA__' type='application/json'>{json.dumps(arr)}</script>"
        a, _ = _adapter({BOOK_URL: html(page)})
        with pytest.raises(lightnovel_fun.LayoutChanged):
            a.get_series("33139")

    def test_entry_lookup_does_not_match_a_longer_id(self):
        data = {"pc-book-detail-33139": {"book": {}}}
        assert lightnovel_fun._entry(data, "pc-book-detail-33") is None
        assert lightnovel_fun._entry({"reader-bootstrap-1-2-public": {"a": 1}},
                                     "reader-bootstrap-1-2") == {"a": 1}


class TestIds:
    def test_non_digit_ids_are_refused_before_any_request(self):
        a, t = _adapter({})
        for bad in ("33139%2F..%2Fsettings", "../settings", "33139/", "", "٣٣"):
            with pytest.raises(SourceError):
                a.get_series(bad)
            with pytest.raises(SourceError):
                a.get_chapter_text(ChapterInfo("lightnovel_fun", "33139", bad, "t"))
        assert t.calls == []


class TestSeriesNoticeAndLinks:
    """Lead's review (2026-09-30): list EPUB links for the person to open
    themselves; never fetch them. Show the work's own notice when it has one."""

    def _series(self, routes, series_id="33139"):
        a, t = _adapter(routes)
        return a.get_series(series_id), t

    def test_the_works_own_notice_replaces_the_generic_one(self):
        s, _ = self._series({BOOK_URL: html(fx.BOOK_PAGE), _reader("323383"): html(fx.READER_323383)})
        first = s.description.split("\n")[0]
        assert first.startswith("Uploader's notice: 仅供个人学习交流使用，禁作商业用途。")
        assert "如需转载请保留制作信息（及群号）。" in first
        assert "她的优点可不止" in s.description

    def test_lanzou_link_with_the_password_on_the_next_line(self):
        s, t = self._series({BOOK_URL: html(fx.BOOK_PAGE), _reader("323383"): html(fx.READER_323383)})
        assert s.links == [{"label": "蓝奏云 (Lanzou)", "url": "https://wwasa.lanzoue.com/b0188mxnyb",
                            "password": "be3j"}]
        assert t.urls() == [BOOK_URL, _reader("323383")]

    def test_baidu_pan_link_and_code_are_listed_and_nothing_is_fetched_from_them(self):
        s, t = self._series({f"{BASE}/book/642": html(fx.BOOK_642_PAGE),
                             f"{BASE}/reader/642/262972": html(fx.READER_262972)}, "642")
        assert s.links == [{"label": "百度网盘 (Baidu Pan)",
                            "url": "https://pan.baidu.com/s/1UW8fzsl6WfJ1RRIXRt_MPw?pwd=roh1",
                            "password": "roh1"}]
        assert t.urls() == [f"{BASE}/book/642", f"{BASE}/reader/642/262972"]
        assert all("baidu" not in u and "lanzou" not in u for u in t.urls())
        assert s.description.startswith("Uploader's notice: 仅供个人学习交流使用，禁作商业用途。")

    def test_a_locked_first_chapter_is_not_fetched(self):
        cat = fx.catalog()
        cat[0] = dict(cat[0], chapters=[fx._ch("323383", "制作信息", 1, locked=True, access="coin",
                                               price=20)] + fx.VOLUME_1[1:])
        book = fx.page({"pc-book-detail-33139": {"book": fx.BOOK, "catalog": cat}})
        s, t = self._series({BOOK_URL: html(book)})
        assert t.urls() == [BOOK_URL]
        assert s.links == [] and s.description.startswith(lightnovel_fun.SITE_NOTICE)

    def test_an_unreachable_first_chapter_falls_back_to_the_site_notice(self):
        s, t = self._series({BOOK_URL: html(fx.BOOK_PAGE)})  # the reader page 404s
        assert s.description.startswith(lightnovel_fun.SITE_NOTICE)
        assert s.links == []

    def test_only_file_lockers_are_listed(self):
        lines = ["原作链接：https://kakuyomu.jp/works/1177354054919288428",
                 "轻之国度：https://www.lightnovel.fun",
                 "链接：https://pan.baidu.com/s/1abc 提取码：x1y2",
                 "https://www.lanzoux.com/iAbc", "密码：zz99",
                 "https://pan.quark.cn/s/q1", "https://pan.baidu.com/s/2def"]
        links = lightnovel_fun._download_links(lines)
        assert [(l["url"], l["password"]) for l in links] == [
            ("https://pan.baidu.com/s/1abc", "x1y2"), ("https://www.lanzoux.com/iAbc", "zz99"),
            ("https://pan.quark.cn/s/q1", ""), ("https://pan.baidu.com/s/2def", "")]
