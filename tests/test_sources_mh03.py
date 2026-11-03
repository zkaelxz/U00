"""
tests/test_sources_mh03.py -- the mh03 adapter, against small invented
fixtures shaped like the live pages seen 2026-11-03. No request reaches the
real site.
"""
import pytest

from sources.adapters import mh03
from sources.http_transport import Response
from sources.models import ChapterInfo, ContentHidden, PageRef

from .sources_helpers import FakeClock, ScriptedTransport, html, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

JPEG_BYTES = bytes.fromhex("ffd8ffe0") + b"jpeg"
BASE = mh03.BASE_URL
SERIES_URL = f"{BASE}/comic_demo.html"
CHAPTER_URL = f"{BASE}/chapter_12_345.html"

SERIES = (
    "<html><h1>《示例》</h1>"
    '<div class="comic-cover-large"><img src="https://img.example/cover.webp"></div>'
    '<div class="comic-stats"><div class="stat-item"><span>评分：9.9</span></div>'
    '<div class="stat-item"><span>作者：甲,乙</span></div></div>'
    '<div class="comic-tags"><span class="tag"></span><span class="tag">连载中</span></div>'
    '<div class="comic-description"><h2>漫画介绍</h2><p>一段简介</p></div>'
    '<div class="chapter-item"><a href="/chapter_12_344.html">预告 上线预告</a></div>'
    '<div class="chapter-item"><a href="/chapter_12_345.html">001 开始</a></div>'
    '<div class="chapter-item"><a href="/chapter_12_345.html">dup</a></div>'
    '<div class="chapter-item"><a href="/chapter_12_346.html">更新提示 更新提示</a></div>'
    '<div class="chapter-item"><a href="/chapter_12_347.html">002 继续</a></div>'
    '<div class="chapter-item"><a href="/chapter_x_1.html">bad</a></div>'
    '<div class="comic-card"><a href="/chapter_99_1.html">elsewhere</a></div></html>')
CHAPTER = (
    '<html><div class="comic-content">'
    '<img class="comic-image lazyload" src="/ph.gif" data-src="https://img.example/a/1.webp">'
    '<img class="comic-image lazyload" src="/ph.gif" data-src="https://img.example/a/2.webp">'
    '<img class="comic-image lazyload" src="/ph.gif" data-src="https://img.example/a/1.webp">'
    '<img class="comic-image lazyload" src="/ph.gif" data-src="http://img.example/a/3.webp">'
    '</div><img class="ad" data-src="https://ads.example/x.gif"></html>')
CHAPTER_NO_IMAGES = ('<html><div class="comic-content"></div>'
                     '<div class="hide buy-box" style="display:none;">paid</div></html>')


def _adapter(routes):
    clock = FakeClock()
    t = ScriptedTransport(routes, clock)
    return mh03.Mh03Source(client=make_client("mh03", t, clock, max_retries=0)), t


class TestSeries:
    def test_parses_details(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        info = a.get_series("demo")
        assert (info.title, info.authors, info.description) == ("示例", ["甲", "乙"], "一段简介")
        assert info.status == "ongoing" and info.language == "zh"
        assert info.cover_url == "https://img.example/cover.webp"

    def test_missing_title_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(mh03.LayoutChanged):
            a.get_series("demo")

    def test_unsafe_series_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(mh03.LayoutChanged):
            a.get_series("../etc")


class TestChapters:
    def test_in_page_order_without_notices_duplicates_or_other_links(self):
        a, _ = _adapter({SERIES_URL: html(SERIES)})
        chapters = a.get_chapters("demo")
        assert [c.chapter_id for c in chapters] == ["12_345", "12_347"]
        assert chapters[0].title == "001 开始" and chapters[0].url == CHAPTER_URL

    def test_no_chapters_is_a_layout_change(self):
        a, _ = _adapter({SERIES_URL: html("<html></html>")})
        with pytest.raises(mh03.LayoutChanged):
            a.get_chapters("demo")


class TestPages:
    def _chapter(self):
        return ChapterInfo("mh03", "demo", "12_345", "001 开始", CHAPTER_URL)

    def test_reads_data_src_in_order_https_only_without_repeats(self):
        a, _ = _adapter({CHAPTER_URL: html(CHAPTER)})
        pages = a.get_pages(self._chapter())
        assert [p.url for p in pages] == ["https://img.example/a/1.webp", "https://img.example/a/2.webp"]
        assert pages[0].headers == {"Referer": BASE + "/"}

    def test_a_reader_without_images_is_reported_as_withheld(self):
        a, _ = _adapter({CHAPTER_URL: html(CHAPTER_NO_IMAGES)})
        with pytest.raises(ContentHidden):
            a.get_pages(self._chapter())

    def test_missing_reader_is_a_layout_change(self):
        a, _ = _adapter({CHAPTER_URL: html("<html></html>")})
        with pytest.raises(mh03.LayoutChanged):
            a.get_pages(self._chapter())

    def test_unsafe_chapter_id_is_refused_before_any_request(self):
        a, _ = _adapter({})
        with pytest.raises(mh03.LayoutChanged):
            a.get_pages(ChapterInfo("mh03", "demo", "../x", "x", ""))

    def test_download_returns_bytes_and_extension(self):
        url = "https://img.example/a/1.webp"
        a, _ = _adapter({url: Response(200, {"content-type": "image/webp"}, b"WEBP", "")})
        assert a.download_page(PageRef("mh03", "c", 0, url)) == (b"WEBP", ".webp")

    def test_download_names_the_file_by_its_bytes_not_the_url(self):
        url = "https://img.example/a/1.webp"
        a, _ = _adapter({url: Response(200, {"content-type": "image/webp"}, JPEG_BYTES, "")})
        assert a.download_page(PageRef("mh03", "c", 0, url)) == (JPEG_BYTES, ".jpg")

    def test_download_keeps_the_url_extension_for_unrecognised_bytes(self):
        url = "https://img.example/a/1.webp"
        a, _ = _adapter({url: Response(200, {}, b"????", "")})
        assert a.download_page(PageRef("mh03", "c", 0, url))[1] == ".webp"


class TestParseUrl:
    def test_series_and_foreign_urls(self):
        a, _ = _adapter({})
        assert a.parse_url(f"{BASE}/comic_demo.html") == ("series", "demo")
        assert a.parse_url(f"{BASE}/chapter_12_345.html") is None
        assert a.parse_url("https://example.com/comic_demo.html") is None
