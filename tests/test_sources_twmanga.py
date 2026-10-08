"""
tests/test_sources_twmanga.py -- the twmanga.com / twbzmg.com adapter,
against small trimmed copies of the live markup in tests/fixtures/twmanga
(placeholder text, same structure). No request reaches the real sites.
"""
import os

import pytest
import requests

from sources import registry
from sources.adapters import twmanga
from sources.http import PacingPolicy
from sources.models import ChapterInfo, FailureReason, SourceError

from .sources_helpers import ScriptedTransport, html, image, make_client

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "twmanga")
TW, BZ = twmanga.MIRRORS
SLUG = "test-series-abc"


def _fx(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def _adapter(routes):
    t = ScriptedTransport(routes)
    return twmanga.TwmangaSource(client=make_client("twmanga", t)), t


def _redirected(body, final_url):
    r = html(body)
    r.url = final_url
    return r


class TestSearch:
    def test_cards_parse_and_duplicates_and_foreign_links_are_dropped(self):
        a, t = _adapter({f"{TW}/search?q=%E4%BB%99": html(_fx("search.html"))})
        results = a.search("仙")
        assert [(r.series_id, r.title) for r in results] == [("first-hit-aaa", "First Hit"),
                                                             ("second-hit-bbb", "Second Hit")]
        assert results[0].url == f"{TW}/comic/first-hit-aaa"
        assert results[0].cover_url.startswith("https://static-tw.baozimh.com/cover/first-hit-aaa")

    def test_only_the_first_page_is_offered(self):
        a, t = _adapter({})
        assert a.search("x", page=2) == []
        assert t.calls == []


class TestSeries:
    def test_metadata(self):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html(_fx("series_short.html"))})
        info = a.get_series(SLUG)
        assert info.title == "Test Series"
        assert info.authors == ["Test Author"]
        assert info.description == "A short placeholder description."
        assert info.status == "ongoing"
        assert info.genres == ["Action", "Comedy"]
        assert info.cover_url.startswith("https://static-tw.baozimh.com/cover/test-series-abc.jpg")
        assert info.url == f"{TW}/comic/{SLUG}"
        assert info.language == "zh" and info.content_type == "manhua"

    def test_a_completed_series(self):
        body = _fx("series_short.html").replace("連載中", "已完結")
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html(body)})
        assert a.get_series(SLUG).status == "completed"

    def test_missing_title_is_a_plain_layout_error(self):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html("<html><body>nothing</body></html>")})
        with pytest.raises(SourceError) as e:
            a.get_series(SLUG)
        assert e.value.reason == FailureReason.LAYOUT_CHANGED
        assert "layout has changed" in str(e.value) and "series title" in str(e.value)

    def test_the_series_page_is_fetched_once_for_series_and_chapters(self):
        a, t = _adapter({f"{TW}/comic/{SLUG}": html(_fx("series_short.html"))})
        a.get_series(SLUG)
        a.get_chapters(SLUG)
        assert t.urls() == [f"{TW}/comic/{SLUG}"]

    def test_an_unsafe_series_id_never_reaches_the_network(self):
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.get_series("../admin")
        assert t.calls == []


