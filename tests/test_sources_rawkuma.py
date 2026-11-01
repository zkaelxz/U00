"""
tests/test_sources_rawkuma.py -- the Rawkuma adapter, against small invented
fixtures shaped like the live pages seen 2026-11-01. No request reaches the
real site.
"""
import json

import pytest

from sources.adapters import rawkuma
from sources.http_transport import Response
from sources.models import ChapterInfo, PageRef, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = rawkuma.BASE_URL
AJAX = rawkuma.AJAX_URL
NONCE_URL = f"{AJAX}?type=search_form&action=get_nonce"
SEARCH_URL = f"{AJAX}?nonce=abc123def4&action=search"
SERIES_URL = f"{BASE}/manga/test-camp/"
CHAPTER_URL = f"{BASE}/manga/test-camp/chapter-2.200/"

NONCE = "<input type='hidden' name='search_nonce' value='abc123def4'>"
SEARCH = (
    '<div id="searchResults"> <a href="https://rawkuma.net/manga/test-camp/" class="x">'
    '<img src="https://rawkuma.net/wp-content/uploads/c1.jpg" alt="Test Camp">'
    '<div><h3 class="t">Test Camp &amp; Co</h3><p>desc</p></div></a>'
    '<a href="https://rawkuma.net/manga/other-title/"><img src="javascript:alert(1)">'
    '<h3>Other Title</h3></a>'
    '<a href="https://rawkuma.net/manga/test-camp/"><h3>Dup</h3></a></div>')

_LD = {"@type": ["Book", "ComicSeries"], "name": "Test Camp", "description": "A camping story.",
       "image": {"@type": "ImageObject", "url": "https://rawkuma.net/wp-content/uploads/c1.jpg"},
       "author": {"@type": "Person", "name": "Some Author"}, "genre": ["Action", "Comedy"],
       "isCompleted": "False"}
SERIES = (f'<html><script type="application/ld+json">{json.dumps(_LD)}</script><body>'
          '<a href=" https://rawkuma.net/manga/test-camp/chapter-10.300/ ">10</a>'
          '<a href=" https://rawkuma.net/manga/test-camp/chapter-2.200/ ">2</a>'
          '<a href=" https://rawkuma.net/manga/test-camp/chapter-2.200/ ">dup</a>'
          '<a href=" https://rawkuma.net/manga/test-camp/chapter-1.5.100/ ">1.5</a>'
          '<a href=" https://rawkuma.net/manga/other/chapter-1.9/ ">elsewhere</a></body></html>')
CHAPTER = ("<html><img src='https://rawkuma.net/wp-content/uploads/logo.png'>"
           "<img src='https://cdn.rawkuma.com/b/test-camp/chapter-2/1.jpg'>"
           "<img src='https://cdn.rawkuma.com/b/test-camp/chapter-2/2.jpg'>"
           "<img src='https://cdn.rawkuma.com/b/test-camp/chapter-2/1.jpg'></html>")


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return rawkuma.RawkumaSource(client=make_client("rawkuma", t, clock, max_retries=0)), t


class TestSearch:
    def test_parses_cards_and_skips_duplicates(self):
        a, _ = _adapter({NONCE_URL: html(NONCE), SEARCH_URL: html(SEARCH)})
        results = a.search(" camp ")
        assert [r.series_id for r in results] == ["test-camp", "other-title"]
        assert results[0].title == "Test Camp & Co"
        assert results[0].url == SERIES_URL
        assert results[0].cover_url == "https://rawkuma.net/wp-content/uploads/c1.jpg"
        assert results[1].cover_url == ""

    def test_later_pages_are_empty_without_a_request(self):
        a, t = _adapter({})
        assert a.search("camp", page=2) == []

    def test_missing_token_is_a_layout_change(self):
        a, _ = _adapter({NONCE_URL: html("<html>new</html>")})
        with pytest.raises(rawkuma.LayoutChanged):
            a.search("camp")


class TestSeries:
    def test_parses_details(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        info = a.get_series("test-camp")
        assert (info.title, info.authors, info.genres) == ("Test Camp", ["Some Author"], ["Action", "Comedy"])
        assert info.status == "ongoing" and info.language == "ja"

    def test_missing_details_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(rawkuma.LayoutChanged):
            a.get_series("test-camp")

    def test_unsafe_series_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(rawkuma.LayoutChanged):
            a.get_series("../etc")


class TestChapters:
    def test_sorted_oldest_first_deduplicated_and_scoped_to_the_series(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        chapters = a.get_chapters("test-camp")
        assert [c.chapter_id for c in chapters] == ["chapter-1.5.100", "chapter-2.200", "chapter-10.300"]
        assert chapters[1].title == "Chapter 2"
        assert chapters[1].url == CHAPTER_URL

    def test_no_chapters_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(rawkuma.LayoutChanged):
            a.get_chapters("test-camp")


class TestPages:
    def test_only_cdn_images_in_order_without_repeats(self):
        a, _ = _adapter({CHAPTER_URL: html(CHAPTER)})
        pages = a.get_pages(ChapterInfo("rawkuma", "test-camp", "chapter-2.200", "Chapter 2", CHAPTER_URL))
        assert [p.url for p in pages] == ["https://cdn.rawkuma.com/b/test-camp/chapter-2/1.jpg",
                                          "https://cdn.rawkuma.com/b/test-camp/chapter-2/2.jpg"]
        assert pages[0].headers == {"Referer": rawkuma.REFERER}

    def test_download_returns_bytes_and_extension(self):
        url = "https://cdn.rawkuma.com/b/test-camp/chapter-2/1.jpg"
        a, _ = _adapter({url: Response(200, {"content-type": "image/jpeg"}, b"JPEG", "")})
        assert a.download_page(PageRef("rawkuma", "c", 0, url)) == (b"JPEG", ".jpg")

    def test_download_refuses_a_non_image(self):
        url = "https://cdn.rawkuma.com/b/x/1.jpg"
        a, _ = _adapter({url: Response(200, {"content-type": "text/html"}, b"blocked", "")})
        with pytest.raises(SourceError):
            a.download_page(PageRef("rawkuma", "c", 0, url))


class TestParseUrl:
    def test_series_chapter_and_foreign_urls(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/manga/test-camp/") == ("series", "test-camp")
        kind, ch = a.parse_url(f"{BASE}/manga/test-camp/chapter-2.200/")
        assert kind == "chapter" and ch.chapter_id == "chapter-2.200"
        assert a.parse_url(f"{BASE}/library/") is None
        assert a.parse_url("https://example.com/manga/x/") is None
