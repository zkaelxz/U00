"""A capture files the pages it saves under a chapter: the number from the page address when
there is one, else one "Imported <time>" chapter per capture run."""
import pytest

import comic_chapters
import db
from services import page_server
from services import extension_title_service as titles

from tests.test_page_server import _b64, _png_bytes, _post, fake_pipeline  # noqa: F401
from tests.test_page_server import token, _reset_module_state  # noqa: F401


@pytest.mark.parametrize("url,title", [
    ("https://comic.naver.com/webtoon/detail?titleId=833532&no=12&week=wed", "Chapter 12"),
    ("https://example.com/comic/slug/chapter-7", "Chapter 7"),
    ("https://example.com/read/episode/3.5/", "Chapter 3.5"),
])
def test_the_chapter_number_is_read_from_the_page_address(url, title):
    ref = titles.capture_chapter_ref(url, "run-abc12345")
    assert ref["title"] == title


def test_a_series_id_is_not_taken_for_a_chapter_number():
    ref = titles.capture_chapter_ref("https://example.com/viewer?id=833532", "run-abc12345")
    assert ref["title"].startswith("Imported ")


def test_an_address_without_a_number_is_one_chapter_per_capture_run():
    a = titles.capture_chapter_ref("https://example.com/viewer/x/9a8f", "aaaa1111")
    b = titles.capture_chapter_ref("https://example.com/viewer/x/9a8f", "aaaa1111")
    c = titles.capture_chapter_ref("https://example.com/viewer/x/9a8f", "bbbb2222")
    assert a["key"] == b["key"] != c["key"]


def test_no_number_and_no_run_gives_no_label():
    assert titles.capture_chapter_ref("https://example.com/x", None) is None


@pytest.fixture
def drama(isolated_db, monkeypatch):
    monkeypatch.setattr(page_server, "_build_engine", lambda config: object())
    return db.create_drama(title_en="Labelled", media_type="manhua", source_language="zh")


def _send(token, drama_id, colour, **extra):
    return _post(token, {"images": [{"data": _b64(_png_bytes(colour=colour)), "content_type": "image/png"}],
                         "drama_id": drama_id, "store": True, **extra}).payload


def _groups(drama_id):
    return comic_chapters.group_pages(db.list_pages(drama_id), comic_chapters.load(drama_id))


def test_saved_pages_are_filed_under_the_chapter_in_the_address(token, fake_pipeline, drama):
    url = "https://comic.naver.com/webtoon/detail?titleId=1&no=4"
    _send(token, drama, (255, 0, 0), source_url=url, run_id="run-aaaa1111")
    _send(token, drama, (0, 255, 0), source_url=url, run_id="run-aaaa1111")
    groups = _groups(drama)
    assert [(g["title"], g["page_count"]) for g in groups] == [("Chapter 4", 2)]


def test_a_second_chapter_follows_the_first_in_order(token, fake_pipeline, drama):
    _send(token, drama, (255, 0, 0), source_url="https://s.example/c?no=1", run_id="run-aaaa1111")
    _send(token, drama, (0, 255, 0), source_url="https://s.example/c?no=2", run_id="run-bbbb2222")
    assert [g["title"] for g in _groups(drama)] == ["Chapter 1", "Chapter 2"]


def test_a_capture_without_a_number_is_stamped_with_its_import_time(token, fake_pipeline, drama):
    _send(token, drama, (255, 0, 0), source_url="https://s.example/viewer/x/9a8f", run_id="run-cccc3333")
    [group] = _groups(drama)
    assert group["known"] and group["title"].startswith("Imported ")


def test_an_overlay_only_page_is_not_filed(token, fake_pipeline, drama):
    _post(token, {"images": [{"data": _b64(_png_bytes()), "content_type": "image/png"}],
                  "drama_id": drama, "store": False, "source_url": "https://s.example/c?no=1"})
    assert comic_chapters.load(drama)["chapters"] == []


@pytest.mark.parametrize("titles,number,label", [
    (["Series", "Series 第12话 重逢 - SiteComic"], "12", "第12话 重逢"),
    (["第十二话"], "12", "第十二话"),
    (["第一百零五章 大结局"], "105", "第一百零五章 大结局"),
    (["Some Name Ep. 7 | Site"], "7", "Ep. 7"),
    (["어떤 웹툰 23화"], "23", "23화"),
    (["ある漫画 第3話 「始まり」"], "3", "第3話 「始まり」"),
    (["Name chapter 007"], "7", "chapter 007"),
    (["第１２話"], "12", "第12話"),
    (["第12话 重逢-SiteComic"], "12", "第12话 重逢"),
    (["第3话 Re-Start"], "3", "第3话 Re-Start"),
])
def test_the_chapter_number_and_name_are_read_from_the_page_titles(titles, number, label):
    ref = titles_ref("https://www.example.com/web/comic/123456/", titles)
    assert (ref["id"], ref["title"]) == (number, label)


def titles_ref(url, page_titles):
    return titles.capture_chapter_ref(url, "run-abc12345", page_titles)


@pytest.mark.parametrize("page_titles", [["Vol 3"], ["第2卷"], ["touch 5"], ["Series 86"], []])
def test_a_volume_or_a_bare_number_in_a_title_is_not_a_chapter(page_titles):
    assert titles_ref("https://example.com/viewer?id=833532", page_titles)["title"].startswith("Imported ")


def test_the_address_number_wins_and_a_title_with_another_number_is_not_its_label():
    url = "https://comic.naver.com/webtoon/detail?titleId=1&no=4"
    assert titles_ref(url, ["Name - 4화 Home"])["title"] == "4화 Home"
    assert titles_ref(url, ["Name - 3화"])["title"] == "Chapter 4"


def test_the_same_chapter_from_the_title_or_the_address_is_one_chapter():
    by_title = titles_ref("https://s.example/web/comic/987/", ["第4话"])
    by_address = titles.capture_chapter_ref("https://s.example/c?no=4", "run-zzzz9999")
    assert by_title["key"] == by_address["key"]


def test_pages_sent_with_page_titles_are_filed_under_that_chapter(token, fake_pipeline, drama):
    url = "https://s.example/web/comic/987/"
    _send(token, drama, (255, 0, 0), source_url=url, run_id="run-aaaa1111",
          page_titles=["Series", "Series 第4话 出发 - SiteComic"])
    _send(token, drama, (0, 255, 0), source_url="https://s.example/web/comic/988/", run_id="run-bbbb2222",
          page_titles=["Series 第5话"])
    assert [(g["title"], g["page_count"]) for g in _groups(drama)] == [("第4话 出发", 1), ("第5话", 1)]


@pytest.mark.parametrize("bad", ["第4话", [5, None], {"a": 1}, ["x" * 5000]])
def test_odd_page_titles_never_fail_the_send(token, fake_pipeline, drama, bad):
    out = _send(token, drama, (255, 0, 0), source_url="https://s.example/viewer/x/9a8f",
                run_id="run-cccc3333", page_titles=bad)
    assert out["stored"] == 1
    assert len(_groups(drama)) == 1
