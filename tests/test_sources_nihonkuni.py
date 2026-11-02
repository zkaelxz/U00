"""
tests/test_sources_nihonkuni.py -- the NihonKuni adapter, against small invented
fixtures shaped like the live pages seen 2026-11-02. No request reaches the
real site.
"""
import pytest

from sources.adapters import nihonkuni
from sources.http_transport import Response
from sources.models import ChapterInfo, PageRef, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = nihonkuni.BASE_URL
SEARCH_URL = f"{BASE}/manga-list.html?name=test%20camp&page=1"
SERIES_URL = f"{BASE}/manga-test-camp.html"
CHAPTER_URL = f"{BASE}/read-test-camp-chapter-2.html"

SEARCH = (
    '<div class="manga-grid"><div class="manga-card">'
    '<a class="manga-cover" href="read-test-camp-chapter-10.html" '
    'style="background-image: url(\'https://s4.ihlv1.xyz/c1.jpg" data-pagespeed-url-hash="1" '
    'onload="x;?imgmax=200\'), url(\'uploads/lazy-loading.gif\');"></a>'
    '<div class="manga-info"><a class="manga-title" href="manga-test-camp.html">TEST CAMP &amp; CO</a>'
    '<a class="manga-chap" href="read-test-camp-chapter-10.html">Last chapter: 10</a></div></div></div>'
    '<div class="manga-card"><a class="manga-cover" href="read-x.html" style="background-image: url(\'http://evil/c.jpg\')"></a>'
    '<div class="manga-info"><a class="manga-title" href="manga-other-title.html">Other</a></div></div></div>'
    '<div class="manga-card"><div class="manga-info"><a class="manga-title" href="manga-test-camp.html">Dup</a>'
    '</div></div></div>'
    '<div class="manga-card"><div class="manga-info"><a class="manga-title" href="manga-../etc.html">Bad</a>'
    '</div></div></div>')

SERIES = (
    '<html><head><meta property="og:title" content="TEST CAMP - Read TEST CAMP Raw Manga Online - NihonKuni.com" />'
    '<meta property="og:image" content="https://s4.ihlv1.xyz/c1.jpg" /></head><body>'
    '<img src="/x.gif" data-original="https://s4.ihlv1.xyz/c1.jpg" alt="TEST CAMP" title="テストキャンプ" class="manga-cover-image">'
    "<a class=\"btn btn-xs btn-info\" href='manga-author-Some Author.html'>Some Author</a>"
    "<a class=\"btn btn-xs btn-danger\" href='manga-list-genre-action.html'>Action</a> "
    "<a class=\"btn btn-xs btn-danger\" href='manga-list-genre-comedy.html'>Comedy</a>"
    '<div class="info-field-label">Status</div>\n<div class="info-field-value">'
    '<a href="/manga-on-going.html" class="btn btn-xs btn-success">On going</a></div>'
    '<div id="chapters_raw_data">'
    '<a href="read-test-camp-chapter-10.html" title="x"><span class="chapter-name">Chapter 10</span></a>'
    '<a href="read-test-camp-chapter-2.html" title="x"><span class="chapter-name">Chapter 2</span></a>'
    '<a href="read-test-camp-chapter-2.html" title="x"><span class="chapter-name">dup</span></a>'
    '<a href="read-test-camp-chapter-1.5.html" title="x"><span class="chapter-name">Chapter 1.5</span></a>'
    '<a href="read-other-chapter-1.html" title="x"><span class="chapter-name">elsewhere</span></a>'
    '</div></body></html>')
CHAPTER = (
    '<html><img src="https://s4.ihlv1.xyz/cover.png" class="manga-cover-thumb">'
    '<img id="page1" class="chapter-img" src="https://s4.ihlv1.xyz/i/001_a.webp" alt="x">'
    '<img\n id="page2"\n class="chapter-img"\n src="https://s4.ihlv1.xyz/i/002_b.jpg"\n loading="eager">'
    '<img id="page1" class="chapter-img" src="https://s4.ihlv1.xyz/i/001_a.webp">'
    '<img class="chapter-img" src="http://s4.ihlv1.xyz/i/003_plain.jpg"></html>')


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return nihonkuni.NihonKuniSource(client=make_client("nihonkuni", t, clock, max_retries=0)), t


