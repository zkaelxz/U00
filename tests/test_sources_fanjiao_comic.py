"""
tests/test_sources_fanjiao_comic.py -- the Fanjiao comic adapter. No network
and no browser: the page render is replaced by a fake capture that returns
rendered HTML plus the page's own API responses, shaped like the ones seen
2026-11-03 (invented values).
"""
import json

import pytest

from sources import registry
from sources.adapters import fanjiao_comic
from sources.http_transport import Response
from sources.models import ChapterInfo, ContentHidden, FailureReason, PageRef, SourceError

from .sources_helpers import ScriptedTransport, FakeClock, make_client

pytestmark = pytest.mark.usefixtures("isolated_db")

COMIC = "4242"
DETAIL_URL = fanjiao_comic._page_url("cartoonDetail", {"comic_id": 4242})
API = "https://api.fanjiao.co/walkman/api"

HOME = {"code": 0, "data": {
    "comic_info": {"comic_id": 4242, "name": "示例漫画 第一季", "description": "一段简介", "author_name": "某作者",
                   "cover": "https://img.example/cover.png", "is_over": 0, "price": 126, "is_payment": 0},
    "chapter_list": [
        {"chapter_id": 11, "comic_id": 4242, "name": "预告", "is_buy": 1, "price": 0},
        {"chapter_id": 12, "comic_id": 4242, "name": "第一话", "is_buy": 1, "price": 0},
        {"chapter_id": 12, "comic_id": 4242, "name": "dup", "is_buy": 1, "price": 0},
        {"chapter_id": 13, "comic_id": 4242, "name": "第二话", "is_buy": 0, "price": 0},
        {"chapter_id": "x", "name": "bad", "is_buy": 1}]}}
CHAPTER_DETAILS = {"code": 0, "data": {"chapter_id": 12, "is_buy": 1, "details": [{"sort": 1}, {"sort": 2}]}}
READER = ('<html><div class="top"><img src="https://static.example/logo.png"></div>'
          '<div class="read-content"><img src="https://static.rela.me/a/1" alt="">'
          '<img src="data:image/png;base64,AAAA"><img src="https://static.rela.me/a/2">'
          '<img src="https://static.rela.me/a/1"><img src="http://static.rela.me/a/3"></div></html>')


def _entry(path, body, status=200):
    return {"url": f"{API}/{path}?x=1", "status": status, "content_type": "application/json",
            "body": json.dumps(body).encode()}


class FakeCapture:
    def __init__(self, html="", captured=()):
        self.html, self.captured, self.urls = html, list(captured), []

    def __call__(self, url):
        self.urls.append(url)
        return self.html, self.captured


def _adapter(capture, allow_browser=True):
    a = fanjiao_comic.FanjiaoComicSource(client=make_client("fanjiao_comic", clock=FakeClock()),
                                         capture=capture)
    a.allow_browser = allow_browser
    return a


def _chapter(cid="12", group=""):
    url = fanjiao_comic._page_url("cartoonRead", {"chapter_id": int(cid), "comic_name": "示例漫画 第一季"})
    return ChapterInfo("fanjiao_comic", COMIC, cid, "第一话", url, group=group)


class TestSeries:
    def test_reads_the_pages_own_home_page_response(self):
        cap = FakeCapture(captured=[_entry("comic/home_page", HOME)])
        info = _adapter(cap).get_series(COMIC)
        assert cap.urls == [DETAIL_URL]
        assert (info.title, info.authors, info.description) == ("示例漫画 第一季", ["某作者"], "一段简介")
        assert info.status == "ongoing" and info.language == "zh"
        assert info.cover_url == "https://img.example/cover.png"

    def test_a_response_for_another_comic_is_a_layout_change(self):
        other = json.loads(json.dumps(HOME))
        other["data"]["comic_info"]["comic_id"] = 1
        with pytest.raises(fanjiao_comic.LayoutChanged):
            _adapter(FakeCapture(captured=[_entry("comic/home_page", other)])).get_series(COMIC)

    def test_a_refused_response_is_a_layout_change(self):
        with pytest.raises(fanjiao_comic.LayoutChanged):
            _adapter(FakeCapture(captured=[_entry("comic/home_page", HOME, status=403)])).get_series(COMIC)

    def test_refuses_the_browser_off_this_pc_and_a_bad_id_before_any_render(self):
        cap = FakeCapture(captured=[_entry("comic/home_page", HOME)])
        with pytest.raises(SourceError) as e:
            _adapter(cap, allow_browser=False).get_series(COMIC)
        assert e.value.reason is FailureReason.JAVASCRIPT_REQUIRED
        with pytest.raises(SourceError):
            _adapter(cap).get_series("../x")
        assert cap.urls == []


