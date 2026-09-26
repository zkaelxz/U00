"""
tests/test_sources_miaoqumh.py -- Step 23h: the miaoqumh.org adapter,
against recorded-shape fixtures only (all shapes -- including the full
base64/XOR/base64/JSON decode pipeline -- checked against the live site
while building the adapter; see sources/adapters/miaoqumh.py's module
docstring). No request ever reaches the real site.
"""
import base64
import json

import pytest

from sources.adapters import miaoqumh
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

BASE = miaoqumh.BASE_URL
MOBILE_BASE = miaoqumh.MOBILE_BASE_URL

SERIES_PAGE = ("<html><body>"
              "<div class='infocomic'>"
              "<div class='infobox'>"
              "<div class='title'>Test Manhua Title</div>"
              "<div class='info'>"
              "<div class='img'><img src='https://img.example.invalid/cover1.jpg'></div>"
              "<p class='tage'>更新至：第10话</p>"
              "<p class='tage'>作者：Test Author</p>"
              "<p class='tage'>类型：<a href='/category/tags/1'>Adventure</a> "
              "<a href='/category/tags/2'>Fantasy</a></p>"
              "<p class='tage'>更新于：2026-01-01</p>"
              "</div></div>"
              "<div class='text'>A test manhua description.</div>"
              "</div>"
              "<ul class='list'>"
              "<li><a href='/1001/2001.html'>Chapter 1</a></li>"
              "<li><a href='/1001/2002.html'>Chapter 2</a></li>"
              "</ul>"
              "</body></html>")

EMPTY_SERIES_PAGE = "<html><body><div class='not-a-series-page'></div></body></html>"


def _make_chapter_page(cid: int, page_urls: list) -> str:
    """Builds a chapter page whose `var DATA='...'` payload round-trips
    through the exact same base64/XOR/base64 pipeline the real site uses
    -- the inverse of decode_page_data(), used only to build test
    fixtures (never used by the adapter itself)."""
    items = [{"id": str(cid * 10 + i), "url": u} for i, u in enumerate(page_urls)]
    plain_json = json.dumps(items).encode("utf-8")
    inner_b64 = base64.b64encode(plain_json)
    key = miaoqumh._XOR_KEYS[cid % 10].encode("ascii")
    xored = bytes(b ^ key[i % 8] for i, b in enumerate(inner_b64))
    outer_b64 = base64.b64encode(xored).decode("ascii")
    return f"<html><body><script>var DATA='{outer_b64}';</script></body></html>"


def _adapter(routes, clock=None, **kw):
    clock = clock or FakeClock()
    t = ScriptedTransport(routes, clock)
    client = make_client("miaoqumh", t, clock, max_retries=kw.pop("max_retries", 0))
    return miaoqumh.MiaoqumhSource(client=client, **kw), t


class TestSeries:
    def test_parses_title_description_author_genre(self):
        a, t = _adapter({f"{MOBILE_BASE}/test-slug": html(SERIES_PAGE)})
        info = a.get_series("test-slug")
        assert info.title == "Test Manhua Title"
        assert info.authors == ["Test Author"]
        assert info.genres == ["Adventure", "Fantasy"]
        # "更新于：2026-01-01" is prepended to the description, per the
        # extension's own mangaDetailsParse() behavior.
        assert "更新于：2026-01-01" in info.description
        assert "A test manhua description." in info.description
        assert info.cover_url == "https://img.example.invalid/cover1.jpg"

    def test_layout_changed_when_infobox_is_missing(self):
        a, t = _adapter({f"{MOBILE_BASE}/test-slug": html(EMPTY_SERIES_PAGE)})
        with pytest.raises(SourceError):
            a.get_series("test-slug")


class TestChapters:
    def test_parses_chapter_list(self):
        a, t = _adapter({f"{MOBILE_BASE}/test-slug": html(SERIES_PAGE)})
        chapters = a.get_chapters("test-slug")
        assert [c.title for c in chapters] == ["Chapter 1", "Chapter 2"]
        assert [c.chapter_id for c in chapters] == ["2001", "2002"]

    def test_series_and_chapters_share_one_fetch(self):
        a, t = _adapter({f"{MOBILE_BASE}/test-slug": html(SERIES_PAGE)})
        a.get_series("test-slug")
        a.get_chapters("test-slug")
        assert len(t.calls) == 1


class TestPageDataDecoding:
    """The roadmap's own exit condition: correctly XOR-decode a sample
    fixture. Exercises the full real pipeline (base64 -> XOR -> base64 ->
    JSON), not a shortcut."""

    @pytest.mark.parametrize("cid", [2001, 2002, 63234, 63230])
    def test_decodes_for_every_key_bucket(self, cid):
        urls = [f"https://img.example.invalid/ch/{cid}/1.jpg",
               f"https://img.example.invalid/ch/{cid}/2.jpg"]
        page = _make_chapter_page(cid, urls)
        assert miaoqumh.decode_page_data(page, cid) == urls

    def test_get_pages_returns_page_refs_in_order(self):
        urls = ["https://img.example.invalid/ch/2001/1.jpg",
               "https://img.example.invalid/ch/2001/2.jpg",
               "https://img.example.invalid/ch/2001/3.jpg"]
        chapter_url = f"{BASE}/1001/2001.html"
        a, t = _adapter({chapter_url: html(_make_chapter_page(2001, urls))})
        chapter = ChapterInfo("miaoqumh", "1001", "2001", "Chapter 1", chapter_url)
        refs = a.get_pages(chapter)
        assert [r.url for r in refs] == urls
        assert [r.index for r in refs] == [0, 1, 2]

    def test_wrong_key_produces_garbage_not_a_crash(self):
        """Decoding with the wrong cid % 10 key must not silently succeed
        with wrong URLs -- it should fail the JSON parse and raise
        LayoutChanged, not return garbage."""
        urls = ["https://img.example.invalid/ch/2001/1.jpg"]
        page = _make_chapter_page(2001, urls)
        with pytest.raises(SourceError):
            miaoqumh.decode_page_data(page, 2002)   # wrong cid -> wrong key bucket

    def test_layout_changed_when_data_var_is_missing(self):
        with pytest.raises(SourceError):
            miaoqumh.decode_page_data("<html><body>no data here</body></html>", 2001)


class TestSearchIsUnsupported:
    def test_search_is_not_offered(self):
        a, t = _adapter({})
        assert not a.supports("search")


class TestParseUrl:
    def test_chapter_url(self):
        a, t = _adapter({})
        kind, chapter = a.parse_url(f"{BASE}/1001/2001.html")
        assert kind == "chapter"
        assert chapter.chapter_id == "2001"
        assert chapter.series_id == "1001"

    def test_series_url(self):
        a, t = _adapter({})
        kind, series_id = a.parse_url(f"{BASE}/test-slug")
        assert kind == "series"
        assert series_id == "test-slug"

    def test_unrelated_url_is_not_claimed(self):
        a, t = _adapter({})
        assert a.parse_url("https://example.com/") is None


class TestRegistration:
    def test_registered_and_found_by_url(self):
        from sources import registry
        adapter = registry.find_for_url("https://miaoqumh.org/1001/2001.html")
        assert adapter is not None
        assert adapter.name == "miaoqumh"
