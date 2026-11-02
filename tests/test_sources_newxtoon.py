"""
tests/test_sources_newxtoon.py -- the NewXToon adapter, against small
invented fixtures shaped like the page layout reported for the live site.
No request reaches the real site.
"""
import json

import pytest

from sources.adapters import newxtoon
from sources.http_transport import Response
from sources.models import ChapterInfo, PageRef, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = newxtoon.BASE_URL

SEARCH = ('<html><body><a href="/ads/click/1"><img src="https://files.example.invalid/ads/b.png">Ad</a>'
          '<a href="/comics/12"><img src="https://files.example.invalid/c12.jpg" alt="Title Twelve">'
          '<span>Title Twelve</span></a>'
          '<a href="/comics/12">again</a>'
          '<a href="/comics/7" title="Title Seven"><img src="javascript:x"></a>'
          '<a href="/comics/abc">bad id</a></body></html>')


def _series_page(chapter_ids, extra=""):
    links = "".join(f'<a href="/comics/12/chapters/{c}">Ep {c}</a>' for c in chapter_ids)
    return (f'<html><head><meta property="og:title" content="Title Twelve">'
            f'<meta property="og:image" content="https://files.example.invalid/c12.jpg">'
            f'<meta property="og:description" content="A story."></head><body><h1>Other</h1>'
            f'{links}<a href="/comics/12/chapters/999">elsewhere?</a>{extra}</body></html>')


def _chapters_json(rows, has_more):
    return json.dumps({"chapters": [{"id": i, "title": t} for i, t in rows], "has_more": has_more})


CHAPTER = ('<html><body><img src="https://files.example.invalid/ads/top.gif">'
           '<img class="reader-page" src="https://user1.files.example.invalid/a/1.jpg">'
           '<img class="reader-page" data-src="https://user1.files.example.invalid/a/2.jpg">'
           '<img class="reader-page" src="https://user1.files.example.invalid/ads/x.jpg">'
           '<img class="reader-page" src="http://insecure.example.invalid/3.jpg">'
           '<img class="reader-page" src="https://user1.files.example.invalid/a/1.jpg"></body></html>')


def _adapter(routes, **kw):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return newxtoon.NewXToonSource(client=make_client("newxtoon", t, clock, max_retries=0), **kw), t


class TestSearch:
    def test_keeps_series_links_and_skips_ads_duplicates_and_bad_ids(self):
        a, _ = _adapter({f"{BASE}/search?q=title": html(SEARCH)})
        results = a.search(" title ")
        assert [(r.series_id, r.title) for r in results] == [("12", "Title Twelve"), ("7", "Title Seven")]
        assert results[0].url == f"{BASE}/comics/12"
        assert results[0].cover_url == "https://files.example.invalid/c12.jpg"
        assert results[1].cover_url == ""

    def test_later_pages_are_empty_without_a_request(self):
        a, _ = _adapter({})
        assert a.search("x", page=2) == []


class TestSeries:
    def test_reads_title_cover_and_description(self):
        a, _ = _adapter({f"{BASE}/comics/12": html(_series_page([1]))})
        info = a.get_series("12")
        assert (info.title, info.description, info.language) == ("Title Twelve", "A story.", "ko")
        assert info.cover_url == "https://files.example.invalid/c12.jpg"

    def test_the_site_name_is_dropped_from_the_title(self):
        page = _series_page([1]).replace('content="Title Twelve"', 'content="Title Twelve — 뉴엑스툰"')
        a, _ = _adapter({f"{BASE}/comics/12": html(page)})
        assert a.get_series("12").title == "Title Twelve"

    def test_no_title_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/comics/12": html("<html><body></body></html>")})
        with pytest.raises(newxtoon.LayoutChanged):
            a.get_series("12")

    def test_non_numeric_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(newxtoon.LayoutChanged):
            a.get_series("../etc")


