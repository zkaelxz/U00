"""The extension's auto-import: page_server's POST /title and
services/extension_title_service.py find or create the title (and series) a page
belongs to, so the popup no longer needs a drama picked first.

The answer reaches the extension's own worker, but like /novel it carries ids,
booleans and the stored display name only: never the page URL or a path."""
import json
import threading

import pytest

import db
import page_server
from services import extension_title_service as titles
from services import ownership_service

from tests.test_page_server import _post

OWNER = {"user_id": None, "is_local_owner": True}
PAGE = "https://www.twmanga.com/comic/chapter/abc/0_12.html?token=s3cret"
SERIES_PAGE = "https://www.twmanga.com/comic/abc"
CANDIDATES = ["一人之下 第12话 - 腾讯漫画", "One Punch Man Chapter 12 | twmanga", "twmanga"]


@pytest.fixture
def token(isolated_db):
    return page_server.load_or_create_token()


def _resolve(token, **body):
    payload = {"url": PAGE, "titles": CANDIDATES, **body}
    return _post(token, payload, path="/title")


def _all_titles():
    return db.list_dramas()


def test_creates_a_title_with_names_link_language_and_series(token):
    h = _resolve(token)
    assert h.status == 200, h.payload
    assert h.payload["created"] is True and h.payload["matched"] is False
    drama = db.get_drama(h.payload["drama_id"])
    assert drama["title_zh"] == "一人之下"
    assert drama["title_en"] == "One Punch Man"
    assert drama["source_language"] == "zh" and drama["media_type"] == "manhua"
    assert drama["source_url"] == "https://www.twmanga.com/comic/chapter/abc/0_12.html"
    assert drama["series_id"] == h.payload["series_id"]
    assert db.get_series_id_by_name("一人之下") == h.payload["series_id"]


def test_the_name_in_the_wrong_script_never_lands_in_the_original_field(token):
    h = _resolve(token, titles=["The Mage Considered Incompetent - Chapter 3"])
    drama = db.get_drama(h.payload["drama_id"])
    assert drama["title_zh"] in ("", None)
    assert drama["title_en"] == "The Mage Considered Incompetent"


def test_the_reply_carries_ids_booleans_and_the_name_but_no_link(token):
    h = _resolve(token)
    body = json.dumps(h.payload, ensure_ascii=False)
    assert "twmanga" not in body and "s3cret" not in body and db.LIBRARY_DIR not in body
    assert set(h.payload) >= {"drama_id", "created", "matched", "series_id", "filled", "title"}


def test_japanese_and_korean_pages_pick_their_language_and_media_type(token):
    ja = _resolve(token, url="https://raw.example/a/1", titles=["ふつうの軽音部 第3話 | raw"])
    ko = _resolve(token, url="https://raw.example/b/1", titles=["나 혼자만 레벨업 12화"])
    assert db.get_drama(ja.payload["drama_id"])["source_language"] == "ja"
    assert db.get_drama(ja.payload["drama_id"])["media_type"] == "manga"
    assert db.get_drama(ko.payload["drama_id"])["source_language"] == "ko"
    assert db.get_drama(ko.payload["drama_id"])["media_type"] == "manhwa"


def test_another_chapter_of_the_same_series_reuses_the_title_and_series(token):
    first = _resolve(token).payload
    again = _resolve(token, url="https://www.twmanga.com/comic/chapter/abc/0_13.html",
                     titles=["一人之下 第13话 - 腾讯漫画"]).payload
    assert again["matched"] is True and again["created"] is False
    assert again["drama_id"] == first["drama_id"] and again["series_id"] == first["series_id"]
    assert len(_all_titles()) == 1 and len(db.list_series()) == 1


def test_a_title_with_the_same_source_link_is_reused_whatever_the_name(token):
    drama_id = db.create_drama(title_zh="手改的名字", source_language="zh", media_type="manhua",
                               source_url=SERIES_PAGE)
    h = _resolve(token, url=SERIES_PAGE + "?utm=1").payload
    assert h["matched"] is True and h["drama_id"] == drama_id
    assert len(_all_titles()) == 1


def test_the_same_name_on_another_site_is_a_new_title(token):
    _resolve(token)
    other = _resolve(token, url="https://other.example/read/9").payload
    assert other["created"] is True
    assert len(_all_titles()) == 2


