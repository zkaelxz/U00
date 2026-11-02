"""
tests/test_sources_marumaru.py -- the Marumaru adapter, against small invented
fixtures shaped like the live JSON seen 2026-11-01. No request reaches the
real site.
"""
import json

import pytest

from sources import store
from sources.adapters import marumaru
from sources.http_transport import Response
from sources.models import ChapterInfo, PageRef, SourceError, SourceUnavailable

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = marumaru.BASE_URL
READER = marumaru.READER_URL


def _json(payload, status: int = 200) -> Response:
    return Response(status, {"content-type": "application/json"}, json.dumps(payload).encode(), "")


WORK = {"id": 1284, "title": "Test Title", "mediaType": "WEBTOON", "adult": False,
        "status": "SERIALIZING", "author": "Writer / Artist", "genres": "Action,Drama",
        "coverUrl": "https://peer.example.invalid/c.bin", "episodeCount": 3}
SEARCH = {"items": [WORK,
                    {**WORK, "id": 9, "title": "A Novel", "mediaType": "NOVEL"},
                    {**WORK, "id": "../x", "title": "Bad id"},
                    {**WORK, "id": 5, "title": "Other", "coverUrl": "javascript:alert(1)"}],
          "total": 4, "page": 0, "size": 70}


def _adapter(routes, **kw):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return marumaru.MarumaruSource(client=make_client("marumaru", t, clock, max_retries=0), **kw), t


class TestSearch:
    def test_keeps_comics_and_skips_novels_bad_ids_and_unsafe_covers(self):
        a, _ = _adapter({f"{BASE}/api/search?q=test": _json(SEARCH)})
        results = a.search(" test ")
        assert [r.series_id for r in results] == ["1284", "5"]
        assert results[0].url == f"{BASE}/webtoon/1284"
        assert results[0].extra["author"] == "Writer / Artist"
        assert results[1].cover_url == ""

    def test_later_pages_are_empty(self):
        a, _ = _adapter({})
        assert a.search("x", page=2) == []

    def test_unexpected_shape_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/api/search?q=test": _json({"items": "no"})})
        with pytest.raises(marumaru.LayoutChanged):
            a.search("test")


class TestSeries:
    def test_parses_details(self):
        a, _ = _adapter({f"{BASE}/api/works/1284": _json({"work": WORK, "synopsis": "Plot."})})
        info = a.get_series("1284")
        assert (info.title, info.authors, info.genres) == ("Test Title", ["Writer", "Artist"], ["Action", "Drama"])
        assert info.status == "ongoing" and info.language == "ko" and info.description == "Plot."

    def test_non_numeric_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(marumaru.LayoutChanged):
            a.get_series("../etc")


class TestChapters:
    def test_pages_through_the_list_and_sorts_by_number(self):
        def ep(i, n):
            return {"id": i, "number": n, "title": f"{n}화"}
        a, _ = _adapter({
            f"{BASE}/api/works/1284/episodes?page=0&size=100":
                _json({"items": [ep(30, 3), ep(20, 2)], "total": 3}),
            f"{BASE}/api/works/1284/episodes?page=1&size=100":
                _json({"items": [ep(10, 1)], "total": 3})})
        chapters = a.get_chapters("1284")
        assert [c.chapter_id for c in chapters] == ["10", "20", "30"]
        assert chapters[0].url == f"{BASE}/webtoon/1284/10"

    def test_empty_list_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/api/works/1284/episodes?page=0&size=100":
                         _json({"items": [], "total": 0})})
        with pytest.raises(marumaru.LayoutChanged):
            a.get_chapters("1284")


class TestPages:
    def _chapter(self):
        return ChapterInfo("marumaru", "1284", "459416", "1화", f"{BASE}/webtoon/1284/459416")

    def test_image_urls_in_order(self):
        body = [{"kind": "image", "url": "https://peer.example.invalid/1.webp"},
                {"kind": "text", "url": "https://peer.example.invalid/x"},
                {"kind": "image", "url": "http://peer.example.invalid/insecure.webp"},
                {"kind": "image", "url": "https://peer.example.invalid/2.webp"}]
        a, _ = _adapter({READER + "459416": _json({"ok": True, "item": {"body": body}})})
        pages = a.get_pages(self._chapter())
        assert [p.url for p in pages] == ["https://peer.example.invalid/1.webp",
                                          "https://peer.example.invalid/2.webp"]

    def test_no_images_is_a_layout_change(self):
        a, _ = _adapter({READER + "459416": _json({"item": {"body": []}})})
        with pytest.raises(marumaru.LayoutChanged):
            a.get_pages(self._chapter())

    def test_download_refuses_a_non_image(self):
        url = "https://peer.example.invalid/1.webp"
        a, _ = _adapter({url: Response(200, {"content-type": "text/html"}, b"blocked", "")})
        with pytest.raises(SourceError):
            a.download_page(PageRef("marumaru", "1", 0, url))

    def test_download_returns_bytes_and_extension(self):
        url = "https://peer.example.invalid/1.webp"
        a, _ = _adapter({url: Response(200, {"content-type": "image/webp"}, b"IMG", "")})
        assert a.download_page(PageRef("marumaru", "1", 0, url)) == (b"IMG", ".webp")


class TestParseUrl:
    def test_series_chapter_and_foreign_urls(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/webtoon/1284") == ("series", "1284")
        kind, ch = a.parse_url(f"{BASE}/webtoon/1284/459416")
        assert kind == "chapter" and ch.chapter_id == "459416"
        assert a.parse_url("https://marumaru109.com/webtoon/1284/5")[0] == "chapter"
        assert a.parse_url(f"{BASE}/novel/1") is None
        assert a.parse_url("https://example.com/webtoon/1") is None


class TestLandingPage:
    LANDING = 'x <a href="https://marumaru109.com">go</a> <a href="https://t.me/info">tg</a>'
    HOME = "<html><head><title>마루마루</title></head></html>"

    def test_dead_domain_proposes_the_address_on_the_landing_page(self):
        routes = {f"{BASE}/api/search?q=x": Response(522, {}, b"", ""),
                  marumaru.LANDING_URL: html(self.LANDING),
                  "https://marumaru109.com/": html(self.HOME)}
        a, _ = _adapter(routes)
        with pytest.raises(SourceUnavailable):
            a.search("x")
        assert "marumaru109.com" in {p["host"] for p in store.domain_proposals("marumaru")}

    def test_a_page_that_is_not_the_site_is_not_proposed(self):
        routes = {f"{BASE}/api/search?q=x": Response(522, {}, b"", ""),
                  marumaru.LANDING_URL: html(self.LANDING),
                  "https://marumaru109.com/": html("<html><title>Parked</title></html>")}
        a, _ = _adapter(routes)
        with pytest.raises(SourceUnavailable):
            a.search("x")
        assert store.domain_proposals("marumaru") == []
