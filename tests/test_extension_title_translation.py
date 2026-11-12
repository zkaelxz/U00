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