class TestSearch:
    def test_parses_cards_and_skips_duplicates_and_unsafe_slugs(self):
        a, _ = _adapter({SEARCH_URL: html(SEARCH)})
        results = a.search(" test camp ")
        assert [r.series_id for r in results] == ["test-camp", "other-title"]
        assert results[0].title == "TEST CAMP & CO"
        assert results[0].url == SERIES_URL
        assert results[0].cover_url == "https://s4.ihlv1.xyz/c1.jpg"
        assert results[1].cover_url == ""

    def test_blank_query_makes_no_request(self):
        a, t = _adapter({})
        assert a.search("  ") == []


class TestSeries:
    def test_parses_details(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        info = a.get_series("test-camp")
        assert (info.title, info.authors, info.genres) == ("テストキャンプ", ["Some Author"], ["Action", "Comedy"])
        assert info.status == "ongoing" and info.language == "ja"
        assert info.cover_url == "https://s4.ihlv1.xyz/c1.jpg"

    def test_falls_back_to_the_page_title_without_a_native_title(self):
        page = SERIES.replace('title="テストキャンプ" ', "")
        a, _ = _adapter({SERIES_URL: html(page)})
        assert a.get_series("test-camp").title == "TEST CAMP"

    def test_missing_title_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(nihonkuni.LayoutChanged):
            a.get_series("test-camp")

    def test_unsafe_series_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(nihonkuni.LayoutChanged):
            a.get_series("../etc")


class TestChapters:
    def test_sorted_oldest_first_deduplicated_and_scoped_to_the_series(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        chapters = a.get_chapters("test-camp")
        assert [c.chapter_id for c in chapters] == ["1.5", "2", "10"]
        assert chapters[1].title == "Chapter 2"
        assert chapters[1].url == CHAPTER_URL

    def test_no_chapters_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(nihonkuni.LayoutChanged):
            a.get_chapters("test-camp")


class TestPages:
    def test_only_https_chapter_images_in_order_without_repeats(self):
        a, _ = _adapter({CHAPTER_URL: html(CHAPTER)})
        pages = a.get_pages(ChapterInfo("nihonkuni", "test-camp", "2", "Chapter 2", CHAPTER_URL))
        assert [p.url for p in pages] == ["https://s4.ihlv1.xyz/i/001_a.webp",
                                          "https://s4.ihlv1.xyz/i/002_b.jpg"]
        assert pages[0].headers == {"Referer": nihonkuni.REFERER}

    def test_no_images_is_a_layout_change(self):
        a, _ = _adapter({CHAPTER_URL: html("<html></html>")})
        with pytest.raises(nihonkuni.LayoutChanged):
            a.get_pages(ChapterInfo("nihonkuni", "test-camp", "2", "Chapter 2", CHAPTER_URL))

    def test_unsafe_chapter_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(nihonkuni.LayoutChanged):
            a.get_pages(ChapterInfo("nihonkuni", "test-camp", "../x", "x", ""))

    def test_download_returns_bytes_and_extension(self):
        url = "https://s4.ihlv1.xyz/i/001_a.webp"
        a, _ = _adapter({url: Response(200, {"content-type": "image/webp"}, b"WEBP", "")})
        assert a.download_page(PageRef("nihonkuni", "c", 0, url)) == (b"WEBP", ".webp")

    def test_download_refuses_a_non_image(self):
        url = "https://s4.ihlv1.xyz/i/001_a.jpg"
        a, _ = _adapter({url: Response(200, {"content-type": "text/html"}, b"blocked", "")})
        with pytest.raises(SourceError):
            a.download_page(PageRef("nihonkuni", "c", 0, url))


class TestParseUrl:
    def test_series_chapter_and_foreign_urls(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/manga-test-camp.html") == ("series", "test-camp")
        assert a.parse_url(f"{BASE}/manga/test-camp-35.html") == ("series", "test-camp")
        kind, ch = a.parse_url(f"{BASE}/read-test-camp-chapter-2.html")
        assert kind == "chapter" and (ch.series_id, ch.chapter_id) == ("test-camp", "2")
        assert ch.url == CHAPTER_URL
        assert a.parse_url(f"{BASE}/account/login") is None
        assert a.parse_url("https://example.com/manga-test-camp.html") is None
