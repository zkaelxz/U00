"""
tests/test_sources_manhuagui.py -- Step 23b: the manhuagui adapter,
against recorded-shape fixtures only (tests/manhuagui_fixtures.py). No
request ever reaches the real site.
"""

import os

import pytest

from sources import pipeline
from sources.adapters import manhuagui as mhg
from sources.http import Response
from sources.lzstring import compress_to_base64, decompress_from_base64
from sources.models import ChallengeDetected, ContentHidden, SourceUnavailable

from . import manhuagui_fixtures as fx
from .sources_helpers import FakeClock, ScriptedTransport, html, image, make_client

W, TW, M, TM = mhg.MIRRORS
CDN, CDN2 = mhg.IMAGE_SERVERS

# Reference vectors produced by the JS reference implementation
# (pieroxy/lz-string 1.5.0, compressToBase64) -- not by this code.
LZ_REFERENCE = [
    ("hello 你好|a|b", "BYUwNmD2AEgG8ovpoB8CGiBGQ==="),
    ("aaaaaaaaaaaaaaaaaaaaaaaaaaaaab", "IY18ZwEZA==="),
    ('{"files":["1.jpg.webp","2.jpg.webp"],"path":"/ps3/a/b/","sl":{"e":1790000000,"m":"AbC-dEf"}}',
     "N4IgZglgNgpgziAXAbRARgHQCsAOBzDAdxgCMcQAaEAJm3yNPIF0qcBDAFwAskQB6HHADMfNnxJ9KIOFCSgYSNAHYA"
     "nAAYNmtVQC2vAIIkAwgFoAJgFEwIAL42gA"),
]


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("manhuagui", t, clock, max_retries=kw.pop("max_retries", 0),
                         host_min_interval=mhg.ManhuaguiSource.host_min_interval)
    client.default_headers.update(mhg.ManhuaguiSource.default_headers)
    return mhg.ManhuaguiSource(client=client, **kw), t


class TestLZString:
    @pytest.mark.parametrize("plain,b64", LZ_REFERENCE)
    def test_matches_the_reference_implementation(self, plain, b64):
        assert decompress_from_base64(b64) == plain
        assert compress_to_base64(plain) == b64

    def test_round_trip_long_cjk(self):
        text = "|".join(f"第{i}话" for i in range(500)) + "漫画柜" * 200
        assert decompress_from_base64(compress_to_base64(text)) == text

    def test_rejects_non_base64(self):
        with pytest.raises(ValueError):
            decompress_from_base64("not*base64")


class TestSearch:
    def test_parses_results(self, isolated_db):
        a, t = _adapter({f"{W}/s/%E6%B5%8B%E8%AF%95_p1.html": html(fx.SEARCH_PAGE)})
        results = a.search("测试")
        assert [(r.series_id, r.title) for r in results] == [("17332", "测试漫画"),
                                                              ("9001", "另一部 测试")]
        assert results[0].url == f"{W}/comic/17332/"
        assert results[0].cover_url == "https://cf.hamreus.com/cpic/h/17332.jpg"
        assert results[1].cover_url == "https://cf.hamreus.com/cpic/h/9001.jpg"
        assert "isAdult" not in t.calls[0]["headers"].get("Cookie", "")
        assert t.calls[0]["headers"]["Referer"] == W + "/"

    def test_no_results_is_an_empty_list(self, isolated_db):
        a, _ = _adapter({f"{W}/s/zzz_p1.html": html(fx.EMPTY_SEARCH_PAGE)})
        assert a.search("zzz") == []

    def test_changed_layout_fails_clearly(self, isolated_db):
        a, _ = _adapter({f"{W}/s/x_p1.html": html(fx.CHANGED_LAYOUT_PAGE)})
        with pytest.raises(mhg.LayoutChanged) as e:
            a.search("x")
        assert "layout has changed" in str(e.value)


class TestSeriesAndChapters:
    def test_series_details(self, isolated_db):
        a, _ = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE)})
        s = a.get_series("17332")
        assert s.title == "测试漫画" and s.status == "ongoing"
        assert s.authors == ["作者甲"] and s.genres == ["百合", "爱情"]
        assert s.description == "两个女孩的故事。"
        assert s.cover_url == "https://cf.hamreus.com/cpic/b/17332.jpg"

    def _expected(self):
        return [("244506", "第01话", "单话"), ("244508", "第03话", "单话"),
                ("244507", "第02话", "单话"), ("250001", "番外 夏日", "番外篇")]

    def test_plain_chapter_list(self, isolated_db):
        a, _ = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE)})
        chapters = a.get_chapters("17332")
        assert [(c.chapter_id, c.title, c.group) for c in chapters] == self._expected()
        assert chapters[0].url == f"{W}/comic/17332/244506.html"

    def test_lzstring_compressed_chapter_list_is_decoded_when_allowed(self, isolated_db):
        a, _ = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE_ADULT)}, allow_adult=True)
        chapters = a.get_chapters("17332")
        assert [(c.chapter_id, c.title, c.group) for c in chapters] == self._expected()

    def test_hidden_chapter_list_is_refused_by_default(self, isolated_db):
        a, t = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE_ADULT)})
        with pytest.raises(ContentHidden) as e:
            a.get_chapters("17332")
        assert "adult-content switch" in str(e.value)
        assert "isAdult" not in t.calls[0]["headers"].get("Cookie", "")

    def test_adult_opt_in_sends_the_cookie(self, isolated_db):
        a, t = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE_ADULT)}, allow_adult=True)
        a.get_chapters("17332")
        assert t.calls[0]["headers"]["Cookie"] == "isAdult=1"

    def test_chapters_sort_naturally_within_their_sections(self, isolated_db):
        from sources import chapter_order
        a, _ = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE)})
        assert a.chapters_in_site_order is False
        ordered = chapter_order.reading_order(a, a.get_chapters("17332"))
        assert [c.title for c in ordered] == ["第01话", "第02话", "第03话", "番外 夏日"]


