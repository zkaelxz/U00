"""
tests/test_sources_toonkor.py -- Step 23h: the ToonKor adapter, against
recorded-shape fixtures only (all shapes checked against the live site
while building the adapter -- see sources/adapters/toonkor.py's module
docstring). No request ever reaches the real site.
"""
import base64

import pytest

from sources.adapters import toonkor
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = toonkor.BASE_URL

SEARCH_PAGE = ("<html><body>"
              "<div class='section-item'><div class='section-item-inner'>"
              "<div class='section-item-photo'><img src='/data/wtoon/thumb1.jpg'></div>"
              "<div class='section-item-title'>"
              "<a id='title' href='/test-webtoon-slug' alt='Test Webtoon Title'>"
              "<h3>Test Webtoon Title</h3></a></div></div></div>"
              "</body></html>")

SERIES_PAGE = ("<html><body>"
              "<table class='bt_view1'><tr>"
              "<td class='bt_thumb'><img src='/data/wtoon/cover1.jpg'></td>"
              "<td class='bt_title'>Test Webtoon Title</td>"
              "<td class='bt_over'>A test webtoon description.</td>"
              "</tr></table>"
              "<table class='web_list'>"
              "<tr class='tborder'>"
              "<td width='2%' name='view_list' data-role='/test-webtoon-slug_1.html'>"
              "<img src='/img/read5.png'></td>"
              "<td class='content__title' name='view_list' data-role='/test-webtoon-slug_1.html'>"
              "1화</td>"
              "<td class='episode__index' name='view_list' data-role='/test-webtoon-slug_1.html'>"
              "2026-01-01</td></tr>"
              "<tr class='tborder'>"
              "<td width='2%' name='view_list' data-role='/test-webtoon-slug_2.html'>"
              "<img src='/img/read5.png'></td>"
              "<td class='content__title' name='view_list' data-role='/test-webtoon-slug_2.html'>"
              "2화</td>"
              "<td class='episode__index' name='view_list' data-role='/test-webtoon-slug_2.html'>"
              "2026-01-02</td></tr>"
              "</table>"
              "</body></html>")

EMPTY_SERIES_PAGE = "<html><body><div class='not-a-series-page'></div></body></html>"


def _make_toon_img_html(image_urls):
    fragment = "".join(f"<img alt='page {i+1}' src=\"{u}\">" for i, u in enumerate(image_urls))
    b64 = base64.b64encode(fragment.encode("utf-8")).decode("ascii")
    return f"<html><body><div id='toon_img'></div><script>var toon_img = '{b64}';</script></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("toonkor", t, clock, max_retries=kw.pop("max_retries", 0))
    return toonkor.ToonkorSource(client=client, **kw), t


class TestSearch:
    def test_parses_search_results(self):
        a, t = _adapter({f"{BASE}/bbs/search.php?sfl=wr_subject%7C%7Cwr_content&stx=test":
                         html(SEARCH_PAGE)})
        results = a.search("test")
        assert [r.title for r in results] == ["Test Webtoon Title"]
        assert results[0].series_id == "test-webtoon-slug"


class TestSeries:
    def test_parses_title_and_description(self):
        a, t = _adapter({f"{BASE}/test-webtoon-slug": html(SERIES_PAGE)})
        info = a.get_series("test-webtoon-slug")
        assert info.title == "Test Webtoon Title"
        assert info.description == "A test webtoon description."

    def test_layout_changed_when_table_is_missing(self):
        a, t = _adapter({f"{BASE}/test-webtoon-slug": html(EMPTY_SERIES_PAGE)})
        with pytest.raises(SourceError):
            a.get_series("test-webtoon-slug")


class TestChapters:
    def test_parses_chapter_list_from_data_role(self):
        a, t = _adapter({f"{BASE}/test-webtoon-slug": html(SERIES_PAGE)})
        chapters = a.get_chapters("test-webtoon-slug")
        assert [c.title for c in chapters] == ["1화", "2화"]
        assert [c.chapter_id for c in chapters] == ["test-webtoon-slug_1", "test-webtoon-slug_2"]

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{BASE}/test-webtoon-slug": html(SERIES_PAGE)})
        a.get_series("test-webtoon-slug")
        a.get_chapters("test-webtoon-slug")
        assert len(t.calls) == 1


class TestPageImageDecoding:
    """The roadmap's own exit condition: correctly decode a sample
    Base64-encoded toon_img blob and extract the right image URLs."""

    def test_decodes_a_base64_toon_img_blob_to_image_urls(self):
        urls = ["https://cdn.example.invalid/data/ch1/001.jpg",
               "https://cdn.example.invalid/data/ch1/002.jpg",
               "https://cdn.example.invalid/data/ch1/003.jpg"]
        page = _make_toon_img_html(urls)
        assert toonkor.decode_toon_img(page) == urls

    def test_get_pages_returns_page_refs_in_order(self):
        urls = ["https://cdn.example.invalid/data/ch1/001.jpg",
               "https://cdn.example.invalid/data/ch1/002.jpg"]
        chapter_url = f"{BASE}/test-webtoon-slug_1.html"
        a, t = _adapter({chapter_url: html(_make_toon_img_html(urls))})
        chapter = ChapterInfo("toonkor", "test-webtoon-slug", "test-webtoon-slug_1",
                              "1화", chapter_url)
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == urls
        assert [r.index for r in refs] == [0, 1]

    def test_layout_changed_when_toon_img_script_is_missing(self):
        chapter_url = f"{BASE}/test-webtoon-slug_1.html"
        a, t = _adapter({chapter_url: html("<html><body>no script here</body></html>")})
        chapter = ChapterInfo("toonkor", "test-webtoon-slug", "test-webtoon-slug_1",
                              "1화", chapter_url)
        with pytest.raises(SourceError):
            a.get_pages(chapter)

    def test_layout_changed_when_decoded_payload_has_no_images(self):
        b64 = base64.b64encode(b"<p>no images here</p>").decode("ascii")
        page = f"<html><body><script>var toon_img = '{b64}';</script></body></html>"
        with pytest.raises(SourceError):
            toonkor.decode_toon_img(page)


class TestParseUrl:
    def test_chapter_url(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url(f"{BASE}/test-webtoon-slug_1.html")
        assert kind == "chapter"
        assert chapter.chapter_id == "test-webtoon-slug_1"

    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/test-webtoon-slug")
        assert kind == "series"
        assert series_id == "test-webtoon-slug"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://toonkor0.org/test-webtoon-slug_1.html")
        assert adapter is not None
        assert adapter.name == "toonkor"