class TestChapters:
    def test_a_newest_first_page_is_returned_oldest_first(self):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html(_fx("series_short.html"))})
        chapters = a.get_chapters(SLUG)
        assert [c.chapter_id for c in chapters] == ["0_0", "0_1"]
        assert [c.title for c in chapters] == ["01-First", "02-Second"]

    def test_hidden_remainder_is_included_in_numeric_order_without_duplicates(self):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html(_fx("series_long.html"))})
        chapters = a.get_chapters(SLUG)
        assert [c.chapter_id for c in chapters] == [f"0_{i}" for i in range(12)]
        assert chapters[0].title == "第1話 Title 1" and chapters[11].title == "第12話 Title 12"

    def test_chapter_urls_are_direct_and_never_go_through_page_direct(self):
        a, t = _adapter({f"{TW}/comic/{SLUG}": html(_fx("series_short.html"))})
        chapters = a.get_chapters(SLUG)
        assert chapters[1].url == f"{TW}/comic/chapter/{SLUG}/0_1.html"
        assert all("page_direct" not in u for u in t.urls())

    def test_chapter_urls_follow_the_mirror_that_served_the_page(self):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": requests.ConnectionError("down"),
                         f"{BZ}/comic/{SLUG}": html(_fx("series_short.html"))})
        assert a.get_chapters(SLUG)[0].url == f"{BZ}/comic/chapter/{SLUG}/0_0.html"

    def test_links_to_another_series_are_ignored(self):
        body = _fx("series_short.html").replace(f"comic_id={SLUG}&amp;section_slot=0&amp;chapter_slot=1",
                                                "comic_id=someone-else&amp;section_slot=0&amp;chapter_slot=1")
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html(body)})
        assert [c.chapter_id for c in a.get_chapters(SLUG)] == ["0_0"]

    def test_no_chapters_is_a_plain_layout_error(self):
        body = _fx("series_short.html").replace("comics-chapters__item", "x")
        a, _ = _adapter({f"{TW}/comic/{SLUG}": html(body)})
        with pytest.raises(SourceError) as e:
            a.get_chapters(SLUG)
        assert "chapter list" in str(e.value)


class TestPages:
    CH = ChapterInfo("twmanga", SLUG, "0_0", "01-First", f"{TW}/comic/chapter/{SLUG}/0_0.html")
    URL = f"{TW}/comic/chapter/{SLUG}/0_0.html"

    def test_images_come_out_in_order_once_each_and_without_recommendations(self):
        a, _ = _adapter({self.URL: html(_fx("chapter.html"))})
        pages = a.get_pages(self.CH)
        assert [p.url for p in pages] == [
            f"https://s1.bzcdn.net/fcomic/{SLUG}/0/0-xxxx/{n}.jpg" for n in (1, 2, 3)]
        assert [p.index for p in pages] == [0, 1, 2]
        assert all(p.headers == {} for p in pages)

    def test_the_chapter_address_is_built_from_the_ids_not_the_stored_url(self):
        ch = ChapterInfo("twmanga", SLUG, "0_0", "x", "https://evil.example/steal")
        a, t = _adapter({self.URL: html(_fx("chapter.html"))})
        a.get_pages(ch)
        assert t.urls() == [self.URL]

    def test_no_images_is_a_plain_layout_error(self):
        a, _ = _adapter({self.URL: html("<html><body><ul class='comic-contain'></ul></body></html>")})
        with pytest.raises(SourceError) as e:
            a.get_pages(self.CH)
        assert e.value.reason == FailureReason.LAYOUT_CHANGED and "page images" in str(e.value)

    def test_images_outside_the_sites_cdn_are_refused(self):
        body = _fx("chapter.html").replace("https://s1.bzcdn.net/", "https://cdn.evil.example/")
        a, _ = _adapter({self.URL: html(body)})
        with pytest.raises(SourceError) as e:
            a.get_pages(self.CH)
        assert e.value.reason == FailureReason.ACCESS_DENIED
        assert "evil" not in str(e.value)

    def test_a_malformed_chapter_id_never_reaches_the_network(self):
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.get_pages(ChapterInfo("twmanga", SLUG, "../x", "x"))
        assert t.calls == []


class TestDownload:
    def test_image_is_fetched_without_referer_or_cookie(self):
        url = f"https://s1.bzcdn.net/fcomic/{SLUG}/0/0-xxxx/1.jpg"
        a, t = _adapter({url: image(10, 10)})
        from sources.models import PageRef
        data, ext = a.download_page(PageRef("twmanga", "0_0", 0, url))
        assert data and ext == ".jpg"
        assert not ({"referer", "cookie"} & {k.lower() for k in t.calls[0]["headers"]})

    @pytest.mark.parametrize("url", ["https://cdn.evil.example/1.jpg", "http://s1.bzcdn.net/1.jpg",
                                     "https://bzcdn.net.evil.example/1.jpg",
                                     "https://evilbzcdn.net/1.jpg"])
    def test_other_hosts_and_plain_http_are_refused_before_any_request(self, url):
        from sources.models import PageRef
        a, t = _adapter({})
        with pytest.raises(SourceError):
            a.download_page(PageRef("twmanga", "0_0", 0, url))
        assert t.calls == []