def test_a_novel_never_matches_a_comic_of_the_same_name(token):
    comic = _resolve(token).payload
    novel = _resolve(token, kind="novel").payload
    assert novel["created"] is True and novel["drama_id"] != comic["drama_id"]
    assert db.get_drama(novel["drama_id"])["media_type"] == "novel"


def test_a_matched_title_keeps_the_names_you_typed(token):
    linked = db.create_drama(title_zh="一人之下", title_en="My Own English Name",
                             source_language="zh", media_type="manhua",
                             source_url="https://www.twmanga.com/comic/abc")
    again = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert again["drama_id"] == linked
    assert db.get_drama(linked)["title_en"] == "My Own English Name"
    assert "title_en" not in again["filled"]


def test_blank_fields_of_a_matched_title_are_filled_and_the_link_set_once(token):
    drama_id = db.create_drama(title_zh="一人之下", source_language="zh", media_type="manhua",
                               source_url="https://www.twmanga.com/comic/abc")
    h = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert h["matched"] is True
    assert db.get_drama(drama_id)["title_en"] == "One Punch Man"
    assert "title_en" in h["filled"]
    assert db.get_drama(drama_id)["source_url"] == "https://www.twmanga.com/comic/abc"


def test_a_matched_title_already_in_a_series_stays_there(token):
    series_id = db.create_series("My Own Series")
    drama_id = db.create_drama(title_zh="一人之下", source_language="zh", media_type="manhua",
                               source_url="https://www.twmanga.com/comic/abc", series_id=series_id)
    h = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert h["series_id"] == series_id
    assert db.get_drama(drama_id)["series_id"] == series_id
    assert db.get_series_id_by_name("一人之下") is None


def test_a_matched_title_with_no_series_joins_one_when_grouping_is_on(token):
    drama_id = db.create_drama(title_zh="一人之下", source_language="zh", media_type="manhua",
                               source_url="https://www.twmanga.com/comic/abc")
    off = _resolve(token, url="https://www.twmanga.com/comic/abc", series=False).payload
    assert off["series_id"] is None and db.get_drama(drama_id)["series_id"] is None
    on = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert on["series_id"] == db.get_series_id_by_name("一人之下")
    assert db.get_drama(drama_id)["series_id"] == on["series_id"]


def test_an_existing_series_of_that_name_is_joined_not_duplicated(token):
    ownership_service.set_share_by_default(OWNER, True)
    existing = db.create_series("一人之下", is_private=0)
    h = _resolve(token).payload
    assert h["series_id"] == existing and len(db.list_series()) == 1


def test_with_grouping_off_no_series_is_made(token):
    h = _resolve(token, series=False).payload
    assert h["series_id"] is None and db.list_series() == []
    assert db.get_drama(h["drama_id"])["series_id"] is None


def test_with_filling_off_the_title_is_a_placeholder_with_no_names(token):
    h = _resolve(token, fill=False).payload
    drama = db.get_drama(h["drama_id"])
    assert drama["title_zh"] == "twmanga.com"
    assert not drama["title_en"]
    assert drama["source_url"] == "https://www.twmanga.com/comic/chapter/abc/0_12.html"


def test_with_filling_off_a_second_chapter_reuses_the_placeholder(token):
    first = _resolve(token, fill=False).payload
    again = _resolve(token, fill=False, url="https://www.twmanga.com/comic/chapter/abc/0_13.html").payload
    assert again["matched"] is True and again["drama_id"] == first["drama_id"]
    assert len(_all_titles()) == 1


def test_a_page_with_no_readable_title_gets_the_site_as_a_placeholder(token):
    h = _resolve(token, titles=["", "   "]).payload
    assert db.get_drama(h["drama_id"])["title_zh"] == "twmanga.com"


def test_with_creating_off_a_page_with_no_title_is_refused_and_nothing_is_made(token):
    h = _resolve(token, create=False)
    assert h.status == 422 and "Pick a drama" in h.payload["error"]
    assert _all_titles() == []


def test_with_creating_off_an_existing_title_is_still_found(token):
    first = _resolve(token).payload
    h = _resolve(token, create=False).payload
    assert h["matched"] is True and h["drama_id"] == first["drama_id"]


def test_bad_input_is_refused_and_nothing_is_made(token):
    assert _resolve(token, titles="not a list").status == 422
    assert _resolve(token, titles=[5]).status == 422
    assert _resolve(token, url=["x"]).status == 422
    assert _resolve(token, url="ftp://x.example/a").status == 422
    assert _resolve(token, kind="video").status == 422
    assert _all_titles() == []


