"""Tests for services/study_service.py: one line's words, readings and meanings, from local data only."""
import pytest

from core import Line
from lib.errors import NotFoundError
from services import study_service


def _title(db, language, text):
    did = db.create_drama(title_en="T", source_language=language)
    db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh=text, en="Hello")])
    return did, db.load_line_objects(did)[0].id


def test_words_carry_their_reading_and_a_saved_meaning(isolated_db, monkeypatch):
    monkeypatch.setattr(study_service.segment, "segment_and_annotate",
                        lambda text, lang, chinese_script="simplified": [("你好", "nǐ hǎo"), ("。", None)])
    did, lid = _title(isolated_db, "zh", "你好。")
    isolated_db.save_vocab_lookup(did, "你好", "nǐ hǎo", ["hello"], "zh")
    out = study_service.line_study(did, lid)
    assert out["original"] == "你好。"
    assert out["words"] == [{"word": "你好", "reading": "nǐ hǎo", "meanings": ["hello"], "start": None},
                            {"word": "。", "reading": None, "meanings": [], "start": None}]


def test_a_tap_never_starts_the_dictionary_download(isolated_db, monkeypatch):
    monkeypatch.setattr(study_service.segment, "segment_and_annotate", lambda *a, **k: [("你好", "nǐ hǎo")])
    monkeypatch.setattr(study_service.dictionary, "CEDICT_PATH", "/nonexistent/cedict.txt")

    def boom(word):
        raise AssertionError("looked up without the dictionary on disk")
    monkeypatch.setattr(study_service.dictionary, "lookup_cedict", boom)
    did, lid = _title(isolated_db, "zh", "你好")
    assert study_service.line_study(did, lid)["words"][0]["reading"] == "nǐ hǎo"


def test_a_missing_word_splitter_is_named_not_raised(isolated_db, monkeypatch):
    def missing(*a, **k):
        raise ImportError("no sudachi", name="sudachipy")
    monkeypatch.setattr(study_service.segment, "segment_and_annotate", missing)
    did, lid = _title(isolated_db, "ja", "こんにちは")
    out = study_service.line_study(did, lid)
    assert out["needs_install"] == "sudachipy" and out["words"] == []


def test_an_english_line_has_no_breakdown(isolated_db):
    did, lid = _title(isolated_db, "zh", "hello")
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="hello", en="Hello", lang="en")])
    lid = isolated_db.load_line_objects(did)[0].id
    assert study_service.line_study(did, lid)["words"] == []


def test_another_titles_line_is_not_found(isolated_db):
    did, _ = _title(isolated_db, "zh", "你好")
    with pytest.raises(NotFoundError):
        study_service.line_study(did, 999999)


def test_each_word_starts_when_its_first_character_is_heard(isolated_db, monkeypatch):
    from segment_splitting import encode_line_words
    monkeypatch.setattr(study_service.segment, "segment_and_annotate",
                        lambda text, lang, chinese_script="simplified": [("你好", "nǐ hǎo"), ("世界", "shì jiè")])
    did = isolated_db.create_drama(title_en="T", source_language="zh")
    # Whisper heard it in other pieces than the word splitter cut it into.
    words = [{"word": "你", "start": 1.0, "end": 1.2}, {"word": "好世", "start": 1.2, "end": 1.6},
             {"word": "界", "start": 1.6, "end": 1.8}]
    isolated_db.save_lines(did, [Line(idx=0, start=1.0, end=2.0, zh="你好世界", en="Hello world",
                                      word_timings=encode_line_words("你好世界", words))])
    lid = isolated_db.load_line_objects(did)[0].id
    assert [w["start"] for w in study_service.line_study(did, lid)["words"]] == [1.0, 1.2]