class TestHostAllowList:
    def test_the_other_mirror_is_accepted_after_a_redirect(self):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": _redirected(_fx("series_short.html"),
                                                          f"{BZ}/comic/{SLUG}")})
        assert a.get_series(SLUG).title == "Test Series"

    @pytest.mark.parametrize("final", ["https://www.baozimh.com/comic/x", "https://evil.example/",
                                       "http://www.twbzmg.com/comic/x",
                                       "https://www.twbzmg.com.evil.example/"])
    def test_a_redirect_anywhere_else_is_refused_with_a_plain_message(self, final):
        a, _ = _adapter({f"{TW}/comic/{SLUG}": _redirected(_fx("series_short.html"), final)})
        with pytest.raises(SourceError) as e:
            a.get_series(SLUG)
        assert e.value.reason == FailureReason.ACCESS_DENIED
        assert "outside its own sites" in str(e.value) and "evil" not in str(e.value)

    def test_helpers(self):
        assert twmanga.is_page_host("https://www.twmanga.com/x")
        assert not twmanga.is_page_host("https://twmanga.com@evil.example/")
        assert not twmanga.is_page_host("ftp://www.twmanga.com/")
        assert twmanga.is_image_url("https://s1-2.bzcdn.net/a.jpg")
        assert not twmanga.is_image_url("https://static-tw.baozimh.com/a.jpg")


class TestMirrors:
    def test_a_dead_primary_falls_back_to_the_other_host_only(self):
        a, t = _adapter({f"{TW}/comic/{SLUG}": requests.ConnectionError("down"),
                         f"{BZ}/comic/{SLUG}": html(_fx("series_short.html"))})
        assert a.get_series(SLUG).title == "Test Series"
        assert set(t.urls()) == {f"{TW}/comic/{SLUG}", f"{BZ}/comic/{SLUG}"}
        assert t.urls()[-1] == f"{BZ}/comic/{SLUG}"


class TestParseUrl:
    def setup_method(self):
        self.a, _ = _adapter({})

    def test_series(self):
        for base in (TW, BZ):
            assert self.a.parse_url(f"{base}/comic/{SLUG}") == ("series", SLUG)

    def test_chapter(self):
        kind, ch = self.a.parse_url(f"{BZ}/comic/chapter/{SLUG}/0_7.html")
        assert kind == "chapter" and (ch.series_id, ch.chapter_id) == (SLUG, "0_7")
        assert ch.url == f"{BZ}/comic/chapter/{SLUG}/0_7.html"

    def test_page_direct_link_is_resolved_without_a_request(self):
        kind, ch = self.a.parse_url(
            f"{TW}/user/page_direct?comic_id={SLUG}&section_slot=0&chapter_slot=12")
        assert kind == "chapter" and ch.chapter_id == "0_12"
        assert ch.url == f"{TW}/comic/chapter/{SLUG}/0_12.html"

    @pytest.mark.parametrize("url", [
        "https://www.baozimh.com/comic/x", "https://example.com/comic/x",
        "http://www.twmanga.com/comic/x", "https://www.twmanga.com.evil.example/comic/x",
        f"{TW}/user/page_direct?comic_id=../x&section_slot=0&chapter_slot=1",
        f"{TW}/user/page_direct?comic_id={SLUG}&section_slot=a&chapter_slot=1",
        f"{TW}/comic/chapter", f"{TW}/", ""])
    def test_unrelated_or_malformed_urls_are_not_claimed(self, url):
        assert self.a.parse_url(url) is None

    def test_registered_and_routed_by_url(self):
        assert registry.adapter_classes()["twmanga"] is twmanga.TwmangaSource
        for url in (f"{TW}/comic/{SLUG}", f"{BZ}/comic/chapter/{SLUG}/0_1.html",
                    f"{TW}/user/page_direct?comic_id={SLUG}&section_slot=0&chapter_slot=1"):
            assert twmanga.TwmangaSource.matches_url(url)
        for url in ("https://www.baozimh.com/comic/x", "https://eviltwmanga.com/comic/x",
                    "https://twmanga.com.evil.example/comic/x"):
            assert not twmanga.TwmangaSource.matches_url(url)