class TestPages:
    FILES = ["001.jpg.webp", "002.jpg.webp", "003.jpg.webp"]
    PATH = "/ps3/c/测试漫画/第01话/"

    def _chapter(self, a):
        from sources.models import ChapterInfo
        return ChapterInfo("manhuagui", "17332", "244506", "第01话",
                           f"{W}/comic/17332/244506.html")

    def test_packed_page_script_is_decoded_to_image_urls(self, isolated_db):
        page = fx.chapter_page(fx.image_data(self.FILES, self.PATH, e=1790001234, m="k3Y-x_z"))
        a, _ = _adapter({f"{W}/comic/17332/244506.html": html(page)})
        pages = a.get_pages(self._chapter(a))
        assert [p.url for p in pages] == [
            f"{self.PATH}{f}?e=1790001234&m=k3Y-x_z" for f in self.FILES]
        assert all(p.headers == {"Referer": W + "/"} for p in pages)

    def test_page_without_the_script_fails_clearly(self, isolated_db):
        a, _ = _adapter({f"{W}/comic/17332/244506.html": html(fx.CHANGED_LAYOUT_PAGE)})
        with pytest.raises(mhg.LayoutChanged):
            a.get_pages(self._chapter(a))

    def test_download_uses_the_cdn_with_a_referer_and_falls_back(self, isolated_db):
        from sources.models import PageRef
        path = f"{self.PATH}001.jpg.webp?e=1&m=x"
        a, t = _adapter({CDN + path: ConnectionError("reset"),
                         CDN2 + path: image(700, 1000, 1)})
        content, ext = a.download_page(PageRef("manhuagui", "244506", 0, path,
                                               headers={"Referer": W + "/"}))
        assert ext == ".webp" and content.startswith(b"\x89PNG")
        assert t.urls() == [CDN + path, CDN2 + path]
        assert all(c["headers"]["Referer"] == W + "/" for c in t.calls)


class TestMirrorFallback:
    def test_failed_primary_mirror_falls_back_to_the_next(self, isolated_db):
        a, t = _adapter({f"{W}/comic/17332/": ConnectionError("unreachable"),
                         f"{TW}/comic/17332/": html(fx.SERIES_PAGE)})
        s = a.get_series("17332")
        assert s.title == "测试漫画"
        assert t.urls() == [f"{W}/comic/17332/", f"{TW}/comic/17332/"]
        assert s.url == f"{TW}/comic/17332/"

    def test_5xx_after_retries_moves_on_too(self, isolated_db):
        a, t = _adapter({f"{W}/comic/17332/": html("bad gateway", 502),
                         f"{TW}/comic/17332/": html("bad gateway", 502),
                         f"{M}/comic/17332/": html(fx.SERIES_PAGE)}, max_retries=1)
        assert a.get_series("17332").title == "测试漫画"
        assert t.urls() == [f"{W}/comic/17332/"] * 2 + [f"{TW}/comic/17332/"] * 2 + \
            [f"{M}/comic/17332/"]

    def test_all_mirrors_down_is_unavailable(self, isolated_db):
        routes = {f"{m}/comic/1/": ConnectionError("down") for m in mhg.MIRRORS}
        a, t = _adapter(routes)
        with pytest.raises(SourceUnavailable) as e:
            a.get_series("1")
        assert len(t.calls) == 4 and "Every configured mirror failed" in str(e.value)

    def test_a_challenge_is_not_routed_around_via_another_mirror(self, isolated_db):
        a, t = _adapter({f"{W}/comic/1/": html("<title>Just a moment...</title>", 403,
                                                {"cf-mitigated": "challenge"}),
                         f"{TW}/comic/1/": html(fx.SERIES_PAGE)})
        with pytest.raises(ChallengeDetected):
            a.get_series("1")
        assert t.urls() == [f"{W}/comic/1/"]

    def test_a_missing_page_is_not_a_mirror_problem(self, isolated_db):
        from sources.models import FetchFailed
        a, t = _adapter({f"{W}/comic/404/": html("not found", 404)})
        with pytest.raises(FetchFailed):
            a.get_series("404")
        assert len(t.calls) == 1


