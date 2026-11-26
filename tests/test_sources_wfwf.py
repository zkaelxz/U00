"""
tests/test_sources_wfwf.py -- the wfwf adapter, against small invented fixtures shaped like the
pages read from the live site on 2026-11-26. No request reaches the real site.
"""
import pytest

from sources.adapters import wfwf
from sources.models import ChapterInfo, SourceError

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

BASE = wfwf.BASE_URL


def _adapter(routes, **kw):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return wfwf.WfwfSource(client=make_client("wfwf", t, clock, max_retries=0), **kw), t


def _card(sid, title):
    return (f'<a class="t-card" href="/list?toon={sid}"><div class="t-img">'
            f'<img alt="{title}" src="https://img.example.invalid/{sid}/c.jpg"></div>'
            f'<div class="t-title">{title}</div><div class="t-genre">로맨스</div></a>')


def _episodes(nums):
    return "".join(f'<a class="ep-item" href="/view?toon=12&num={n}" data-num="{n}">'
                   f'<div class="ep-content"><span class="ep-title">{n}화</span></div></a>' for n in nums)


def _series(nums, extra=""):
    return ('<html><body><div class="thumb-wrap"><img src="https://img.example.invalid/12/c.jpg"></div>'
            '<h1 class="w-title">Title Twelve</h1><div class="summary" id="summary"> A  story. </div>'
            f'{_episodes(nums)}{extra}</body></html>')


class TestSearch:
    def test_reads_cards_and_skips_duplicates_and_bad_ids(self):
        page = ("<html><body>" + _card(12, "Title Twelve") + _card(12, "again") + _card(7, "Title Seven")
                + '<a class="t-card" href="/list?toon=abc"><div class="t-title">bad</div></a>'
                + '<a class="nav-item" href="/list?toon=5">Menu</a></body></html>')
        # "ab" is not Korean; the point is that the query is percent-encoded for the site's own charset.
        a, _ = _adapter({f"{BASE}/sh?q=ab": html(page)})
        results = a.search(" ab ")
        assert [(r.series_id, r.title) for r in results] == [("12", "Title Twelve"), ("7", "Title Seven")]
        assert results[0].url == f"{BASE}/list?toon=12"
        assert results[0].cover_url == "https://img.example.invalid/12/c.jpg"

    def test_a_korean_query_is_sent_as_euc_kr_bytes(self):
        a, t = _adapter({f"{BASE}/sh?q=%C1%A4%BD%C7": html("<html></html>")})
        assert a.search("정실") == []
        assert t.calls[0]["url"].endswith("q=%C1%A4%BD%C7")

    def test_later_pages_are_empty_without_a_request(self):
        a, t = _adapter({})
        assert a.search("x", page=2) == [] and not t.calls


class TestSeries:
    def test_reads_title_cover_and_summary(self):
        a, _ = _adapter({f"{BASE}/list?toon=12": html(_series([1]))})
        info = a.get_series("12")
        assert (info.title, info.description, info.language) == ("Title Twelve", "A story.", "ko")
        assert info.cover_url == "https://img.example.invalid/12/c.jpg"

    def test_no_title_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/list?toon=12": html("<html><body></body></html>")})
        with pytest.raises(wfwf.LayoutChanged):
            a.get_series("12")

    def test_non_numeric_id_is_refused_before_any_request(self):
        a, t = _adapter({})
        with pytest.raises(wfwf.LayoutChanged):
            a.get_series("../etc")
        assert not t.calls