class TestPacing:
    def test_every_host_has_a_floor_of_at_least_five_seconds(self):
        floors = twmanga.TwmangaSource.host_min_interval
        assert set(floors) == {"www.twmanga.com", "www.twbzmg.com", "s1.bzcdn.net"}
        assert min(floors.values()) >= 5.0

    def test_one_request_at_a_time_whatever_the_global_setting(self, isolated_db):
        from sources import store
        store.set_setting("max_concurrent", 4)
        a = twmanga.TwmangaSource()
        assert a.client.policy.max_concurrent == 1
        assert a.client.policy.host_min_interval["www.twmanga.com"] == 5.0
        assert isinstance(a.client.policy, PacingPolicy)

    def test_capabilities_say_what_was_and_was_not_checked(self):
        caps = twmanga.TwmangaSource(client=make_client("twmanga", ScriptedTransport({}))).capabilities()
        assert caps.technical["browser_required"] is False
        assert caps.terms["tos_prohibited"] is False and "Not reviewed" in caps.terms["tos"]


class TestPasteAndImport:
    """A pasted twmanga link through the real front door and import job."""

    @pytest.fixture
    def routed(self, isolated_db, monkeypatch):
        from .sources_helpers import png
        from sources.http import Response
        pages = [f"https://s1.bzcdn.net/fcomic/{SLUG}/0/0-xxxx/{n}.jpg" for n in (1, 2, 3)]
        routes = {f"{TW}/comic/{SLUG}": html(_fx("series_short.html")),
                  f"{TW}/comic/chapter/{SLUG}/0_0.html": html(_fx("chapter.html"))}
        routes.update({u: Response(200, {"content-type": "image/jpeg"}, png(20, 30, i), "")
                       for i, u in enumerate(pages)})
        transport = ScriptedTransport(routes)

        class Wired(twmanga.TwmangaSource):
            def __init__(self, **kw):
                super().__init__(client=make_client("twmanga", transport), **kw)

        monkeypatch.setattr(registry, "adapter_classes", lambda: {"twmanga": Wired})
        return transport

    def test_series_url_previews_as_a_comic_with_its_chapter_count(self, routed):
        from sources import front_door
        p = front_door.preview(f"{TW}/comic/{SLUG}")
        assert (p.adapter, p.content_type, p.title, p.chapter_count) == \
            ("twmanga", front_door.COMIC, "Test Series", 2)
        assert p.series_id == SLUG

    def test_chapter_url_previews_with_its_ids(self, routed):
        from sources import front_door
        p = front_door.preview(f"{BZ}/comic/chapter/{SLUG}/0_0.html")
        assert (p.adapter, p.content_type, p.series_id, p.chapter_id) == \
            ("twmanga", front_door.COMIC, SLUG, "0_0")

    def test_import_writes_the_chapter_pages_into_a_manhua_drama(self, routed):
        import db
        from sources import pipeline
        did = db.create_drama(title_en="M", media_type="manhua")
        adapter = registry.get_adapter("twmanga")
        chapter = adapter.get_chapters(SLUG)[0]
        pipeline.run_import_job("sourceimport_t", "twmanga", [chapter], did, adapter=adapter)
        assert len(db.list_pages(did)) == 3
        assert all(u.startswith(("https://www.twmanga.com/", "https://s1.bzcdn.net/"))
                   for u in routed.urls())
