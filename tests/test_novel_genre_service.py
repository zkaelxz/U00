import json

import pytest

import db
from core import Line
from engine_backends import local as engine_local
from services import novel_genre_service as genre, profile_service, translate_run_service

HISTORICAL = ["殿下说：", "公主在府中等你。", "将军进了宫中。", "陛下召见微臣。"]
MODERN = ["她拿起手机。", "公司楼下的咖啡很好喝。", "老师在学校等同学。"]


def _novel(texts, **fields):
    fields.setdefault("content_mode", "novel_narration")
    did = db.create_drama(title_zh="D", **fields)
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z) for i, z in enumerate(texts)])
    return db.get_drama(did)


@pytest.fixture(autouse=True)
def _no_ollama_probe(monkeypatch):
    monkeypatch.setattr(engine_local, "check_ollama_reachable", lambda url: False)


def test_detect_setting_needs_a_clear_majority():
    assert genre.detect_setting("".join(HISTORICAL)) == "historical"
    assert genre.detect_setting("".join(MODERN)) == "modern"
    # Too few clues, or both settings at once (a transmigration story): no call.
    assert genre.detect_setting("公主来了。") is None
    assert genre.detect_setting("".join(HISTORICAL[:2] + MODERN)) is None
    assert genre.detect_setting("") is None


def test_only_chinese_novel_titles_are_read(isolated_db):
    assert genre.title_setting(_novel(HISTORICAL)) == "historical"
    assert genre.title_setting(_novel(HISTORICAL, content_mode="audio_drama")) is None
    assert genre.title_setting(_novel(HISTORICAL, source_language="ja")) is None


def test_default_note_prefers_settings_then_historical_then_none(isolated_db):
    historical, modern = _novel(HISTORICAL), _novel(MODERN)
    assert genre.default_style_note(historical, "Mine.") == "Mine."
    assert genre.default_style_note(historical, "") == genre.HISTORICAL_STYLE_NOTE
    assert genre.default_style_note(modern, "") == ""


def test_translate_form_starts_with_the_note_for_a_historical_novel(isolated_db):
    historical, modern = _novel(HISTORICAL), _novel(MODERN)
    config = translate_run_service.get_translate_config
    assert config(historical["id"])["default_style_note"] == genre.HISTORICAL_STYLE_NOTE
    assert config(modern["id"])["default_style_note"] == ""


def test_unattended_run_keeps_the_titles_own_note(isolated_db):
    historical = _novel(HISTORICAL)
    assert profile_service.locale_and_note(historical, "en-US", "")[1] == genre.HISTORICAL_STYLE_NOTE
    # A note chosen for the title before, even an empty one, is the title's choice.
    db.update_drama(historical["id"], translate_start_json=json.dumps({"style_note": ""}))
    assert profile_service.locale_and_note(db.get_drama(historical["id"]), "en-US", "")[1] == ""
