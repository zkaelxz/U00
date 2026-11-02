"""
tests/test_sources_weloma.py -- the Weloma adapter, against small invented
fixtures shaped like the live pages seen 2026-11-02. No request reaches the
real site.
"""
import base64
import json

import pytest

from sources.adapters import weloma
from sources.http_transport import Response
from sources.models import ChapterInfo, PageRef, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = weloma.BASE_URL
SEARCH_URL = f"{BASE}/app/manga/controllers/search.single.php?q=test%20camp"
SERIES_URL = f"{BASE}/m/abc123"
CHAPTER_URL = f"{BASE}/c/ch0002"


def _b64(url):
    return base64.b64encode(url.encode()).decode()


SEARCH = json.dumps([{"header": {"title": "Manga"}, "data": [
    {"primary": "TEST CAMP &amp; CO", "image": "https://s4.ihlv1.xyz/c1.jpg", "url": "/m/abc123"},
    {"primary": "Other", "image": "http://evil/c.jpg", "url": "/m/other9"},
    {"primary": "Dup", "image": "", "url": "/m/abc123"},
    {"primary": "Bad", "image": "", "url": "/m/../etc"}]}])
SERIES = (
    '<html><ol><li class="breadcrumb-item active" aria-current="page">TEST CAMP</li></ol>'
    '<img class="thumbnail" width="180" src="https://s4.ihlv1.xyz/c1.jpg" title="">'
    '<ul class="manga-info"><li><b><i class="fa"></i> Author(s)</b>: <small>'
    "<a class=\"btn btn-xs btn-info\" href='/l/1'>Some Author</a></small></li>"
    '<li><b><i class="fa"></i> Genre(s)</b>: <small>'
    "<a class=\"btn\" href='/l/2'>Action</a>  <a class=\"btn\" href='/l/3'>Comedy</a></small></li>"
    '<li><b><i class="fa"></i> Status</b>: <a href="/manga-on-going.html" class="btn">On going</a></li></ul>'
    '<ul class="list-chapters at-series">'
    '<a href="/c/ch0010" title="Chapter 10"><li>x</li></a>'
    '<a href="/c/ch0002" title="Chapter 2"><li>x</li></a>'
    '<a href="/c/ch0002" title="dup"><li>x</li></a>'
    '<a href="/c/ch0001" title="Chapter 1"><li>x</li></a></ul>'
    '<ul class="side"><a href="/c/zzzzzz" title="Chapter 9 elsewhere"></a></ul></html>')
CHAPTER = (
    "<html><img class='captcha' src='/x'>"
    f"<img class='chapter-img lazyload' src='data:image/gif;base64,R0' data-img='{_b64('https://s4.ihlv1.xyz/i/001_a.webp')}' alt='x'>"
    f"<img class='chapter-img lazyload' src='data:image/gif;base64,R0' data-img='{_b64('https://s4.ihlv1.xyz/i/002_b.jpg')}'>"
    f"<img class='chapter-img lazyload' src='x' data-img='{_b64('https://s4.ihlv1.xyz/i/001_a.webp')}'>"
    f"<img class='chapter-img lazyload' src='x' data-img='{_b64('http://s4.ihlv1.xyz/i/003_plain.jpg')}'>"
    "<img class='chapter-img lazyload' src='x' data-img='%%%not-base64'></html>")


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return weloma.WelomaSource(client=make_client("weloma", t, clock, max_retries=0)), t


class TestSearch:
    def test_parses_rows_and_skips_duplicates_and_unsafe_codes(self):
        a, _ = _adapter({SEARCH_URL: html(SEARCH)})
        results = a.search(" test camp ")
        assert [r.series_id for r in results] == ["abc123", "other9"]
        assert results[0].title == "TEST CAMP & CO"
        assert results[0].url == SERIES_URL
        assert results[0].cover_url == "https://s4.ihlv1.xyz/c1.jpg"
        assert results[1].cover_url == ""

    def test_later_pages_and_blank_queries_make_no_request(self):
        a, _ = _adapter({})
        assert a.search("camp", page=2) == [] and a.search("  ") == []

    def test_a_non_json_answer_is_a_layout_change(self):
        a, _ = _adapter({SEARCH_URL: html("<html>new</html>")})
        with pytest.raises(weloma.LayoutChanged):
            a.search("test camp")


class TestSeries:
    def test_parses_details(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        info = a.get_series("abc123")
        assert (info.title, info.authors, info.genres) == ("TEST CAMP", ["Some Author"], ["Action", "Comedy"])
        assert info.status == "ongoing" and info.language == "ja"
        assert info.cover_url == "https://s4.ihlv1.xyz/c1.jpg"

    def test_missing_title_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(weloma.LayoutChanged):
            a.get_series("abc123")

    def test_unsafe_series_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(weloma.LayoutChanged):
            a.get_series("../etc")


class TestChapters:
    def test_oldest_first_deduplicated_and_only_the_series_list(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        chapters = a.get_chapters("abc123")
        assert [c.chapter_id for c in chapters] == ["ch0001", "ch0002", "ch0010"]
        assert chapters[1].title == "Chapter 2" and chapters[1].url == CHAPTER_URL

    def test_no_chapters_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(weloma.LayoutChanged):
            a.get_chapters("abc123")


class TestPages:
    def test_decodes_https_urls_in_order_without_repeats_or_junk(self):
        a, _ = _adapter({CHAPTER_URL: html(CHAPTER)})
        pages = a.get_pages(ChapterInfo("weloma", "abc123", "ch0002", "Chapter 2", CHAPTER_URL))
        assert [p.url for p in pages] == ["https://s4.ihlv1.xyz/i/001_a.webp",
                                          "https://s4.ihlv1.xyz/i/002_b.jpg"]
        assert pages[0].headers == {"Referer": weloma.REFERER}

    def test_no_images_is_a_layout_change(self):
        a, _ = _adapter({CHAPTER_URL: html("<html></html>")})
        with pytest.raises(weloma.LayoutChanged):
            a.get_pages(ChapterInfo("weloma", "abc123", "ch0002", "Chapter 2", CHAPTER_URL))

    def test_unsafe_chapter_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(weloma.LayoutChanged):
            a.get_pages(ChapterInfo("weloma", "abc123", "../x", "x", ""))

    def test_download_returns_bytes_and_extension(self):
        url = "https://s4.ihlv1.xyz/i/001_a.webp"
        a, _ = _adapter({url: Response(200, {"content-type": "image/webp"}, b"WEBP", "")})
        assert a.download_page(PageRef("weloma", "c", 0, url)) == (b"WEBP", ".webp")

    def test_download_refuses_a_non_image(self):
        url = "https://s4.ihlv1.xyz/i/001_a.jpg"
        a, _ = _adapter({url: Response(200, {"content-type": "text/html"}, b"blocked", "")})
        with pytest.raises(SourceError):
            a.download_page(PageRef("weloma", "c", 0, url))


class TestParseUrl:
    def test_series_and_foreign_urls(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/m/abc123") == ("series", "abc123")
        assert a.parse_url(f"{BASE}/c/ch0002") is None
        assert a.parse_url(f"{BASE}/account/login") is None
        assert a.parse_url("https://example.com/m/abc123") is None
