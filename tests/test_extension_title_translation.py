"""A title the capture creates with only its original name gets one English name from one small
AI call, once, and never over a name already there."""
import pytest

import db
import page_server
from engine_backends import engine_registry, llm_tasks
from lib.errors import UnsupportedOperationError
from services import translate_run_service

from tests.test_page_server import _post

KOREAN_ONLY = ["남편이 아이를 숨겼다 1화 - 네이버 웹툰", "네이버 웹툰"]
URL = "https://comic.naver.com/webtoon/detail?titleId=1&no=1"


@pytest.fixture
def token(isolated_db):
    return page_server.load_or_create_token()


@pytest.fixture
def ai(monkeypatch):
    calls = []
    page_server.set_translation_config(engine="claude", api_key="k", base_url=None, free_tier=False)
    monkeypatch.setattr(engine_registry, "get_engine", lambda *a, **k: object())
    monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent", lambda *a, **k: None)

    def fake(engine, prompt, **kwargs):
        calls.append(prompt)
        return '{"title": "My Husband Hid the Child"}'
    monkeypatch.setattr(llm_tasks, "call_llm_json", fake)
    yield calls
    page_server.set_translation_config(engine=None, api_key="", base_url=None, free_tier=False)


def _resolve(token, **body):
    return _post(token, {"url": URL, "titles": KOREAN_ONLY, **body}, path="/title")


def test_a_new_title_gets_an_english_name_beside_the_original(token, ai):
    h = _resolve(token)
    drama = db.get_drama(h.payload["drama_id"])
    assert drama["title_zh"] == "남편이 아이를 숨겼다" and drama["title_en"] == "My Husband Hid the Child"
    assert "title_en" in h.payload["filled"] and len(ai) == 1


def test_a_later_chapter_does_not_spend_again(token, ai):
    _resolve(token)
    _resolve(token, url="https://comic.naver.com/webtoon/detail?titleId=1&no=2")
    assert len(ai) == 1


def test_a_name_that_is_already_there_is_kept_and_nothing_is_spent(token, ai):
    h = _resolve(token, titles=["One Punch Man Chapter 1 | site", "남편이 아이를 숨겼다 1화 - 네이버 웹툰"])
    assert db.get_drama(h.payload["drama_id"])["title_en"] == "One Punch Man"
    assert ai == []


def test_fill_off_spends_nothing(token, ai):
    _resolve(token, fill=False)
    assert ai == []


def test_a_spent_monthly_cap_skips_the_call_and_the_import_goes_on(token, ai, monkeypatch):
    def refuse(*a, **k):
        raise UnsupportedOperationError("The monthly cap is used up.")
    monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent", refuse)
    h = _resolve(token)
    assert h.status == 200 and db.get_drama(h.payload["drama_id"])["title_en"] == ""
    assert ai == []


def test_an_engine_without_a_key_skips_the_call(token, ai, monkeypatch):
    page_server.set_translation_config(engine="claude", api_key="", base_url=None, free_tier=False)
    h = _resolve(token)
    assert h.status == 200 and ai == []


def test_an_unusable_reply_leaves_the_name_blank(token, ai, monkeypatch):
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: "sorry, no")
    h = _resolve(token)
    assert h.status == 200 and db.get_drama(h.payload["drama_id"])["title_en"] == ""


def test_it_uses_the_engine_the_extension_is_set_up_with_not_the_default(token, ai, monkeypatch):
    seen = []
    monkeypatch.setattr(engine_registry, "get_engine",
                        lambda name, *a, **k: seen.append(name) or object())
    from tests.saved_settings import patch_setting
    patch_setting(monkeypatch, "default_engine", "openai")
    _resolve(token)
    assert seen == ["claude"]