class TestPacingAndRouting:
    def test_main_site_honours_the_robots_crawl_delay(self, isolated_db):
        clock = FakeClock()
        a, t = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE),
                         f"{W}/s/x_p1.html": html(fx.EMPTY_SEARCH_PAGE)}, clock)
        a.get_series("17332")
        a.search("x")
        assert t.calls[1]["t"] - t.calls[0]["t"] >= mhg.CRAWL_DELAY

    @pytest.mark.parametrize("url,kind", [
        ("https://www.manhuagui.com/comic/17332/", "series"),
        ("https://tw.mhgui.com/comic/17332/244506.html", "chapter"),
        ("https://m.manhuagui.com/comic/17332/", "series"),
    ])
    def test_url_routing(self, isolated_db, url, kind):
        from sources import registry
        adapter = registry.find_for_url(url)
        assert adapter is not None and adapter.name == "manhuagui"
        assert adapter.parse_url(url)[0] == kind

    def test_unrelated_url_is_not_claimed(self, isolated_db):
        from sources import registry
        assert registry.find_for_url("https://example.com/comic/17332/") is None

    def test_capabilities_keep_technical_and_terms_apart(self, isolated_db):
        caps = mhg.ManhuaguiSource(client=make_client("manhuagui", ScriptedTransport())).capabilities()
        assert caps.status == "UNTESTED"
        assert "ClaudeBot" in caps.terms["robots_txt"] and caps.terms["tos_prohibited"] is False
        assert caps.technical["browser_required"] is False


class TestEndToEnd:
    def test_search_chapters_pages_into_scanlate(self, isolated_db):
        files = ["001.jpg.webp", "002.jpg.webp"]
        path = "/ps3/c/测试漫画/第01话/"
        page = fx.chapter_page(fx.image_data(files, path))
        img_routes = {f"{CDN}{path}{f}?e=1790000000&m=AbC-dEf_9": image(720, 1080, i)
                      for i, f in enumerate(files)}
        a, t = _adapter({f"{W}/s/%E6%B5%8B%E8%AF%95_p1.html": html(fx.SEARCH_PAGE),
                         f"{W}/comic/17332/": html(fx.SERIES_PAGE),
                         f"{W}/comic/17332/244506.html": html(page), **img_routes})
        series = a.search("测试")[0]
        chapter = next(c for c in a.get_chapters(series.series_id) if c.title == "第01话")
        drama_id = isolated_db.create_drama(title_zh="测试漫画", media_type="manhua")

        import background_jobs
        job = "source_import_manhuagui_17332"
        background_jobs._jobs[job] = {"status": "running", "progress": 0.0, "message": "",
                                      "cancel_requested": False, "result": None}
        pipeline.run_import_job(job, "manhuagui", [chapter], drama_id, adapter=a)
        result = background_jobs.get_status(job)["result"]
        background_jobs.clear_job(job)
        assert result["chapters"] == [{"chapter_id": "244506", "title": "第01话", "ok": True,
                                       "pages": 2}]
        pages = isolated_db.list_pages(drama_id)
        assert [p["filename"] for p in pages] == [os.path.join("pages", "page_0000.png"),
                                                   os.path.join("pages", "page_0001.png")]
        assert (pages[0]["width"], pages[0]["height"]) == (720, 1080)


class TestRequestEconomy:
    def test_series_and_chapters_share_one_page_fetch(self, isolated_db):
        a, t = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE)})
        a.get_series("17332")
        a.get_chapters("17332")
        assert len(t.calls) == 1


class TestAdultOptIn:
    def test_off_by_default_and_the_message_names_the_toggle(self, isolated_db):
        a, _ = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE_ADULT)})
        assert a.allow_adult is False
        with pytest.raises(ContentHidden) as e:
            a.get_chapters("17332")
        assert "Include adult-flagged works" in str(e.value)

    def test_turning_it_on_for_this_source_is_picked_up(self, isolated_db):
        from sources import store
        store.set_adult_enabled("manhuagui", True)
        a, t = _adapter({f"{W}/comic/17332/": html(fx.SERIES_PAGE_ADULT)})
        assert a.allow_adult is True
        assert len(a.get_chapters("17332")) == 4
        assert t.calls[0]["headers"]["Cookie"] == "isAdult=1"
        store.set_adult_enabled("manhuagui", False)
        assert _adapter({})[0].allow_adult is False

    def test_opt_in_is_per_source(self, isolated_db):
        from sources import store
        store.set_adult_enabled("some_other_source", True)
        assert _adapter({})[0].allow_adult is False

    def test_the_cookie_never_goes_to_the_image_cdn(self, isolated_db):
        from sources.models import PageRef
        path = "/ps3/x/001.jpg.webp?e=1&m=x"
        a, t = _adapter({CDN + path: image(700, 1000, 1)}, allow_adult=True)
        a.download_page(PageRef("manhuagui", "1", 0, path, headers={"Referer": W + "/"}))
        assert "Cookie" not in t.calls[0]["headers"]

    def test_adapters_without_a_switch_ignore_the_flag(self, isolated_db):
        from sources.base import SourceAdapter

        class Plain(SourceAdapter):
            name = "plain"
        assert Plain(client=make_client("plain", ScriptedTransport()), allow_adult=True).allow_adult is False