class TestChapters:
    def test_pages_are_followed_until_nothing_new_oldest_first(self):
        a, t = _adapter({
            f"{BASE}/list?toon=12&s=o&pg=1": html(_series([1, 2, 3])),
            f"{BASE}/list?toon=12&s=o&pg=2": html(_series([4, 5])),
            f"{BASE}/list?toon=12&s=o&pg=3": html(_series([]))})
        chapters = a.get_chapters("12")
        assert [c.chapter_id for c in chapters] == ["1", "2", "3", "4", "5"]
        assert chapters[3].title == "4화"
        assert chapters[0].url == f"{BASE}/view?toon=12&num=1"
        assert len(t.calls) == 3

    def test_episodes_of_another_series_are_ignored(self):
        other = '<a class="ep-item" href="/view?toon=99&num=1"><span class="ep-title">x</span></a>'
        a, _ = _adapter({f"{BASE}/list?toon=12&s=o&pg=1": html(_series([1], other)),
                         f"{BASE}/list?toon=12&s=o&pg=2": html(_series([]))})
        assert [c.chapter_id for c in a.get_chapters("12")] == ["1"]

    def test_no_episodes_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/list?toon=12&s=o&pg=1": html(_series([]))})
        with pytest.raises(wfwf.LayoutChanged):
            a.get_chapters("12")


CHAPTER = ('<html><body><div class="viewer-wrap"><img src="https://ads.example.invalid/banner.gif" alt="ad">'
           '<div class="vimg-area" id="vimg-area">'
           '<img data-src="https://img.example.invalid/12/a_0.jpg" src="/assets/img/sprite.png">'
           '<img data-src="https://img.example.invalid/12/a_1.png" src="/assets/img/sprite.png">'
           '<img data-src="http://insecure.example.invalid/2.jpg">'
           '<img data-src="https://img.example.invalid/12/a_0.jpg"></div></div></body></html>')


class TestPages:
    def test_only_reader_images_in_order_with_the_site_referer(self):
        a, _ = _adapter({f"{BASE}/view?toon=12&num=3": html(CHAPTER)})
        pages = a.get_pages(ChapterInfo("wfwf", "12", "3", "3화", ""))
        assert [p.url for p in pages] == ["https://img.example.invalid/12/a_0.jpg",
                                          "https://img.example.invalid/12/a_1.png"]
        assert pages[0].headers == {"Referer": BASE + "/"}

    def test_no_images_is_a_layout_change(self):
        a, _ = _adapter({f"{BASE}/view?toon=12&num=3": html("<html><body></body></html>")})
        with pytest.raises(wfwf.LayoutChanged):
            a.get_pages(ChapterInfo("wfwf", "12", "3", "3화", ""))

    def test_a_non_image_download_is_refused(self):
        a, _ = _adapter({"https://img.example.invalid/12/a_0.jpg": html("<html>blocked</html>")})
        page = wfwf.PageRef("wfwf", "3", 0, "https://img.example.invalid/12/a_0.jpg", headers={})
        with pytest.raises(SourceError):
            a.download_page(page)


class TestUrls:
    @pytest.mark.parametrize("url", [
        "https://wfwf546.com/list?toon=12", "https://wfwf547.com/list?toon=12&s=n&pg=2",
        "https://www.wfwf9.com/list?toon=12"])
    def test_series_links_on_any_domain_number(self, url):
        a, _ = _adapter({})
        assert a.parse_url(url) == ("series", "12")

    def test_chapter_link(self):
        a, _ = _adapter({})
        kind, ch = a.parse_url("https://wfwf547.com/view?toon=12&num=5&x=1")
        assert (kind, ch.series_id, ch.chapter_id) == ("chapter", "12", "5")
        assert ch.url == f"{BASE}/view?toon=12&num=5"

    @pytest.mark.parametrize("url", ["https://wfwf546.com/", "https://notwfwf546.com/list?toon=12",
                                     "https://wfwf546.com.example/list?toon=12", "https://wfwf546.com/list?toon=x"])
    def test_other_links_are_not_ours(self, url):
        a, _ = _adapter({})
        assert a.parse_url(url) is None

    def test_verify_site_wants_three_series_links(self):
        assert wfwf.WfwfSource.verify_site("".join(f'<a href="/list?toon={i}">x</a>' for i in (1, 2, 3)))
        assert not wfwf.WfwfSource.verify_site('<a href="/list?toon=1">x</a>')