class TestChapters:
    def test_in_list_order_without_teasers_duplicates_or_bad_ids(self):
        a = _adapter(FakeCapture(captured=[_entry("comic/home_page", HOME)]))
        chapters = a.get_chapters(COMIC)
        assert [(c.chapter_id, c.title) for c in chapters] == [("12", "第一话"), ("13", "第二话")]
        assert [c.group for c in chapters] == ["", fanjiao_comic.PAID_GROUP]
        assert chapters[0].url.startswith("https://www.fanjiao.co/cartoonRead?data=")

    def test_series_and_chapters_share_one_render(self):
        cap = FakeCapture(captured=[_entry("comic/home_page", HOME)])
        a = _adapter(cap)
        a.get_series(COMIC)
        a.get_chapters(COMIC)
        assert cap.urls == [DETAIL_URL]

    def test_no_chapters_is_a_layout_change(self):
        empty = {"code": 0, "data": {"comic_info": HOME["data"]["comic_info"], "chapter_list": []}}
        with pytest.raises(fanjiao_comic.LayoutChanged):
            _adapter(FakeCapture(captured=[_entry("comic/home_page", empty)])).get_chapters(COMIC)


class TestPages:
    def test_reads_https_images_in_order_without_repeats(self):
        cap = FakeCapture(READER, [_entry("chapter/details", CHAPTER_DETAILS)])
        pages = _adapter(cap).get_pages(_chapter())
        assert [p.url for p in pages] == ["https://static.rela.me/a/1", "https://static.rela.me/a/2"]
        assert pages[0].headers == {"Referer": fanjiao_comic.REFERER}

    def test_a_chapter_marked_not_bought_is_refused_before_any_render(self):
        cap = FakeCapture(READER)
        with pytest.raises(ContentHidden) as e:
            _adapter(cap).get_pages(_chapter("13", group=fanjiao_comic.PAID_GROUP))
        assert e.value.reason is FailureReason.PURCHASE_REQUIRED
        assert cap.urls == []

    def test_a_page_that_reports_is_buy_0_is_withheld_even_with_images(self):
        unbought = {"code": 0, "data": {"chapter_id": 13, "is_buy": 0, "details": []}}
        with pytest.raises(ContentHidden):
            _adapter(FakeCapture(READER, [_entry("chapter/details", unbought)])).get_pages(_chapter("13"))

    def test_a_reader_without_images_is_withheld(self):
        cap = FakeCapture('<html><div class="read-content"></div></html>',
                          [_entry("chapter/details", CHAPTER_DETAILS)])
        with pytest.raises(ContentHidden):
            _adapter(cap).get_pages(_chapter())

    def test_fewer_images_than_the_page_lists_is_a_layout_change(self):
        three = {"code": 0, "data": {"is_buy": 1, "details": [{}, {}, {}]}}
        with pytest.raises(fanjiao_comic.LayoutChanged):
            _adapter(FakeCapture(READER, [_entry("chapter/details", three)])).get_pages(_chapter())

    def test_a_missing_reader_is_a_layout_change(self):
        with pytest.raises(fanjiao_comic.LayoutChanged):
            _adapter(FakeCapture("<html></html>", [_entry("chapter/details", CHAPTER_DETAILS)])).get_pages(_chapter())

    def test_an_unusable_chapter_address_is_refused_before_any_render(self):
        cap = FakeCapture(READER)
        with pytest.raises(fanjiao_comic.LayoutChanged):
            _adapter(cap).get_pages(ChapterInfo("fanjiao_comic", COMIC, "12", "x", "https://evil.example/cartoonRead?data=e30"))
        assert cap.urls == []

    def test_download_names_the_file_by_its_bytes_not_the_url(self):
        url = "https://static.rela.me/a/1.webp"
        jpeg = bytes.fromhex("ffd8ffe0") + b"jpeg"
        clock = FakeClock()
        t = ScriptedTransport({url: Response(200, {"content-type": "image/webp"}, jpeg, "")}, clock)
        a = fanjiao_comic.FanjiaoComicSource(client=make_client("fanjiao_comic", t, clock, max_retries=0),
                                             capture=FakeCapture())
        assert a.download_page(PageRef("fanjiao_comic", "12", 0, url)) == (jpeg, ".jpg")


class TestParseUrl:
    def test_series_chapter_and_foreign_links(self):
        a = _adapter(FakeCapture())
        assert a.parse_url(DETAIL_URL) == ("series", COMIC)
        kind, chapter = a.parse_url(_chapter().url)
        assert kind == "chapter" and chapter.chapter_id == "12" and chapter.series_id == ""
        assert chapter.title == "示例漫画 第一季" and chapter.url == _chapter().url
        assert a.parse_url("https://www.fanjiao.co/cartoonDetail?data=%%%") is None
        assert a.parse_url("https://www.fanjiao.co/pages/share.html?album_id=1") is None
        assert a.parse_url("https://example.com/cartoonDetail?data=e30") is None


def test_registered_as_a_browser_comic_source():
    assert "fanjiao_comic" in registry.adapter_classes()
    a = _adapter(FakeCapture())
    assert a.needs_browser and not a.supports("search")
    assert a.supports("get_pages") and a.supports("download_page")
    assert fanjiao_comic.FanjiaoComicSource.matches_url(_chapter().url)