class TestChapters:
    def test_first_page_alone_when_the_series_names_no_more_pages(self):
        a, t = _adapter({f"{BASE}/comics/12": html(_series_page([30, 10]))})
        chapters = a.get_chapters("12")
        assert [c.chapter_id for c in chapters] == ["10", "30", "999"]
        assert chapters[0].url == f"{BASE}/comics/12/chapters/10"
        assert len(t.calls) == 1

    def test_follows_the_json_list_oldest_first_without_repeats(self):
        marker = '<div data-chapters-url="/comics/12/chapters" data-chapter-next-page="2"></div>'
        a, _ = _adapter({
            f"{BASE}/comics/12": html(_series_page([30, 10], marker)),
            f"{BASE}/comics/12/chapters?page=2": html(_chapters_json([(20, "Ep 20"), (10, "Ep 10")], True)),
            f"{BASE}/comics/12/chapters?page=3": html(_chapters_json([(5, "Ep 5")], False))})
        chapters = a.get_chapters("12")
        assert [c.chapter_id for c in chapters] == ["5", "10", "20", "30", "999"]
        assert chapters[2].title == "Ep 20"

    def test_a_list_that_never_ends_is_a_layout_change_not_a_silent_cut(self, monkeypatch):
        monkeypatch.setattr(newxtoon, "MAX_LIST_PAGES", 3)
        marker = '<div data-chapters-url="/comics/12/chapters"></div>'
        a, _ = _adapter({
            f"{BASE}/comics/12": html(_series_page([1], marker)),
            f"{BASE}/comics/12/chapters?page=2": html(_chapters_json([(2, "Ep 2")], True)),
            f"{BASE}/comics/12/chapters?page=3": html(_chapters_json([(3, "Ep 3")], True))})
        with pytest.raises(newxtoon.LayoutChanged):
            a.get_chapters("12")

    def test_a_json_list_without_chapters_is_a_layout_change(self):
        marker = '<div data-chapters-url="/comics/12/chapters"></div>'
        a, _ = _adapter({f"{BASE}/comics/12": html(_series_page([1], marker)),
                         f"{BASE}/comics/12/chapters?page=2": html("not json")})
        with pytest.raises(newxtoon.LayoutChanged):
            a.get_chapters("12")

    def test_no_chapters_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/comics/12": html("<html><h1>x</h1></html>")})
        with pytest.raises(newxtoon.LayoutChanged):
            a.get_chapters("12")


class TestPages:
    def _chapter(self):
        return ChapterInfo("newxtoon", "12", "30", "Ep 30", f"{BASE}/comics/12/chapters/30")

    def test_only_reader_images_in_order_without_ads_repeats_or_insecure_urls(self):
        a, _ = _adapter({f"{BASE}/comics/12/chapters/30": html(CHAPTER)})
        assert [p.url for p in a.get_pages(self._chapter())] == [
            "https://user1.files.example.invalid/a/1.jpg", "https://user1.files.example.invalid/a/2.jpg"]

    def test_page_refs_carry_the_site_referer_the_image_host_requires(self):
        a, _ = _adapter({f"{BASE}/comics/12/chapters/30": html(CHAPTER)})
        refs = a.get_pages(self._chapter())
        assert all(p.headers == {"Referer": f"{a.base_url}/"} for p in refs)

    def test_no_images_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/comics/12/chapters/30": html("<html><img src='https://x.invalid/a.jpg'></html>")})
        with pytest.raises(newxtoon.LayoutChanged):
            a.get_pages(self._chapter())

    def test_download_returns_bytes_and_refuses_a_non_image(self):
        ok, bad = "https://user1.files.example.invalid/a/1.jpg", "https://user1.files.example.invalid/a/2.jpg"
        a, _ = _adapter({ok: Response(200, {"content-type": "image/jpeg"}, b"IMG", ""),
                         bad: Response(200, {"content-type": "text/html"}, b"blocked", "")})
        assert a.download_page(PageRef("newxtoon", "30", 0, ok)) == (b"IMG", ".jpg")
        with pytest.raises(SourceError):
            a.download_page(PageRef("newxtoon", "30", 1, bad))


class TestUrlsAndSite:
    def test_parse_url_for_series_chapter_and_foreign_links(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/comics/12") == ("series", "12")
        kind, ch = a.parse_url("https://xn--vg1b002axlc8ts.com/comics/12/chapters/30")
        assert (kind, ch.series_id, ch.chapter_id) == ("chapter", "12", "30")
        assert a.parse_url(f"{BASE}/ads/click/1") is None
        assert a.parse_url("https://example.com/comics/12") is None

    def test_verify_site_needs_several_series_links(self):
        home = "".join(f'<a href="/comics/{i}">t</a>' for i in (1, 2, 3))
        assert newxtoon.NewXToonSource.verify_site(home)
        assert not newxtoon.NewXToonSource.verify_site('<a href="/comics/1">t</a>')
        assert not newxtoon.NewXToonSource.verify_site("")