def test_two_new_titles_at_once_cannot_both_pass_a_cap_with_room_for_one(token, ai, monkeypatch):
    # The spend is only recorded once the reply is back, so a check that runs while another title's
    # call is still out sees room that call is about to use.
    import threading
    from services import extension_title_service
    spent, entered, other_entered = [], [], threading.Event()

    def refuse(*a, **k):
        if spent:
            raise UnsupportedOperationError("The monthly cap is used up.")
    monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent", refuse)

    def slow(engine, prompt, **kwargs):
        entered.append(prompt)
        if len(entered) == 1:
            other_entered.wait(1.0)
        else:
            other_entered.set()
        spent.append(prompt)
        return '{"title": "My Husband Hid the Child"}'
    monkeypatch.setattr(llm_tasks, "call_llm_json", slow)
    ids = [db.create_drama(title_zh=f"남편 {n}", source_language="ko") for n in (1, 2)]
    threads = [threading.Thread(target=extension_title_service._translate_title_once,
                                args=(drama_id, "남편이 아이를 숨겼다", "ko")) for drama_id in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(entered) == 1


def test_the_series_is_named_with_the_english_title(token, ai):
    h = _resolve(token)
    assert db.get_series_id_by_name("My Husband Hid the Child") == h.payload["series_id"]
    assert db.get_series_id_by_name("남편이 아이를 숨겼다") is None


def test_the_series_keeps_the_raw_name_when_translation_fails(token, ai, monkeypatch):
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: "sorry, no")
    h = _resolve(token)
    assert db.get_series_id_by_name("남편이 아이를 숨겼다") == h.payload["series_id"]


def test_an_existing_raw_named_series_is_joined_not_duplicated(token, ai):
    existing = db.create_series("남편이 아이를 숨겼다", is_private=True)
    h = _resolve(token)
    assert h.payload["series_id"] == existing
    assert db.get_series_id_by_name("My Husband Hid the Child") is None


def test_a_capture_arriving_during_the_translation_leaves_the_series_to_the_first(token, ai, monkeypatch):
    import threading
    started, release = threading.Event(), threading.Event()

    def slow(engine, prompt, **kwargs):
        started.set()
        release.wait(5)
        return '{"title": "My Husband Hid the Child"}'
    monkeypatch.setattr(llm_tasks, "call_llm_json", slow)
    first = []
    t = threading.Thread(target=lambda: first.append(_resolve(token)))
    t.start()
    assert started.wait(5)
    second = _resolve(token, url="https://comic.naver.com/webtoon/detail?titleId=1&no=2")
    assert second.payload["series_id"] is None
    release.set()
    t.join(5)
    assert db.get_series_id_by_name("남편이 아이를 숨겼다") is None
    assert db.get_drama(first[0].payload["drama_id"])["series_id"] == first[0].payload["series_id"]
    assert len(db.list_series()) == 1


def _other_site(token, **body):
    return _post(token, {"url": "https://page.kakao.com/content/1/viewer/2", "titles": KOREAN_ONLY, **body},
                 path="/title")


def test_the_same_work_from_two_sites_with_different_translations_is_one_series(token, ai, monkeypatch):
    replies = iter(['{"title": "The Outcast"}', '{"title": "Under One Person"}'])
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: next(replies))
    first, second = _resolve(token), _other_site(token)
    assert first.payload["drama_id"] != second.payload["drama_id"]
    assert first.payload["series_id"] is not None and second.payload["series_id"] == first.payload["series_id"]
    assert len(db.list_series()) == 1


def test_a_translation_differing_only_in_case_joins_the_existing_series(token, ai, monkeypatch):
    existing = db.create_series("My Husband Hid the Child", is_private=True)
    monkeypatch.setattr(llm_tasks, "call_llm_json", lambda *a, **k: '{"title": "MY HUSBAND HID THE CHILD"}')
    assert _resolve(token).payload["series_id"] == existing
    assert len(db.list_series()) == 1


def test_a_failed_series_join_does_not_leave_the_title_marked_in_progress(token, ai, monkeypatch):
    from services import extension_title_service as svc

    def boom(*a, **k):
        raise RuntimeError("join failed")
    monkeypatch.setattr(svc, "_join_series", boom)
    with pytest.raises(RuntimeError):
        svc.resolve_title({"url": URL, "titles": KOREAN_ONLY})
    assert svc._series_pending == set()


def test_invisible_characters_in_the_translation_are_removed_from_title_and_series(token, ai, monkeypatch):
    monkeypatch.setattr(llm_tasks, "call_llm_json",
                        lambda *a, **k: '{"title": "My\\u202e Hus\\u200bband  Hid"}')
    h = _resolve(token)
    assert db.get_drama(h.payload["drama_id"])["title_en"] == "My Husband Hid"
    assert db.get_series_id_by_name("My Husband Hid") == h.payload["series_id"]