def test_oversized_names_are_cut_and_control_characters_dropped(token):
    h = _resolve(token, titles=["书\x00\x07名" * 150]).payload
    name = db.get_drama(h["drama_id"])["title_zh"]
    assert "\x00" not in name and "\x07" not in name and len(name) <= titles.MAX_NAME_CHARS


def test_the_route_sits_behind_the_bridge_token_and_loopback_check(token):
    assert _resolve(None).status == 401
    assert _resolve("wrong").status == 401
    assert _post(token, {"url": PAGE, "titles": CANDIDATES}, path="/title",
                 client=("192.168.1.9", 5000)).status == 403
    assert _all_titles() == []


def test_a_failure_names_no_path(token, monkeypatch):
    def boom(*a, **k):
        raise PermissionError(13, "denied", db.LIBRARY_DIR + "/secret")
    monkeypatch.setattr(titles.drama_service, "create_drama", boom)
    h = _resolve(token)
    assert h.status == 500 and db.LIBRARY_DIR not in json.dumps(h.payload)


def test_two_sends_at_once_make_one_title(token):
    results = []

    def go():
        results.append(titles.resolve_title({"url": PAGE, "titles": CANDIDATES}))

    threads = [threading.Thread(target=go) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r["drama_id"] for r in results}) == 1
    assert sum(1 for r in results if r["created"]) == 1 and len(_all_titles()) == 1


# -- other people's titles and series, kinds, and invisible characters --

def _someone_elses(**fields):
    return db.create_drama(title_zh="一人之下", source_language="zh", media_type="manhua",
                           source_url="https://www.twmanga.com/comic/abc", **fields)


def _state(drama_id):
    d = db.get_drama(drama_id)
    return (d["title_en"], d["source_url"], d["series_id"], d["is_private"], d["owner_user_id"])


def test_another_users_private_title_is_not_matched_filled_or_joined(token):
    theirs = _someone_elses(owner_user_id=999, is_private=1)
    before = _state(theirs)
    h = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert h["created"] is True and h["drama_id"] != theirs
    assert _state(theirs) == before
    assert db.get_series_id_by_name("一人之下") != db.get_drama(theirs)["series_id"]


def test_a_title_in_another_users_private_series_is_not_matched(token):
    series = db.create_series("他的系列", owner_user_id=999, is_private=1)
    theirs = _someone_elses(owner_user_id=999, series_id=series)
    before = _state(theirs)
    h = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert h["created"] is True
    assert _state(theirs) == before


@pytest.mark.parametrize("share", [True, False])
def test_a_series_of_that_name_owned_by_someone_else_is_not_joined(token, share):
    ownership_service.set_share_by_default(OWNER, share)
    theirs = db.create_series("一人之下", owner_user_id=999, is_private=0)
    h = _resolve(token).payload
    assert h["created"] is True and h["series_id"] != theirs
    assert db.get_drama(h["drama_id"])["series_id"] != theirs


@pytest.mark.parametrize("share", [True, False])
def test_privacy_and_visibility_are_unchanged_after_auto(token, share):
    ownership_service.set_share_by_default(OWNER, share)
    mine = db.create_drama(title_zh="一人之下", source_language="zh", media_type="manhua",
                           source_url="https://www.twmanga.com/comic/abc",
                           is_private=int(not share))
    member = {"user_id": 5, "is_admin": False, "is_local_owner": False}
    seen_before = ownership_service.can_see(member, "drama", mine)
    h = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert h["drama_id"] == mine
    assert ownership_service.can_see(member, "drama", mine) is seen_before
    new = _resolve(token, url="https://other.example/x", titles=["新作 第1话"]).payload
    assert ownership_service.can_see(member, "drama", new["drama_id"]) is share


def test_a_comic_page_never_matches_an_audio_or_video_title(token):
    audio = db.create_drama(title_zh="一人之下", source_language="zh", media_type="audio_drama",
                            source_url="https://www.twmanga.com/comic/abc")
    h = _resolve(token, url="https://www.twmanga.com/comic/abc").payload
    assert h["created"] is True and h["drama_id"] != audio


def test_bidi_and_zero_width_characters_are_stripped_from_names(token):
    h = _resolve(token, titles=["一\u202e人\u200f之\u2066下 第3话"]).payload
    assert db.get_drama(h["drama_id"])["title_zh"] == "一人之下"
    assert db.get_series_id_by_name("一人之下") == h["series_id"]
