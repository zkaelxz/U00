"""
tests/test_sources_mangak.py -- the MangaK adapter, against small invented
fixtures shaped like the live pages and chapter-list JSON seen 2026-10-03.
No request reaches the real site.
"""
import json

import pytest

from sources.adapters import mangak
from sources.http import FetchFailed, Response
from sources.models import ChapterInfo, PageRef, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = mangak.BASE_URL
API = mangak.API_URL


def _next_page(page_props: dict) -> str:
    data = json.dumps({"props": {"pageProps": page_props}, "page": "/x", "buildId": "b"})
    return (f'<!DOCTYPE html><html><head><title>MangaK</title></head><body><div id="__next"></div>'
            f'<script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>')


def _json(payload, status: int = 200) -> Response:
    return Response(status, {"content-type": "application/json"}, json.dumps(payload).encode(), "")


MANGA = {
    "id": "z8NokzY7", "slug": "test-camp", "name": "Test Camp", "cv": 1789905062214,
    "cover": "https://rx.example.invalid/covers/abc.webp", "status": "Ongoing",
    "summary": "A camping story.", "type": {"name": "manga", "slug": "manga"},
    "authors": [{"name": "Some Author"}], "genres": [{"name": "Slice of life"}, {"name": "Comedy"}],
    "isAdult": False,
}

SEARCH = {"ssrItems": [
    {"slug": "test-camp", "name": "Test Camp", "cover": "https://rx.example.invalid/c1.webp",
     "status": "ongoing", "isAdult": True, "stats": {"chaptersCount": 3}},
    {"slug": "../escape", "name": "Bad slug"},
    {"slug": "other-title", "name": "Other Title", "cover": "javascript:alert(1)"},
], "ssrPagination": {"page": 1, "has_next": True}}

CHAPTERS = {"success": True, "data": {"chapters": [
    {"id": "c3", "slug": "chapter-3", "name": "Chapter 3", "number": 3},
    {"id": "c2", "slug": "chapter-2", "name": "Chapter 2", "number": 2},
    {"id": "c1", "slug": "vol-1-chapter-1-start", "name": "Vol.1 Chapter 1: Start", "number": 1},
]}}

CHAPTER = {"initialChapter": {
    "slug": "chapter-2", "name": "Chapter 2",
    "images": ["https://rx.qvzrc.example.invalid/r/p/1.webp", "https://rx.qvzrd.example.invalid/r/p/2.webp"],
    "pages": [{"url": "https://rx.qvzrc.example.invalid/r/p/1.webp", "width": 1120, "height": 1600},
              {"url": "https://rx.qvzrd.example.invalid/r/p/2.webp", "width": 1120, "height": 1600}],
}}

SERIES_URL = f"{BASE}/test-camp"
CHAPTERS_URL = f"{API}/titles/z8NokzY7/chapters?cv=1789905062214"


def _adapter(routes, max_retries=0):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("mangak", t, clock, max_retries=max_retries)
    return mangak.MangaKSource(client=client), t


class TestSearch:
    def test_parses_results_and_skips_unsafe_slugs_and_covers(self):
        a, t = _adapter({f"{BASE}/search?q=test%20camp": html(_next_page(SEARCH))})
        results = a.search(" test camp ")
        assert [r.series_id for r in results] == ["test-camp", "other-title"]
        assert results[0].url == SERIES_URL
        assert results[0].cover_url == "https://rx.example.invalid/c1.webp"
        assert results[0].extra == {"status": "ongoing", "adult": True, "chapters": 3}
        assert results[1].cover_url == ""

    def test_second_page(self):
        a, t = _adapter({f"{BASE}/search?q=camp&page=2": html(_next_page(SEARCH))})
        assert len(a.search("camp", page=2)) == 2

    def test_missing_next_data_is_a_layout_change(self):
        a, t = _adapter({f"{BASE}/search?q=camp": html("<html><body>new site</body></html>")})
        with pytest.raises(mangak.LayoutChanged) as e:
            a.search("camp")
        assert e.value.reason.value == "LAYOUT_CHANGED"


class TestSeries:
    def test_parses_details(self):
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": MANGA}))})
        info = a.get_series("test-camp")
        assert info.title == "Test Camp"
        assert info.authors == ["Some Author"]
        assert info.genres == ["Slice of life", "Comedy"]
        assert info.status == "ongoing"
        assert info.content_type == "manga"
        assert info.language == "en"
        assert info.cover_url == "https://rx.example.invalid/covers/abc.webp"

    def test_missing_manga_is_a_layout_change(self):
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": None}))})
        with pytest.raises(mangak.LayoutChanged):
            a.get_series("test-camp")

    def test_unsafe_series_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(mangak.LayoutChanged):
            a.get_series("../etc")
        assert t.calls == []

    def test_missing_series_is_not_found(self):
        a, t = _adapter({})
        with pytest.raises(FetchFailed) as e:
            a.get_series("no-such-title")
        assert e.value.reason.value == "NOT_FOUND"


