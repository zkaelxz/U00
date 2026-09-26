"""
tests/test_sources_guazimanhua.py -- Step 23h: the guazimanhua.com
adapter, against recorded-shape fixtures only (all shapes checked against
the live site while building the adapter -- see
sources/adapters/guazimanhua.py's module docstring, including the
confirmed deviation from the roadmap's "needs the browser-rendered tier"
claim, which no longer holds). No request ever reaches the real site.
"""
import pytest

from sources.adapters import guazimanhua
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = guazimanhua.BASE_URL

LISTING_PAGE = ("<html><body>"
               "<article class='card'>"
               "<a class='cover-wrap' href='/comic.php?id=14109'>"
               "<img class='cover' src='https://img.example.invalid/cover1.webp'>"
               "</a>"
               "<h3><a href='/comic.php?id=14109'>Test Comic Title</a></h3>"
               "<div class='meta'>Test Author · Adventure · Fantasy</div>"
               "</article>"
               "</body></html>")

SERIES_PAGE = ("<html><body>"
              "<section class='mobile-comic-hero'>"
              "<img class='mobile-comic-cover cover' src='https://img.example.invalid/cover1.webp'>"
              "<div class='mobile-comic-hero-info'>"
              "<div class='mobile-comic-title'>Test Comic Title</div>"
              "<p class='mobile-comic-meta'>连载<span>·</span>10话</p>"
              "<p class='mobile-comic-tags'>Adventure / Fantasy</p>"
              "<p class='mobile-comic-desc'>A test comic description.</p>"
              "</div></section>"
              "<div class='cinema-strip'>"
              "<div><span>作者</span><b>Test Author</b></div>"
              "<div><span>分类</span><b>Adventure / Fantasy</b></div>"
              "</div>"
              "<section class='mobile-comic-all-chapters'>"
              "<div class='mobile-chapter-grid'>"
              "<a href='/chapter.php?id=201'>Chapter 2</a>"
              "<a href='/chapter.php?id=101'>Chapter 1</a>"
              "</div></section>"
              "</body></html>")

EMPTY_SERIES_PAGE = "<html><body><div class='not-a-series-page'></div></body></html>"

CHAPTER_PAGE_WITH_PLAIN_IMAGES = ("<html><body>"
                                  "<img src='https://img.example.invalid/cover/other.webp' alt='cover'>"
                                  "<section class='reader-images' data-reader-images>"
                                  "<img id='page-1' class='reading-image' "
                                  "src='https://img.example.invalid/ch101/1.webp' data-page='1'>"
                                  "<img id='page-2' class='reading-image' "
                                  "src='https://img.example.invalid/ch101/2.webp' data-page='2'>"
                                  "</section>"
                                  "</body></html>")

CHAPTER_PAGE_NO_READER_SECTION = "<html><body><p>nothing here</p></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("guazimanhua", t, clock, max_retries=kw.pop("max_retries", 0))
    return guazimanhua.GuazimanhuaSource(client=client, **kw), t


class TestSearch:
    def test_parses_cards(self):
        a, t = _adapter({f"{BASE}/category.php?keyword=test": html(LISTING_PAGE)})
        results = a.search("test")
        assert [r.title for r in results] == ["Test Comic Title"]
        assert results[0].series_id == "14109"


class TestSeries:
    def test_parses_title_description_genres_status_author(self):
        a, t = _adapter({f"{BASE}/comic.php?id=14109": html(SERIES_PAGE)})
        info = a.get_series("14109")
        assert info.title == "Test Comic Title"
        assert info.description == "A test comic description."
        assert info.genres == ["Adventure", "Fantasy"]
        assert info.status == "ongoing"
        assert info.authors == ["Test Author"]

    def test_layout_changed_when_title_is_missing(self):
        a, t = _adapter({f"{BASE}/comic.php?id=14109": html(EMPTY_SERIES_PAGE)})
        with pytest.raises(SourceError):
            a.get_series("14109")


class TestChapters:
    def test_parses_chapter_grid(self):
        a, t = _adapter({f"{BASE}/comic.php?id=14109": html(SERIES_PAGE)})
        chapters = a.get_chapters("14109")
        assert [c.title for c in chapters] == ["Chapter 2", "Chapter 1"]
        assert [c.chapter_id for c in chapters] == ["201", "101"]

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{BASE}/comic.php?id=14109": html(SERIES_PAGE)})
        a.get_series("14109")
        a.get_chapters("14109")
        assert len(t.calls) == 1


class TestPagesArePlainHttpNotBrowserRendered:
    """The roadmap's own text says this site needs the browser-rendered
    tier for get_pages(). That's been independently re-verified as no
    longer true (see the module docstring) -- a real chapter fetch now
    shows page images directly in the static HTML. This test asserts the
    CURRENT, re-verified real behavior: a single plain HTTP GET, images
    read straight out of section.reader-images -- not a
    page_fetch.smart_fetch-shaped mock."""

    def test_extracts_real_page_images_from_a_plain_http_response(self):
        chapter_url = f"{BASE}/chapter.php?id=101"
        a, t = _adapter({chapter_url: html(CHAPTER_PAGE_WITH_PLAIN_IMAGES)})
        chapter = ChapterInfo("guazimanhua", "14109", "101", "Chapter 1", chapter_url)
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == ["https://img.example.invalid/ch101/1.webp",
                                         "https://img.example.invalid/ch101/2.webp"]
        # A cover/recommendation <img> elsewhere on the page must not leak in.
        assert not any("cover/other" in r.url for r in refs)
        # Exactly one plain HTTP request -- no browser-rendering call of any kind.
        assert len(t.calls) == 1

    def test_layout_changed_when_reader_section_is_missing(self):
        chapter_url = f"{BASE}/chapter.php?id=101"
        a, t = _adapter({chapter_url: html(CHAPTER_PAGE_NO_READER_SECTION)})
        chapter = ChapterInfo("guazimanhua", "14109", "101", "Chapter 1", chapter_url)
        with pytest.raises(SourceError):
            a.get_pages(chapter)


class TestParseUrl:
    def test_chapter_url(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url(f"{BASE}/chapter.php?id=101")
        assert kind == "chapter"
        assert chapter.chapter_id == "101"

    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/comic.php?id=14109")
        assert kind == "series"
        assert series_id == "14109"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://guazimanhua.com/comic.php?id=14109")
        assert adapter is not None
        assert adapter.name == "guazimanhua"
