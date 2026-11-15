"""misheard_finder / misheard_service: sound-alike suggestions, offline with a fake word cutter."""
import importlib.util

import pytest

import db
import misheard_finder as mf
from core import Line
from services import misheard_service

pytest.importorskip("pypinyin")


def cut(text):
    """Two-character words, which is all these fake lines hold."""
    return [text[i:i + 2] for i in range(0, len(text), 2)]


def _lines(*texts):
    return [(i, 100 + i, t) for i, t in enumerate(texts)]


class TestFinder:
    def test_rare_sound_alike_of_a_glossary_name_is_suggested(self):
        out = mf.find_misheard(_lines("亞羅赫來了"[:4], "亞洛赫說"[:4]), [("亞洛", "glossary")], cut)
        found = {s["heard"]: s for s in out["suggestions"]}
        assert found["亞羅"]["suggested"] == "亞洛"
        assert found["亞羅"]["source"] == "glossary" and found["亞羅"]["lines"][0]["line_id"] == 100

    def test_a_word_heard_often_is_trusted_and_a_rare_twin_points_to_it(self):
        lines = _lines("書院", "書院", "書院", "數院")
        out = mf.find_misheard(lines, [], cut)
        assert [(s["heard"], s["suggested"], s["source"]) for s in out["suggestions"]] == [
            ("數院", "書院", "frequent")]
        assert out["suggestions"][0]["suggested_count"] == 3

    def test_traditional_and_simplified_are_one_word(self):
        fold = {"书": "書"}.get
        out = mf.find_misheard(_lines("书院", "書院", "書院", "書院"), [], cut,
                               canon=lambda w: "".join(fold(c, c) for c in w))
        assert out["suggestions"] == []

    def test_different_length_or_unrelated_sound_is_not_suggested(self):
        out = mf.find_misheard(_lines("今天", "今天", "今天", "明年"), [("亞洛赫", "name")], cut)
        assert out["suggestions"] == []

    def test_glossary_outranks_a_frequent_word(self):
        lines = _lines("書院", "書院", "書院", "數院")
        out = mf.find_misheard(lines, [("輸院", "glossary")], cut)
        assert out["suggestions"][0]["suggested"] == "輸院"

    def test_capped_and_reports_more_existed(self, monkeypatch):
        monkeypatch.setattr(mf, "MAX_SUGGESTIONS", 1)
        out = mf.find_misheard(_lines("書院", "書院", "書院", "數院", "疏院"), [], cut)
        assert len(out["suggestions"]) == 1 and out["truncated"]


@pytest.mark.skipif(importlib.util.find_spec("jieba") is None, reason="jieba not installed")
def test_service_reads_title_lines(isolated_db):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="zh")
    db.upsert_glossary_term(sid, "亞洛赫", "Yaloch", aliases="")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="亞羅赫來了", en="")])
    out = misheard_service.find_for_title(did)
    assert out["available"] and out["suggestions"][0]["suggested"] == "亞洛赫"


def test_service_reports_unavailable_for_other_languages(isolated_db):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="ja")
    out = misheard_service.find_for_title(did)
    assert not out["available"] and out["suggestions"] == []


def test_service_reports_missing_jieba(isolated_db, monkeypatch):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language="zh")
    monkeypatch.setattr(misheard_service.segment, "missing_word_segmenters", lambda lang: ["jieba"])
    out = misheard_service.find_for_title(did)
    assert not out["available"] and "jieba" in out["reason"]