class TestChapters:
    def test_full_list_from_the_api_in_reading_order(self):
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": MANGA})),
                         CHAPTERS_URL: _json(CHAPTERS)})
        chapters = a.get_chapters("test-camp")
        assert [c.chapter_id for c in chapters] == ["vol-1-chapter-1-start", "chapter-2", "chapter-3"]
        assert chapters[0].title == "Vol.1 Chapter 1: Start"
        assert chapters[1].url == f"{BASE}/test-camp/chapter-2"

    def test_series_and_chapters_share_the_series_fetch(self):
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": MANGA})),
                         CHAPTERS_URL: _json(CHAPTERS)})
        a.get_series("test-camp")
        a.get_chapters("test-camp")
        assert t.urls() == [SERIES_URL, CHAPTERS_URL]

    def test_empty_list_is_an_error_not_an_empty_import(self):
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": MANGA})),
                         CHAPTERS_URL: _json({"success": True, "data": {"chapters": []}})})
        with pytest.raises(mangak.LayoutChanged):
            a.get_chapters("test-camp")

    def test_unsafe_chapter_slug_is_refused(self):
        bad = {"data": {"chapters": [{"slug": "../../x", "name": "x", "number": 1}]}}
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": MANGA})),
                         CHAPTERS_URL: _json(bad)})
        with pytest.raises(mangak.LayoutChanged):
            a.get_chapters("test-camp")

    def test_server_error_is_retried_then_succeeds(self):
        a, t = _adapter({SERIES_URL: html(_next_page({"initialManga": MANGA})),
                         CHAPTERS_URL: [_json({}, status=502), _json(CHAPTERS)]}, max_retries=1)
        assert len(a.get_chapters("test-camp")) == 3
        assert t.urls().count(CHAPTERS_URL) == 2


class TestPages:
    def test_pages_carry_the_referer_the_cdn_needs(self):
        a, t = _adapter({f"{BASE}/test-camp/chapter-2": html(_next_page(CHAPTER))})
        refs = a.get_pages(ChapterInfo("mangak", "test-camp", "chapter-2", "Chapter 2"))
        assert [r.url for r in refs] == ["https://rx.qvzrc.example.invalid/r/p/1.webp",
                                         "https://rx.qvzrd.example.invalid/r/p/2.webp"]
        assert all(r.headers == {"Referer": "https://mangak.io/"} for r in refs)

    def test_no_images_is_a_layout_change(self):
        a, t = _adapter({f"{BASE}/test-camp/chapter-2": html(_next_page({"initialChapter": {}}))})
        with pytest.raises(mangak.LayoutChanged):
            a.get_pages(ChapterInfo("mangak", "test-camp", "chapter-2", "Chapter 2"))

    def test_download_page_sends_referer_and_keeps_the_extension(self):
        url = "https://rx.qvzrc.example.invalid/r/p/1.webp"
        a, t = _adapter({url: Response(200, {"content-type": "image/webp"}, b"RIFFxxxxWEBP", "")})
        data, ext = a.download_page(PageRef("mangak", "chapter-2", 0, url,
                                            headers={"Referer": "https://mangak.io/"}))
        assert data == b"RIFFxxxxWEBP"
        assert ext == ".webp"
        assert t.calls[0]["headers"]["Referer"] == "https://mangak.io/"


    def test_a_page_that_is_not_an_image_is_refused(self):
        url = "https://rx.qvzrc.example.invalid/r/p/1.webp"
        a, t = _adapter({url: Response(200, {"Content-Type": "text/html; charset=utf-8"},
                                       b"<html>blocked</html>", "")})
        with pytest.raises(SourceError) as e:
            a.download_page(PageRef("mangak", "chapter-2", 0, url))
        assert "not an image" in str(e.value)


class TestParseUrl:
    def test_series_and_chapter_urls(self):
        a, t = _adapter({})
        assert a.parse_url(f"{BASE}/test-camp") == ("series", "test-camp")
        kind, chapter = a.parse_url(f"https://mangak.io/test-camp/chapter-2?x=1")
        assert kind == "chapter"
        assert (chapter.series_id, chapter.chapter_id) == ("test-camp", "chapter-2")

    def test_site_pages_and_other_hosts_are_not_series(self):
        a, t = _adapter({})
        assert a.parse_url(f"{BASE}/search?q=x") is None
        assert a.parse_url(f"{BASE}/terms-of-service") is None
        assert a.parse_url("https://notmangak.io/test-camp") is None
        assert a.parse_url(f"{BASE}/a/b/c") is None

    def test_matches_url(self):
        assert mangak.MangaKSource.matches_url("https://mangak.io/test-camp")
        assert not mangak.MangaKSource.matches_url("https://mangak.io.evil.example/test-camp")


def test_terms_finding_is_recorded():
    caps = mangak.MangaKSource(client=make_client("mangak", ScriptedTransport({}))).capabilities()
    assert caps.automation_permission == "EXPLICITLY_RESTRICTED"
    assert caps.terms["tos_prohibited"] is True


def test_page_props_rejects_broken_json():
    with pytest.raises(SourceError):
        mangak.page_props('<script id="__NEXT_DATA__" type="application/json">{not json</script>')
